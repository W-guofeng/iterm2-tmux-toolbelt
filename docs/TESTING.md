# Verification and reproducible tests

All mutation tests use new disposable sessions and explicit private tmux sockets. Never substitute a work server/socket or a pane running real tasks. Installation, API access, AutoLaunch and remote setup are separate user actions.

## Automated tests

```sh
python3 -m unittest discover -v
TMUX_TEST_BINARY="$(command -v tmux)" python3 -m unittest discover -v
node --check toolbelt/web/app.js
node --check toolbelt/web/i18n.js
python3 scripts/build_iterm_script.py
```

Python 3.9+ runs the core suite. The managed iTerm2 Runtime used for acceptance was Python 3.12.13 / SDK 2.30. A separate optional environment can use `python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`; it still needs user-approved iTerm2 API access. The pinned standalone SDK is distinct from iTerm2's managed SDK.

Without `TMUX_TEST_BINARY`, real tmux tests skip. Tests cover exact IDs/creation times, server/client/context changes, two-socket isolation, no mutation retries, direct deletion, detach policy, request injection, loopback authentication/origin checks, bounded state recovery, one-click setup backups/refusals, language selection, translation coverage and deterministic bundling. macOS zsh tests need the installed official integration and verify actual first → second → first screen output, not just session metadata. They also check unchanged history, partial-input refusal and terminal restoration.

## Native WebKit and isolated SSH

```sh
xcrun swiftc -module-cache-path /tmp/toolbelt-swift-cache scripts/webkit_smoke.swift -o /tmp/toolbelt-webkit-smoke -framework WebKit -framework AppKit
python3 scripts/webkit_smoke.py --tmux "$(command -v tmux)" --crud --width 200 --appearance dark
python3 scripts/webkit_smoke.py --tmux "$(command -v tmux)" --crud --width 240 --appearance light
python3 scripts/ssh_smoke.py --tmux "$(command -v tmux)"
```

The native WebKit fixture renders the actual page and exercises its real HTTP and private tmux path; metadata adapters replace iTerm2 focus/SSH identity. The loopback fixture uses temporary sshd/test keys and a private PTY; personal keys and system sshd settings are untouched. Its SDK prompt/focus adapter is simulated. Neither test claims physical mouse operation in an actual Toolbelt sidebar.

The explicit `--iterm-qa` developer entry additionally uses the real iTerm2 SDK, new SSH/Profile tabs and a private target-host tmux server. It reads an authorized target/profile from private stdin, fences every action to the new pane/socket/server, observes distinct screen markers, verifies Return SSH and cleans only its fixture. Use only on an explicitly authorized test host. No target values, local receipts or private acceptance logs belong in public commits.

## Release evidence

The v0.0.1 preparation on macOS / iTerm2 3.7.4 passed 102 tests with zero skips under both Python 3.9 and Python 3.12 with an explicit test tmux. It also passed dark 200px/light 240px native WebKit CRUD, isolated SSH/ZLE, installation from the extracted release ZIP, deterministic bundle/archive checks, and 20 English/Chinese native screenshots. Current and historical source/blobs plus 27 current/historical screenshot images were reviewed for private identifiers; generated environments, logs and internal records are excluded from publication. Real SDK/authorized Linux checks had also confirmed same-pane session screens, preserved tasks on detach, no printable attach command, exact-client switching and stale/busy-input refusal. These checks do not establish all other iTerm2 builds, SSH wrappers, shell configurations or server policies.

A display regression was specifically reproduced: a helper opened stdin read-only, so tmux could change its selected session but could not write its screen through that terminal fd. Opening fd 0 read/write fixed it. The regression test asserts actual screen markers and the control path now verifies the post-switch client/session identity.

Screenshot capture uses anonymous demo metadata and actual local tmux operations; [provenance and commands](screenshots/README.md) state the boundary. Do not replace real interface captures with mockups.
