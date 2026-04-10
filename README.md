# astrbot_plugin_bilibili_subscribe

一个基于 AstrBot 常规插件结构实现的 **Bilibili 直播间订阅插件**。

当前版本以 **Agent / LLM 工具调用** 为主：

- 群聊里需要先 `@机器人`
- 再由 AstrBot Agent 判断是否调用本插件完成订阅
- 如果 Agent 首轮没有调用工具，插件也会对明确的订阅意图做兜底，确保二阶段会话能接住
- 如果用户还没提供直播间，或没说明提醒方式，插件会记录待确认状态继续追问
- 进入追问阶段后，用户直接跟着回复即可，不必再次 `@机器人`

## 功能特性

- 供 Agent 调用的 Bilibili 直播订阅工具
- 支持识别 Bilibili 直播间 URL 或房间号
- 群聊中必须 `@机器人` 才会处理订阅请求
- 群订阅仅限群管理员发起
- 私聊订阅不限制身份，但需要先添加机器人好友，并在私聊中发起
- 支持两种订阅模式：
  - 私聊订阅：提醒发送到用户私聊
  - 群订阅：提醒发送到当前群聊
- 支持给订阅设置备注，通知时优先显示备注名
- 唯一性规则：
  - 私聊订阅按 `用户 + 房间号 + 订阅方式` 去重
  - 群订阅按 `群号 + 房间号 + 订阅方式` 去重
- 使用 JSON 文件持久化订阅数据
- 定时轮询直播间状态（默认 `30` 秒）
- 开播/下播自动提醒，并附带直播房封面
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

- `@机器人 请帮我订阅直播间：https://live.bilibili.com/123456`
- `@机器人 帮我订阅这个 B 站直播间 123456`
- `@机器人 订阅直播间 https://live.bilibili.com/123456 私聊`
- `@机器人 订阅直播间 https://live.bilibili.com/123456 群订阅`
- `@机器人 订阅直播间 32800932 群订阅 备注夏老板`
- `我想订阅直播间 32800932，备注“夏老板”`（私聊内可直接发送）

如果用户没有明确写订阅模式，插件会继续追问：

- `要订阅到哪里？请回复“私聊”或“群订阅”。`

如果用户一开始没有给直播间，插件会提示：

- `目前只支持订阅 Bilibili 直播间。请把直播间链接或房间号发给我。`

如果用户在群里要求创建私聊订阅，插件会提示：

- `私聊订阅请先添加机器人好友，再在私聊里发起。`

如果用户在群里不是管理员就要求创建群订阅，插件会提示：

- `只有群管理员才能创建群订阅。`

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
- `remark`（可选，订阅备注）
- `session_id`
- `notify_origin`
- `created_at`
- `last_live_status`
- `last_notified_status`
- `last_title`
- `last_uname`
- `last_cover_url`

## 配置项

AstrBot 支持通过 `_conf_schema.json` 自动注入插件配置。当前支持：

- `check_interval_seconds`：检查直播状态间隔，默认 `30`
- `max_subscriptions_per_user`：每个用户最大订阅数量，默认 `20`
- `live_on_template`：开播提醒模板
- `live_off_template`：下播提醒模板
- `bilibili_api_timeout_seconds`：请求 Bilibili API 超时秒数

提醒模板支持这些变量：

- `{display_name}`：优先使用备注，否则使用主播名
- `{remark}`：备注；未设置时为空字符串
- `{uname}`：主播名
- `{title}`：直播标题
- `{room_url}` / `{room_id}`：直播间链接 / 房间号
- `{area_name}`：分区名
- `{cover_url}`：直播房封面 URL

配置示例：

```json
{
  "check_interval_seconds": 30,
  "max_subscriptions_per_user": 20,
  "live_on_template": "【开播提醒】{display_name} 开播了\n标题：{title}\n直播间：{room_url}",
  "live_off_template": "【下播提醒】{display_name} 下播了\n直播间：{room_url}",
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

1. 群聊里用户先 `@机器人`，私聊则可直接发起
2. AstrBot Agent 根据用户话语判断是否调用本插件的 `subscribe_bilibili_live_room` 工具
3. 插件解析直播间链接或房间号
4. 如果未提供直播间，插件会提示仅支持 Bilibili 直播间，并记录待补充房间号状态
5. 如果未指定 `私聊` 或 `群订阅`，插件会记录待确认状态并询问用户
6. 如果带了 `备注/别名/昵称`，插件会一并记录，并在提醒时优先显示
7. 进入追问阶段后，用户继续直接回复即可，不必再次 `@机器人`
8. 创建群订阅前会校验当前发言者是否为群管理员
9. 创建私聊订阅前会要求用户在私聊中发起，以确保机器人已加好友
10. 订阅成功后，后台定时任务按 `check_interval_seconds` 轮询直播间状态
11. 若直播状态从非开播变为开播，则发送开播提醒
12. 若直播状态从开播变为非开播，则发送下播提醒

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
   - 当前使用 `await self.context.send_message(unified_msg_origin, MessageChain([...]))`
   - 开播/下播提醒会拼接文本 + 封面图片

3. 插件退出钩子：
   - 当前实现了 `terminate()` 以便取消后台轮询任务
   - 如果当前版本使用其它生命周期钩子，请按版本调整

## Bilibili API 说明

本插件使用公开可访问的直播间信息接口：

- `https://api.live.bilibili.com/room/v1/Room/get_info?room_id=xxxx`

该接口可返回直播间标题、主播名、直播状态等信息。

## 返回消息示例

- `已为你创建私聊订阅：主播名 / https://live.bilibili.com/123456`
- `已为你创建群订阅：夏老板（主播：主播名） / https://live.bilibili.com/123456`
- `订阅失败：无法获取直播间信息`
- `订阅失败：你已达到最大订阅数量限制（20）`
- `要订阅到哪里？请回复“私聊”或“群订阅”。`
- `只有群管理员才能创建群订阅。`
- `私聊订阅请先添加机器人好友，再在私聊里发起。`

## 参考文档

- AstrBot 插件配置文档：https://docs.astrbot.app/dev/star/guides/plugin-config.html
- AstrBot 消息事件文档：https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- AstrBot 消息发送文档：https://docs.astrbot.app/dev/star/guides/send-message.html
- AstrBot 插件存储文档：https://docs.astrbot.app/dev/star/guides/storage.html
- Bilibili 直播接口整理（非官方汇总，便于字段参考）：https://socialsisteryi.github.io/bilibili-API-collect/docs/live/info.html
