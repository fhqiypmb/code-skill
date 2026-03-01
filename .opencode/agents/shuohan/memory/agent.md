# Agent Memory

- **2026-03-01 23:15**: 确认memory_read/write工具需通过bash调用shuohan技能脚本实现，已调整工作模式
- **2026-03-01 23:23**: 执行commit操作后必须立即写入记忆，即使使用了git commit也需补记hot记忆
- **2026-03-01 23:27**: 当检测到node_modules缺失时，应检查bun.lock文件并自动恢复依赖
