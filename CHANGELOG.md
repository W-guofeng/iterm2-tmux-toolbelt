# Release notes

## v0.0.1

First public release of the iTerm2 tmux Toolbelt extension.

- Discover sessions for the active SSH tab/pane and selected remote tmux socket.
- Create detached sessions; switch in the original verified zsh pane; rename; delete directly; Return to SSH while preserving tmux tasks.
- Reuse existing native `tmux -CC` connections where available.
- Explicit one-click shell integration/no-echo helper setup, backups and known-helper display repair.
- Automatic English/Simplified Chinese UI, matching documentation screenshots, native semantic light/dark colors.
- Exact target/client/session validation, loopback authentication/origin protection, bounded recovery and private logs.
- Deterministic single-file bundle, reproducible fixtures and Apache-2.0 licensing for project-authored code.

Initial acceptance targets iTerm2 3.7.4. Same-pane first attachment requires remote interactive zsh and noninteractive SSH authentication. Native `-CC` window mapping, unsupported wrappers/shells, existing-client rebinding and opaque WebView material have the limits documented in the READMEs. Delete terminates all tasks in its session without confirmation.
