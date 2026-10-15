"""Persistent, noninteractive SSH query channel and exact socket/client actions."""
import asyncio
import base64
import os
from pathlib import Path
import re
import secrets
import shlex
import tempfile
import time
from .observability import event
from .core import Identity, ToolbeltError, parse_sessions, readable, session_id, validate_name

SAFE_OPTIONS = ['-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oUpdateHostKeys=no',
                '-oAddKeysToAgent=no', '-oPermitLocalCommand=no', '-oRemoteCommand=none',
                '-oClearAllForwardings=yes', '-oForwardAgent=no', '-oForwardX11=no',
                '-oConnectTimeout=5', '-oServerAliveInterval=15', '-oServerAliveCountMax=2',
                '-oLogLevel=ERROR']


def socket_path(value):
    readable(value)
    if not value.startswith('/') or len(value) > 4096 or '%' in value or '\\' in value:
        raise ToolbeltError('Socket 须为远端绝对路径，不能包含控制字符、% 或反斜杠。', status=400)
    return value


class QueryChannel:
    def __init__(self, spec, spawn=None):
        self.spec = spec
        self.spawn = spawn
        self.process = None
        self.folder = None
        self.nonce = secrets.token_hex(24)
        self.master = None
        self.lock = asyncio.Lock()
        self.generation = secrets.token_hex(16)

    def argv(self, remote, terminal=False):
        sharing = ['-oControlMaster=no'] if terminal else ['-oControlMaster=auto', '-oControlPersist=no']
        return ['/usr/bin/ssh', *SAFE_OPTIONS, *sharing, *self.spec.options,
                *(['-S', self.master] if self.master else []), '-tt' if terminal else '-T', self.spec.destination, remote]

    async def start(self):
        if self.process is not None:
            if self.process.returncode is not None:
                raise ToolbeltError('SSH 查询连接已断开；保留远端任务，请重新连接 SSH 后刷新。', 'offline', 503)
            return
        self.folder = tempfile.TemporaryDirectory(prefix='tb-ssh-', dir='/tmp')
        self.master = str(Path(self.folder.name) / 'master')
        script = (Path(__file__).with_name('remote.sh')).read_text()
        remote = 'sh -c ' + shlex.quote(script) + ' toolbelt ' + self.nonce
        # Reuse an already available configured master before making a private
        # master. This check is read-only; never calls -O exit on a user's master.
        if self.spawn is None:
            check = await asyncio.create_subprocess_exec('/usr/bin/ssh', *SAFE_OPTIONS,
                *self.spec.options, '-O', 'check', self.spec.destination,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                shared = await asyncio.wait_for(check.wait(), 2) == 0
            except BaseException:
                if check.returncode is None: check.kill()
                await check.wait()
                raise
            if shared:
                self.master = None
        argv = self.argv(remote)
        if self.master is None:
            # User's ControlPath remains in config/original options.
            argv[argv.index('-oControlMaster=auto')] = '-oControlMaster=no'
        self.process = await (self.spawn(argv) if self.spawn else asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=262144,
            env={**os.environ, 'SSH_ASKPASS_REQUIRE':'never'}))

    async def request(self, op, socket='-', sid='-', name='-', expected='-', created='-', tty='-', clientpid='-', inbox='-'):
        if op not in {'hello','setup','inspect','create','switch','rename','delete','detach','prepare-attach','cancel-attach'}:
            raise ToolbeltError('未知 SSH 查询操作。', status=400)
        if socket != '-': socket_path(socket)
        if sid != '-': session_id(sid)
        if name != '-': validate_name(name)
        if expected != '-' and expected != 'absent' and not re.fullmatch(r'[0-9]+', expected):
            raise ToolbeltError('无效服务器身份。', status=400)
        if tty != '-' and not re.fullmatch(r'/dev/(pts/[0-9]+|ttys[0-9A-Za-z]+|tty[0-9]+)', tty):
            raise ToolbeltError('无效客户端 TTY。', status=400)
        if any(str(value) != '-' and not re.fullmatch(r'[0-9]{1,20}', str(value)) for value in (created,clientpid)):
            raise ToolbeltError('无效客户端或创建时间。', status=400)
        if op in ('prepare-attach','cancel-attach'):
            if not re.fullmatch(r'[0-9a-f]{32}', name) or not re.fullmatch(r'/tmp/it2tb-[0-9]{1,20}-'+re.escape(str(clientpid))+r'-[A-Za-z0-9]{8,32}', inbox):
                raise ToolbeltError('无效 helper 请求身份。', status=400)
        elif inbox != '-':
            raise ToolbeltError('当前操作不能指定 helper 目录。', status=400)
        fields = [op, socket, sid, name, expected, str(created), tty, str(clientpid), inbox]
        for item in fields:
            readable(item)
            if not item or '\t' in item or '\n' in item: raise ToolbeltError('无效 SSH 请求字段。', status=400)
        async with self.lock:
            try:
                await self.start()
                self.process.stdin.write(('\t'.join(fields)+'\n').encode())
                await self.process.stdin.drain()
                # Skip only bounded login banner lines; nonce frames responses.
                for _ in range(32):
                    line = await asyncio.wait_for(self.process.stdout.readline(), 7)
                    if not line: raise EOFError()
                    if not line.startswith((self.nonce+'\t').encode()): continue
                    prefix, status, encoded = line.rstrip(b'\n').split(b'\t', 2)
                    result = base64.b64decode(encoded, validate=True).decode('utf-8', 'strict')
                    if status != b'0':
                        if status == b'21': raise ToolbeltError('远端开启了 destroy-unattached，分离可能结束任务；已拒绝分离，请自行检查该 session 策略。', 'policy')
                        if status == b'20': raise ToolbeltError('远端没有可用 tmux；请先在该 Linux 主机安装 tmux。', 'unsupported')
                        if status == b'30': raise ToolbeltError('远端空闲 shell/helper 身份已变化或存在未消费请求，请刷新提示符后重试。', 'stale')
                        if status == b'15': raise ToolbeltError('远端服务器、session 或客户端身份已改变，操作取消。', 'stale')
                        raise ToolbeltError('远端 tmux 查询或操作失败；请检查 socket、权限与 tmux 安装。', 'offline', 503)
                    return result
                raise ValueError('missing frame')
            except ToolbeltError:
                raise
            except BaseException as exc:
                await self.close()
                if isinstance(exc, asyncio.CancelledError): raise
                raise ToolbeltError('无法建立或保持无交互 SSH 查询。需要已有密钥/agent 或可复用连接，以及已验证的主机密钥；不会弹密码或修改配置。', 'offline', 503) from exc

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.folder: self.folder.cleanup()
        # Keep the exited process marker: this channel must never silently retry
        # a mutation or reconnect under the same browser context/generation.


