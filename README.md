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
- `/t left`、`/t right`、`/t tab`、`/t pgup`、`/t pgdn`、`/t ctrlc`、`/t stop`：高级控制

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
