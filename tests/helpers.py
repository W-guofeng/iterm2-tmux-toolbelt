from toolbelt.core import Target, IDENTITY_COMMAND, LIST_COMMAND


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.identity = "server-a\t/tmp/tmux-a\t101\t/dev/ttys1\t1\t$1\n"
        self.rows = "$1\t10\t1\t1\talpha\n$2\t20\t1\t0\tbeta\n"
        self.fail = False
        self.hook = None

    async def async_send_command(self, command):
        self.commands.append(command)
        if self.hook:
            self.hook(command)
        if self.fail:
            raise RuntimeError("private RPC detail")
        if command == IDENTITY_COMMAND:
            return self.identity
        if command == LIST_COMMAND:
            return self.rows
        return ""

    @property
    def mutations(self):
        return [c for c in self.commands if c not in (IDENTITY_COMMAND, LIST_COMMAND)]


class FakeResolver:
    def __init__(self):
        self.transport = FakeTransport()
        self.target = Target("w1", "t1", "p1", "conn-a", self.transport)
        self.error = None
        self.on_resolve = None

    async def resolve(self):
        if self.error:
            raise self.error
        if self.on_resolve:
            self.on_resolve()
        return self.target
