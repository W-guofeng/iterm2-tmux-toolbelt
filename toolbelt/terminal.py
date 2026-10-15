"""Attach using a verified ZLE widget, in the exact original idle SSH pane."""
import asyncio
import re
from .bridge import KEY, parse_ready
from .core import ToolbeltError


class ManagedTerminal:
    def __init__(self, api, app, resolver):
        self.api = api
        self.app = app
        self.resolver = resolver

    async def refresh_client(self, transport):
        await self.app.async_refresh()
        pane = self.app.get_session_by_id(transport.managed)
        transport.tty = '-'
        if not pane:
            transport.managed = None
            transport.clientpid = '-'
            return
        value = await pane.async_get_variable('user.toolbelt_tmux_tty')
        if isinstance(value, str) and value.startswith(transport.marker+':'):
            tty = value[len(transport.marker)+1:]
            if re.fullmatch(r'/dev/(pts/[0-9]+|ttys[0-9A-Za-z]+|tty[0-9]+)', tty):
                transport.tty = tty

    async def verify_idle(self, active, pane, transport):
        await self.app.async_refresh()
        window = self.app.current_terminal_window
        tab = window.current_tab if window else None
        focused = tab.current_session if tab else None
        if not focused or (window.window_id, tab.tab_id, focused.session_id) != (active.window, active.tab, active.pane):
            raise ToolbeltError('活动 pane 已改变，附着取消。', 'stale')
        prompt = await self.api.async_get_last_prompt(self.resolver.connection, active.pane)
        host = await pane.async_get_variable('hostname')
        user = await pane.async_get_variable('username')
        if not isinstance(host, str) or host.rstrip('.').lower() != transport.host.rstrip('.').lower() or user != transport.user:
            raise ToolbeltError('尚未收到匹配的远端 Shell Integration 主机/用户信息；不会向当前终端发送输入。', 'unsupported')
        if not prompt or prompt.state != self.api.PromptState.EDITING or (prompt.command or '') != '':
            raise ToolbeltError('当前 SSH pane 不是已验证的空闲且空输入提示符；不会向 Claude 或未完成命令发送输入。', 'unsupported')
        if await pane.async_get_variable('user.toolbelt_zsh_display_version') != '3':
            raise ToolbeltError('当前 SSH 仍加载旧版 helper，可能附着成功却无法显示。请使用侧栏一键修复并另开 SSH 连接。', 'unsupported')
        return parse_ready(await pane.async_get_variable('user.toolbelt_zsh_ready'), transport.uid)

    async def attach(self, transport, row, identity):
        active = await self.resolver.resolve()
        if active.transport is not transport:
            raise ToolbeltError('活动 pane 已改变，附着取消。', 'stale')
        pane = self.app.get_session_by_id(active.pane)
        if not pane: raise ToolbeltError('当前 pane 已退出。', 'offline', 503)
        # Never holds an iTerm2 transaction while waiting on network I/O.
        async with self.api.Transaction(self.resolver.connection):
            bridge = await self.verify_idle(active, pane, transport)
        fields = bridge.fields(transport, row, identity)
        prepared = sent = False
        try:
            await transport.channel.request('prepare-attach', **fields)
            prepared = True
            async with self.api.Transaction(self.resolver.connection):
                if await self.verify_idle(active, pane, transport) != bridge:
                    raise ToolbeltError('远端提示符或 helper 身份已变化，附着取消。', 'stale')
                transport.marker = bridge.nonce
                transport.managed = transport.origin = active.pane
                transport.tty = '-'
                # No printable command, newline, history or shell code is sent.
                await pane.async_send_text(KEY, suppress_broadcast=True)
                sent = True
        finally:
            if prepared and not sent:
                try:
                    await asyncio.wait_for(transport.channel.request('cancel-attach', **fields), 2)
                except Exception:
                    pass  # Expiring data cannot execute without this exact widget.
        for _ in range(35):
            await self.refresh_client(transport)
            if transport.tty != '-':
                fresh, _ = await transport.async_inspect()
                if fresh.current == row.id: return
            result = await pane.async_get_variable('user.toolbelt_zsh_result')
            if isinstance(result, str) and result.startswith(bridge.nonce+'|'):
                reason = result.split('|', 1)[1]
                raise ToolbeltError('无回显附着未完成（' + reason + '）；请刷新核实，不会重复输入。', 'stale')
            await asyncio.sleep(.1)
        raise ToolbeltError('无回显附着已请求，但尚未确认客户端。请刷新核实，不会自动重发。', 'offline', 503)

    async def return_to_ssh(self, transport):
        transport.tty = '-'
        transport.clientpid = '-'
