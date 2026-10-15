"""Darwin foreground SSH identification. Read argv, never process environments."""
import asyncio
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from .core import ToolbeltError, readable


@dataclass(frozen=True)
class SSHSpec:
    pid: int
    started: str
    destination: str
    options: tuple
    argv: tuple
    bootstrap: str = "plain"

    @property
    def key(self):
        return hashlib.sha256(json.dumps([self.pid, self.started, self.argv]).encode()).hexdigest()


# Preserve only routing/authentication options. Session commands/forwarding never
# carry over. Existing SSH config remains owned by the user and is not rewritten.
OPTION_KEYS = {'hostname','user','port','identityfile','certificatefile','identitiesonly',
               'identityagent','proxyjump','proxycommand','hostkeyalias','userknownhostsfile',
               'globalknownhostsfile','bindaddress','bindinterface','addressfamily',
               'preferredauthentications','pubkeyauthentication','controlpath'}
SKIP_OPTIONS = {'batchmode','stricthostkeychecking','updatehostkeys','addkeystoagent','permitlocalcommand',
                'remotecommand','clearallforwardings','forwardagent','forwardx11','connecttimeout',
                'serveraliveinterval','serveralivecountmax','loglevel','controlmaster','controlpersist'}
VALUE_FLAGS = {'p','l','i','F','J','S','b','c','m'}
IGNORED_FLAGS = set('46ACaKkqTvtxXy')


def parse_ssh_argv(argv, pid=0, started='test'):
    if not argv or Path(argv[0]).name != 'ssh':
        raise ToolbeltError('活动前台进程不是 OpenSSH。', 'unsupported')
    options = []
    destination = None
    remote = ()
    i = 1
    while i < len(argv):
        arg = argv[i]
        readable(arg)
        if arg == '--':
            i += 1
            if i == len(argv): break
            destination = argv[i]
            remote = tuple(argv[i+1:])
            break
        if not arg.startswith('-'):
            destination = arg
            remote = tuple(argv[i+1:])
            break
        if arg in ('-N','-s','-W','-O','-G','-V','-f'):
            raise ToolbeltError('转发、子系统或后台 SSH 不能作为交互 pane 目标。', 'unsupported')
        flags = arg[1:]
        while flags:
            flag, flags = flags[0], flags[1:]
            if flag in VALUE_FLAGS or flag == 'o':
                if flags:
                    value, flags = flags, ''
                else:
                    i += 1
                    if i >= len(argv): raise ToolbeltError('SSH 参数不完整。', 'unsupported')
                    value = argv[i]
                readable(value)
                if flag in ('i','F','S') and not (value.startswith('/') or value.startswith('~/') or (flag == 'S' and value == 'none')):
                    raise ToolbeltError('相对路径的 SSH 配置/身份文件无法可靠还原，请使用绝对路径或 Host 别名配置。', 'unsupported')
                if flag == 'o':
                    key = re.split(r'[=\s]', value, 1)[0].lower()
                    if key in SKIP_OPTIONS:
                        continue
                    if key not in OPTION_KEYS:
                        # Known harmless connection preferences may remain in
                        # config, but unsupported explicit arguments fail closed.
                        raise ToolbeltError('此 SSH 显式选项暂不支持自动接入。', 'unsupported')
                options += ['-'+flag, value]
            elif flag in IGNORED_FLAGS:
                if flag in '46': options.append('-'+flag)
            else:
                raise ToolbeltError('此 SSH 启动参数暂不支持自动接入。', 'unsupported')
        i += 1
    if not isinstance(destination, str) or not re.fullmatch(r'[A-Za-z0-9_.@:%\[\]-]{1,255}', destination) or destination.startswith('-'):
        raise ToolbeltError('无法安全确定 SSH 目标。', 'unsupported')
    bootstrap = 'plain'
    if remote:
        from .bootstrap import original_iterm_ssh_args
        original = original_iterm_ssh_args(remote)
        if original is None:
            raise ToolbeltError('含未知远端启动命令的 SSH 暂不自动接入；只接受已验证的 iTerm2 原生 SSH 包装。', 'unsupported')
        plain = parse_ssh_argv(('ssh', *original), pid, started)
        if plain.bootstrap != 'plain' or plain.destination != destination or plain.options != tuple(options):
            raise ToolbeltError('SSH 包装内外的连接目标/参数不一致，已拒绝接入。', 'unsupported')
        bootstrap = 'iterm2-conductor-v3'
    return SSHSpec(int(pid), started, destination, tuple(options), tuple(argv), bootstrap)


def darwin_argv(pid):
    if sys.platform != 'darwin':
        raise ToolbeltError('自动识别目前仅支持 macOS OpenSSH。', 'unsupported')
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(262144)
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        raise ToolbeltError('无法读取活动 SSH 的参数；连接可能已退出。', 'offline', 503)
    raw = buf.raw[:size.value]
    executable_end = raw.find(b'\0', 4)
    if raw[4:executable_end] != b'/usr/bin/ssh':
        raise ToolbeltError('目前仅自动接入 macOS 系统 OpenSSH。', 'unsupported')
    argc = int.from_bytes(raw[:4], sys.byteorder)
    offset = raw.find(b'\0', 4) + 1  # executable path, not argv
    if argc < 1 or argc > 4096 or offset <= 4:
        raise ToolbeltError('SSH 进程参数格式异常。', 'unsupported')
    while offset < len(raw) and raw[offset] == 0: offset += 1
    argv = []
    for _ in range(argc):
        end = raw.find(b'\0', offset)
        if end < 0: raise ToolbeltError('SSH 进程参数不完整。', 'unsupported')
        argv.append(raw[offset:end].decode('utf-8', 'strict'))
        offset = end + 1
    # Deliberately stop at argc: the following bytes are environment variables.
    return tuple(argv)


async def output(*argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        data, _ = await asyncio.wait_for(proc.communicate(), 2)
    except BaseException:
        if proc.returncode is None: proc.kill()
        await proc.wait()
        raise
    if proc.returncode: raise ToolbeltError('活动 SSH 已断开。', 'offline', 503)
    return data.decode('utf-8', 'strict')


class ForegroundSSH:
    async def identify(self, pane):
        tty = await pane.async_get_variable('tty')
        if not isinstance(tty, str) or not re.fullmatch(r'/dev/ttys[0-9A-Za-z]+', tty):
            raise ToolbeltError('无法确认活动 pane 的本机 TTY。', 'unsupported')
        table = await output('/bin/ps', '-t', tty[5:], '-o', 'pid=,pgid=,tpgid=,comm=')
        candidates = []
        for line in table.splitlines():
            parts = line.split(None, 3)
            if len(parts) != 4: continue
            pid, group, foreground, executable = parts
            if group == foreground and Path(executable).name == 'ssh': candidates.append(int(pid))
        if len(candidates) != 1:
            raise ToolbeltError('活动 pane 未运行可唯一识别的前台 SSH；本机 shell 不会操作远端或本机 tmux。', 'unsupported')
        pid = candidates[0]
        started = (await output('/bin/ps', '-p', str(pid), '-o', 'lstart=')).strip()
        argv = await asyncio.to_thread(darwin_argv, pid)
        spec = parse_ssh_argv(argv, pid, started)
        # PID reuse or process exit during argv reading must invalidate selection.
        if (await output('/bin/ps', '-p', str(pid), '-o', 'lstart=')).strip() != started:
            raise ToolbeltError('SSH 进程身份已改变。', 'stale')
        return spec
