# 原生主题与半透明材质

iTerm2 3.7.4 的 Toolbelt 容器创建 `NSVisualEffectView`，使用 `NSVisualEffectMaterialSidebar` 和 `BehindWindow` 混合。原生侧栏背景会随窗口后的内容变化，截图采样色无法替代该材质。

源码 `sources/Toolbelt/ToolWebView.m` 创建默认 `WKWebView`，只注册页面 URL，没有将其设为透明。公开 Python `async_register_web_view_tool` 也没有颜色、透明度或材质参数。网页 `background: transparent` 或 `backdrop-filter` 不能越过宿主默认的不透明 WebView 取得窗口后的像素。因此本项目没有通过 CSS 透明值假称实现透色，也不注入 iTerm2 进程或修改应用包。

当前实现读取 `App.async_get_theme()` 的深浅及 highContrast 属性；WebView CSS 使用系统语义色 `-apple-system-control-background`、label、secondary-label、separator、tertiary-fill、control-accent、red，标准 Canvas 等作为兼容回退。主题数据不可用时跟随原生 WebView 的 `prefers-color-scheme`。所有颜色由系统解析；不再断言固定 RGB 才是正确主题。终端 Profile 的调色板和背景透明度并不是原生侧栏材质，不直接复制为面板底色。

真正透出原生材质需要 iTerm2 宿主提供透明 WebView 支持（例如由宿主禁用背景绘制并使用透明网页），目前本插件无法经公开 API 完成。未改系统/iTerm2 全局主题设置。系统语义色跟随已经测试，原生半透明效果没有实现或宣称通过。

参考：

- [iTerm2 Tool API](https://iterm2.com/python-api/tool.html)
- [iTerm2 App theme API](https://iterm2.com/python-api/app.html#iterm2.App.async_get_theme)
- [ToolWebView 源码](https://github.com/gnachman/iTerm2/blob/v3.7.4/sources/Toolbelt/ToolWebView.m)
- [Toolbelt 原生材质源码](https://github.com/gnachman/iTerm2/blob/v3.7.4/sources/Toolbelt/iTermToolbeltView.m)
- [WebKit 系统颜色关键字](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/css/CSSValueKeywords.in)
