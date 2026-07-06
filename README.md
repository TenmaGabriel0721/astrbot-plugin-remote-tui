# Remote TUI

通过 OneBot 文本指令远程控制本机 Codex / Claude Code TUI，会话画面以图片返回。

## 必要依赖

Python 依赖由 AstrBot 读取 `requirements.txt` 安装：

- `Pillow`
- `wcwidth`

系统依赖需要在 AstrBot 运行环境里手动安装：

- `tmux`
- `codex`
- `claude`

`tmux` 是必要运行时依赖，不可用时插件不会启动 TUI，会返回图片错误提示。可以通过后台配置 `tmux_path` 指定 tmux 的绝对路径。

## 指令

- `/t`：刷新当前截图
- `/t codex`：启动或切换 Codex
- `/t claude`：启动或切换 Claude Code
- `/t 内容`：发送内容并自动回车
- `/t up`、`/t down`、`/t enter`、`/t esc`：控制 TUI 菜单
- `/t send /root/a.png`：直接发送本机文件或目录
- `/t 把 /root/a.png 发出来`：直接发送本机文件或目录
- `/t left`、`/t right`、`/t tab`、`/t pgup`、`/t pgdn`、`/t ctrlc`、`/t stop`：高级控制

## 文件发送

插件会给 Codex / Claude Code 的 tmux 会话注入 `qqsend` 命令：

```bash
qqsend /root/a.png
qqsend ./dist/result.zip
qqsend ./output
```

- 图片扩展名默认按图片消息发送
- 普通文件按文件消息发送
- 目录会自动打包成 zip 后发送
- `/t send <路径>` 和 `/t 把 <路径> 发出来` 不经过 TUI，直接发送
- 如果普通 TUI 请求里包含“发出来/发送”等意图，插件会给 Codex/Claude 附加 `qqsend` 用法提示
- Codex/Claude 执行 `qqsend` 后，插件会在本次回复或下次 `/t` 刷新时发送文件

安全限制：

- 默认只允许发送 `/root` 下的文件
- 默认禁止 `.ssh`、`.git`、`.env`、token、secret 等敏感路径
- 默认单文件最大 50MB
- 默认目录打包最大 100MB
- 默认一次最多发送 10 个路径

## 截图等待

插件不会在发送内容后立刻截图，而是轮询 tmux pane：

- 检测到 Codex / Claude 回到输入提示时截图
- 检测到确认菜单、选择菜单、权限提示时截图
- 画面需要连续稳定一小段时间，避免截到半刷新状态
- 超过 `submit_wait_timeout_seconds` 后返回当前进度图，可继续 `/t` 刷新

相关配置：

- `submit_wait_timeout_seconds`：发送文本后的最长等待时间，默认 120 秒
- `control_wait_timeout_seconds`：控制按键后的最长等待时间，默认 8 秒
- `wait_stable_ms`：画面稳定判定时间，默认 1200 毫秒
- `wait_poll_interval_ms`：轮询间隔，默认 500 毫秒
- `submit_delay_ms`：粘贴文本后到发送 Enter 的延迟，默认 200 毫秒
