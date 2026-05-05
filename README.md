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

- `闭嘴 [时长]`：让机器人闭嘴，默认时长 600 秒
  - 示例：`闭嘴 5m`(让机器人闭嘴 5 分钟)
  - 支持单位 s(秒) m(分钟) h(小时) d(天)
- `说话`：解除机器人闭嘴状态

## 配置项

在插件配置文件中可自定义以下设置：

- `shutup_commands`：闭嘴指令列表，默认 `闭嘴,stop`
- `unshutup_commands`：解除闭嘴指令列表，默认 `说话,停止闭嘴`
- `default_duration`：默认闭嘴时长(秒)，默认 `600`
- `shutup_reply`：闭嘴时的回复消息，支持占位符 `{duration}`(禁言时长，秒)和 `{expiry_time}`(禁言结束时间)，默认 `好的，我闭嘴了~`
- `unshutup_reply`：解除闭嘴时的回复消息，支持占位符 `{duration}` 和 `{expiry_time}`，默认 `好的，我恢复说话了~`
- `require_prefix`：是否启用前缀模式，默认`启用`
- `scheduled_shutup_enabled`：是否启用定时闭嘴，默认`关闭`
- `scheduled_shutup_times`：定时闭嘴时间段
- `bot_name`：机器人的自称（用于睡眠唤醒等提示语中），默认 `小爱`
- `sleep_mode_enabled`：是否启用睡眠唤醒互动（开启后定时闭嘴期间会有相应的哄睡、叫醒等提示语），默认 `启用`
- `temp_wake_duration`：睡眠模式下被临时叫醒后保持清醒的时长（秒），默认使用插件内置值，如需调整请在配置文件中显式设置
- `group_card_update_enabled`：是否启用群昵称剩余时长显示
- `group_card_template`：群昵称显示模板
- `llm_tool_enabled`：是否启用 LLM 工具调用，默认`关闭`
- `require_admin`：是否需要管理员权限，默认`关闭`

## LLM 工具调用

启用 `llm_tool_enabled` 配置后，LLM 可以主动调用闭嘴功能：

- **shutup**：让机器人在指定时间内停止回复消息
  - LLM 会根据用户意图自主决定合适的闭嘴时长
  - 支持时间单位：s(秒)、m(分钟)、h(小时)
  - 最长闭嘴时长限制为 60 分钟
  - 默认单位为分钟

### 使用示例

当启用 LLM 工具后，用户可以用自然语言表达：
- "让机器人安静一会儿" → LLM 调用 shutup
- "让它闭嘴 10 分钟" → LLM 调用 shutup(10, "m")
- "机器人别说话了" → LLM 调用 shutup

## 注意事项
- 闭嘴状态仅对当前会话有效
- 在闭嘴期间，可以随时使用解除指令让机器人恢复回复
- 定时闭嘴期间，无法通过指令解除(后续更新可能会加上)
- LLM 工具调用的闭嘴时长最长为 60 分钟

## 更新日志
### v1.5.1
- 修复的 Bug
  - 命令匹配问题：命令列表现在按长度降序排列，_find_matching_command 匹配最长的命令，防止 "闭嘴" 先于 "闭嘴说话" 匹配
  - getattr(self, "temp_wake_map", {}) — 替换为直接的 self.temp_wake_map（始终在 __init__ 中初始化），移除了不必要的防御性代码
- 结构重构
  - 新增 _find_matching_command() — 集中命令匹配，按最长前缀优先
  - 新增 _parse_duration() — 从消息文本中提取时长，便于复用
  - 新增 _handle_control_command() — 独立的权限检查与命令分发
  - 新增 _handle_sleep_interaction() — 将睡眠模式逻辑从 handle_message 中剥离
  - 新增 _is_talking_to_bot() — 提取了检查消息是否针对机器人的逻辑
  - handle_message 从 ~100 行精简为 ~40 行
- 质量改进
  - 所有方法的参数和返回值均添加了完整类型提示
  - 为所有公共和私有方法添加了完整的 Google 风格 docstring
  - 按逻辑分区组织代码（持久化 / 时间 / 命令 / 群名片 / 处理器 / LLM 工具 / 生命周期）
  - MessageEventResult 显式从 astrbot.api.event 导入
  
### v1.5.0
- 实现 LLM 工具调用功能
  - 新增 `shutup` 工具，LLM 可以根据用户意图自主决定闭嘴时长
  - 限制 LLM 工具调用的最大闭嘴时长为 60 分钟
  - 通过 `llm_tool_enabled` 配置项控制是否启用
- 新增权限控制功能
  - 添加 `require_admin` 配置项，可限制只有管理员才能使用闭嘴指令
- 调整默认优先级为 10000
- 修复多次闭嘴指令后，群昵称错误的问题

### v1.4
- 添加群昵称剩余时长显示功能
  - 支持自定义群昵称模板
  - 闭嘴结束后自动恢复原始群昵称
- 限制 default_duration 范围(0-86400秒)
  - 配置超出范围时自动修正并保存

### v1.3
- 添加logo
- 提高闭嘴优先级(可自定义)，理论上可以屏蔽大部分插件消息

### v1.2
- 修复前缀模式禁言失效的问题
- 添加定时闭嘴功能

### v1.1
- 将silence_map进行持久化存储，重启bot也不会丢失数据
- 添加前缀模式，开启后须带有指令前缀或@才会触发
- 修改日志输出

### v1.0
- 首次发布