"""Opt-in official Shell Integration installer over the existing SSH route."""
import asyncio
import base64
import hashlib
from pathlib import Path
import shlex
from .core import ToolbeltError, readable


HASHES = {
    'zsh': '20e90813ba1a1703cdf05df724a042d7ce69857273e933d0411fee7f2b20f09f',
    'bash': 'c8754d9c35bc24934be7ab20735651c9a019bd2b442ebfb41d9103e62e95c5b0',
}
PREVIOUS_NOECHO_SHA = '564dfb9a6ed680a88ea04a07e26e8ac03dddc21dc305e2172b76211ea0c266f6'
RESOURCE_ROOTS = (Path('/Applications/iTerm.app/Contents/Resources'),
                  Path.home() / 'Applications/iTerm.app/Contents/Resources')


def official_script(flavor, roots=RESOURCE_ROOTS):
    if flavor not in HASHES:
        raise ToolbeltError('一键配置支持远端 bash / zsh；其他 shell 请使用 iTerm2 官方 Shell Integration 安装说明。', 'unsupported')
    for root in roots:
        path = root / ('iterm2_shell_integration.' + flavor)
        try:
            if not path.is_file() or path.stat().st_size > 262144: continue
            data = path.read_bytes()
        except OSError: continue
        if hashlib.sha256(data).hexdigest() == HASHES[flavor]: return data
    raise ToolbeltError('未找到已验证的 iTerm2 3.7.4 官方集成脚本；请使用 iTerm2 菜单安装 Shell Integration。', 'unsupported')


def install_script(host, user, flavor, data, noecho=False):
    readable(host); readable(user)
    if flavor not in HASHES: raise ToolbeltError('不支持的远端 shell。', 'unsupported')
    values = {'install_kind': 'noecho' if noecho else 'official', 'expected_host': host, 'expected_user': user, 'flavor': flavor,
              'script_b64': base64.b64encode(data).decode('ascii'),
              'script_sha': hashlib.sha256(data).hexdigest(),
              'previous_sha': PREVIOUS_NOECHO_SHA if noecho else ''}
    return ('\n'.join(k + '=' + shlex.quote(v) for k, v in values.items()) + '\n' +
            Path(__file__).with_suffix('.sh').read_text()).encode()


def parse_probe(output):
    lines = output.splitlines()
    shell = lines[0] if lines else ''
    readable(shell)
    flavor = shell.rsplit('/', 1)[-1]
    files = {}
    for line in lines[1:]:
        fields = line.split('|')
        if len(fields) != 3 or fields[1] not in ('0', '1') or fields[2] not in ('0', '1'):
            raise ToolbeltError('远端配置检查返回异常。', 'protocol')
        files[fields[0]] = (fields[1] == '1', fields[2] == '1')
    integration = files.get('.iterm2_shell_integration.' + flavor, (False, False))[0]
    startup = ('.zshrc',) if flavor == 'zsh' else ('.bashrc', '.bash_profile', '.bash_login', '.profile')
    referenced = any(files.get(name, (False, False))[1] for name in startup)
    bridge = files.get('.iterm2_toolbelt_attach.zsh', (False, False))
    return {'shell': flavor, 'installed': integration and referenced, 'noecho_installed':all(bridge), 'noecho_current': files.get('helper-display',(False,False))[0]}


