## 更新日志

### v1.7.1
- 将定时闭嘴相关配置、描述与日志统一调整为睡眠语义
- 新增 `sleep_settings` 与 `temporary_wake_*` 配置键

### v1.7.0
- 新增 `shutup_suppress_reply` 主流程日志，明确记录已设置 suppress 标记
- 将发送前兜底日志调整为 fallback 语义，便于区分主流程与兜底清理
- 调整 `shutup_suppress_reply` 行为，避免通过清空最终响应对象导致模型空输出重试

### v1.6.4
- 移除旧版顶层配置、字符串配置与旧持久化格式兼容逻辑
- 闭嘴命中时同步停止当前会话中的进行中响应
- 优化闭嘴期间模型结果与流式回复拦截逻辑

### v1.6.3
- 将群昵称恢复所需的 bot 原始群名片与 QQ 昵称持久化到 `silence_map.json`
- 修复闭嘴状态跨重启或运行态缓存丢失后，群昵称可能无法恢复为原名的问题

### v1.6.2
- 修复定时闭嘴睡眠提示在新版本管线中可能返回空响应的问题
- 修复 WebChat 私聊场景下普通文本未被识别为对 bot 说话，导致 `sleep_prompt_reply` 不触发的问题

### v1.6.1
- 重排配置模板，将基础开关、闭嘴指令、定时闭嘴、群昵称显示拆分为配置组
- 将定时闭嘴时间段改为列表配置，支持逐项编辑
- 修复新版本管线下指令提醒文本可能无法正常发送的问题
- 支持自定义定时闭嘴/临时唤醒提醒文本
- 新增可选的 LLM 临时唤醒回复生成，并支持自定义提示词
- 临时唤醒期间收到新消息会刷新倒计时，持续空闲达到 `temp_wake_duration` 后才重新闭嘴
- 非定时闭嘴期间的临时唤醒指令、临时唤醒状态下重复唤醒、正常状态下说话指令将被忽略并继续后续流程

### v1.6.0
- 将闭嘴/解除闭嘴改为 AstrBot 框架指令注册，配置列表首项作为主指令，其余项作为别名
- 移除 `require_prefix` 配置项和插件内手动前缀检查逻辑
- 新增永久闭嘴指令，直到使用解除闭嘴指令才恢复
- 将 `bot_name` 配置项改为 `temp_wake_commands` 临时唤醒指令配置项
- 默认指令调整为 `说话`、`闭嘴`、`永久闭嘴`、`醒醒`

### v1.5.3
- 修改函数工具注册形式

### v1.5.2
- **程序文件架构重构**：从单文件拆分为 5 模块多文件结构
  - `core/state.py` — SilenceStore 持久化状态管理
  - `core/config.py` — 纯函数配置处理（可独立测试）
  - `core/handlers.py` — MessageHandlers 消息/命令分发逻辑
  - `core/group_card.py` — GroupCardUpdater 群名片更新（aiocqhttp）
  - `tools/shutup_tool.py` — ShutupTool FunctionTool 数据类（AstrBot v4.5.1+ 推荐模式）
  - `main.py` 精简为 ~190 行薄编排层
- **LLM 工具迁移**：从 `@filter.llm_tool` 装饰器改为 `FunctionTool` 数据类，通过 `context.add_llm_tools()` 注册
- **数据目录改进**：使用 `StarTools.get_data_dir()` 替代手动路径拼接

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