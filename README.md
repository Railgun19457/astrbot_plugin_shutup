# AstrBot ShutUp 插件
![:name](https://count.getloli.com/@astrbot_plugin_shutup?name=astrbot_plugin_shutup&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)


## 功能介绍

- 可以让机器人在指定时间内停止回复消息
- 支持自定义闭嘴时长，如 60s、1m、1h、1d 等
- 支持自定义指令，可在配置面板修改
- 定时闭嘴
- 修改群昵称显示闭嘴状态
- 支持睡眠模式互动，在定时闭嘴期间支持沉浸式哄睡与叫醒体验

## 使用方法

### 基本指令

插件使用 AstrBot 框架指令系统，触发方式遵循 AstrBot 平台设置（例如命令前缀、唤醒词、@机器人或私聊）。

- `闭嘴 [时长]`：让机器人闭嘴，默认时长 600 秒
  - 示例：`闭嘴 5m`(让机器人闭嘴 5 分钟)
  - 支持单位 s(秒) m(分钟) h(小时) d(天)
- `永久闭嘴`：让机器人一直闭嘴，直到使用 `说话` 解除
- `说话`：解除机器人闭嘴状态
- `醒醒`：在定时闭嘴且睡眠互动启用期间，临时唤醒机器人

## 配置项

在插件配置文件中可自定义以下设置：

- `llm_tool_enabled`：是否启用 LLM 工具调用，默认`关闭`
- `require_admin`：是否需要管理员权限，默认`关闭`
- `priority`：插件优先级，默认 `10000`
- `command_settings`：闭嘴/说话指令设置
  - `shutup_commands`：闭嘴指令列表，首项作为框架主指令，其余项作为别名，默认 `闭嘴`
  - `unshutup_commands`：解除闭嘴指令列表，首项作为框架主指令，其余项作为别名，默认 `说话`
  - `permanent_shutup_commands`：永久闭嘴指令列表，首项作为框架主指令，其余项作为别名，默认 `永久闭嘴`
  - `default_duration`：默认闭嘴时长(秒)，默认 `600`
  - `shutup_reply`：闭嘴时的回复消息，支持占位符 `{duration}`(禁言时长，秒)和 `{expiry_time}`(禁言结束时间)，默认 `好的，我闭嘴了~`
  - `unshutup_reply`：解除闭嘴时的回复消息，支持占位符 `{duration}` 和 `{expiry_time}`，默认 `好的，我恢复说话了~`
- `scheduled_settings`：定时闭嘴设置
  - `scheduled_shutup_enabled`：是否启用定时闭嘴，默认`关闭`
  - `scheduled_shutup_times`：定时闭嘴时间段列表，每项格式 `HH:MM-HH:MM`
  - `sleep_mode_enabled`：是否启用睡眠唤醒互动，默认 `启用`
  - `temp_wake_commands`：临时唤醒指令列表，首项作为框架主指令，其余项作为别名，默认 `醒醒`
  - `temp_wake_duration`：睡眠模式下被临时叫醒后的空闲保持时长（秒），每条新消息都会刷新倒计时，默认 `300`
  - `sleep_prompt_reply`：定时闭嘴中被呼叫时的提示文本
  - `temp_wake_reply`：临时唤醒成功回复，可使用 `{wake_minutes}` 等占位符
  - `temp_wake_llm_reply_enabled`：是否使用 LLM 生成临时唤醒回复
  - `temp_wake_llm_prompt`：临时唤醒 LLM 提示词，支持 `{wake_command}`、`{wake_minutes}`、`{temp_wake_duration}`、`{sender_name}`
- `group_card_settings`：群昵称显示设置
  - `group_card_update_enabled`：是否启用群昵称剩余时长显示
  - `group_card_template`：群昵称显示模板

## LLM 工具调用

启用 `llm_tool_enabled` 配置后，LLM 可以主动调用闭嘴功能：

- **shutup**：让机器人在指定时间内停止回复消息
  - LLM 会根据用户意图自主决定合适的闭嘴时长
  - 支持时间单位：s(秒)、m(分钟)、h(小时)
  - 最长闭嘴时长限制为 60 分钟
  - 默认单位为分钟

### 使用示例

当启用 LLM 工具后，用户可以用自然语言表达：
- "机器人安静一会儿" → LLM 调用 shutup
- "闭嘴 10 分钟" → LLM 调用 shutup(10, "m")
- "机器人别说话了" → LLM 调用 shutup

## 注意事项
- 闭嘴状态仅对当前会话有效
- 在闭嘴期间，可以随时使用解除指令让机器人恢复回复
- 定时闭嘴期间，可使用临时唤醒指令短暂恢复回复
- LLM 工具调用的闭嘴时长最长为 60 分钟
