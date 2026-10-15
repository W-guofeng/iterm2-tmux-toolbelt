"""Follow the exact active pane: prefer -CC; otherwise verify foreground SSH."""
import asyncio
import time
import socket
from .core import Target, ToolbeltError
from .process import ForegroundSSH
from .ssh import QueryChannel, SSHTransport
from .terminal import ManagedTerminal


class ItermResolver:
    def __init__(self, iterm2, connection, app, detector=None, channel_factory=None):
        self.iterm2 = iterm2
        self.connection = connection
        self.app = app
        self.detector = detector or ForegroundSSH()
        self.channel_factory = channel_factory or QueryChannel
        self.terminal = ManagedTerminal(iterm2, app, self)
        self.channels = {}
        self.failures = {}

    async def resolve(self):
        await self.app.async_refresh()
        window = self.app.current_terminal_window
        tab = window.current_tab if window else None
        pane = tab.current_session if tab else None
        if not pane:
            raise ToolbeltError('没有活动终端 pane。', 'offline', 503)
        role = await pane.async_get_variable('tmuxRole')
        if role in ('client','gateway'):
            connections = await self.iterm2.async_get_tmux_connections(self.connection)
            if role == 'client':
                matches = [c for c in connections if c.connection_id == tab.tmux_connection_id]
            else:
                matches = [c for c in connections if c.owning_session and c.owning_session.session_id == pane.session_id]
            if len(matches) != 1:
                raise ToolbeltError('当前 pane 的 tmux 连接已断开或无法唯一确定。', 'offline', 503)
            conn = matches[0]
            return Target(window.window_id, tab.tab_id, pane.session_id, conn.connection_id, conn)
        spec = await self.detector.identify(pane)
        if spec.bootstrap != 'plain':
            level = await pane.async_get_variable('sshIntegrationLevel')
            if level not in (1, 2):
                raise ToolbeltError('已识别原生 SSH Profile 包装，但 SSH Integration 尚未确认连接，请稍后刷新。', 'unsupported')
        if spec.key in self.failures and time.monotonic() < self.failures[spec.key][0]:
            raise self.failures[spec.key][1]
        transport = self.channels.get(spec.key)
        if transport is None:
            if len(self.channels) >= 8:
                raise ToolbeltError('查询连接达到上限（8）。请重启本脚本释放已关闭的连接。', 'offline', 503)
            transport = SSHTransport(self.channel_factory(spec), self.terminal)
            try:
                await transport.initialize()
            except BaseException as exc:
                await transport.channel.close()
                if isinstance(exc, ToolbeltError): self.failures[spec.key] = (time.monotonic()+60, exc)
                raise
            transport.origin = pane.session_id
            self.channels[spec.key] = transport
        integration_host = await pane.async_get_variable('hostname')
        local_host = socket.gethostname().rstrip('.').lower()
        if isinstance(integration_host, str) and integration_host and integration_host.rstrip('.').lower() not in {transport.host.rstrip('.').lower(), local_host, local_host.split('.')[0]}:
            raise ToolbeltError('Shell Integration 主机与本机 SSH 目标不一致，可能是嵌套 SSH 或旧状态。已禁用操作；不会猜测另一台主机。', 'unsupported')
        return Target(window.window_id, tab.tab_id, pane.session_id, 'ssh:'+transport.channel.generation, transport)

    async def appearance(self):
        try:
            theme = await asyncio.wait_for(self.app.async_get_theme(), 1)
        except Exception:
            return {'mode': 'auto', 'contrast': False}
        return {'mode': 'dark' if 'dark' in theme else 'light' if 'light' in theme else 'auto',
                'contrast': 'highContrast' in theme}

    async def describe(self, target):
        from .setup import describe
        return await describe(self, target)

    async def configure(self, target):
        from .setup import configure
        return await configure(self, target)

    async def configure_noecho(self, target):
        from .setup import configure
        return await configure(self, target, noecho=True)

    async def reconnect(self):
        await self.app.async_refresh()
        window = self.app.current_terminal_window
        tab = window.current_tab if window else None
        pane = tab.current_session if tab else None
        if not pane: raise ToolbeltError('没有活动 SSH pane。', 'offline', 503)
        if await pane.async_get_variable('tmuxRole') in ('client','gateway'): return
        spec = await self.detector.identify(pane)
        prior = self.channels.pop(spec.key, None)
        self.failures.pop(spec.key, None)
        if prior: await prior.channel.close()
        target = await self.resolve()
        if prior and target.transport.host == prior.host and target.transport.user == prior.user:
            target.transport.socket = prior.socket
            for name in ('managed','origin','marker','tty','clientpid'):
                if hasattr(prior,name): setattr(target.transport,name,getattr(prior,name))

    async def close(self):
        for transport in self.channels.values(): await transport.channel.close()