class SSHTransport:
    mode = 'ssh'
    def __init__(self, channel, terminal=None):
        self.channel = channel
        self.terminal = terminal
        self.host = self.user = self.uid = self.default = None
        self.sockets = []
        self.socket = None
        self.tty = '-'
        self.clientpid = '-'
        self.managed = None
        self.origin = None

    async def initialize(self):
        result = await self.channel.request('hello')
        lines = result.splitlines()
        if not lines: raise ToolbeltError('SSH 身份响应为空。', 'protocol')
        fields = lines[0].split('\t')
        if len(fields) != 4 or not re.fullmatch(r'[0-9]+', fields[2]):
            raise ToolbeltError('无法验证远端 SSH 用户。', 'protocol')
        host, user, uid, default = fields
        readable(host); readable(user)
        if not host or not user: raise ToolbeltError('远端身份不完整。', 'protocol')
        self.host, self.user, self.uid, self.default = host, user, uid, socket_path(default)
        self.sockets = sorted(set(socket_path(p) for p in lines[1:]))
        if len(self.sockets) > 50: raise ToolbeltError('tmux socket 数量过多，请明确目标。', 'unsupported')
        self.socket = self.sockets[0] if len(self.sockets) == 1 else self.default if not self.sockets else None

    async def async_inspect(self):
        if self.host is None: await self.initialize()
        if self.socket is None:
            raise ToolbeltError('远端有多个 tmux socket；先在侧栏选择目标，避免同名 session 误操作。', 'scope')
        if self.terminal and self.managed:
            await self.terminal.refresh_client(self)
        output = await self.channel.request('inspect', self.socket, tty=self.tty)
        first, sep, rows = output.partition('\n')
        fields = first.split('\t')
        if len(fields) != 6:
            raise ToolbeltError('远端 tmux 身份响应无效。', 'protocol')
        host, socket, pid, tty, clientpid, current = fields
        readable(host); socket_path(socket)
        if host != self.host or socket != self.socket or not re.fullmatch(r'[0-9]+|absent', pid):
            raise ToolbeltError('远端目标身份已改变。', 'stale')
        if tty != '-':
            if tty != self.tty or not re.fullmatch(r'[0-9]+', clientpid):
                raise ToolbeltError('远端客户端不能唯一确定。', 'protocol')
            session_id(current)
        else:
            current = ''
        self.clientpid = clientpid
        identity = Identity(self.user+'@'+host, socket, pid, tty+':'+clientpid, current)
        return identity, parse_sessions('\n'.join(line.replace('|', '\t', 4) for line in rows.splitlines()))

    def scope(self):
        return {'mode':'ssh', 'sockets':list(dict.fromkeys([self.default, *self.sockets])) if self.default else [],
                'selected_socket':self.socket, 'attached':self.clientpid != '-'}

    async def async_perform_action(self, action, identity, row, name=None):
        if action == 'switch' and self.clientpid == '-':
            if not self.terminal: raise ToolbeltError('终端客户端尚未接入。', 'unsupported')
            # Verify identity/session once more before launching an attach, since
            # attachment itself happens in the terminal channel.
            fresh, rows = await self.async_inspect()
            if fresh.pid != identity.pid or not any(r.id == row.id and r.created == row.created for r in rows):
                raise ToolbeltError('session 已改变，附着取消。', 'stale')
            await self.terminal.attach(self, row, identity)
            return
        await self.channel.request(action, self.socket, sid=row.id if row else '-', name=name or '-',
            expected=identity.pid, created=row.created if row else '-', tty=self.tty, clientpid=self.clientpid)
        if action == 'switch':
            fresh, rows = await self.async_inspect()
            verified = (fresh.pid == identity.pid and fresh.client == identity.client
                        and fresh.current == row.id
                        and any(r.id == row.id and r.created == row.created for r in rows))
            event('SWITCH_CHECK',requested=row.id,before=identity.current,after=fresh.current,verified=verified)
            if not verified:
                raise ToolbeltError('切换请求已返回，但目标客户端未确认切到所选 session。请刷新核实，不会自动重试。', 'stale')
        if action == 'detach' and self.terminal:
            await self.terminal.return_to_ssh(self)
