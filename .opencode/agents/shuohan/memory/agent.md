# Agent Memory

- **2026-03-01 23:15**: 确认memory_read/write工具需通过bash调用shuohan技能脚本实现，已调整工作模式
- **2026-03-01 23:23**: 执行commit操作后必须立即写入记忆，即使使用了git commit也需补记hot记忆
- **2026-03-01 23:27**: 当检测到node_modules缺失时，应检查bun.lock文件并自动恢复依赖
- **2026-03-01 23:34**: system.md文件目前不是自进化的，但agent记忆系统可以实现功能进化
- **2026-03-01 23:38**: 实现system.md自进化机制：添加版本控制、进化规则和安全备份
- **2026-03-01 23:40**: 建立安全确认机制：在执行git push和删除文件前必须向用户请求确认
- **2026-03-02 10:17**: [经验] 修复记忆系统: 1. 在memory.ts中添加chcp 65001确保UTF-8编码 2. 在memory_ops.py中导入io模块 3. 确保所有文件操作使用utf-8和surrogateescape错误处理
- **2026-03-02 10:25**: [经验] 换电脑后工具不加载的解决方案：使用bash调用Python脚本作为备用方案，脚本位置：.opencode/skills/shuohan/memory_ops.py
- **2026-03-02 10:28**: [经验] 修复记忆系统插件：1.将TypeScript改为ESM格式的JavaScript 2.插件需导出async函数返回tool对象 3.使用default export格式
- **2026-03-02 10:31**: [经验] 跨设备继续工作的实现：1.更新system.md添加备用方案说明 2.每次新对话开始时自动读取hot和cold记忆 3.所有文件通过Git同步到新电脑
- **2026-03-02 10:38**: [经验] memory_read/write 工具在 Windows 下有 spawnSync 超时问题。对话结束后必须使用备用方案（bash + Python脚本）写入记忆，不能仅依赖原生工具。
