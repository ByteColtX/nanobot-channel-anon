# CLAUDE.md

## 项目概述（WHAT）

构建自定义 nanobot 频道插件，将 NapCat（OneBot v11）通过 WebSocket 接入 nanobot，让 nanobot 充当 QQ 聊天助手。

**当前目录骨架：**

- `nanobot_channel_anon/__init__.py` — 导出 `AnonChannel`
- `nanobot_channel_anon/channel.py` — 频道主实现与配置入口
- `pyproject.toml` — 项目元数据、依赖与 `nanobot.channels` entry-point 注册

**当前仓库为单包 Python 插件项目。**

**主要依赖：**

- `nanobot-ai` — 提供 `BaseChannel`、`MessageBus`、`ChannelManager` 与插件发现机制
- WebSocket 客户端依赖将用于对接 NapCat 的 OneBot v11 事件流与动作调用

**目标接入形态：**

- 通过 WebSocket 连接 NapCat / OneBot v11 服务端
- 接收入站事件并转换为 nanobot 可处理的消息
- 将 nanobot 的出站回复转换为 OneBot 动作，再发回 QQ 会话
- 保持与 nanobot 既有频道契约一致，继续复用 `BaseChannel`、消息总线与频道生命周期管理

## 项目目的（WHY）

将 nanobot 这种 Agent 智能体接入 QQ 会话，让它以聊天伙伴的形式与用户持续互动。

**当前阶段目标：**

- 优先保证插件能被 nanobot 通过 entry-point 成功发现并加载
- 打通 NapCat（OneBot v11）WebSocket 接入与 nanobot 消息总线之间的链路
- 补全真正的出站发送能力，让 nanobot 的回复能够回到 QQ 会话
- 保持与 nanobot 既有频道契约一致，尤其是 `allow_from` 权限控制、消息收发边界与频道生命周期约定

## 开发约定（HOW）

### 常用命令

```bash
uv sync --locked
uv build
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
uv run nanobot plugins list
uv run nanobot onboard --config /path/to/config.json
uv run nanobot gateway --config /path/to/config.json
uv run nanobot channels status -c /path/to/config.json
```

### 验证顺序

- 纯代码改动：至少执行 `uv run ruff check .`、`uv run pyright`、`uv build`
- 涉及测试时：执行 `uv run pytest`
- 涉及插件注册、导入或打包：补充执行 `uv run nanobot plugins list`
- 涉及运行态链路：再结合 `onboard`、`gateway` 和 `channels status` 用真实配置验证；后续接入 WebSocket 后，优先验证连接建立、事件接收和消息回发

### 测试约定

- 测试框架：`pytest`
- 测试目录：`tests/`
- 测试文件命名：`test_*.py`
- 运行全部测试：`uv run pytest`
- 运行单个测试文件：`uv run pytest tests/test_xxx.py`
- 运行单个测试用例：`uv run pytest tests/test_xxx.py -k case_name`
- 当前仓库还没有现成测试文件；新增测试时遵循以上布局

### 代码风格

- 文档注释风格：Google，描述使用中文
- 格式化：`uv run ruff format .`
- 静态检查：`uv run ruff check .`
- 类型检查：`uv run pyright`
- 只改和需求直接相关的代码，不要为了“顺手统一风格”扩散修改

### Commit 规范

- 如需提交，优先使用 Conventional Commits
- 常用类型：`feat` / `fix` / `refactor` / `test` / `docs` / `chore`
- 提交信息应直接描述这次插件改动，不要沿用其他项目的示例 scope

### 安全注意事项

- `allow_from` 为空时语义是“拒绝所有人”；如果频道被启用但该列表为空，nanobot 会直接中止启动
- 若后续接入 NapCat 回调地址、鉴权头或 token，不要硬编码到仓库里，也不要把敏感值写入日志
