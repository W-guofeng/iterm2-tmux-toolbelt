#!/usr/bin/env python3
"""Capture the actual product page in native WebKit with anonymous local fixtures.

Uses real isolated tmux CRUD and a private PTY client. SSH/focus metadata is an
explicit demo adapter, not a claim of remote discovery or physical sidebar QA.
Never registers a tool or touches the user's iTerm2 sessions/settings.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Obj

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from toolbelt.core import Service,Target
from toolbelt.http import LocalServer
from toolbelt.process import parse_ssh_argv
from toolbelt.ssh import QueryChannel,SSHTransport


async def run(args):
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tb-doc-demo-',dir='/tmp') as folder:
        root=Path(folder);socket=str(root/'server.sock');fds=[];children=[];readers=[]
        env={**os.environ,'TMUX':'','TERM':'xterm-256color','PATH':str(Path(args.tmux).resolve().parent)+':'+os.environ['PATH']}
        async def cli(*command):
            process=await asyncio.create_subprocess_exec(args.tmux,'-S',socket,'-f','/dev/null',*command,env=env,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            data,_=await process.communicate()
            if process.returncode:raise RuntimeError('Private demonstration tmux command failed')
            return data.decode().strip()
        async def spawn(argv):
            # Replace transport only. Run the real, nonce-framed remote protocol
            # locally, with socket discovery fenced to this private fixture.
            script=argv[-1].replace('/tmp',str(root))
            return await asyncio.create_subprocess_exec('/bin/sh','-c',script,env=env,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        class DemoTerminal:
            async def attach(self,transport,row,identity):
                master,slave=pty.openpty();fds.extend([master,slave])
                code='import os,fcntl,termios;os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0);os.execv('+repr(args.tmux)+','+repr([args.tmux,'-S',socket,'-f','/dev/null','attach-session','-t',row.id])+')'
                proc=subprocess.Popen([sys.executable,'-c',code],env=env,stdin=slave,stdout=slave,stderr=slave)
                children.append(proc);transport.tty=os.ttyname(slave)
                os.set_blocking(master,False)
                async def drain():
                    while proc.poll() is None:
                        try: os.read(master,65536)
                        except BlockingIOError: pass
                        except OSError: return
                        await asyncio.sleep(.02)
                readers.append(asyncio.create_task(drain()))
                for _ in range(60):
                    fresh,_=await transport.async_inspect()
                    if fresh.current==row.id:return
                    await asyncio.sleep(.05)
                raise RuntimeError('Private demonstration attachment was not confirmed')
            async def return_to_ssh(self,transport):transport.tty='-';transport.clientpid='-'
        channel=QueryChannel(parse_ssh_argv(['ssh','demo@localhost']),spawn=spawn)
        transport=SSHTransport(channel,DemoTerminal());server=None
        try:
            for name in ('research','build-api','review'):
                await cli('new-session','-d','-s',name,'sleep','600')
            transport.socket=socket
            await transport.initialize();transport.socket=socket
            target=Target('demo-window','demo-tab','demo-pane','demo-connection',transport)
            class Resolver:
                async def resolve(self):return target
            service=Service(Resolver(),timeout=12)
            class Demo:
                async def snapshot(self):
                    state=await service.snapshot()
                    state['appearance']={'mode':'dark','contrast':False}
                    if state.get('target'):
                        state['target'].update(host='demo@localhost',socket='demo-socket',sockets=[],selected_socket='demo-socket')
                    if args.setup:
                        state['setup']={'kind':'noecho-missing','available':True,'action':'setup-noecho',
                            'button':'启用无回显附着',
                            'message':'首次切换需要 zsh 无回显 helper。点击后新增 ~/.iterm2_toolbelt_attach.zsh（0600），备份并追加 ~/.zshrc 加载行；新连接生效。不会修改当前前台程序。'}
                    return state
                async def action(self,payload):
                    if str(payload.get('action','')).startswith('setup'):raise RuntimeError('Documentation fixtures cannot configure hosts')
                    return await service.action(payload)
            server=LocalServer(Demo());url=await server.start()
            native='zh-Hans' if args.language=='zh-CN' else 'en'
            child=await asyncio.create_subprocess_exec(args.binary,'-AppleLanguages','('+native+')','-AppleLocale','zh_CN' if native=='zh-Hans' else 'en_US',
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            payload={'url':url,'output':str(output),'language':args.language,'setup':args.setup}
            stdout,_=await asyncio.wait_for(child.communicate(json.dumps(payload).encode()),70)
            result=json.loads(stdout)
            result.update(real_native_webkit=True,real_private_tmux=True,ssh_focus_metadata='anonymous demo adapter',production_sessions_used=False)
            print(json.dumps(result),flush=True)
            if child.returncode or not result.get('passed'):raise SystemExit(1)
        finally:
            if server:await server.close()
            await channel.close()
            try:await cli('kill-server')
            except RuntimeError:pass
            for reader in readers:reader.cancel()
            # Close our private PTY first so a blocked terminal write can exit.
            for fd in fds:
                try:os.close(fd)
                except OSError:pass
            for proc in children:
                if proc.poll() is None:
                    proc.terminate()
                    try: await asyncio.to_thread(proc.wait,2)
                    except subprocess.TimeoutExpired:
                        proc.kill();await asyncio.to_thread(proc.wait,2)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tmux',required=True)
    parser.add_argument('--binary',default='/tmp/toolbelt-capture-screenshots')
    parser.add_argument('--output',required=True)
    parser.add_argument('--language',choices=('en','zh-CN'),required=True)
    parser.add_argument('--setup',action='store_true')
    asyncio.run(run(parser.parse_args()))
