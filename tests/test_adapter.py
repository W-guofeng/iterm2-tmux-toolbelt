import unittest
from types import SimpleNamespace as Obj
from toolbelt.adapter import ItermResolver
from toolbelt.core import ToolbeltError


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def role(_): return self.role
        async def refresh(): pass
        self.role = "client"
        self.pane = Obj(session_id="p2", async_get_variable=role)
        self.tab = Obj(tab_id="t", current_session=self.pane, tmux_connection_id="b")
        window = Obj(window_id="w", current_tab=self.tab)
        self.app = Obj(current_terminal_window=window, async_refresh=refresh)
        self.a = Obj(connection_id="a", owning_session=Obj(session_id="p1"))
        self.b = Obj(connection_id="b", owning_session=Obj(session_id="gateway-b"))
        self.conns = [self.a, self.b]
        async def connections(_): return self.conns
        self.api = Obj(async_get_tmux_connections=connections)
        self.resolver = ItermResolver(self.api, None, self.app)

    async def test_theme_attributes_and_api_failure_fallback(self):
        async def theme(): return ['minimal', 'dark', 'highContrast']
        self.app.async_get_theme = theme
        self.assertEqual(await self.resolver.appearance(), {'mode':'dark', 'contrast':True})
        async def unavailable(): raise RuntimeError('Unavailable')
        self.app.async_get_theme = unavailable
        self.assertEqual(await self.resolver.appearance(), {'mode':'auto', 'contrast':False})

    async def test_exact_client_connection(self):
        self.assertIs((await self.resolver.resolve()).transport, self.b)

    async def test_gateway_maps_owning_pane(self):
        self.role = "gateway"
        self.b.owning_session.session_id = "p2"
        self.assertIs((await self.resolver.resolve()).transport, self.b)

    async def test_no_fallback_to_first_connection(self):
        self.tab.tmux_connection_id = "missing"
        with self.assertRaises(ToolbeltError): await self.resolver.resolve()
        self.role = "gateway"
        with self.assertRaises(ToolbeltError): await self.resolver.resolve()

    async def test_plain_ssh_and_mixed_panes_fail_closed(self):
        self.role = ""
        with self.assertRaises(ToolbeltError) as error: await self.resolver.resolve()
        self.assertEqual(error.exception.code, "unsupported")

    async def test_ambiguous_gateway(self):
        self.role = "gateway"
        self.a.owning_session.session_id = self.b.owning_session.session_id = "p2"
        with self.assertRaises(ToolbeltError): await self.resolver.resolve()


class OrdinarySSHAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_pane_maps_exact_ssh_and_reconnect_changes_context(self):
        from toolbelt.core import Service
        from toolbelt.process import parse_ssh_argv
        spec=parse_ssh_argv(['ssh','fixture'],pid=123,started='start')
        variables={'user.toolbelt_zsh_display_version':'3','tmuxRole':'','hostname':'fixture','username':'user','user.toolbelt_zsh_ready':'2|501|123|/dev/ttys099|'+('a'*32)+'|/tmp/it2tb-501-123-abcdefgh','tty':'/dev/ttys099'}
        async def var(name): return variables.get(name,'')
        async def refresh(): pass
        pane=Obj(session_id='p',async_get_variable=var)
        app=Obj(current_terminal_window=Obj(window_id='w',current_tab=Obj(tab_id='t',current_session=pane)),async_refresh=refresh)
        app.get_session_by_id=lambda sid:pane if sid == 'p' else None
        class Detector:
            async def identify(self,pane): return spec
        channels=[]
        class Channel:
            def __init__(self,spec):
                self.spec=spec;self.generation=str(len(channels));self.closed=False;channels.append(self)
            async def request(self,op,*args,**kwargs):
                if op=='hello': return 'fixture\tuser\t501\t/tmp/fixture/default'
                if op=='inspect': return 'fixture\t/tmp/fixture/default\tabsent\t-\t-\t-'
                raise AssertionError('Unexpected mutation')
            async def close(self): self.closed=True
        resolver=ItermResolver(None,None,app,detector=Detector(),channel_factory=Channel)
        service=Service(resolver)
        first=await service.snapshot()
        self.assertEqual(first['status'],'online')
        self.assertEqual(first['target']['pane'],'p')
        self.assertEqual(first['sessions'],[])
        await service.action({'action':'reconnect'})
        second=await service.snapshot()
        self.assertNotEqual(first['context'],second['context'])
        self.assertTrue(channels[0].closed)
        variables['hostname']='different-remote'
        self.assertEqual((await service.snapshot())['status'],'unsupported')
        await resolver.close()
