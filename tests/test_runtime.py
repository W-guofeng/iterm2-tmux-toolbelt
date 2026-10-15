import asyncio
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Obj
import unittest
from toolbelt.runtime import InstanceLock, reveal_toolbelt


class LockTests(unittest.TestCase):
    def test_other_process_rejected_and_release_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.lock"
            lock = InstanceLock(path)
            self.assertTrue(lock.acquire())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            code = "from toolbelt.runtime import InstanceLock; import sys; x=InstanceLock(sys.argv[1]); print(x.acquire()); x.close()"
            result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "False")
            lock.close()
            result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "True")

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("untouched")
            path = Path(directory) / "lock"
            path.symlink_to(target)
            with self.assertRaises(OSError): InstanceLock(path).acquire()
            self.assertEqual(target.read_text(), "untouched")


class VisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, checked, enabled=True, fail=False, remount=False):
        values = {name: Obj(checked=checked, enabled=enabled) for name in ("Toolbelt.tmux Sessions", "Show Toolbelt")}
        selections = []
        async def get(_, name):
            if fail: raise RuntimeError("private error data")
            return values[name]
        async def select(_, name):
            selections.append(name)
            values[name].checked = not values[name].checked
        api = Obj(MainMenu=Obj(async_get_menu_item_state=get, async_select_menu_item=select))
        output = io.StringIO()
        with redirect_stdout(output): result = await reveal_toolbelt(api, None, remount=remount)
        return result, selections, output.getvalue()

    async def test_hidden_sidebar_is_revealed(self):
        result, selections, _ = await self.check(False)
        self.assertTrue(result)
        self.assertEqual(selections, ["Toolbelt.tmux Sessions", "Show Toolbelt"])

    async def test_already_visible_never_hidden(self):
        result, selections, _ = await self.check(True)
        self.assertTrue(result)
        self.assertEqual(selections, [])

    async def test_disabled_menu_and_error_are_actionable(self):
        for options in ({"enabled":False}, {"fail":True}):
            result, selections, output = await self.check(False, **options)
            self.assertFalse(result)
            self.assertEqual(selections, [])
            self.assertIn("View > Show Toolbelt", output)
            self.assertNotIn("private error data", output)

    async def test_remount_rebuilds_only_tool_and_keeps_sidebar_visible(self):
        result, selections, _ = await self.check(True, remount=True)
        self.assertTrue(result)
        self.assertEqual(selections, ["Toolbelt.tmux Sessions", "Toolbelt.tmux Sessions"])
