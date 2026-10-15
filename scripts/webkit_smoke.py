#!/usr/bin/env python3
"""Native WebKit + real disposable tmux fixture. Never drives a user's server."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from toolbelt.core import Service, Target
from toolbelt.http import LocalServer
from tests.test_tmux_integration import ControlTransport


async def run(args):
    with tempfile.TemporaryDirectory(prefix="toolbelt-webkit-") as folder:
        socket = str(Path(folder) / "server.sock")
        async def cli(*command):
            process = await asyncio.create_subprocess_exec(args.tmux, "-S", socket, "-f", "/dev/null", *command,
                env={**os.environ,"TMUX":"","TERM":"xterm-256color"},stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            output,error = await process.communicate()
            if process.returncode: raise RuntimeError("Isolated tmux command failed")
            return output.decode().strip()
        transport = server = None
        try:
            await cli("new-session","-d","-s","dev","sleep","600")
            await cli("set-option","-g","default-command","sleep 600")
            await cli("new-session","-d","-s","build","sleep","600")
            await cli("new-session","-d","-s","review","sleep","600")
            process = await asyncio.create_subprocess_exec(args.tmux,"-S",socket,"-f","/dev/null","-C","attach-session","-t","=dev",
                env={**os.environ,"TMUX":"","TERM":"xterm-256color"},stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            transport = ControlTransport(process)
            await transport.block()
            target = Target("qa-window","qa-tab","qa-pane","qa-connection",transport)
            class Resolver:
                async def resolve(self): return target
            service=Service(Resolver())
            class Fixture:
                configured=False
                reads=0
                async def snapshot(self):
                    state=await service.snapshot()
                    self.reads += 1
                    if args.theme_cycle:
                        state['appearance']={'mode':'dark' if self.reads < 4 else 'light','contrast':False}
                    if args.setup:
                        state['setup']={'kind':'reconnect' if self.configured else 'missing','available':not self.configured,
                                        'action':'setup-noecho' if args.noecho else 'setup',
                                        'button':'启用无回显附着' if args.noecho else '一键配置远端集成',
                                        'message':'配置将复制官方集成脚本，备份并补充 ~/.zshrc；新连接生效。'}
                    if state["target"]:
                        state["target"]["host"]="demo-host"
                        state["target"]["socket"]="/tmp/demo.sock"
                        state["target"]["client"]="test-client"
                    return state
                async def action(self,payload):
                    if args.setup and payload.get('action')==('setup-noecho' if args.noecho else 'setup'):
                        self.configured=True
                        return {'ok':True,'message':'隔离界面测试回执：已配置，请另开连接。备份：~/.zshrc.toolbelt-backup-fixture'}
                    return await service.action(payload)
            server=LocalServer(Fixture())
            url=await server.start()
            payload={"url":url,"width":args.width,"appearance":args.appearance,"screenshot":args.screenshot,"crud":args.crud,"setup":args.setup,"themeCycle":args.theme_cycle}
            child=await asyncio.create_subprocess_exec(args.binary,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            output,_=await asyncio.wait_for(child.communicate(json.dumps(payload).encode()),50)
            result=json.loads(output)
            result["transport"]=server.diagnostics.copy()
            result["remaining_sessions"]=(await cli("list-sessions","-F","#{session_name}")).splitlines()
            print(json.dumps(result,ensure_ascii=False))
            if child.returncode or not result.get("passed") or server.diagnostics["ui_reports"] == 0:
                raise SystemExit(1)
        finally:
            if server: await server.close()
            if transport: await transport.close()
            try: await cli("kill-server")
            except RuntimeError: pass


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--tmux",required=True,help="Explicit local tmux binary for disposable sockets")
    parser.add_argument("--binary",default="/tmp/toolbelt-webkit-smoke")
    parser.add_argument("--width",type=int,default=240)
    parser.add_argument("--appearance",choices=("dark","light"),default="dark")
    parser.add_argument("--screenshot")
    parser.add_argument("--crud",action="store_true")
    parser.add_argument("--noecho",action="store_true",help="Exercise explicit no-echo setup action in the setup fixture")
    parser.add_argument("--setup",action="store_true",help="Fixture-only configuration card click; no remote config changes")
    parser.add_argument("--theme-cycle",action="store_true",help="Fixture app appearance changes dark to light")
    asyncio.run(run(parser.parse_args()))
