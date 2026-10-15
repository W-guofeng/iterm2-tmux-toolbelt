# Third-party components

Project-authored code is licensed under Apache-2.0. This does not change the licenses of external components.

| Component | License / source | Distribution in this project |
| --- | --- | --- |
| iTerm2 and its Python SDK (`iterm2`) | [GPL-2.0-or-later](https://github.com/gnachman/iTerm2/blob/master/LICENSE) | Installed separately by the user/iTerm2; SDK, app, Python runtime and their dependencies are not bundled. |
| Official iTerm2 shell integration | GPL-2.0-or-later; headers in the installed Resources scripts | Not bundled. Explicit setup copies an unchanged, checksum-verified script from the user's iTerm2 app and preserves its license header. |
| tmux | [ISC license and component notices](https://github.com/tmux/tmux/blob/master/COPYING) | Required on the target host; no binary or source is bundled. |
| macOS WebKit / AppKit | System frameworks | Used by optional screenshot/test executables; no framework or font is redistributed. |

The release script bundles only this project's `toolbelt/` files. It does not bundle Python site-packages, the official integration scripts, test SSH keys, logs, or local tmux binaries. Bootstrap test data is synthetic; production code verifies a public upstream bootstrap digest without embedding that upstream script.

Reproduce dependency review with the installed Python distribution metadata and the upstream license links above. When redistributing external components or a combined work, preserve their applicable notices and fulfill their own license obligations. Apache-2.0 here grants rights only to project-authored material.
