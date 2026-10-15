#!/usr/bin/env python3
"""Real loopback SSH + disposable tmux QA. No installed settings or personal keys."""
import argparse
import asyncio
import getpass
import json
import os
import base64
import shlex
import pty
import tty as tty_module
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from toolbelt.core import Service, Target
from toolbelt.process import parse_ssh_argv, ForegroundSSH
from toolbelt.terminal import ManagedTerminal
from types import SimpleNamespace as Obj
from toolbelt.ssh import QueryChannel, SSHTransport


class SSHFixture:
    def __init__(self,tmux): self.tmux=str(Path(tmux).resolve())

    async def start(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tb-sshqa-',dir='/tmp')
        self.root=Path(self.temp.name)
        for name in ('host','client'):
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.root/name)],check=True)
        (self.root/'authorized_keys').write_bytes((self.root/'client.pub').read_bytes())
        hostkey=(self.root/'host.pub').read_text().split()[:2]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        (self.root/'known_hosts').write_text('[127.0.0.1]:'+str(self.port)+' '+' '.join(hostkey)+'\n')
        entry=self.root/'entry.sh'
        integration=Path('/Applications/iTerm.app/Contents/Resources/iterm2_shell_integration.zsh')
        (self.root/'.zshrc').write_text('source '+shlex.quote(str(integration))+'\nsource '+shlex.quote(str(ROOT/'toolbelt/zsh_attach.zsh'))+'\nPROMPT=__TOOLBELT_QA_READY__\nHISTFILE='+shlex.quote(str(self.root/'history'))+'\nHISTSIZE=1000\nSAVEHIST=1000\nsetopt INC_APPEND_HISTORY\n')
        entry.write_text('if test -n "$SSH_ORIGINAL_COMMAND"; then exec /bin/sh -c "$SSH_ORIGINAL_COMMAND"; fi\nexport ZDOTDIR='+shlex.quote(str(self.root))+' TERM_PROGRAM=iTerm.app\nunset ITERM_SHELL_INTEGRATION_INSTALLED\nexec /usr/bin/script -q /dev/null /bin/zsh -d -i\n')
        config=self.root/'sshd_config' 
        config.write_text(f'''ListenAddress 127.0.0.1
Port {self.port}
HostKey {self.root}/host
PidFile {self.root}/pid
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile {self.root}/authorized_keys
StrictModes no
AllowUsers {getpass.getuser()}
ForceCommand /bin/sh {entry}
SetEnv PATH={Path(self.tmux).parent}:/usr/bin:/bin:/usr/sbin:/sbin TMPDIR={self.root} TERM=xterm-256color SHELL=/bin/sh
''')
        self.log=open(self.root/'server.log','wb')
        self.server=await asyncio.create_subprocess_exec('/usr/sbin/sshd','-D','-e','-f',str(config),stdout=asyncio.subprocess.DEVNULL,stderr=self.log)
        for _ in range(30):
            try:
                reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
                await asyncio.wait_for(reader.readline(),1)
                writer.close();await writer.wait_closed()
                break
            except (OSError,asyncio.TimeoutError): await asyncio.sleep(.05)
        self.spec=parse_ssh_argv(['/usr/bin/ssh','-F','/dev/null','-p',str(self.port),'-i',str(self.root/'client'),
            '-o','UserKnownHostsFile='+str(self.root/'known_hosts'),'-o','GlobalKnownHostsFile=/dev/null',
            getpass.getuser()+'@127.0.0.1'])
        self.socket=str(self.root/'tmux.sock')
        class FixtureChannel(QueryChannel):
            def argv(self,remote,terminal=False):
                remote=remote.replace('"$bin" -u', '"$bin" -f /dev/null -u')
                return super().argv(remote,terminal)
        self.channel=FixtureChannel(self.spec)
        self.transport=SSHTransport(self.channel)
        await self.transport.initialize()
        # Explicit fixture socket before any snapshot or mutation. No production
        # session list or default socket is used for QA.
        self.transport.socket=self.socket
        target=Target('qa-window','qa-tab','qa-pane','ssh:'+self.channel.generation,self.transport)
        class Resolver:
            async def resolve(self): return target
        self.service=Service(Resolver(),timeout=12)
        return self

    async def close(self):
        if getattr(self,'master_fd',None) is not None:
            os.close(self.master_fd);self.master_fd=None
        if getattr(self,'terminal_process',None) and self.terminal_process.returncode is None:
            self.terminal_process.terminate()
            try: await asyncio.wait_for(self.terminal_process.wait(),2)
            except asyncio.TimeoutError:
                self.terminal_process.kill();await self.terminal_process.wait()
        if getattr(self,'master_fd',None) is not None: os.close(self.master_fd)
        if getattr(self,'channel',None): await self.channel.close()
        if getattr(self,'socket',None):
            proc=await asyncio.create_subprocess_exec(self.tmux,'-S',self.socket,'kill-server',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
        if getattr(self,'server',None) and self.server.returncode is None:
            self.server.terminate();await self.server.wait()
        if getattr(self,'log',None): self.log.close()
        if getattr(self,'temp',None): self.temp.cleanup()


async def terminal_roundtrip(fixture):
    master,slave=pty.openpty()
    tty_module.setraw(slave)
    fixture.master_fd=master
    os.set_blocking(master,False)
    tty=os.ttyname(slave)
    argv=['/usr/bin/ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes',*fixture.spec.options,'-T',fixture.spec.destination]
    code='import os,fcntl,termios; os.setsid(); fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv("/usr/bin/ssh",'+repr(argv)+')'
    fixture.terminal_process=await asyncio.create_subprocess_exec(sys.executable,'-c',code,stdin=slave,stdout=slave,stderr=slave,
        env={**os.environ,'TERM':'xterm-256color','SSH_ASKPASS_REQUIRE':'never'})
    os.close(slave)
    captured=bytearray()
    variable=''
    bridge_variable=''
    prompt=Obj(state='editing',command='')
    inputs=[]
    def drain():
        nonlocal variable,bridge_variable
        while True:
            try: data=os.read(master,32768)
            except BlockingIOError: break
            except OSError: break
            if not data: break
            captured.extend(data)
        matches=re.findall(rb'SetUserVar=toolbelt_tmux_tty=([A-Za-z0-9+/=]+)\x07',captured)
        if matches: variable=base64.b64decode(matches[-1]).decode()
        matches=re.findall(rb'SetUserVar=toolbelt_zsh_ready=([A-Za-z0-9+/=]+)\x07',captured)
        if matches: bridge_variable=base64.b64decode(matches[-1]).decode()
    async def ready(prior=None):
        for _ in range(60):
            drain()
            if bridge_variable.startswith('2|') and (prior is None or bridge_variable.split('|')[4]!=prior): return
            if fixture.terminal_process.returncode is not None: raise RuntimeError('Isolated SSH terminal exited')
            await asyncio.sleep(.05)
        raise RuntimeError('Isolated SSH terminal not ready')
    await ready()
    async def value(name):
        drain()
        return {'tty':tty,'hostname':fixture.transport.host,'username':fixture.transport.user,
                'user.toolbelt_tmux_tty':variable,'user.toolbelt_zsh_ready':bridge_variable,
                'user.toolbelt_zsh_display_version': '3' if b'SetUserVar=toolbelt_zsh_display_version=Mw==' in captured else ''}.get(name,'')
    async def send(text,**kwargs):
        assert kwargs.get('suppress_broadcast') is True
        inputs.append(text);captured.clear();prompt.state='running'
        os.write(master,text.encode())
    pane=Obj(session_id='qa-pane',async_get_variable=value,async_send_text=send)
    if sys.platform=='darwin':
        identified=await ForegroundSSH().identify(pane)
        assert identified.pid==fixture.terminal_process.pid and identified.destination==fixture.spec.destination
    async def refresh(): drain()
    app=Obj(current_terminal_window=Obj(window_id='qa-window',current_tab=Obj(tab_id='qa-tab',current_session=pane)),
            get_session_by_id=lambda _:pane,async_refresh=refresh)
    target=Target('qa-window','qa-tab','qa-pane','ssh:'+fixture.channel.generation,fixture.transport)
    async def resolve(): return target
    async def get_prompt(*_): return prompt
    class Transaction:
        def __init__(self,*_): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
    api=Obj(Transaction=Transaction,async_get_last_prompt=get_prompt,PromptState=Obj(EDITING='editing'))
    fixture.transport.terminal=ManagedTerminal(api,app,Obj(connection=None,resolve=resolve))
    state=await fixture.service.snapshot()
    for name in ('one','two'):
        await fixture.service.action({'context':state['context'],'action':'create','name':name})
        state=await fixture.service.snapshot()
    rows=state['sessions']
    async def click(row):
        state=await fixture.service.snapshot()
        await fixture.service.action({'context':state['context'],'action':'switch','id':row['id'],'created':row['created']})
    await click(rows[0])
    await click(rows[1])
    await click(rows[0])
    assert len(inputs)==1
    state=await fixture.service.snapshot()
    assert next(row for row in state['sessions'] if row['current'])['id']==rows[0]['id']
    prior=bridge_variable.split('|')[4]
    await fixture.service.action({'context':state['context'],'action':'detach'})
    await ready(prior)
    assert fixture.terminal_process.returncode is None and len(inputs)==1
    assert len((await fixture.service.snapshot())['sessions'])==2
    prompt.state='editing'
    await click(rows[1])
    state=await fixture.service.snapshot()
    assert next(row for row in state['sessions'] if row['current'])['id']==rows[1]['id']
    prior=bridge_variable.split('|')[4]
    await fixture.service.action({'context':state['context'],'action':'detach'})
    await ready(prior)
    assert all(value=='\x1b[1337;777~' for value in inputs)
    history=(fixture.root/'history').read_text() if (fixture.root/'history').exists() else ''
    assert 'base64 -d' not in history and 'if-shell' not in history
    return {'no_printable_attach_command':True,'history_clean':True,'switches_same_terminal':4,'safe_attach_inputs':len(inputs),'ssh_process_preserved':True,
            'sessions_preserved_after_detach':2,'native_iterm_api':False,'darwin_process_mapping':sys.platform=='darwin'}


async def run(tmux):
    fixture=SSHFixture(tmux)
    try:
        await fixture.start()
        state=await fixture.service.snapshot()
        assert state['status']=='online' and state['sessions']==[]
        await fixture.service.action({'context':state['context'],'action':'create','name':'ssh-测试'})
        state=await fixture.service.snapshot()
        assert len(state['sessions'])==1 and state['sessions'][0]['name']=='ssh-测试' and state['sessions'][0]['attached']==0
        row=state['sessions'][0]
        base={'context':state['context'],'id':row['id'],'created':row['created']}
        await fixture.service.action({**base,'action':'rename','name':'ssh-renamed'})
        await fixture.service.action({**base,'action':'delete'})
        assert (await fixture.service.snapshot())['sessions']==[]
        terminal=await terminal_roundtrip(fixture)
        print(json.dumps({'real_ssh':True,'private_loopback':True,'empty_create_rename_delete':'passed',
            'personal_keys_used':False,'production_mutations':False,'terminal':terminal}))
    finally: await fixture.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--tmux',required=True)
    asyncio.run(run(parser.parse_args().tmux))
