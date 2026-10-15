"""Validated, one-shot bridge identity. No executable text enters a terminal."""
from dataclasses import dataclass
import re
from .core import ToolbeltError

KEY = '\x1b[1337;777~'


@dataclass(frozen=True)
class Bridge:
    uid: str
    pid: str
    tty: str
    nonce: str
    inbox: str

    def fields(self, transport, row, identity):
        return dict(socket=transport.socket, sid=row.id, name=self.nonce,
                    expected=identity.pid, created=row.created,
                    tty=self.tty, clientpid=self.pid, inbox=self.inbox)


def parse_ready(value, uid):
    if not isinstance(value, str) or len(value) > 512:
        raise ToolbeltError('当前 SSH 尚未加载无回显 helper；请按侧栏说明安装后另开连接。', 'unsupported')
    fields = value.split('|')
    if len(fields) != 6 or fields[0] != '2':
        raise ToolbeltError('当前 SSH 尚未加载受支持的无回显 helper；请按侧栏说明另开连接。', 'unsupported')
    _, actual_uid, pid, tty, nonce, inbox = fields
    if (actual_uid != str(uid) or not re.fullmatch(r'[0-9]{1,20}', actual_uid)
            or not re.fullmatch(r'[1-9][0-9]{0,19}', pid)
            or not re.fullmatch(r'/dev/(pts/[0-9]+|ttys[0-9A-Za-z]+|tty[0-9]+)', tty)
            or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or not re.fullmatch(r'/tmp/it2tb-' + re.escape(actual_uid) + '-' + re.escape(pid) + r'-[A-Za-z0-9]{8,32}', inbox)):
        raise ToolbeltError('远端无回显 helper 身份不匹配，附着已取消。', 'stale')
    return Bridge(actual_uid, pid, tty, nonce, inbox)
