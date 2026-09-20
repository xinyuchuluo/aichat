# AIChat · macOS 运行方式

## 方式 1：双击脚本（最快，先试这个）⭐

**双击 `AIChat.command` 即可启动。**

> 首次 macOS 若提示「无法验证开发者」：右键 AIChat.command → 打开 → 再点「打开」。

## 方式 2：命令行

```bash
cd /Users/chenzhichao/WorkBuddy/2026-07-04-18-03-19/aichat
python3 main.py
```

## 方式 3：打包成真正的 macOS App AIChat.app

双击 `build_app.command`（3–10 分钟）。

完成后 `dist/AIChat.app` 就是真正的 Mac 应用：
- 双击直接运行，无需命令行
- 可拖进「应用程序」文件夹 (/Applications)
- 可固定到 Dock

> 首次双击 `.app` 被拦截 → 右键 AIChat.app → 打开 → 再点「打开」。

---

项目根: /Users/chenzhichao/WorkBuddy/2026-07-04-18-03-19/aichat
