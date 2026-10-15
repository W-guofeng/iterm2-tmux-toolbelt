import asyncio
import os
from pathlib import Path
import pty
import sys
import tempfile
import unittest
from types import SimpleNamespace as Obj
from toolbelt.core import Service, Target, ToolbeltError
from toolbelt.process import parse_ssh_argv
from toolbelt.ssh import QueryChannel, SSHTransport, socket_path
from toolbelt.terminal import ManagedTerminal
from toolbelt.bridge import KEY
from unittest.mock import AsyncMock


class SSHArgumentTests(unittest.IsolatedAsyncioTestCase):
    def test_route_auth_and_combined_flags(self):
        spec=parse_ssh_argv(['/usr/bin/ssh','-vvtt','-p2222','-i','/a key','-J','jump','-o','HostName=example','user@alias'])
        self.assertEqual(spec.destination,'user@alias')
        self.assertEqual(spec.options,('-p','2222','-i','/a key','-J','jump','-o','HostName=example'))

    def test_reject_ambiguous_or_remote_commands(self):
        for args in (['-N','host'],['-W','target:22','jump'],['-L','1:2:3','host'],['-o','SetEnv=x=y','host'],['-o','LocalCommand=evil','host'],['-p'],['host;id'],['$(id)'],['-host'],['host','ssh','inner']):
            with self.subTest(args=args), self.assertRaises(ToolbeltError): parse_ssh_argv(['ssh',*args])

    def test_socket_fields(self):
        self.assertEqual(socket_path('/tmp/a;$(id) sock'),'/tmp/a;$(id) sock')
        for path in ('relative','/tmp/a\nb','/tmp/a\tb','/tmp/%x','/tmp/\\a'):
            with self.assertRaises(ToolbeltError): socket_path(path)

    def test_channel_safety_overrides_original_options(self):
        channel=QueryChannel(parse_ssh_argv(['ssh','-oStrictHostKeyChecking=no','example']))
        channel.master='/tmp/private/master'
        argv=channel.argv('fixed command')
        self.assertIn('-oStrictHostKeyChecking=yes',argv)
        self.assertNotIn('StrictHostKeyChecking=no',argv)
        self.assertIn('-oBatchMode=yes',argv)
        self.assertIn('-oPermitLocalCommand=no',argv)
        self.assertEqual(argv[-2:],["example","fixed command"])


TMUX=os.environ.get('TMUX_TEST_BINARY')

