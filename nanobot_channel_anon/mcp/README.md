# MCP

这个目录放 nanobot-channel-anon 的 MCP 运行时代码。

## 主要用途

- 启动一个基于 `FastMCP` 的 stdio server
- 通过 NapCat HTTP 调用 QQ 管理类接口
- 给 nanobot 暴露可调用的 MCP tools

## 当前已实现的工具

当前已实现 27 个 MCP tools。

### 消息与媒体相关

- `delete_msg`：撤回消息
- `send_poke`：发送戳一戳
- `set_msg_emoji_like`：设置消息表情回应
- `get_friend_msg_history`：获取好友历史消息
- `get_group_msg_history`：获取群历史消息
- `create_flash_task`：创建闪传任务
- `send_flash_msg`：发送闪传消息

### AI 语音相关

- `get_ai_characters`：获取群 AI 语音角色列表
- `get_ai_record`：获取群 AI 语音链接
- `send_group_ai_record`：发送群 AI 语音消息

### 账号相关

- `get_friend_list`：获取好友列表
- `set_friend_add_request`：处理好友请求
- `delete_friend`：删除好友或加入黑名单
- `send_like`：发送点赞

### 群聊与成员相关

- `get_group_list`：获取群列表
- `get_group_info`：获取群信息
- `get_group_detail_info`：获取群详细信息
- `get_group_member_info`：获取群成员信息
- `get_group_member_list`：获取群成员列表
- `set_group_add_request`：处理加群请求或群邀请
- `set_group_ban`：设置群禁言
- `set_group_whole_ban`：设置全员禁言
- `set_group_kick`：踢出群成员
- `set_group_card`：设置群名片
- `set_group_leave`：退群或解散群

### 群相册相关

- `get_qun_album_list`：获取群相册列表
- `upload_image_to_qun_album`：上传图片到群相册

## 大致结构

- `server.py`：MCP 服务入口
- `settings.py`：环境变量配置读取
- `napcat_client.py`：NapCat HTTP 调用封装
- `models.py`：共享请求/响应模型
- `tools/`：按接口拆分的 MCP tool 实现

运行时配置通过环境变量传入，不在代码里硬编码 NapCat 地址或 token。

## 配置示例

```json
{
  "tools": {
    "mcpServers": {
      "napcat-qq-actions": {
        "type": "stdio",
        "command": "nanobot-anon-mcp",
        "env": {
          "NAPCAT_HTTP_URL": "http://127.0.0.1:3000",
          "NAPCAT_HTTP_ACCESS_TOKEN": "your-token"
        },
        "enabledTools": [
          "delete_msg",
          "send_poke",
          "send_like",
          "set_group_add_request",
          "set_friend_add_request",
          "get_group_member_list"
        ]
      }
    }
  }
}
```
