# Changelog

## v0.5.0 - 2026-07-07

### Added

- Added `remote_tui_run` LLM tool so AstrBot can delegate tasks to Codex / Claude Code through Remote TUI.
- Added `remote_tui_key` LLM tool so AstrBot can inspect and operate TUI menus with capture/up/down/enter/esc/etc.
- Added LLM tool configuration:
  - `llm_tool_enabled`
  - `llm_tool_default_app`
  - `llm_tool_max_result_chars`
- Added optional permission-request auto confirmation for Codex/Claude operation prompts:
  - `auto_confirm_permissions`
  - `auto_confirm_max_per_turn`
  - `auto_confirm_delay_ms`

### Changed

- Interactive screen detection now separates permission prompts from selection menus.
- `/model`, `/resume`, model selection, and session selection menus are excluded from auto confirmation.
- Plugin metadata version updated to `v0.5.0`.

## v0.4.3 - 2026-07-07

### Changed

- Image input prompt output now keeps the user message and image URL/path on one line, with whitespace normalized.
- This avoids some TUI clients treating the image URL/path as a second submitted message.
- Plugin metadata version updated to `v0.4.3`.

## v0.4.2 - 2026-07-07

### Added

- Added `image_input_prefer_url`.

### Changed

- Image input now prefers `http/https` URLs from OneBot image components.
- Images without a usable URL still fall back to local cache paths.
- Prompt output remains simple: original message plus one image URL or absolute path per line.

## v0.4.1 - 2026-07-07

### Changed

- Simplified image input prompts: the terminal now receives only the user message plus cached absolute image path lines.
- Image messages without text now send only the cached image path.

## v0.4.0 - 2026-07-07

### Added

- Added QQ image input caching for `/t` messages.
- Added support for direct image messages and quoted image messages.
- Added prompt injection with cached local image paths for Codex / Claude Code.
- Added image input configuration:
  - `image_input_enabled`
  - `image_input_include_replies`
  - `image_input_max_images`
  - `image_input_max_file_size_mb`

### Changed

- `/t` with images and no text now sends a default image-inspection request to the active TUI session.
- Cached input images are cleaned up with the plugin cache retention policy.
- Plugin metadata version updated to `v0.4.0`.

## v0.3.0 - 2026-07-07

### Added

- Bundled screenshot fonts for fresh installs:
  - `NotoSansMono-Regular.ttf`
  - `NotoSansMono-Bold.ttf`
  - `wqy-zenhei.ttc`
- Added font license documentation in `THIRD_PARTY_LICENSES.md` and `assets/fonts/licenses/`.
- Added full installation instructions for AstrBot plugin installs.
- Added `tmux` installation examples for Debian/Ubuntu, CentOS/RHEL, Arch, Alpine, and macOS.
- Added Docker, Codex/Claude login, `qqsend`, and font troubleshooting notes.

### Changed

- Terminal rendering now prefers bundled fonts before falling back to system fonts.
- Plugin metadata version updated to `v0.3.0`.
- Repository is intended to be public-installable.

## v0.2.0 - 2026-07-07

### Added

- Added file sending support through `/t send <path>`.
- Added Chinese direct file-send parsing, such as `/t 把 /root/a.png 发出来`.
- Added `qqsend <path>` interface inside Remote TUI Codex / Claude sessions.
- Added file queue handling between tmux sessions and AstrBot.
- Added image/file routing:
  - image extensions are sent as image messages
  - ordinary files are sent as file messages
  - directories are zipped before sending
- Added file sending safety controls:
  - allowed root directories
  - denied names and sensitive keywords
  - max file size
  - max archive size
  - max files per archive
  - max paths per send request

### Changed

- TUI prompts that look like file-send requests can append a short `qqsend` usage hint for Codex/Claude.
- `qqsend` is also installed to `~/.local/bin/qqsend` when safe, improving compatibility with existing tmux sessions.
- `qqsend` can infer the tmux session from `TMUX_PANE` if Remote TUI environment variables are missing.
- File queue consumption tolerates older sessions that do not include a user key in queue records.

## v0.1.0 - 2026-07-07

### Added

- Initial Remote TUI plugin for AstrBot.
- Added OneBot text command entry through `/t`.
- Added Codex and Claude Code tmux session management.
- Added terminal screenshot rendering as images.
- Added core commands:
  - `/t`
  - `/t codex`
  - `/t claude`
  - `/t <content>`
  - `/t up`
  - `/t down`
  - `/t enter`
  - `/t esc`
- Added advanced controls:
  - `/t left`
  - `/t right`
  - `/t tab`
  - `/t pgup`
  - `/t pgdn`
  - `/t ctrlc`
  - `/t stop`
- Added permission checks for users and groups.
- Added tmux startup environment handling for common Node/npm/nvm/local binary paths.
- Added screen wait logic to avoid fixed-delay screenshots during Codex/Claude work.
- Added CJK-aware terminal rendering with `wcwidth`.
