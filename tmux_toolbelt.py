#!/usr/bin/env python3
"""Run manually with iTerm2's Python environment; never enables the API."""
import asyncio
import sys
import os
import time
import importlib.metadata
import iterm2
from toolbelt import __version__
from toolbelt.adapter import ItermResolver
from toolbelt.core import Service
from toolbelt.http import LocalServer
from toolbelt.runtime import InstanceLock, TOOL_IDENTIFIER, TOOL_NAME, reveal_toolbelt, bounded_operation, wait_for_disconnect_or_stall
from toolbelt.observability import configure, event

RESTART_REQUESTED = False
CONNECTED_AT = None


async def main(connection):
    global RESTART_REQUESTED, CONNECTED_AT
    if '--iterm-qa' in sys.argv:
        from toolbelt.qa import run
        await run(iterm2, connection)
        return
    if '--diagnose' in sys.argv:
        from toolbelt.diagnostics import inspect_setup
        await inspect_setup(iterm2, connection)
        return
    instance = InstanceLock()
    server = None
    resolver = None
    try:
        if not instance.acquire():
            event("INSTANCE_ALREADY_RUNNING")
            print("tmux Toolbelt 已在运行；显示现有侧栏，不启动第二个服务。", flush=True)
            await reveal_toolbelt(iterm2, connection, remount=True)
            return
        CONNECTED_AT = time.monotonic()
        event("API_CONNECTED")
        app = await bounded_operation(iterm2.async_get_app(connection), 10, "startup.app")
        resolver = ItermResolver(iterm2, connection, app)
        server = LocalServer(Service(resolver, timeout=12))
        server.runtime["api_connected"] = True
        url = await server.start()
        event("HTTP_STARTED",pid=os.getpid())
        await bounded_operation(iterm2.tool.async_register_web_view_tool(
            connection, TOOL_NAME, TOOL_IDENTIFIER, True, url), 10, "startup.register")
        visible = await reveal_toolbelt(iterm2, connection, remount=True)
        print("tmux Toolbelt 已启动；" + ("侧栏已显示。" if visible else "请手动显示 Toolbelt。"), flush=True)
        print("跟随活动 SSH / tmux -CC pane；普通 SSH 使用独立无交互查询；仅点击时在已验证的空闲 SSH 提示符附着。", flush=True)
        event("PANEL_REGISTERED",revision=server.ui_revision)
        if '--disconnect-once' in sys.argv:
            sys.argv.remove('--disconnect-once')
            async def test_disconnect():
                await asyncio.sleep(3)
                event('QA_CLOSE_API_ONCE')
                await connection.websocket.close()
            asyncio.create_task(test_disconnect())
        code = await wait_for_disconnect_or_stall(connection, server.states.stalled)
        if code == 'state-stalled':
            event('STATE_STALL_RECOVERY')
            try:
                await bounded_operation(connection.websocket.close(),1,'recovery.api_close')
            except Exception:
                pass
        server.runtime['api_connected'] = False
        event('API_DISCONNECTED',close_code=code)
        RESTART_REQUESTED = True
    except Exception as exc:
        event('RUNTIME_FAILED',error_type=type(exc).__name__)
        RESTART_REQUESTED = True
    finally:
        if server:
            await server.close()
        if resolver:
            try:
                await bounded_operation(resolver.close(),2,'shutdown.query_channels')
            except Exception:
                pass
        instance.close()


if __name__ == "__main__":
    log_path = configure()
    event('PROCESS_START',pid=os.getpid())
    print("日志：" + log_path,flush=True)
    print("tmux Toolbelt " + __version__ + " / Python " + sys.version.split()[0] + " / SDK " + importlib.metadata.version("iterm2"), flush=True)
    print("正在连接 iTerm2 Python API…首次授权提示需由你确认。", flush=True)
    try:
        iterm2.run_until_complete(main)
    finally:
        if RESTART_REQUESTED:
            attempts=int(os.environ.get('IT2_TOOLBELT_RECOVERY_ATTEMPTS','0'))
            if CONNECTED_AT and time.monotonic()-CONNECTED_AT>30:
                attempts=0
            if attempts<3:
                os.environ['IT2_TOOLBELT_RECOVERY_ATTEMPTS']=str(attempts+1)
                event('PROCESS_RECONNECT',attempt=attempts+1)
                time.sleep(min(attempts+1,3))
                os.execv(sys.executable,[sys.executable,*sys.argv])
            event('RECOVERY_LIMIT_REACHED')
            print('iTerm2 API 连续断开，已停止自动重试。请查看日志后在 Scripts 重启本插件。',flush=True)
