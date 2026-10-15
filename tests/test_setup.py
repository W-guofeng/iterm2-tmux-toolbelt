import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import AsyncMock, patch

from toolbelt.core import Service, Target, ToolbeltError
from toolbelt.setup import configure, install_script, official_script, parse_probe


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tb-setup-', dir='/tmp')
        self.home = Path(self.temp.name)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        for name, body in {'hostname': 'printf fixture',
                           'getent': "printf 'fixture:x:1000:1000::/fixture:/bin/bash\\n'"}.items():
            path = self.bin / name
            path.write_text('#!/bin/sh\n' + body + '\n')
            path.chmod(0o700)
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.data = b'# official-script test stand-in\n: integration_fixture\n'

    def tearDown(self): self.temp.cleanup()

    def run_install(self, host='fixture', flavor='bash', noecho=False):
        return subprocess.run(['/bin/sh'], input=install_script(host, self.user, flavor, self.data, noecho=noecho),
            env={**os.environ, 'HOME': str(self.home), 'PATH':str(self.bin)+':'+os.environ['PATH']},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)

    def test_backup_preserve_permissions_idempotence_and_login_precedence(self):
        original = b'# keep original bytes\nexport SAFE_ORIGINAL=1'  # no trailing newline
        rc = self.home / '.bashrc'; rc.write_bytes(original); rc.chmod(0o640)
        profile = self.home / '.profile'; profile.write_text('# existing login\n')
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(self.home.glob('.bashrc.toolbelt-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertTrue(rc.read_bytes().startswith(original))
        self.assertEqual(rc.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.home / '.bash_profile').exists())
        self.assertIn('.iterm2_shell_integration.bash', profile.read_text())
        self.assertEqual(subprocess.run(['/bin/sh','-n',str(profile)]).returncode, 0)
        dest = self.home / '.iterm2_shell_integration.bash'
        self.assertEqual(dest.read_bytes(), self.data)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        first = rc.read_bytes(); first_stat = rc.stat()
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(rc.read_bytes(), first)
        self.assertEqual(rc.stat().st_mtime_ns, first_stat.st_mtime_ns)
        self.assertEqual(len(list(self.home.glob('.bashrc.toolbelt-backup-*'))), 1)
        self.assertFalse((self.home / '.iterm2-toolbelt-setup.lock').exists())

    def test_symlink_unknown_integration_and_custom_hooks_refused(self):
        outside = self.home / 'outside'; outside.write_text('# untouched\n')
        rc = self.home / '.bashrc'; rc.symlink_to(outside)
        self.assertEqual(self.run_install().returncode, 32)
        self.assertEqual(outside.read_text(), '# untouched\n')
        rc.unlink(); rc.write_text('# normal\n')
        dest = self.home / '.iterm2_shell_integration.bash'; dest.write_text('# custom\n')
        self.assertEqual(self.run_install().returncode, 37)
        self.assertEqual(dest.read_text(), '# custom\n')
        dest.unlink(); rc.write_text('source custom/.iterm2_shell_integration.bash\n')
        self.assertEqual(self.run_install().returncode, 37)
        self.assertFalse(dest.exists())

    def test_identity_syntax_and_lock_fail_before_changes(self):
        rc = self.home / '.bashrc'; rc.write_text('if\n')
        self.assertEqual(self.run_install('wrong-host').returncode, 31)
        self.assertEqual(self.run_install().returncode, 38)
        self.assertEqual(rc.read_text(), 'if\n')
        self.assertFalse((self.home / '.iterm2_shell_integration.bash').exists())
        (self.home / '.iterm2-toolbelt-setup.lock').mkdir()
        self.assertEqual(self.run_install().returncode, 34)
        self.assertTrue((self.home / '.iterm2-toolbelt-setup.lock').is_dir())

    def test_all_preflight_happens_before_any_startup_file_changes(self):
        rc = self.home / '.bashrc'; rc.write_text('# preserve\n')
        login = self.home / '.bash_profile'; login.symlink_to(rc)
        self.assertEqual(self.run_install().returncode, 32)
        self.assertEqual(rc.read_text(), '# preserve\n')
        self.assertFalse((self.home / '.iterm2_shell_integration.bash').exists())
        self.assertEqual(list(self.home.glob('*.toolbelt-backup-*')), [])

    @unittest.skipUnless(shutil.which('zsh'), 'zsh syntax checker unavailable')
    def test_zsh_one_file_install_and_repeat(self):
        (self.bin / 'getent').write_text("#!/bin/sh\nprintf 'fixture:x:1000:1000::/fixture:/bin/zsh\\n'\n")
        result = self.run_install(flavor='zsh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / '.zshrc').is_file())
        self.assertEqual((self.home / '.iterm2_shell_integration.zsh').read_bytes(), self.data)
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertEqual(self.run_install(flavor='zsh').returncode, 0)
        self.assertEqual((self.home / '.zshrc').read_text().count('&& source'), 1)

    def test_failed_second_commit_rolls_back_only_own_changes(self):
        rc = self.home / '.bashrc'; rc.write_text('# original rc\n')
        login = self.home / '.bash_profile'; login.write_text('# original login\n')
        wrapper = self.bin / 'mv'
        wrapper.write_text('#!/bin/sh\ncase "$2" in */.bash_profile) exit 1;; esac\nexec /bin/mv "$@"\n')
        wrapper.chmod(0o700)
        self.assertEqual(self.run_install().returncode, 35)
        self.assertEqual(rc.read_text(), '# original rc\n')
        self.assertEqual(login.read_text(), '# original login\n')
        self.assertFalse((self.home / '.iterm2_shell_integration.bash').exists())
        self.assertFalse((self.home / '.iterm2-toolbelt-setup.lock').exists())
        self.assertEqual(len(list(self.home.glob('*.toolbelt-backup-*'))), 2)

    @unittest.skipUnless(shutil.which('zsh'), 'zsh syntax checker unavailable')
    def test_noecho_install_preserves_official_integration_and_is_idempotent(self):
        (self.bin/'getent').write_text("#!/bin/sh\nprintf 'fixture:x:1000:1000::/fixture:/bin/zsh\\n'\n")
        official=self.home/'.iterm2_shell_integration.zsh';official.write_text(': official_fixture\n')
        original=b'# keep original\nsource "$HOME/.iterm2_shell_integration.zsh"\n'
        rc=self.home/'.zshrc';rc.write_bytes(original);rc.chmod(0o640)
        result=self.run_install(flavor='zsh',noecho=True)
        self.assertEqual(result.returncode,0,result.stderr)
        helper=self.home/'.iterm2_toolbelt_attach.zsh'
        self.assertEqual(helper.read_bytes(),self.data)
        self.assertEqual(helper.stat().st_mode & 0o777,0o600)
        backups=list(self.home.glob('.zshrc.toolbelt-noecho-backup-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),original)
        self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)
        self.assertTrue(rc.read_bytes().startswith(original))
        self.assertEqual(rc.stat().st_mode & 0o777,0o640)
        self.assertEqual(self.run_install(flavor='zsh',noecho=True).returncode,0)
        self.assertEqual(rc.read_text().count('&& source'),1)
        self.assertEqual(official.read_text(),': official_fixture\n')
        self.assertEqual(len(list(self.home.glob('.zshrc.toolbelt-noecho-backup-*'))),1)

    @unittest.skipUnless(shutil.which('zsh'), 'zsh syntax checker unavailable')
    def test_known_old_helper_upgrade_backed_up_custom_helper_refused(self):
        import hashlib
        (self.bin/'getent').write_text("#!/bin/sh\nprintf 'fixture:x:1000:1000::/fixture:/bin/zsh\\n'\n")
        (self.home/'.iterm2_shell_integration.zsh').write_text(': official\n')
        (self.home/'.zshrc').write_text('source "$HOME/.iterm2_shell_integration.zsh"\n[[ -r "$HOME/.iterm2_toolbelt_attach.zsh" ]] && source "$HOME/.iterm2_toolbelt_attach.zsh"\n')
        helper=self.home/'.iterm2_toolbelt_attach.zsh'
        old=b': previous_known_helper\n';helper.write_bytes(old);helper.chmod(0o600)
        before=(self.home/'.zshrc').read_bytes()
        with patch('toolbelt.setup.PREVIOUS_NOECHO_SHA',hashlib.sha256(old).hexdigest()):
            result=self.run_install(flavor='zsh',noecho=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(helper.read_bytes(),self.data)
        backups=list(self.home.glob('.iterm2_toolbelt_attach.zsh.toolbelt-backup-*'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),old)
        self.assertEqual(backups[0].stat().st_mode&0o777,0o600)
        self.assertEqual((self.home/'.zshrc').read_bytes(),before)
        helper.write_bytes(b': custom\n')
        self.assertEqual(self.run_install(flavor='zsh',noecho=True).returncode,37)
        self.assertEqual(helper.read_bytes(),b': custom\n')

    def test_local_script_must_match_verified_official_digest(self):
        (self.home / 'iterm2_shell_integration.zsh').write_bytes(b'custom')
        with self.assertRaises(ToolbeltError): official_script('zsh', (self.home,))
        with self.assertRaises(ToolbeltError): official_script('fish', (self.home,))


class SetupActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_and_current_context_guard_no_automatic_writes(self):
        from tests.helpers import FakeResolver
        resolver = FakeResolver()
        resolver.configure = AsyncMock(return_value={'ok':True})
        service = Service(resolver)
        state = await service.snapshot()
        resolver.configure.assert_not_awaited()
        with self.assertRaises(ToolbeltError):
            await service.action({'action':'setup', 'context':'stale'})
        resolver.configure.assert_not_awaited()
        await service.action({'action':'setup', 'context':state['context']})
        resolver.configure.assert_awaited_once_with(resolver.target)
        self.assertFalse(resolver.transport.mutations)

    async def test_final_focus_change_prevents_installer_process(self):
        transport = Obj(mode='ssh', host='fixture', user='fixture')
        target = Target('w', 't', 'p', 'conn', transport)
        replacement = Target('w', 't', 'other', 'other', transport)
        resolver = Obj(resolve=AsyncMock(return_value=replacement))
        with patch('toolbelt.setup.asyncio.create_subprocess_exec') as spawn:
            with self.assertRaises(ToolbeltError): await configure(resolver, target)
            spawn.assert_not_called()

    async def test_missing_noecho_shows_explicit_action_without_installing(self):
        from toolbelt.setup import describe
        variables={'hostname':'fixture','username':'user','user.toolbelt_zsh_ready':''}
        async def variable(name): return variables.get(name,'')
        pane=Obj(async_get_variable=variable)
        channel=Obj(request=AsyncMock(return_value='/bin/zsh\n.zshrc|1|1\n.iterm2_shell_integration.zsh|1|0\n.iterm2_toolbelt_attach.zsh|0|0'))
        transport=Obj(mode='ssh',host='fixture',user='user',uid='1000',channel=channel)
        target=Target('w','t','p','c',transport)
        resolver=Obj(app=Obj(get_session_by_id=lambda sid:pane))
        with patch('toolbelt.setup.asyncio.create_subprocess_exec') as spawn:
            first=await describe(resolver,target)
            second=await describe(resolver,target)
            spawn.assert_not_called()
        self.assertEqual(first['action'],'setup-noecho')
        self.assertTrue(first['available'])
        self.assertEqual(first,second)
        channel.request.assert_awaited_once_with('setup')

    async def test_old_helper_renders_one_click_repair_and_updated_file_needs_new_connection(self):
        from toolbelt.setup import describe
        variables={'hostname':'fixture','username':'user','user.toolbelt_zsh_ready':'2|1000|123|/dev/pts/7|'+'a'*32+'|/tmp/it2tb-1000-123-abcdefgh'}
        async def variable(name): return variables.get(name,'')
        pane=Obj(async_get_variable=variable)
        channel=Obj(request=AsyncMock(return_value='/bin/zsh\n.zshrc|1|1\n.iterm2_shell_integration.zsh|1|0\n.iterm2_toolbelt_attach.zsh|1|1\nhelper-display|0|0'))
        transport=Obj(mode='ssh',host='fixture',user='user',uid='1000',channel=channel)
        target=Target('w','t','p','c',transport)
        resolver=Obj(app=Obj(get_session_by_id=lambda sid:pane))
        with patch('toolbelt.setup.asyncio.create_subprocess_exec') as spawn:
            old=await describe(resolver,target)
            self.assertEqual(old['kind'],'noecho-upgrade');self.assertTrue(old['available'])
            transport.integration_probe['noecho_current']=True
            self.assertEqual((await describe(resolver,target))['kind'],'reconnect')
            variables['user.toolbelt_zsh_display_version']='3'
            self.assertEqual((await describe(resolver,target))['kind'],'ready')
            spawn.assert_not_called()

    async def test_read_only_probe_parse(self):
        result = parse_probe('/bin/zsh\n.zshrc|1|1\n.iterm2_shell_integration.zsh|1|0')
        self.assertEqual(result, {'shell':'zsh', 'installed':True, 'noecho_installed':False,'noecho_current':False})
        self.assertFalse(parse_probe('/bin/bash\n.bashrc|1|0')['installed'])
        with self.assertRaises(ToolbeltError): parse_probe('/bin/zsh\n.zshrc|yes|0')
