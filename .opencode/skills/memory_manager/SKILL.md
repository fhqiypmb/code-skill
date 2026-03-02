---
name: memory_manager
description: "交互式记忆管理器 - 在交互完成后写入记忆"
has_scripts: true
---

# 记忆管理器

在每轮交互完成后，自动将对话内容写入记忆系统。

## 工作流程

1. 用户提问
2. Agent回答
3. **交互完成后**调用此skill写入记忆

## 使用方法

```bash
python .opencode/skills/memory_manager/save_interaction.py "用户问题" "Agent回答"
```

## 记忆写入规则

- **Hot记忆**：每次交互都写入
- **Cold记忆**：检测到用户偏好时写入
- **Agent记忆**：检测到技术经验时写入
