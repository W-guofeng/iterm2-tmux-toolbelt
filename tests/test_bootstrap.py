import base64
import shlex
import unittest
from toolbelt.process import parse_ssh_argv
from toolbelt.core import ToolbeltError

# Original synthetic protocol fixture; no upstream script is redistributed.
import hashlib
from unittest.mock import patch
TAIL = ';s="";IFS=""; : synthetic-bootstrap-fixture'
FIXTURE_HASH = hashlib.sha256(TAIL.encode()).hexdigest()


def profile_argv(host='qa@fixture.invalid', original=None, tail=TAIL):
    original = original if original is not None else host
    encoded=base64.b64encode(original.encode()).decode()
    payload=('stty -echo -isig; printf "%s '
             '\x1b]1337;Env=report=all:Rk9PPUJBUg==\x07'
             '\x1b]1337;it2ssh=fixture-nonce 12345 YWJj '+encoded+'\x07'
             '\x1b]1337;SendConductor=v=3\x07"'+tail)
    return ['ssh','-t','--',host,'exec','sh','-c',shlex.quote('eval "$(echo "$0")"'),shlex.quote(payload)]


@patch('toolbelt.bootstrap.TAIL_SHA256', FIXTURE_HASH)
class NativeProfileTests(unittest.TestCase):
    def test_exact_recognized_wrapper_envelope(self):
        spec=parse_ssh_argv(profile_argv(),12,'start')
        self.assertEqual(spec.bootstrap,'iterm2-conductor-v3')
        self.assertEqual(spec.destination,'qa@fixture.invalid')
        self.assertEqual(spec.options,())

    def test_unknown_or_modified_payload_stays_rejected(self):
        for argv in (profile_argv(tail=TAIL+'; evil'),['ssh','qa@fixture.invalid','tmux','attach'],
                     ['ssh','qa@fixture.invalid','sh','-c','ssh inner'],profile_argv(original='other@fixture.invalid'),
                     profile_argv(original='qa@fixture.invalid ssh inner')):
            with self.subTest(argv_length=len(argv)),self.assertRaises(ToolbeltError): parse_ssh_argv(argv)

    def test_routes_must_match(self):
        argv=profile_argv(original='-p 2222 qa@fixture.invalid')
        with self.assertRaises(ToolbeltError): parse_ssh_argv(argv)
        argv[1:1]=['-p','2222']
        self.assertEqual(parse_ssh_argv(argv).options,('-p','2222'))

    def test_inherited_environment_is_data_not_a_script(self):
        argv=profile_argv()
        # Invalid characters in the Env field cannot be hidden by a known tail.
        argv[-1]=argv[-1].replace('Rk9PPUJBUg==','Rk9P;evil')
        with self.assertRaises(ToolbeltError): parse_ssh_argv(argv)


@patch('toolbelt.bootstrap.TAIL_SHA256', FIXTURE_HASH)
class ProfileProofTests(unittest.IsolatedAsyncioTestCase):
    async def make(self,level):
        from types import SimpleNamespace as Obj
        from toolbelt.adapter import ItermResolver
        from toolbelt.core import Service
        spec=parse_ssh_argv(profile_argv(),12,'start')
        async def var(name): return {'user.toolbelt_zsh_display_version':'3','tmuxRole':'','sshIntegrationLevel':level,'hostname':'fixture.invalid','username':'qa','user.toolbelt_zsh_ready':'2|501|123|/dev/ttys099|'+('a'*32)+'|/tmp/it2tb-501-123-abcdefgh'}.get(name,'')
        async def refresh(): pass
        pane=Obj(session_id='p',async_get_variable=var)
        app=Obj(current_terminal_window=Obj(window_id='w',current_tab=Obj(tab_id='t',current_session=pane)),async_refresh=refresh)
        app.get_session_by_id=lambda sid:pane if sid == 'p' else None
        class Detector:
            async def identify(self,pane): return spec
        made=[]
        class Channel:
            def __init__(self,spec): self.generation='qa';made.append(self)
            async def request(self,op,*args,**kwargs):
                if op=='hello': return 'fixture.invalid\tqa\t501\t/tmp/qa/default'
                if op=='inspect': return 'fixture.invalid\t/tmp/qa/default\tabsent\t-\t-\t-'
                raise AssertionError('Mutation not allowed')
            async def close(self): pass
        resolver=ItermResolver(None,None,app,detector=Detector(),channel_factory=Channel)
        return await Service(resolver).snapshot(),made

    async def test_wrapper_without_native_integration_proof_refused(self):
        state,made=await self.make(0)
        self.assertEqual(state['status'],'unsupported')
        self.assertEqual(made,[])

    async def test_verified_profile_reads_without_attaching(self):
        state,made=await self.make(2)
        self.assertEqual(state['status'],'online')
        self.assertEqual(len(made),1)
        self.assertEqual(state['sessions'],[])
