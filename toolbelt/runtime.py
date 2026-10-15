"""Single process ownership and idempotent Toolbelt visibility."""
import asyncio
import fcntl
import os
from pathlib import Path
import stat
import tempfile

TOOL_IDENTIFIER = "dev.iterm2-tmux-toolbelt.sessions"
TOOL_NAME = "tmux Sessions"


class InstanceLock:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(tempfile.gettempdir()) / ("iterm2-tmux-toolbelt-" + str(os.getuid()) + ".lock")
        self.fd = None

    def acquire(self):
        flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise RuntimeError("Invalid instance lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        # Never unlink a lock file: another process might own its inode.


async def reveal_toolbelt(api, connection, remount=False):
    """Show the tool and sidebar; never toggle an already checked menu off."""
    visible = True
    for identifier in ("Toolbelt." + TOOL_NAME, "Show Toolbelt"):
        try:
            state = await asyncio.wait_for(api.MainMenu.async_get_menu_item_state(connection, identifier), 5)
            if not state.enabled:
                visible = False
                continue
            if remount and identifier == "Toolbelt." + TOOL_NAME and state.checked:
                # A surviving WKWebView can retain a previous script's URL.
                # Await removal before rebuilding only this tool from the registry.
                await asyncio.wait_for(api.MainMenu.async_select_menu_item(connection, identifier), 5)
                state = await asyncio.wait_for(api.MainMenu.async_get_menu_item_state(connection, identifier), 5)
            if not state.checked:
                await asyncio.wait_for(api.MainMenu.async_select_menu_item(connection, identifier), 5)
            state = await asyncio.wait_for(api.MainMenu.async_get_menu_item_state(connection, identifier), 5)
            visible = visible and state.checked
        except Exception:
            visible = False
    if not visible:
        print("无法自动显示侧栏；请选 View > Show Toolbelt，再选 Toolbelt > tmux Sessions。", flush=True)
    return visible


async def bounded_operation(coro, seconds, stage):
    from .observability import event, waiting_stack
    task=asyncio.create_task(coro)
    done,_=await asyncio.wait({task},timeout=seconds)
    if not done:
        event('OPERATION_TIMEOUT',stage=stage,stack=waiting_stack(task))
        task.cancel()
        raise asyncio.TimeoutError(stage)
    return task.result()


async def wait_for_disconnect(connection):
    await connection.websocket.wait_closed()
    return getattr(connection.websocket,'close_code',None)


async def wait_for_disconnect_or_stall(connection, stalled):
    closed=asyncio.create_task(wait_for_disconnect(connection))
    stuck=asyncio.create_task(stalled.wait())
    done,pending=await asyncio.wait({closed,stuck},return_when=asyncio.FIRST_COMPLETED)
    for task in pending: task.cancel()
    return 'state-stalled' if stuck in done else closed.result()
