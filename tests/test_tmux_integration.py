"""Real tmux tests opt in with TMUX_TEST_BINARY. Never use a user's server."""
import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from toolbelt.core import Service, Target, IDENTITY_COMMAND, parse_identity

BINARY = os.environ.get("TMUX_TEST_BINARY")


class ControlTransport:
    def __init__(self, process):
        self.process = process
        self.lock = asyncio.Lock()

    async def block(self):
        output = []
        begun = None
        while True:
            line = await asyncio.wait_for(self.process.stdout.readline(), 5)
            if not line:
                raise RuntimeError("Control client disconnected")
            text = line.decode().rstrip("\n")
            if text.startswith("%begin "):
                begun = text[7:].split()[:2]
            elif begun and text.startswith(("%end ", "%error ")):
                if text.split()[1:3] == begun:
                    if text.startswith("%error "):
                        raise RuntimeError("tmux command failed")
                    return "\n".join(output) + ("\n" if output else "")
            elif begun:
                output.append(text)

    async def async_send_command(self, command):
        async with self.lock:
            self.process.stdin.write(command.encode() + b"\n")
            await self.process.stdin.drain()
            return await self.block()

    async def close(self):
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.terminate()
                await self.process.wait()


@unittest.skipUnless(BINARY, "Set TMUX_TEST_BINARY to run isolated tmux tests")
class RealTmuxTests(unittest.IsolatedAsyncioTestCase):
    async def cli(self, socket, *args):
        # argv only, explicit private socket, no default tmux server.
        env = {**os.environ, "TMUX":"", "TERM":"xterm-256color"}
        process = await asyncio.create_subprocess_exec(BINARY, "-S", socket, "-f", "/dev/null", *args,
                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode())
        return stdout.decode().strip()

    async def control(self, socket, name):
        process = await asyncio.create_subprocess_exec(BINARY, "-S", socket, "-f", "/dev/null", "-C", "attach-session", "-t", "=" + name,
            env={**os.environ, "TMUX":"", "TERM":"xterm-256color"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        transport = ControlTransport(process)
        await transport.block()  # initial attach response, notifications follow
        self.controls.append(transport)
        return transport

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="toolbelt-isolated-")
        self.sockets = [str(Path(self.directory.name) / name) for name in ("server-a.sock", "server-b.sock")]
        self.controls = []
        for socket in self.sockets:
            await self.cli(socket, "new-session", "-d", "-s", "base", "sleep", "600")
            # Isolated server option only; keep test control clients alive.
            await self.cli(socket, "set-option", "-s", "exit-unattached", "off")
        self.transport = await self.control(self.sockets[0], "base")
        self.target = Target("window", "tab", "pane", "connection-a", self.transport)
        test = self
        class Resolver:
            async def resolve(self): return test.target
        self.service = Service(Resolver())
        self.state = await self.service.snapshot()
        self.assertEqual(self.state["status"], "online", self.state)

    async def asyncTearDown(self):
        for control in self.controls:
            await control.close()
        for socket in self.sockets:
            try:
                await self.cli(socket, "kill-server")
            except RuntimeError:
                pass
        self.directory.cleanup()

    async def row(self, name):
        state = await self.service.snapshot()
        row = next(r for r in state["sessions"] if r["name"] == name)
        return state, {"context":state["context"], "id":row["id"], "created":row["created"]}

    async def test_crud_switch_issuing_client_and_socket_isolation(self):
        # A second control client on the same server must stay on its session.
        other = await self.control(self.sockets[0], "base")
        other_before = parse_identity(await other.async_send_command(IDENTITY_COMMAND))
        await self.service.action({"context":self.state["context"], "action":"create", "name":"toolbelt-test"})
        state, base = await self.row("toolbelt-test")
        self.assertEqual(state["target"]["socket"], self.sockets[0])
        await self.service.action({**base, "action":"switch"})
        current = parse_identity(await self.transport.async_send_command(IDENTITY_COMMAND))
        other_after = parse_identity(await other.async_send_command(IDENTITY_COMMAND))
        self.assertEqual(current.current, base["id"])
        self.assertEqual(other_after.current, other_before.current)
        await self.service.action({**base, "action":"rename", "name":"重命名-1"})
        self.assertEqual(await self.cli(self.sockets[0], "display-message", "-p", "-t", base["id"], "#{session_name}"), "重命名-1")
        # Switch back before deleting the disposable test session.
        _, original = await self.row("base")
        await self.service.action({**original, "action":"switch"})
        await self.service.action({**base, "action":"delete"})
        self.assertEqual(await self.cli(self.sockets[0], "list-sessions", "-F", "#{session_name}"), "base")
        self.assertEqual(await self.cli(self.sockets[1], "list-sessions", "-F", "#{session_name}"), "base")

    async def test_same_names_and_ids_on_different_sockets_reject_stale_action(self):
        old = self.state
        second = await self.control(self.sockets[1], "base")
        # Even a hypothetical reused connection ID cannot bypass socket identity.
        self.target = Target("window", "tab", "pane", "connection-a", second)
        with self.assertRaises(Exception):
            await self.service.action({"context":old["context"], "action":"rename", "id":"$0", "created":old["sessions"][0]["created"], "name":"wrong"})
        self.assertEqual(await self.cli(self.sockets[1], "list-sessions", "-F", "#{session_name}"), "base")

    async def test_disconnect_offline_no_mutation(self):
        await self.transport.close()
        state = await self.service.snapshot()
        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["sessions"], [])
        self.assertEqual(await self.cli(self.sockets[0], "list-sessions", "-F", "#{session_name}"), "base")
