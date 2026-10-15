import asyncio
from dataclasses import replace
import unittest
from toolbelt.core import (Service, ToolbeltError, Target, validate_name,
                           session_id, parse_sessions, parse_identity, quote)
from tests.helpers import FakeResolver


class ValidationTests(unittest.TestCase):
    def test_names(self):
        for value in ("Claude-任务_01", "abc", "123", "équipe"):
            self.assertEqual(validate_name(value), value)
        for value in ("", "a" * 65, None, [], "a b", "a;b", "$(id)", "a:b", "a.b", "a'", 'a"', "a\n", "a\\b", "#{run}", "-a;kill-server"):
            with self.subTest(value=value), self.assertRaises(ToolbeltError):
                validate_name(value)

    def test_exact_ids_and_quote(self):
        self.assertEqual(quote(session_id("$12")), "'$12'")
        for value in ("alpha", "$1;kill-server", "-t", "$-1", "$1\n", "$1:0", None):
            with self.assertRaises(ToolbeltError):
                session_id(value)
        with self.assertRaises(ToolbeltError):
            quote("unsafe'")

    def test_fail_closed_protocol(self):
        for output in ("$1\t1\t1\t0\tx\tbad", "$1\t1\t1\t0\tx\n$1\t1\t1\t0\tx", "$1\tbad\t1\t0\tx", "$1\t1\t1\t0\t\x1b[31m"):
            with self.assertRaises(ToolbeltError):
                parse_sessions(output)
        with self.assertRaises(ToolbeltError):
            parse_identity("host\t/socket\t10\tclient\t0\t$1")
        self.assertEqual(parse_sessions(""), [])
        self.assertEqual(parse_sessions("$1\t1\t2\t0\ta;danger")[0].name, "a;danger")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.resolver = FakeResolver()
        self.service = Service(self.resolver)
        self.state = await self.service.snapshot()
        self.base = {"context": self.state["context"], "id": "$2", "created": 20}

    async def test_reads_never_mutate(self):
        self.assertEqual(self.state["status"], "online")
        self.assertEqual(self.state["target"]["host"], "server-a")
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_create_switch_rename(self):
        await self.service.action({"context": self.state["context"], "action": "create", "name": "新任务-1"})
        await self.service.action({**self.base, "action": "switch"})
        await self.service.action({**self.base, "action": "rename", "name": "changed"})
        self.assertEqual(self.resolver.transport.mutations, [
            "new-session -d -s '新任务-1' -P -F '#{session_id}'",
            "switch-client -t '$2'", "rename-session -t '$2' 'changed'"])

    async def test_duplicate_injection_unknown_action(self):
        for payload in ({"action":"create", "name":"alpha"}, {"action":"create", "name":"a;kill-server"}, {"action":"kill-server"}):
            with self.assertRaises(ToolbeltError):
                await self.service.action({"context":self.state["context"], **payload})
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_direct_delete_precise_id_without_challenge(self):
        await self.service.action({**self.base, "action":"delete"})
        self.assertEqual(self.resolver.transport.mutations, ["kill-session -t '$2'"])

    async def test_delete_current_preserves_issuing_client_on_other_session(self):
        await self.service.action({"context":self.state["context"], "id":"$1", "created":10, "action":"delete"})
        self.assertEqual(self.resolver.transport.mutations, ["switch-client -t '$2'", "kill-session -t '$1'"])

    async def test_old_challenge_or_confirmation_fields_rejected(self):
        for payload in ({**self.base, "action":"prepare-delete"}, {**self.base, "action":"delete", "ticket":"old"}, {**self.base, "action":"delete", "confirmation":"beta"}):
            with self.assertRaises(ToolbeltError):
                await self.service.action(payload)
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_direct_delete_stale_context_or_created_rejected(self):
        for payload in ({**self.base, "created":21}, {**self.base, "context":"old"}, {**self.base, "id":"$999"}):
            with self.assertRaises(ToolbeltError):
                await self.service.action({**payload, "action":"delete"})
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_stale_pane_connection_socket_pid(self):
        for scenario in ("pane", "connection", "socket", "pid"):
            resolver = FakeResolver()
            service = Service(resolver)
            state = await service.snapshot()
            if scenario == "pane": resolver.target = replace(resolver.target, pane="p2")
            if scenario == "connection": resolver.target = replace(resolver.target, connection="conn-b")
            if scenario == "socket": resolver.transport.identity = resolver.transport.identity.replace("/tmp/tmux-a", "/tmp/tmux-b")
            if scenario == "pid": resolver.transport.identity = resolver.transport.identity.replace("101", "102")
            with self.assertRaises(ToolbeltError):
                await service.action({"context":state["context"], "action":"switch", "id":"$2", "created":20})
            self.assertEqual(resolver.transport.mutations, [])

    async def test_focus_change_during_inspection(self):
        self.resolver.transport.hook = lambda _: setattr(self.resolver, "target", replace(self.resolver.target, pane="p2"))
        with self.assertRaises(ToolbeltError):
            await self.service.action({**self.base, "action":"switch"})
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_offline_and_plain_ssh_clear_sessions(self):
        self.resolver.transport.fail = True
        state = await self.service.snapshot()
        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["sessions"], [])
        self.assertNotIn("private", str(state))
        self.resolver.error = ToolbeltError("仅支持 -CC", "unsupported")
        self.assertEqual((await self.service.snapshot())["status"], "unsupported")
        with self.assertRaises(ToolbeltError):
            await self.service.action({**self.base, "action":"rename", "name":"safe"})

    async def test_created_and_missing_id(self):
        for payload in ({**self.base, "created":21}, {**self.base, "created":True}, {**self.base, "id":"$999"}):
            with self.assertRaises(ToolbeltError):
                await self.service.action({**payload, "action":"switch"})
        self.assertEqual(self.resolver.transport.mutations, [])

    async def test_timeout_no_retry(self):
        class Slow:
            async def async_send_command(self, _):
                await asyncio.sleep(1)
        self.resolver.target = replace(self.resolver.target, transport=Slow())
        self.service.timeout = .01
        self.assertEqual((await self.service.snapshot())["status"], "offline")
