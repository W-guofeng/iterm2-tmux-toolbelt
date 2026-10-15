import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from scripts import install

class InstallerLifecycleTests(unittest.TestCase):
    def test_preview_install_unknown_refusal_update_uninstall(self):
        with tempfile.TemporaryDirectory(prefix='tb-install-',dir='/tmp') as folder:
            home=Path(folder);target=home/'Library/Application Support/iTerm2/Scripts/tmux_toolbelt.py'
            def run(*args):
                with patch.object(Path,'home',return_value=home),patch.object(sys,'argv',['install.py',*args]),contextlib.redirect_stdout(io.StringIO()):
                    install.main()
            run();self.assertFalse(target.exists())
            run('--confirm');first=target.read_bytes()
            self.assertTrue(first.startswith(b'#!/usr/bin/env python3'))
            run('--update','--confirm');self.assertEqual(first,target.read_bytes())
            target.write_text('# unrelated user script\n')
            with self.assertRaises(SystemExit):run('--update','--confirm')
            with self.assertRaises(SystemExit):run('--uninstall','--confirm')
            self.assertEqual(target.read_text(),'# unrelated user script\n')
            target.write_bytes(first)
            neighbor=target.with_name('other.py');neighbor.write_text('# keep\n')
            run('--uninstall','--confirm')
            self.assertFalse(target.exists());self.assertTrue(neighbor.exists())
