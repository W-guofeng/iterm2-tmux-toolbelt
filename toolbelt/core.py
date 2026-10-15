"""Validated context-bound tmux reads and explicit actions over exact transports."""
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata


class ToolbeltError(Exception):
    def __init__(self, message, code="invalid", status=409):
        super().__init__(message)
        self.code = code
        self.status = status


def readable(value, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum or any(
        unicodedata.category(c).startswith("C") for c in value
    ):
        raise ToolbeltError("服务器返回了无法安全显示的数据。", "protocol")
    return value


def validate_name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or not all(
        c in "_-" or unicodedata.category(c)[0] in "LN" for c in value
    ):
        raise ToolbeltError("名称须为 1–64 个字母、数字、中文、下划线或短横线。", status=400)
    return value


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"\$[0-9]{1,20}", value):
        raise ToolbeltError("无效的 session ID。", status=400)
    return value


def quote(value):
    # Callers use validated IDs/names or fixed format strings. Single quotes
    # suppress tmux variable expansion (including the $ in a session ID).
    if not isinstance(value, str) or "'" in value or "\n" in value or "\r" in value:
        raise ToolbeltError("不可序列化的 tmux 参数。", status=400)
    return "'" + value + "'"


IDENTITY_COMMAND = "display-message -p " + quote(
    "#{host}\t#{socket_path}\t#{pid}\t#{client_name}\t#{client_control_mode}\t#{session_id}"
)
LIST_COMMAND = "list-sessions -F " + quote(
    "#{session_id}\t#{session_created}\t#{session_windows}\t#{session_attached}\t#{session_name}"
)


@dataclass(frozen=True)
class Identity:
    host: str
    socket: str
    pid: str
    client: str
    current: str


def parse_identity(output):
    fields = output.rstrip("\n").split("\t")
    if len(fields) != 6:
        raise ToolbeltError("无法确认 tmux 服务器和控制客户端。", "protocol")
    host, socket, pid, client, control, current = fields
    for value in (host, socket, client):
        readable(value)
        if not value:
            raise ToolbeltError("tmux 连接身份不完整。", "protocol")
    if not re.fullmatch(r"[0-9]+", pid) or control != "1":
        raise ToolbeltError("当前连接不是可验证的 tmux 控制客户端。", "unsupported")
    return Identity(host, socket, pid, client, session_id(current))


@dataclass(frozen=True)
class Session:
    id: str
    created: int
    windows: int
    attached: int
    name: str


def parse_sessions(output):
    result = []
    seen = set()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            raise ToolbeltError("session 名称含控制字符，无法安全解析列表。", "protocol")
        sid, created, windows, attached, name = fields
        session_id(sid)
        if sid in seen or not all(re.fullmatch(r"[0-9]{1,20}", n) for n in (created, windows, attached)):
            raise ToolbeltError("tmux 列表格式异常。", "protocol")
        seen.add(sid)
        readable(name)
        result.append(Session(sid, int(created), int(windows), int(attached), name))
        if len(result) > 1000:
            raise ToolbeltError("session 数量超过面板上限（1000）。", "protocol")
    return result


@dataclass(frozen=True)
class Target:
    window: str
    tab: str
    pane: str
    connection: str
    transport: object

    def coordinates(self):
        return self.window, self.tab, self.pane, self.connection


