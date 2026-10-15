# Manual acceptance / 人工验收

Use a disposable test machine/socket only. Review installation/API access separately. No production session deletion, Claude input, global theme/permission changes or iTerm2 restart is part of this checklist.

1. Start the marked script; tmux Sessions should load once, with no cross-site error. Duplicate launch should not hide the sidebar/create a second server. Inspect narrow/light/dark layout; verify semantic colors with the native Toolbelt.
2. Connect ordinary system `ssh test-host`. Verify the displayed host, UID-derived/default or explicitly chosen private socket and active pane. Without noninteractive auth, expect an actionable offline message and no password prompt/local fallback.
3. Prepare a private test socket (short path), empty tmux config and existing authorized Shell Integration. In Connection details select only this test socket. With no server, reads must not create one. Click Create: one detached session appears, with no new terminal window/split and no attachment.
4. At a matching idle SSH prompt, click the test session. It must attach in the exact original pane/tab. Create a second detached session; click back and forth, checking current status and unchanged window/pane/SSH process identity.
5. Click Return to SSH. The original SSH prompt must resume; sessions and a test `sleep` task remain. Click again to reattach without adding a pane/window. Verify another client remains unchanged. With destroy-unattached on in a separate attached test session, expect detach to be refused, without policy changes.
6. Run a harmless test foreground CLI or partially type a command in an unbound pane. Clicking a session must refuse initial attachment and send no input. Missing/mismatched Shell Integration must also refuse. Do not use production Claude to test this.
7. Rename to a Chinese name; directly delete a disposable session through its separate dangerous button. No confirmation dialog or row switch should occur. Delete the final disposable session and verify ordinary SSH returns to a creatable empty state. Never use a real work session.
8. Put identical names/IDs on two test sockets/hosts. Change active tab/pane/socket during form submission; stale requests must be rejected. Multiple discovered sockets require a choice; an invalid socket must be correctable in details.
9. Interrupt only the test query/SSH connection. Expect empty list/offline actions. Refresh query or reconnect SSH, and check remaining test jobs. No mutation should repeat. Restart only the plugin and verify new URL/revision; unverified existing clients may require manual detach/rebind.
10. Separately test existing -CC gateway/client routing. Its native window mapping is iTerm2 behavior. Confirm only the issuing client switches/detaches. Stop/uninstall the plugin without session deletion and clean only the test servers.

Record application/SDK/tmux versions, exact steps, failures and evidence. A native WebKit fixture is UI evidence for its own page; a real SSH PTY test with simulated SDK is transport evidence; neither is an authorized test host acceptance. Redact hostnames/paths/session names before sharing screenshots.

中文：全部动作仅用测试机器/私有 socket。重点核对空服务器只点击才新建、原 pane/tab 切换、不分屏、“回到 SSH”只 detach 且保留任务、运行中 CLI/半输入命令绝不接收附着文本、跨 socket/主机的旧请求拒绝。分别记录原生 WebKit、SSH/PTY、实际 iTerm2 鼠标和授权测试主机 的验证范围，不混为已通过。


## Optional setup and appearance QA

Use an isolated test account or a private temporary HOME for installer tests; do not remove a real user's existing integration to make a setup card appear. Verify missing integration shows purpose, affected startup files and a single explicit setup button before any write. Confirm a backup receipt and reconnect guidance, no duplicate hooks, preserved original bytes/permissions, refusal for symlinks/custom hooks/syntax errors, and no command input into a busy SSH terminal. For an already configured user, opening a new SSH connection should remove the setup guidance after metadata arrives.

Run the native fixture with --setup to validate only the card/click/receipt, and --theme-cycle to verify dark-to-light SDK appearance changes even in an Aqua host. These fixtures do not claim production setup clicks or native-material transparency. Do not change the user's global appearance as a test.

11. Open the page under English and Chinese preferred system languages. Verify static labels, errors, setup guidance, accessibility labels and forms use the selected language, while session names/IDs/actions remain unchanged.
