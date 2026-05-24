# AstrBot ShutUp 插件
![:name](https://count.getloli.com/@astrbot_plugin_shutup?name=astrbot_plugin_shutup&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)


## 功能介绍

- 让 bot 在当前会话中停止回复一段时间
- 支持普通闭嘴、永久闭嘴、定时睡眠三种模式
- 支持自定义指令与别名
- 支持睡眠互动：睡眠时段内可提示用户并临时叫醒 bot
- 支持 LLM 主动调用闭嘴或不回复工具
- 支持通过群昵称展示闭嘴 / 睡眠剩余时间
- 支持持久化禁言状态，重启后不会丢失
- 支持持久化 bot 原始群名片 / QQ 昵称，闭嘴结束后更可靠地恢复原名

## 使用方法

### 基本指令

插件使用 AstrBot 框架指令系统，触发方式遵循 AstrBot 平台设置（例如命令前缀、唤醒词、@机器人或私聊）。

- `闭嘴 [时长]`：让机器人闭嘴一段时间
  - 未填写时长时使用 `default_duration`
  - 支持单位：`s` 秒、`m` 分钟、`h` 小时、`d` 天
  - 示例：
    - `闭嘴`
    - `闭嘴 60s`
    - `闭嘴 5m`
    - `闭嘴 1h`
- `永久闭嘴`：让机器人一直闭嘴，直到使用 `说话` 解除
- `说话`：解除当前会话的闭嘴状态
- `醒醒`：仅在定时睡眠且启用了睡眠互动时可用，用于临时唤醒机器人

### 行为说明

- 普通闭嘴和永久闭嘴都只作用于**当前会话**
- `说话` 在当前没有闭嘴状态时，会忽略并继续后续流程
- `醒醒` 在非睡眠时段不会强行拦截，而是忽略并继续后续流程
- 如果 bot 正处于临时唤醒状态，再次发送普通消息会刷新清醒倒计时

## 配置项

### 基础配置

- `llm_tool_options`：选择向 LLM 暴露的函数工具
- `require_admin`：是否仅允许管理员使用闭嘴相关指令
- `priority`：插件优先级，默认 `10000`

### `command_settings`

闭嘴 / 解除闭嘴指令配置。

- `shutup_commands`：闭嘴指令列表
  - 首项会注册为框架主指令
  - 其余项会注册为别名
- `unshutup_commands`：解除闭嘴指令列表
- `permanent_shutup_commands`：永久闭嘴指令列表
- `default_duration`：默认闭嘴时长，单位秒，范围 `0-86400`
- `shutup_tool_max_duration`：LLM `shutup` 工具最大闭嘴时长，单位秒，范围 `1-86400`，默认 `3600`
- `shutup_reply`：闭嘴成功回复
  - 支持占位符：`{duration}`、`{expiry_time}`
- `unshutup_reply`：解除闭嘴回复
  - 支持占位符：`{duration}`、`{expiry_time}`

### `sleep_settings`

定时睡眠与临时唤醒相关配置。

- `sleep_enabled`：是否启用定时睡眠
- `sleep_time_ranges`：睡眠时间段列表
  - 每项格式：`HH:MM-HH:MM`
  - 支持跨天，例如：`23:00-07:00`
  - 支持配置多个时间段
- `sleep_interaction_enabled`：睡眠时段内是否启用睡眠互动
- `temporary_wake_commands`：临时唤醒指令列表
- `temporary_wake_duration`：临时唤醒持续时长，单位秒
  - 在临时唤醒期间，每收到一条新消息都会刷新倒计时
- `sleep_prompt_reply`：睡眠时段被呼叫时的提示文本
  - 支持占位符：`{wake_word}`、`{wake_command}`、`{wake_minutes}`、`{temporary_wake_duration}`
- `temporary_wake_reply`：临时唤醒成功后的回复文本
  - 支持占位符：`{wake_minutes}`、`{temporary_wake_duration}`、`{wake_command}`
- `temporary_wake_llm_reply_enabled`：是否使用 LLM 生成临时唤醒回复
- `temporary_wake_llm_prompt`：临时唤醒 LLM 提示词
  - 支持占位符：`{wake_command}`、`{wake_minutes}`、`{temporary_wake_duration}`、`{sender_name}`

### `group_card_settings`

群昵称显示相关配置。

- `group_card_update_enabled`：是否启用群昵称剩余时长显示
- `group_card_template`：闭嘴状态群昵称模板
- `sleep_group_card_template`：睡眠状态群昵称模板
- `temporary_wake_group_card_template`：临时唤醒状态群昵称模板
  - 支持占位符：
    - `{remaining}`：剩余分钟数；闭嘴状态下也可能为 `永久`
    - `{original_card}`：原始群名片
    - `{original_nickname}`：原始 QQ 昵称
    - `{original_name}`：优先使用原始群名片，否则使用原始 QQ 昵称
    - `{status}`：当前状态标识，可为 `muted`、`sleep`、`temporary_wake`

模板选择规则：

- 普通闭嘴 / 永久闭嘴：使用 `group_card_template`
- 定时睡眠中：使用 `sleep_group_card_template`
- 睡眠时段内临时唤醒：使用 `temporary_wake_group_card_template`

## 睡眠模式说明

当同时满足以下条件时，插件会进入“定时睡眠”：

- `sleep_enabled = true`
- 当前时间命中 `sleep_time_ranges`
- `sleep_interaction_enabled = true`

此时行为如下：

- 如果用户没有明确在对 bot 说话，bot 会直接保持沉默
- 如果用户是在私聊中发消息，或消息已经被框架识别为唤醒消息，插件会返回 `sleep_prompt_reply`
- 用户发送 `醒醒` 后，bot 会在 `temporary_wake_duration` 指定的时间内暂时恢复说话
- 临时唤醒期间若持续有新消息，清醒时间会不断续期

## LLM 工具调用

在 `llm_tool_options` 中启用对应工具后，LLM 可以主动调用闭嘴或不回复功能：

- **shutup**：让机器人在当前会话中停止回复消息
  - 参数：
    - `duration`：时长数值
    - `unit`：时间单位，支持 `s` / `m` / `h` / `d`
  - 实际最大生效时长由 `shutup_tool_max_duration` 控制，默认 `3600` 秒（60 分钟）
  - 超过上限会自动截断到配置的最大值
- **not_reply**：本条消息静默处理
  - 适用场景：
    - 用户明确要求“这条不用回”
    - 模型判断此时最合适的行为就是不回复
  - 触发效果：
    - 平台侧不会向用户发送任何消息

### 使用示例

当启用 LLM 工具后，用户可以用自然语言表达：

- “机器人安静一会儿” → LLM 调用 `shutup`
- “闭嘴 10 分钟” → LLM 调用 `shutup(10, "m")`
- “你先别说话了” → LLM 调用 `shutup`
- “这条不用回” → LLM 调用 `not_reply`

## 持久化说明

插件会在数据目录下维护 `silence_map.json`，用于保存：

- 当前会话的闭嘴状态
- 永久闭嘴状态
- 群昵称恢复所需的 bot 原始群名片
- 群昵称恢复所需的 bot 原始 QQ 昵称

这意味着：

- bot 重启后，未过期的闭嘴状态不会直接丢失
- 群昵称恢复不再只依赖进程内缓存，恢复原名更稳定

## 注意事项

- 闭嘴状态以当前会话为单位，不是全局静音
- 群昵称显示功能目前仅对 **aiocqhttp / OneBot QQ** 链路有效
- 若未配置有效的 `sleep_time_ranges`，定时睡眠不会生效
- 群昵称模板占位符写错时，插件会按当前状态回退到默认展示格式
- LLM 工具调用的闭嘴时长虽然支持多种单位，但最终最大只会生效 `shutup_tool_max_duration` 配置的时长
