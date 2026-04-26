# nanobot-channel-anon

`nanobot-channel-anon` 是一个给 [nanobot](https://github.com/HKUDS/nanobot) 使用的 QQ 频道插件，通过 NapCat 的 OneBot v11 接口接入 QQ，目标场景是 **群聊** 和 **群管**。

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

日常使用推荐直接安装已发布版本；如果你要跟进最新提交或参与开发，再使用源码方式。

### uv 安装

推荐优先使用 [`uv`](https://github.com/astral-sh/uv)，把 `nanobot` 主程序和本插件装到同一个 tool 环境里：

```bash
uv tool install nanobot-ai --with nanobot-channel-anon --with-executables-from nanobot-channel-anon
nanobot plugins list
```

### pip 安装

如果你不用 `uv`，也可以在已经安装 `nanobot-ai` 的同一个 Python 环境里安装本插件：

```bash
pip install nanobot-ai nanobot-channel-anon
nanobot plugins list
```

正常情况下应能看到名为 `Anon` 的插件，频道名为 `anon`。

安装完成后，包还会提供一个 MCP server 启动命令：`nanobot-anon-mcp`。

### 源码开发安装

如果你是在本仓库内开发或调试，可继续使用源码方式：

```bash
git clone https://github.com/ByteColtX/nanobot-channel-anon.git
cd nanobot-channel-anon
uv sync --locked
uv run nanobot plugins list
```

## 快速开始

### 1. 初始化工作区

```bash
nanobot onboard
```

如果你是在源码仓库里直接运行，也可以使用：

```bash
uv run nanobot onboard
```

### 2. 在配置里启用本频道

在 nanobot 配置文件中补上本频道的最小配置，例如：

```json
{
  "channels": {
    "anon": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "accessToken": "your_access_token",
      "allowFrom": ["*"]
    }
  }
}
```

- `wsUrl`：你的 NapCat WebSocket 地址
- `allowFrom`：允许接入的 QQ 号或群号

> 注意：`allowFrom=[]` 的语义是拒绝所有人。

### 3. 启动 nanobot

```bash
nanobot gateway --config /path/to/config.json
nanobot channels status -c /path/to/config.json
```

如果你是在源码仓库里直接运行，也可以继续使用：

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

- 启用频道时，`wsUrl` 必填
- `allowFrom=[]` 表示拒绝所有来源
- 语音转写依赖上游能力，必要时会使用 `ffmpeg` 转码
