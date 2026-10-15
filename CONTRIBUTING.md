# Contributing

[English README](README.md) | [简体中文 README](README.zh-CN.md)

Project-authored contributions use Apache-2.0. Preserve external component notices described in [THIRD_PARTY.md](THIRD_PARTY.md).

- Read [architecture](docs/ARCHITECTURE.md) and [testing](docs/TESTING.md). Use disposable private tmux sockets, never production servers or a running work CLI.
- Preserve same-pane attachment, exact target/client validation, direct-delete behavior, no terminal command text and no automatic mutation retries. A meaningful failure-mode regression test is required for changes to those boundaries.
- Run unit/integration tests, JavaScript syntax checks and the deterministic bundle build. Keep `dist/tmux_toolbelt.py` aligned with source.
- Keep the complete English and Chinese READMEs aligned. Add every new user-facing message to the English catalog and preserve user session names. Screenshot each locale with the actual page; [capture instructions](docs/screenshots/README.md) provide anonymous fixtures.
- Do not enable API/AutoLaunch, change global themes/permissions, install remote configuration or interrupt existing terminals as a test side effect. Setup buttons must explain changes before the user's click.
- Never commit credentials, private host addresses/names, personal paths, process environments, logs, backup receipts, `.git` snapshots or internal acceptance records. Review image pixels and decoded bundle contents before publishing.
- Report security issues privately to the repository owner; do not include exploitable private-host details in public issues.

## 贡献约定

自有贡献采用 Apache-2.0，第三方组件保留原授权。测试只使用明确的私有临时 socket，保护真实任务；原 pane 路由、身份校验、直接删除、无命令文本及不重试修改操作属于核心边界。

同步完整中英文 README 与界面翻译，新增文案应有英文；session 名等用户数据不翻译。截图使用真实页面和匿名演示环境。提交前检查代码、图片像素和解包后的产物，不包含私人主机、凭证、日志或备份。API/AutoLaunch/远端配置和用户终端不能作为测试副作用修改。
