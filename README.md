# astrbot_plugin_bilibili_subscribe

一个基于 AstrBot 常规插件结构实现的 **Bilibili 直播间订阅插件**。

插件会根据用户自然语言识别订阅意图，提取 Bilibili 直播间链接或房间号，询问订阅模式（私聊 / 群订阅），并定时检测直播间状态，在开播或下播时主动推送提醒。

## 功能特性

- 自然语言识别订阅意图
- 支持识别 Bilibili 直播间 URL 或房间号
- 支持两种订阅模式：
  - 私聊订阅：提醒发送到用户私聊
  - 群订阅：提醒发送到当前群聊
- 使用 JSON 文件持久化订阅数据
- 定时轮询直播间状态
- 开播/下播自动提醒
- 支持可配置的检查间隔、提醒模板、最大订阅数

## 目录结构

```text
astrbot_plugin_bilibili_subscribe/
├─ main.py
├─ metadata.yaml
├─ README.md
├─ config.py
├─ _conf_schema.json
├─ requirements.txt
├─ data/
│  └─ subscriptions.json
├─ services/
│  ├─ bilibili_client.py
│  ├─ subscription_manager.py
│  └─ intent_parser.py
└─ utils/
   └─ json_storage.py
```

## 支持的自然语言示例

- `请帮我订阅直播间：https://live.bilibili.com/123456`
- `帮我订阅这个 B 站直播间 123456`
- `订阅直播间 https://live.bilibili.com/123456 私聊`
- `订阅直播间 https://live.bilibili.com/123456 群订阅`

如果用户没有明确写订阅模式，插件会继续追问：

- `要订阅到哪里？请回复“私聊”或“群订阅”。`

## 数据存储结构

`data/subscriptions.json` 默认结构如下：

```json
{
  "subscriptions": [],
  "pending_confirmations": []
}
```

其中每条订阅记录包含：

- `room_id`
- `room_url`
- `user_id`
- `group_id`（群订阅时存在）
- `mode`
- `session_id`
- `notify_origin`
- `created_at`
- `last_live_status`
- `last_notified_status`
- `last_title`
- `last_uname`

## 配置项

AstrBot 支持通过 `_conf_schema.json` 自动注入插件配置。当前支持：

- `check_interval_seconds`：检查直播状态间隔，默认 `60`
- `max_subscriptions_per_user`：每个用户最大订阅数量，默认 `20`
- `live_on_template`：开播提醒模板
- `live_off_template`：下播提醒模板
- `bilibili_api_timeout_seconds`：请求 Bilibili API 超时秒数

配置示例：

```json
{
  "check_interval_seconds": 60,
  "max_subscriptions_per_user": 20,
  "live_on_template": "【开播提醒】{title}\n主播：{uname}\n直播间：{room_url}",
  "live_off_template": "【下播提醒】{uname} 的直播已结束\n直播间：{room_url}",
  "bilibili_api_timeout_seconds": 10.0
}
```

## 安装方式

根据 AstrBot 官方开发文档，插件通常放在：

```text
AstrBot/data/plugins/astrbot_plugin_bilibili_subscribe
```

当前项目位于：

```text
/Users/hacchiroku/AIWorkspace/astrbot_plugin_bilibili_subscribe
```

你可以复制或软链接到 AstrBot 的插件目录。

## 依赖安装

```bash
cd astrbot_plugin_bilibili_subscribe
pip install -r requirements.txt
```

## 运行逻辑说明

1. 插件监听所有消息
2. 当识别到 “订阅 / 直播间 / bilibili / b站”等关键词，并解析出直播间链接或房间号时，进入订阅流程
3. 如果未指定 `私聊` 或 `群订阅`，插件会记录待确认状态并询问用户
4. 订阅成功后，后台定时任务按 `check_interval_seconds` 轮询直播间状态
5. 若直播状态从非开播变为开播，则发送开播提醒
6. 若直播状态从开播变为非开播，则发送下播提醒

## 主动消息实现

根据 AstrBot 文档，主动消息推送依赖：

- `event.unified_msg_origin`
- `self.context.send_message(unified_msg_origin, MessageChain)`

本插件：

- 群订阅使用当前群聊 `unified_msg_origin`
- 私聊订阅会尝试构造 `platform:private_message:user_id`

> 注意：不同 AstrBot 版本或不同平台适配器，对主动私聊推送的支持可能存在差异。

## AstrBot 版本适配点

如果你的 AstrBot 版本不同，可能需要调整以下位置：

1. 导入路径：
   - 当前使用 `from astrbot.api.event import AstrMessageEvent, MessageChain, filter`
   - 当前使用 `from astrbot.api.star import Context, Star, register`

2. 主动发送消息：
   - 当前使用 `await self.context.send_message(unified_msg_origin, MessageChain().message(...))`

3. 插件退出钩子：
   - 当前实现了 `terminate()` 以便取消后台轮询任务
   - 如果当前版本使用其它生命周期钩子，请按版本调整

## Bilibili API 说明

本插件使用公开可访问的直播间信息接口：

- `https://api.live.bilibili.com/room/v1/Room/get_info?room_id=xxxx`

该接口可返回直播间标题、主播名、直播状态等信息。

## 返回消息示例

- `已为你创建私聊订阅：主播名 / https://live.bilibili.com/123456`
- `已为你创建群订阅：主播名 / https://live.bilibili.com/123456`
- `订阅失败：无法获取直播间信息`
- `订阅失败：你已达到最大订阅数量限制（20）`
- `要订阅到哪里？请回复“私聊”或“群订阅”。`

## 参考文档

- AstrBot 插件配置文档：https://docs.astrbot.app/dev/star/guides/plugin-config.html
- AstrBot 消息事件文档：https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- AstrBot 消息发送文档：https://docs.astrbot.app/dev/star/guides/send-message.html
- AstrBot 插件存储文档：https://docs.astrbot.app/dev/star/guides/storage.html
- Bilibili 直播接口整理（非官方汇总，便于字段参考）：https://socialsisteryi.github.io/bilibili-API-collect/docs/live/info.html
