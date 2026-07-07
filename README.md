# Remote TUI

通过 OneBot 文本指令远程控制本机 Codex / Claude Code TUI，会话画面以图片返回。

## 安装

在 AstrBot 插件目录安装：

```bash
cd /root/AstrBot/data/plugins
git clone https://github.com/TenmaGabriel0721/astrbot-plugin-remote-tui.git astrbot_plugin_remote_tui
```

然后在 AstrBot 后台重载插件，或重启 AstrBot。

Python 依赖由 AstrBot 读取 `requirements.txt` 自动安装：

- `Pillow`
- `wcwidth`

插件已内置截图字体：

- `NotoSansMono-Regular.ttf`
- `NotoSansMono-Bold.ttf`
- `wqy-zenhei.ttc`

一般不需要额外安装中文字体。字体许可证见 `assets/fonts/licenses/`。

## 系统依赖

必须在 AstrBot 运行环境里安装：

- `tmux`
- `codex` 或 `claude`

`tmux` 安装示例：

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install -y tmux

# CentOS / RHEL / Rocky / AlmaLinux
sudo dnf install -y tmux

# 旧版 CentOS
sudo yum install -y tmux

# Arch Linux
sudo pacman -S tmux

# Alpine Linux
sudo apk add tmux

# macOS
brew install tmux
```

如果 AstrBot 跑在 Docker 容器里，需要在容器内安装 `tmux`，或者把安装命令写进镜像构建流程。

Codex / Claude Code 需要先在同一个系统用户下安装并完成登录。插件默认会自动查找：

- `/root/.local/bin`
- `/root/.cargo/bin`
- `/root/.npm-global/bin`
- `/root/.nvm/versions/node/*/bin`

`tmux` 是必要运行时依赖，不可用时插件不会启动 TUI，会返回图片错误提示。可以通过后台配置 `tmux_path` 指定 tmux 的绝对路径。

## 指令

- `/t`：刷新当前截图
- `/t codex`：启动或切换 Codex
- `/t claude`：启动或切换 Claude Code
- `/t 内容`：发送内容并自动回车
- `图片 + /t 内容`：读取 QQ 图片，并把公网 URL 或本机完整路径发给当前 Codex/Claude
- `/t up`、`/t down`、`/t enter`、`/t esc`：控制 TUI 菜单
- `/t send /root/a.png`：直接发送本机文件或目录
- `/t 把 /root/a.png 发出来`：直接发送本机文件或目录
- `/t left`、`/t right`、`/t tab`、`/t pgup`、`/t pgdn`、`/t ctrlc`、`/t stop`：高级控制

## 图片输入

插件支持读取 QQ 消息里的图片，并把图片 URL 或本机路径追加到发给 Codex / Claude Code 的内容里。

用法：

```text
发送图片，并附带：/t 分析这张图
发送图片，并附带：/t 根据这张图生成 HTML 页面
发送图片，并附带：/t
```

插件发给 TUI 的内容就是原消息加图片地址，全部放在同一行。默认优先使用 OneBot 图片里的 `http/https` 链接；没有可用链接时，才下载缓存成本机完整绝对路径：

```text
分析这张图 https://example.com/qq-image.jpg
```

或：

```text
分析这张图 /root/AstrBot/data/plugin_data/astrbot_plugin_remote_tui/uploads/input_...png
```

说明：

- 这不是 OneBot 按钮或原生图片附件，而是图片 URL 或本机文件路径
- Codex/Claude 需要能访问该 URL 或读取该路径
- 默认读取当前消息图片和引用消息里的图片
- 默认优先传 `http/https` 图片链接
- 默认一次最多 4 张图片
- 默认单张图片最大 20MB
- 回退生成的本机缓存会按 `cache_retention_minutes` 清理
- 如果需要把处理结果发回 QQ，让 Codex/Claude 保存文件后执行 `qqsend <路径>`

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
- 插件会同时把 `qqsend` 安装到 `~/.local/bin`，用于兼容旧 tmux 会话
- 如果 Codex/Claude 提示 `qqsend: command not found`，请重载插件后 `/t stop` 再 `/t codex` 或 `/t claude`

安全限制：

- 默认只允许发送 `/root` 下的文件
- 默认禁止 `.ssh`、`.git`、`.env`、token、secret 等敏感路径
- 默认单文件最大 50MB
- 默认目录打包最大 100MB
- 默认一次最多发送 10 个路径

## LLM 工具

插件会注册 LLM 工具，让 AstrBot 的主 LLM 可以把开发任务委派给 Codex / Claude Code TUI。

### `remote_tui_run`

- `prompt`：发送给 Codex/Claude Code 的完整任务说明
- `app`：`current`、`codex` 或 `claude`，默认 `current`

行为：

- `current` 会优先使用当前用户的活动 TUI 会话
- 没有活动会话时，按 `llm_tool_default_app` 启动默认 TUI
- 工具返回终端纯文本结果给主 LLM，不发送截图
- 如果当前 TUI 停在 `/model`、`/resume` 或其他菜单，工具不会把任务粘进去，会返回终端文本让主 LLM 调用 `remote_tui_key` 处理
- 如果 Codex/Claude 在会话里执行 `qqsend <路径>`，工具会尝试直接把文件发回当前 QQ 会话
- 工具仍会检查本插件权限，默认只允许管理员或配置放行的用户实际执行

### `remote_tui_key`

- `key`：`capture`、`up`、`down`、`left`、`right`、`enter`、`esc`、`tab`、`pgup`、`pgdn`、`ctrlc`
- `app`：`current`、`codex` 或 `claude`，默认 `current`

用于让主 LLM 自己处理 TUI 菜单：

- `/model`、`/resume`、历史会话选择、模型选择：先 `capture` 读取菜单，再按 `up/down/enter/esc`
- 权限请求：开启 `auto_confirm_permissions` 时 `remote_tui_run` 会自动确认；未开启时主 LLM 可在确认当前画面确实是权限请求后调用 `remote_tui_key key=enter`

相关配置：

- `llm_tool_enabled`：启用 LLM 工具，默认开启
- `llm_tool_default_app`：无当前会话时默认使用 `codex` 或 `claude`
- `llm_tool_max_result_chars`：返回给主 LLM 的终端文本最大长度，默认 6000

## 常见问题

### 启动后立即退出

确认 `codex` / `claude` 在 AstrBot 所在用户下能直接运行，并且已经登录。

### 工作目录不对

设置 `default_cwd`，默认是 `/root`。插件启动 TUI 时会显式 `cd` 到这个目录。

### `qqsend: command not found`

重载插件后重新启动 TUI 会话：

```text
/t stop
/t codex
```

插件也会把 `qqsend` 安装到 `~/.local/bin/qqsend` 兼容旧会话。

### 截图字体不好读

默认使用内置字体。仍不满意时可以在配置里指定：

- `font_path`
- `cjk_font_path`
- `font_size`

## 截图等待

插件不会在发送内容后立刻截图，而是轮询 tmux pane：

- 检测到 Codex / Claude 回到输入提示时截图
- 检测到确认菜单、选择菜单、权限提示时截图
- 画面需要连续稳定一小段时间，避免截到半刷新状态
- 超过 `submit_wait_timeout_seconds` 后返回当前进度图，可继续 `/t` 刷新

### 自动确认权限请求

如果不想频繁发送 `/t enter`，可以开启：

```text
auto_confirm_permissions = true
```

开启后，插件只会在识别到 Codex / Claude 的操作权限请求时自动按 Enter，例如运行命令、调用工具、写入或修改文件。`/model`、`/resume`、模型选择、历史会话选择、搜索/筛选列表等菜单不会自动确认，仍会截图返回给你手动选择。

相关配置：

- `submit_wait_timeout_seconds`：发送文本后的最长等待时间，默认 120 秒
- `control_wait_timeout_seconds`：控制按键后的最长等待时间，默认 8 秒
- `wait_stable_ms`：画面稳定判定时间，默认 1200 毫秒
- `wait_poll_interval_ms`：轮询间隔，默认 500 毫秒
- `submit_delay_ms`：粘贴文本后到发送 Enter 的延迟，默认 200 毫秒
- `auto_confirm_permissions`：自动确认操作权限请求，默认关闭
- `auto_confirm_max_per_turn`：单次 `/t` 操作最多自动确认权限次数，默认 3
- `auto_confirm_delay_ms`：自动确认后继续轮询前的等待时间，默认 200 毫秒
