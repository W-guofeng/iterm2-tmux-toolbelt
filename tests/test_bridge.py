import unittest
from toolbelt.bridge import KEY, parse_ready
from toolbelt.core import ToolbeltError


class BridgeIdentityTests(unittest.TestCase):
    def test_identity_and_nonprintable_trigger(self):
        value='2|1000|123|/dev/pts/7|'+'a'*32+'|/tmp/it2tb-1000-123-abcdefgh'
        bridge=parse_ready(value,'1000')
        self.assertEqual(bridge.pid,'123')
        self.assertEqual(bridge.tty,'/dev/pts/7')
        self.assertNotIn('\n',KEY)
        self.assertTrue(KEY.startswith('\x1b['))

    def test_forged_uid_pid_directory_version_and_fields_rejected(self):
        value='2|1000|123|/dev/pts/7|'+'a'*32+'|/tmp/it2tb-1000-123-abcdefgh'
        for bad in ('',None,value.replace('2|','1|',1),value.replace('1000','501'),
                    value.replace('/dev/pts/7','/dev/tty'),value.replace('-123-','-124-'),
                    value.replace('a'*32,'$(id)'),value+'|extra',value.replace('abcdefgh','../escape')):
            with self.subTest(value=bad), self.assertRaises(ToolbeltError): parse_ready(bad,'1000')
