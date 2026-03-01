---
name: shuohan
description: "硕含Agent记忆系统 - 三层记忆管理"
has_scripts: true
---

# 硕含 Agent 记忆系统

三层记忆系统，用于记录会话上下文、用户偏好和Agent经验。

## 记忆类型

| 类型 | 用途 | 自动写入时机 |
|------|------|-------------|
| hot | 会话级记忆，当前对话 | 每次对话后 |
| cold | 用户级记忆，偏好习惯 | 用户透露重要信息时 |
| agent | Agent级记忆，经验沉淀 | 学到新经验时 |

## 使用方法

**脚本路径:** `.opencode/skills/shuohan/memory_ops.py`

### 读取记忆
```bash
python .opencode/skills/shuohan/memory_ops.py read hot
python .opencode/skills/shuohan/memory_ops.py read cold
python .opencode/skills/shuohan/memory_ops.py read agent
```

### 写入记忆
```bash
# 写入hot记忆
python .opencode/skills/shuohan/memory_ops.py write hot "用户询问了JWT认证"

# 写入cold记忆
python .opencode/skills/shuohan/memory_ops.py write cold "用户偏好使用TypeScript"

# 写入agent记忆
python .opencode/skills/shuohan/memory_ops.py write agent "JWT认证推荐使用middleware模式"
```

### 搜索记忆
```bash
python .opencode/skills/shuohan/memory_ops.py search "JWT"
```

## 每次对话后的标准流程

1. **写入Hot记忆** - 记录本次对话摘要
2. **判断是否写入Cold** - 用户是否透露了偏好/习惯/项目信息
3. **判断是否写入Agent** - 是否学到了可复用的经验

## 示例

对话结束后执行：
```bash
python .opencode/skills/shuohan/memory_ops.py write hot "[用户] 询问如何配置JWT"
python .opencode/skills/shuohan/memory_ops.py write hot "[硕含] 建议使用middleware方式"
```