def target_key(target, identity):
    # Opaque browser context; host and socket are displayed separately.
    encoded = json.dumps([*target.coordinates(), identity.host, identity.socket,
                          identity.pid, identity.client], ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


class Service:
    def __init__(self, resolver, timeout=5):
        self.resolver = resolver
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.phase = "idle"

    async def send(self, target, command):
        try:
            return await asyncio.wait_for(target.transport.async_send_command(command), self.timeout)
        except asyncio.TimeoutError as exc:
            raise ToolbeltError("tmux 请求超时；操作结果可能未知，请刷新确认，勿重复点击。", "timeout", 504) from exc
        except ToolbeltError:
            raise
        except Exception as exc:
            # RPC errors may include private server details. Never log them.
            raise ToolbeltError("tmux 连接已断开或命令失败，请刷新检查。", "offline", 503) from exc

    async def resolve(self):
        try:
            return await asyncio.wait_for(self.resolver.resolve(), self.timeout)
        except ToolbeltError:
            raise
        except Exception as exc:
            raise ToolbeltError("iTerm2 连接不可用。", "offline", 503) from exc

    async def inspect(self):
        self.phase = "target.resolve"
        target = await self.resolve()
        scope_needed = False
        scope_error = None
        if hasattr(target.transport, 'async_inspect'):
            try:
                self.phase = 'tmux.inspect'
                identity, rows = await asyncio.wait_for(target.transport.async_inspect(), self.timeout)
            except asyncio.TimeoutError as exc:
                raise ToolbeltError('目标状态查询超时，请刷新连接。', 'timeout', 504) from exc
            except ToolbeltError as exc:
                if exc.code == 'scope':
                    scope_needed = True
                    identity = Identity(target.transport.user+'@'+target.transport.host, '请选择 socket', 'scope', '-', '')
                    rows = []
                elif exc.code in ('offline','protocol','stale') and target.transport.host:
                    scope_error = exc
                    identity = Identity(target.transport.user+'@'+target.transport.host, target.transport.socket or '请选择 socket', 'unavailable', '-', '')
                    rows = []
                else: raise
        else:
            identity = parse_identity(await self.send(target, IDENTITY_COMMAND))
            rows = parse_sessions(await self.send(target, LIST_COMMAND))
        # Detect focus changes during the read. Never fall back to another connection.
        self.phase = "target.recheck"
        latest = await self.resolve()
        if latest.coordinates() != target.coordinates():
            raise ToolbeltError("活动 tab/pane 已改变，请等待刷新后重试。", "stale")
        key = target_key(target, identity)
        state = {"status": "online", "message": "跟随前台 iTerm2 窗口的活动 tab / pane",
                 "context": key, "target": {"host": identity.host, "socket": identity.socket,
                 "server_pid": identity.pid, "client": identity.client,
                 "window": target.window, "tab": target.tab, "pane": target.pane,
                 "connection": target.connection},
                 "sessions": [{**asdict(row), "current": row.id == identity.current} for row in rows]}
        if hasattr(target.transport, 'scope'):
            state['target'].update(target.transport.scope())
            state['message'] = '普通 SSH · 跟随当前 pane'
        if hasattr(self.resolver, 'describe'):
            self.phase = 'shell.describe'
            state['setup'] = await self.resolver.describe(target)
        if scope_needed:
            state['status'] = 'unsupported'
            state['message'] = '远端有多个 tmux socket，请先明确选择目标。'
        if scope_error:
            state['status'] = 'offline'
            state['message'] = str(scope_error)
        return target, identity, rows, state

    async def snapshot(self):
        async with self.lock:
            try:
                state = (await self.inspect())[3]
            except ToolbeltError as exc:
                state = {"status": "unsupported" if exc.code == "unsupported" else "offline",
                         "message": str(exc), "context": None, "target": None, "sessions": []}
            if hasattr(self.resolver, 'appearance'):
                self.phase = 'appearance'
                state['appearance'] = await self.resolver.appearance()
            self.phase = "idle"
            return state

    async def action(self, payload):
        if not isinstance(payload, dict) or set(payload) - {
            "action", "context", "id", "created", "name", "socket"
        }:
            raise ToolbeltError("无效请求。", status=400)
        action = payload.get("action")
        if action not in {"create", "switch", "rename", "delete", "detach", "socket", "reconnect", "setup", "setup-noecho"}:
            raise ToolbeltError("未知操作。", status=400)
        async with self.lock:
            if action == 'reconnect':
                if not hasattr(self.resolver,'reconnect'):
                    raise ToolbeltError('当前连接不提供重新查询。', 'unsupported')
                await asyncio.wait_for(self.resolver.reconnect(), self.timeout)
                return {'ok':True,'message':'查询连接已更新；远端任务保留。'}
            target, identity, rows, state = await self.inspect()
            if not isinstance(payload.get("context"), str) or payload["context"] != state["context"]:
                raise ToolbeltError("目标机器、socket 或活动 pane 已改变，请刷新后重试。", "stale")
            if action in ('setup','setup-noecho'):
                self.phase = 'action.' + action
                method = getattr(self.resolver, 'configure_noecho' if action == 'setup-noecho' else 'configure', None)
                if method is None:
                    raise ToolbeltError('当前连接不支持远端配置。', 'unsupported')
                try:
                    return await asyncio.wait_for(method(target), 20)
                except asyncio.TimeoutError as exc:
                    raise ToolbeltError('配置结果尚未确认，请刷新检查；不会自动重复写入。', 'timeout', 504) from exc
            if action == 'socket':
                if not hasattr(target.transport, 'scope'):
                    raise ToolbeltError('控制模式 socket 由现有连接确定。', status=400)
                from .ssh import socket_path
                path = socket_path(payload.get('socket'))
                if target.transport.clientpid != '-':
                    raise ToolbeltError('先回到 SSH，再改变 socket。', 'stale')
                target.transport.socket = path
                return {'ok':True, 'message':'已选择 socket，等候列表刷新。'}
            if state['status'] != 'online':
                raise ToolbeltError('先选择目标 socket。', 'unsupported')
            row = None
            name = None
            if action not in ('create', 'detach'):
                sid = session_id(payload.get("id"))
                row = next((r for r in rows if r.id == sid), None)
                if row is None or type(payload.get("created")) is not int or row.created != payload["created"]:
                    raise ToolbeltError("session 已消失或身份已改变，请刷新后重试。", "stale")
            if action in {"create", "rename"}:
                name = validate_name(payload.get("name"))
                if any(r.name == name and (row is None or r.id != row.id) for r in rows):
                    raise ToolbeltError("该名称已经存在。", "duplicate")
            # Recheck target immediately before sending a single explicit command.
            latest = await self.resolve()
            if latest.coordinates() != target.coordinates():
                raise ToolbeltError("活动 tab/pane 已改变，操作已取消。", "stale")
            if hasattr(target.transport, 'async_perform_action'):
                try:
                    self.phase = 'action.' + action
                    await asyncio.wait_for(target.transport.async_perform_action(action, identity, row, name), self.timeout)
                except asyncio.TimeoutError as exc:
                    raise ToolbeltError('操作超时，结果可能未知，请刷新核实；不会自动重试。', 'timeout', 504) from exc
                return {'ok':True, 'message':'操作已执行，请等待列表刷新。'}
            if action == 'detach':
                result = await self.send(target, "if-shell -F '#{==:#{destroy-unattached},off}' 'detach-client' 'display-message -p TOOLBELT_DETACH_UNSAFE'")
                if result.strip() == 'TOOLBELT_DETACH_UNSAFE':
                    raise ToolbeltError('远端 destroy-unattached 策略可能结束任务，已拒绝分离。', 'policy')
                return {'ok':True,'message':'已分离当前客户端；未删除 session。'}
            if action == "create":
                command = "new-session -d -s " + quote(name) + " -P -F '#{session_id}'"
            elif action == "switch":
                # No -c: switch the issuing control client, never another user's client.
                command = "switch-client -t " + quote(row.id)
            elif action == "rename":
                command = "rename-session -t " + quote(row.id) + " " + quote(name)
            else:
                # Keep the issuing client attached when another session exists.
                # Never switch other clients; commands stay on the validated socket.
                fallback = next((r for r in rows if r.id != row.id), None)
                if row.id == identity.current and fallback:
                    await self.send(target, "switch-client -t " + quote(fallback.id))
                command = "kill-session -t " + quote(row.id)
            await self.send(target, command)
            return {"ok": True, "message": "操作已执行，请等待列表刷新。"}
