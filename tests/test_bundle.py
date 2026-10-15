import base64
import io
from pathlib import Path
import unittest
import zipfile
from scripts.build_iterm_script import build, ROOT, OUTPUT


class BundleTests(unittest.TestCase):
    def test_reproducible_complete_bundle(self):
        build()
        first = OUTPUT.read_bytes()
        build()
        self.assertEqual(first, OUTPUT.read_bytes())
        source = first.decode()
        encoded = source.split("_payload = '", 1)[1].split("'", 1)[0]
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
            names = archive.namelist()
            self.assertIn("toolbelt/web/index.html", names)
            self.assertIn("toolbelt/web/app.js", names)
            self.assertIn("toolbelt/http.py", names)
            self.assertIn("toolbelt/web/i18n.js", names)
            self.assertIn("LICENSE", names)
            self.assertIn("NOTICE", names)
            self.assertFalse(any(".." in Path(n).parts or Path(n).is_absolute() or "__pycache__" in n for n in names))
            for name in names:
                self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
