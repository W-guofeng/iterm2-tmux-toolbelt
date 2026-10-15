# Screenshot provenance / 截图来源

`en/` and `zh-CN/` contain real PNG snapshots produced by native macOS `WKWebView.takeSnapshot` while loading this project's unmodified HTML/CSS/JavaScript. The English page selected English from its native process language; the Chinese page selected Chinese. Process-only `AppleLanguages` launch arguments simulate each preferred language without writing macOS/iTerm2 settings.

The capture fixture runs the real core/SSH operation protocol locally against a random, private tmux socket and a private PTY client. New, switch, rename, immediate deletion, Return SSH and the empty state are real fixture operations. SSH/focus metadata is an explicitly anonymous demo adapter (`demo@localhost`, demo tab/pane labels). The setup image shows the actual product setup card with demo readiness metadata; no remote installer is run.

These are actual interface captures, not drawings, generated illustrations or edited production screenshots. They are not evidence of an actual remote SSH identity or a physical mouse click in iTerm2's Toolbelt. No user terminal, private server, personal path, key, token, browser URL bar or task content was captured. No pixels were composited or blurred; private information was absent at capture time. Generic documented integration filenames in the setup guidance are product instructions, not personal paths.

```sh
xcrun swiftc -module-cache-path /tmp/toolbelt-swift-cache scripts/capture_screenshots.swift -o /tmp/toolbelt-capture-screenshots -framework WebKit -framework AppKit
python3 scripts/capture_screenshots.py --tmux "$(command -v tmux)" --output docs/screenshots/en --language en
python3 scripts/capture_screenshots.py --tmux "$(command -v tmux)" --output docs/screenshots/en --language en --setup
python3 scripts/capture_screenshots.py --tmux "$(command -v tmux)" --output docs/screenshots/zh-CN --language zh-CN
python3 scripts/capture_screenshots.py --tmux "$(command -v tmux)" --output docs/screenshots/zh-CN --language zh-CN --setup
```

Review every image visually after capture. The fixture never registers a production Toolbelt, changes system language, accesses personal SSH credentials or targets the user's tmux server.

中文：两套图片均为真实原生 WebView 页面截图，使用隔离 tmux 的真实操作及匿名主机/焦点适配数据。语言通过截图进程的临时首选语言选择，不修改系统设置。配置图展示真实配置卡片，但不执行安装。没有编辑、遮盖或合成私人截图，不将演示元数据冒充远端真机验收。
