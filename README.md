# nanobot-channel-anon

`nanobot-channel-anon` 是一个给 nanobot 使用的 QQ 频道插件，通过 NapCat 的 OneBot v11 接口接入 QQ，目标场景是 **群聊陪聊** 和 **群管理**。

它可以把 QQ 私聊、群聊事件接入 nanobot，让机器人在群里陪聊、响应触发消息，并结合 MCP 工具完成一些常见群管操作。

## 功能

- 接入 NapCat OneBot v11 WebSocket
- 支持 QQ 私聊、群聊、poke 事件
- 支持群聊触发：关键词、@ 机器人、回复机器人、概率触发
- 支持上下文缓存，并把最近聊天整理成统一的 CTX 文本上下文后再交给 nanobot
- 支持图片、语音等常见消息处理
- 支持文本、图片、语音、视频、文件发送
- 附带 NapCat HTTP MCP 工具，可用于删消息、戳一戳、点赞、处理加群/加好友请求


## 安装

当前建议从源码安装和使用：

```bash
uv sync --locked
uv build
uv run nanobot plugins list
```

正常情况下应能看到名为 `anon` 的频道入口。

> PyPI 安装方式预留：后续发布后会在这里补充。

## 快速开始

### 1. 先执行 onboarding

先让 nanobot 生成配置文件和工作区：

```bash
uv run nanobot onboard
```

### 2. 在生成的配置里启用本频道

在 nanobot 配置文件中补上本频道的最小配置，例如：

```json
{
  "enabled": true,
  "ws_url": "ws://127.0.0.1:3001",
  "allow_from": ["123456", "987654321"]
}
```

- `ws_url`：你的 NapCat WebSocket 地址
- `allow_from`：允许接入的 QQ 号或群号

> 注意：`allow_from=[]` 的语义是拒绝所有人。

### 3. 启动 nanobot

```bash
uv run nanobot gateway --config /path/to/config.json
uv run nanobot channels status -c /path/to/config.json
```

## MCP 群管工具

MCP 的安装、配置和使用说明见 [nanobot_channel_anon/mcp/README.md](nanobot_channel_anon/mcp/README.md)。

## 开发

```bash
uv run ruff check .
uv run pyright
uv run pytest
uv build
```

## 注意事项

- 启用频道时，`ws_url` 必填
- `allow_from=[]` 表示拒绝所有来源
- 语音转写依赖上游能力，必要时会使用 `ffmpeg` 转码

