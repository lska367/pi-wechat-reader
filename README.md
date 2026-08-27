# pi-wechat-reader

pi extension：读取微信公众号文章（mp.weixin.qq.com）的专用工具。

## 功能

注册 `wechat_read` 工具，输入微信公众号文章链接，返回：

- 标题 / 公众号 / 发布时间 / 正文 / 图片列表
- 可选 `markdown: true` 输出带格式的正文（引用/代码块/图片）

## 原理

- 抓取层：**[curl_cffi](https://github.com/lexiforest/curl_cffi)** TLS/HTTP2 指纹伪装（`impersonate="chrome124"`）+ 微信内 UA（`MicroMessenger/8.0`）
- 无需登录、无需浏览器、无需代理
- 微信常见的普通 HTTP 请求会被"环境异常（完成验证）"反爬页拦截；curl_cffi 指纹伪装是本方案实测唯一稳定路径（jina reader 等服务器端抓取同样无效）

## 安装

通过 pi packages 安装（推荐）：

```bash
pi install git:github.com/lska367/pi-wechat-reader
```

或手动放到扩展目录（任选其一）：

```
~/.pi/agent/extensions/wechat-reader/   # 全局
.pi/extensions/wechat-reader/           # 项目内
```

`/reload` 或重启 pi 后生效。

## 使用

```
wechat_read url: "https://mp.weixin.qq.com/s/..." [markdown: true]
```

## 依赖自举

Python 环境惰性初始化：首次调用自动用 `uv` 创建 `~/.cache/wechat-reader/venv`（curl_cffi + beautifulsoup4），后续复用（实测二次调用约 1.2s）。需要系统已装 `uv`（`brew install uv` / `cargo install uv`）。

## 解析细节

- `#publish_time` 是 JS 填充的空壳 → 改用页面 `ct = "<unix ts>"` 变量兜底，再 fallback 到 `og:published_time` meta
- 正文提取只取"叶子 block"（无 block 级子元素的 p/section/li/pre…），避免微信 section 嵌套造成段落重复
- 图片 URL 去除 watermarks 参数保留 wx_fmt

## 已知限制

- 仅支持公开文章（无需登录可见）
- 代码块若以图片/SVG 渲染则无法还原为源码
- 部分账号文章迁移后链接会失效

## License

MIT