@unittest.skipUnless(TMUX, 'Set TMUX_TEST_BINARY for disposable real-tmux tests')
class SSHProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tb-remote-',dir='/tmp')
        self.root=Path(self.tmp.name)
        self.children=[]
        self.fds=[]
        self.sockets=set()
        async def spawn(argv):
            # Redirect socket discovery strictly to this test directory. Same
            # POSIX sh protocol, but this test does not claim SSH auth coverage.
            command=argv[-1].replace('/tmp', str(self.root)).replace('\"$bin\" -u', '\"$bin\" -f /dev/null -u')
            return await asyncio.create_subprocess_exec('/bin/sh','-c',command,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ,'TMPDIR':str(self.root),'PATH':str(Path(TMUX).parent)+':'+os.environ['PATH']},limit=262144)
        self.channel=QueryChannel(parse_ssh_argv(['ssh','fixture.invalid']),spawn=spawn)
        self.transport=SSHTransport(self.channel)
        target=Target('w','t','p','ssh:'+self.channel.generation,self.transport)
        class Resolver:
            async def resolve(self): return target
        self.service=Service(Resolver(),timeout=12)

    async def cli(self, socket, *args):
        self.sockets.add(str(socket))
        proc=await asyncio.create_subprocess_exec(TMUX,'-S',str(socket),'-f','/dev/null',*args,
            env={**os.environ,'TMUX':'','TERM':'xterm-256color'},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await proc.communicate()
        if proc.returncode: raise RuntimeError('Disposable tmux command failed: '+err.decode())
        return out.decode().strip()

    async def asyncTearDown(self):
        if self.transport.socket: self.sockets.add(self.transport.socket)
        await self.channel.close()
        for socket in self.sockets:
            try: await self.cli(socket,'kill-server')
            except RuntimeError: pass
        for proc in self.children:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
        for fd in self.fds:
            try: os.close(fd)
            except OSError: pass
        self.tmp.cleanup()

    async def create(self,name='first'):
        state=await self.service.snapshot()
        self.assertEqual(state['status'],'online',state['message'])
        await self.service.action({'context':state['context'],'action':'create','name':name})
        self.sockets.add(self.transport.socket)
        return await self.service.snapshot()

    async def client(self, socket, sid):
        master,slave=pty.openpty()
        self.fds.append(master)
        # New process owns only this disposable pty, never a user's TTY.
        code='import os,fcntl,termios; os.setsid(); fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv('+repr(TMUX)+','+repr([TMUX,'-S',socket,'-f','/dev/null','attach-session','-t',sid])+')'
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c',code,stdin=slave,stdout=slave,stderr=slave,
                env={**os.environ,'TMUX':'','TERM':'xterm-256color'})
        os.close(slave)
        self.children.append(proc)
        for _ in range(50):
            table=await self.cli(socket,'list-clients','-F','#{client_tty}|#{client_pid}|#{session_id}')
            for line in table.splitlines():
                path,pid,current=line.split('|')
                if pid==str(proc.pid): return path,pid,proc
            await asyncio.sleep(.02)
        self.fail('Disposable pty client did not attach')

    async def test_empty_create_rename_delete_then_empty(self):
        empty=await self.service.snapshot()
        self.assertEqual(empty['status'],'online',empty['message'])
        self.assertEqual(empty['sessions'],[])
        self.assertEqual(empty['target']['server_pid'],'absent')
        self.assertFalse(Path(self.transport.socket).exists())
        state=await self.create('中文任务')
        row=state['sessions'][0]
        self.assertEqual(row['name'],'中文任务')
        self.assertEqual(row['attached'],0)
        base={'context':state['context'],'id':row['id'],'created':row['created']}
        await self.service.action({**base,'action':'rename','name':'renamed'})
        await self.service.action({**base,'action':'delete'})
        result=await self.service.snapshot()
        self.assertEqual(result['sessions'],[])
        self.assertEqual(result['status'],'online')

    async def test_socket_selection_and_stale_context_are_isolated(self):
        a=self.root/'a.sock'; b=self.root/'b.sock'
        await self.cli(a,'new-session','-d','-s','same','sleep','600')
        await self.cli(b,'new-session','-d','-s','same','sleep','600')
        state=await self.service.snapshot()
        await self.service.action({'context':state['context'],'action':'socket','socket':str(a)})
        first=await self.service.snapshot(); row=first['sessions'][0]
        await self.service.action({'context':first['context'],'action':'socket','socket':str(b)})
        with self.assertRaises(ToolbeltError):
            await self.service.action({'context':first['context'],'action':'delete','id':row['id'],'created':row['created']})
        self.assertEqual(await self.cli(a,'list-sessions','-F','#{session_name}'),'same')
        self.assertEqual(await self.cli(b,'list-sessions','-F','#{session_name}'),'same')

    async def test_multiple_discovered_sockets_require_choice(self):
        directory=self.root/('tmux-'+str(os.getuid())); directory.mkdir()
        for name in ('a','b'): await self.cli(directory/name,'new-session','-d','-s','same','sleep','600')
        state=await self.service.snapshot()
        self.assertEqual(state['status'],'unsupported')
        with self.assertRaises(ToolbeltError):
            await self.service.action({'context':state['context'],'action':'create','name':'wrong'})
        await self.service.action({'context':state['context'],'action':'socket','socket':str(directory/'b')})
        self.assertEqual((await self.service.snapshot())['status'],'online')

    async def test_switch_and_detach_preserve_other_client_and_tasks(self):
        state=await self.create()
        await self.service.action({'context':state['context'],'action':'create','name':'second'})
        state=await self.service.snapshot()
        first=state['sessions'][0]; second=state['sessions'][1]
        tty,pid,owned=await self.client(self.transport.socket,first['id'])
        other_tty,other_pid,other=await self.client(self.transport.socket,first['id'])
        self.transport.tty=tty
        state=await self.service.snapshot()
        await self.service.action({'context':state['context'],'action':'switch','id':second['id'],'created':second['created']})
        clients=await self.cli(self.transport.socket,'list-clients','-F','#{client_pid}|#{session_id}')
        self.assertIn(other_pid+'|'+first['id'],clients)
        self.assertIn(pid+'|'+second['id'],clients)
        state=await self.service.snapshot()
        await self.service.action({'context':state['context'],'action':'detach'})
        await asyncio.wait_for(owned.wait(),2)
        self.assertIsNone(other.returncode)
        self.assertEqual(len((await self.service.snapshot())['sessions']),2)

    async def test_delete_current_switches_only_owned_client_before_kill(self):
        state=await self.create('first')
        await self.service.action({'context':state['context'],'action':'create','name':'second'})
        state=await self.service.snapshot(); first,second=state['sessions']
        tty,pid,owned=await self.client(self.transport.socket,first['id'])
        other_tty,other_pid,other=await self.client(self.transport.socket,second['id'])
        self.transport.tty=tty
        state=await self.service.snapshot()
        await self.service.action({'context':state['context'],'action':'delete','id':first['id'],'created':first['created']})
        result=await self.service.snapshot()
        self.assertEqual([row['id'] for row in result['sessions']],[second['id']])
        self.assertIsNone(owned.returncode)
        self.assertIsNone(other.returncode)
        clients=await self.cli(self.transport.socket,'list-clients','-F','#{client_pid}|#{session_id}')
        self.assertIn(pid+'|'+second['id'],clients)
        self.assertIn(other_pid+'|'+second['id'],clients)

    async def test_detach_refuses_destroy_unattached_policy(self):
        state=await self.create()
        row=state['sessions'][0]
        tty,pid,client=await self.client(self.transport.socket,row['id'])
        self.transport.tty=tty
        await self.cli(self.transport.socket,'set-option','-t',row['id'],'destroy-unattached','on')
        state=await self.service.snapshot()
        with self.assertRaises(ToolbeltError) as error:
            await self.service.action({'context':state['context'],'action':'detach'})
        self.assertEqual(error.exception.code,'policy')
        self.assertIsNone(client.returncode)
        self.assertEqual(len((await self.service.snapshot())['sessions']),1)

    async def test_invalid_socket_scope_can_be_corrected_in_toolbelt(self):
        state=await self.create()
        good=self.transport.socket
        invalid=str(self.root/'not-a-socket')
        Path(invalid).write_text('fixture')
        await self.service.action({'context':state['context'],'action':'socket','socket':invalid})
        offline=await self.service.snapshot()
        self.assertEqual(offline['status'],'offline')
        self.assertIsNotNone(offline['target'])
        await self.service.action({'context':offline['context'],'action':'socket','socket':good})
        self.assertEqual((await self.service.snapshot())['status'],'online')

    async def test_remote_pid_created_and_injection_rejected(self):
        state=await self.create(); row=state['sessions'][0]
        with self.assertRaises(ToolbeltError):
            await self.channel.request('delete',self.transport.socket,sid=row['id'],expected='999999',created=row['created'])
        with self.assertRaises(ToolbeltError):
            await self.channel.request('delete',self.transport.socket,sid=row['id'],expected=state['target']['server_pid'],created=row['created']+1)
        with self.assertRaises(ToolbeltError):
            await self.channel.request('rename',self.transport.socket,sid=row['id'],name='x;kill-server')
        strange=str(self.root/'literal;$(touch hacked).sock')
        self.transport.socket=strange; self.sockets.add(strange)
        state=await self.service.snapshot()
        await self.service.action({'context':state['context'],'action':'create','name':'safe'})
        self.assertFalse((self.root/'hacked').exists())
        self.assertEqual(await self.cli(strange,'list-sessions','-F','#{session_name}'),'safe')

    async def test_disconnect_never_retries_mutations(self):
        await self.create(); proc=self.channel.process
        await self.channel.close()
        self.assertEqual((await self.service.snapshot())['status'],'offline')
        self.assertIs(self.channel.process,proc)


class TerminalSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent=[]
        self.vars={'user.toolbelt_zsh_display_version':'3','hostname':'fixture','username':'test','user.toolbelt_tmux_tty':'','user.toolbelt_zsh_ready':'2|1000|123|/dev/pts/7|'+('a'*32)+'|/tmp/it2tb-1000-123-abcdefgh'}
        async def var(name): return self.vars.get(name,'')
        async def send(text,**kwargs): self.sent.append((text,kwargs))
        async def refresh(): pass
        self.pane=Obj(session_id='p',async_get_variable=var,async_send_text=send)
        window=Obj(window_id='w',current_tab=Obj(tab_id='t',current_session=self.pane))
        self.app=Obj(current_terminal_window=window,async_refresh=refresh,get_session_by_id=lambda _:self.pane)
        self.prompt=Obj(state='editing',command='')
        async def prompt(*_): return self.prompt
        class Transaction:
            def __init__(self,*_): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*_): pass
        api=Obj(Transaction=Transaction,async_get_last_prompt=prompt,PromptState=Obj(EDITING='editing'))
        self.transport=Obj(host='fixture',user='test',uid='1000',channel=Obj(request=AsyncMock(return_value='')),socket='/tmp/fixture',managed=None,clientpid='-',tty='-')
        self.target=Target('w','t','p','connection',self.transport)
        async def resolve(): return self.target
        self.manager=ManagedTerminal(api,self.app,Obj(connection=None,resolve=resolve))
        self.identity=Obj(pid='12'); self.row=Obj(id='$1',created=1)

    async def test_running_claude_and_partial_command_never_receive_input(self):
        for state,command in [('running','claude'),('editing','unfinished'),('unknown','')]:
            self.prompt.state=state; self.prompt.command=command
            with self.assertRaises(ToolbeltError): await self.manager.attach(self.transport,self.row,self.identity)
        self.assertEqual(self.sent,[])

    async def test_wrong_host_or_user_never_receive_input(self):
        for key in ('hostname','username'):
            prior=self.vars[key]; self.vars[key]='other'
            with self.assertRaises(ToolbeltError): await self.manager.attach(self.transport,self.row,self.identity)
            self.vars[key]=prior
        self.assertEqual(self.sent,[])

    async def test_idle_original_pane_attaches_with_broadcast_suppressed(self):
        async def inspect(): return Obj(current='$1'),[]
        self.transport.async_inspect=inspect
        async def refresh(transport): transport.tty='/dev/pts/7'
        self.manager.refresh_client=refresh
        await self.manager.attach(self.transport,self.row,self.identity)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0][1],{'suppress_broadcast':True})
        self.assertEqual(self.transport.managed,'p')
        self.assertEqual(self.sent[0][0],KEY)
        self.assertEqual(self.transport.channel.request.await_args.args[0],'prepare-attach')
        # Test pane has no split/create/restart method: any such call fails.

    async def test_prepared_request_cancelled_when_user_input_or_nonce_changes(self):
        for change in ('input','nonce'):
            self.prompt.command=''
            self.vars['user.toolbelt_zsh_ready']='2|1000|123|/dev/pts/7|'+('a'*32)+'|/tmp/it2tb-1000-123-abcdefgh'
            ops=[]
            async def request(op,**fields):
                ops.append(op)
                if op=='prepare-attach':
                    if change=='input': self.prompt.command='unfinished'
                    else: self.vars['user.toolbelt_zsh_ready']=self.vars['user.toolbelt_zsh_ready'].replace('a'*32,'b'*32)
            self.transport.channel.request=request
            with self.assertRaises(ToolbeltError): await self.manager.attach(self.transport,self.row,self.identity)
            self.assertEqual(ops,['prepare-attach','cancel-attach'])
            self.assertEqual(self.sent,[])

    async def test_missing_bridge_never_falls_back_to_shell_text(self):
        self.vars['user.toolbelt_zsh_ready']=''
        with self.assertRaises(ToolbeltError): await self.manager.attach(self.transport,self.row,self.identity)
        self.assertEqual(self.sent,[])
        self.transport.channel.request.assert_not_awaited()

    async def test_old_display_helper_never_receives_attach_key(self):
        self.vars['user.toolbelt_zsh_display_version']=''
        with self.assertRaises(ToolbeltError): await self.manager.attach(self.transport,self.row,self.identity)
        self.assertEqual(self.sent,[])
        self.transport.channel.request.assert_not_awaited()

    async def test_return_does_not_send_input_or_create_pane(self):
        self.transport.tty='/dev/pts/7'; self.transport.clientpid='123'
        await self.manager.return_to_ssh(self.transport)
        self.assertEqual(self.sent,[])
        self.assertEqual(self.transport.clientpid,'-')


class SwitchVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_return_without_target_transition_is_rejected_without_retry(self):
        from toolbelt.core import Identity, Session
        channel=Obj(request=AsyncMock(return_value=''))
        transport=SSHTransport(channel)
        transport.socket='/tmp/test.sock';transport.tty='/dev/pts/7';transport.clientpid='123'
        before=Identity('user@fixture',transport.socket,'99','/dev/pts/7:123','$0')
        row=Session('$1',1,1,0,'two')
        transport.async_inspect=AsyncMock(return_value=(before,[row]))
        with self.assertRaises(ToolbeltError) as exc:
            await transport.async_perform_action('switch',before,row)
        self.assertEqual(exc.exception.code,'stale')
        channel.request.assert_awaited_once()