async def describe(resolver, target):
    transport = target.transport
    if getattr(transport, 'mode', None) != 'ssh': return None
    pane = resolver.app.get_session_by_id(target.pane)
    if not pane: return None
    host = await pane.async_get_variable('hostname')
    user = await pane.async_get_variable('username')
    matched = (isinstance(host, str) and host.rstrip('.').lower() == transport.host.rstrip('.').lower() and user == transport.user)
    from .bridge import parse_ready
    if matched:
        try:
            parse_ready(await pane.async_get_variable('user.toolbelt_zsh_ready'), transport.uid)
            if await pane.async_get_variable('user.toolbelt_zsh_display_version') == '3':
                return {'kind': 'ready'}
        except ToolbeltError: pass
    if not hasattr(transport, 'integration_probe'):
        transport.integration_probe = parse_probe(await transport.channel.request('setup'))
    probe = transport.integration_probe
    if probe['installed']:
        if probe['noecho_installed'] and not probe['noecho_current']:
            return {'kind':'noecho-upgrade', 'available':probe['shell']=='zsh', 'action':'setup-noecho',
                    'button':'一键修复 tmux 画面', 'message':'检测到旧版 helper：可切换但画面不显示。点击备份并修复 helper；之后另开 SSH 连接生效，现有任务保留。'}
        if not matched or probe['noecho_installed']:
            return {'kind': 'reconnect', 'message': '远端配置已安装。请另开 SSH 连接加载无回显 helper；现有任务可保留。若新连接仍未就绪，请检查 zsh 绑定冲突或自定义启动流程。', 'available': False}
        return {'kind':'noecho-missing', 'available':probe['shell']=='zsh', 'action':'setup-noecho',
                'button':'启用无回显附着', 'message':'首次切换需要 zsh 无回显 helper。点击后新增 ~/.iterm2_toolbelt_attach.zsh（0600），备份并追加 ~/.zshrc 加载行；新连接生效。不会修改当前前台程序。' if probe['shell']=='zsh' else '当前无回显附着仅支持远端 zsh。列表和 detached 创建仍可使用；没有向终端输入长命令的兼容回退。'}
    flavor = probe['shell']
    try:
        official_script(flavor)
        available = True
        message = ('列表与新建可直接使用。切换到当前 pane 需要官方 Shell Integration，以确认空闲提示符，避免把命令输入 Claude。'
                   '点击后复制官方脚本，备份并补充 ' + ('~/.zshrc' if flavor == 'zsh' else '~/.bashrc 和登录配置') +
                   ' 的加载行；新连接生效，现有任务保留。')
    except ToolbeltError as exc:
        available = False
        message = str(exc)
    return {'kind': 'missing', 'shell': flavor, 'available': available, 'message': message}


async def configure(resolver, target, noecho=False):
    current = await resolver.resolve()
    if current.coordinates() != target.coordinates() or current.transport is not target.transport:
        raise ToolbeltError('活动 SSH pane 已改变，配置取消。', 'stale')
    info = await describe(resolver, current)
    allowed = ('noecho-missing','noecho-upgrade') if noecho else ('missing',)
    if not info or info.get('kind') not in allowed or not info.get('available'):
        raise ToolbeltError('当前连接不需要或不支持一键配置；请刷新查看提示。', 'unsupported')
    transport = current.transport
    # Fresh shell/file flags before the explicit write, never on a poll.
    probe = parse_probe(await transport.channel.request('setup'))
    flavor = probe['shell']
    data = Path(__file__).with_name('zsh_attach.zsh').read_bytes() if noecho else official_script(flavor)
    latest = await resolver.resolve()
    if latest.coordinates() != current.coordinates() or latest.transport is not transport:
        raise ToolbeltError('活动 SSH pane 已改变，配置取消。', 'stale')
    process = await asyncio.create_subprocess_exec(*transport.channel.argv('sh -s'),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(install_script(transport.host, transport.user, flavor, data, noecho=noecho)), 15)
    except BaseException as exc:
        if process.returncode is None: process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError): raise
        raise ToolbeltError('配置结果尚未确认。请刷新后检查，不会自动重复写入。', 'offline', 503) from exc
    errors = {31:'远端主机或用户身份已变化。', 32:'目标文件不是本人拥有的普通文件，或路径是符号链接。',
              33:'远端登录 shell 不支持自动配置。', 34:'另一个配置操作仍在进行。',
              36:'官方脚本校验或语法检查失败。', 37:'存在自定义集成脚本或加载行，请保留并手动检查。',
              38:'追加后配置语法未通过，未应用。', 39:'配置文件在操作期间发生变化。'}
    if process.returncode:
        raise ToolbeltError('一键配置未完成：' + errors.get(process.returncode, 'SSH 断开或写入失败，请检查备份后重试。'), 'unsupported')
    if len(output) > 8192 or not output.startswith(b'ok\n'):
        raise ToolbeltError('配置回执异常，请刷新检查；不会自动重试。', 'protocol')
    backups = []
    for line in output.decode('utf-8').splitlines()[1:]:
        if not line.startswith('backup\t'): raise ToolbeltError('配置回执异常。', 'protocol')
        backups.append(readable(line.split('\t', 1)[1]))
    if hasattr(transport, 'integration_probe'): del transport.integration_probe
    return {'ok': True, 'message': ('无回显 helper 已配置，请另开 SSH 连接生效。现有任务保留。' if noecho else '官方集成已配置，请另开 SSH 连接生效。现有任务保留。') +
            (' 备份：' + '；'.join(backups) if backups else '')}
