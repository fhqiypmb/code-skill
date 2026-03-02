---
name: shuohan
description: "硕含Agent记忆系统 - 三层记忆管理（V8.0 强制防呆版）"
has_scripts: true
---

# 硕含 Agent 记忆系统 V8.0

⚠️ **重要更新 V8.0**: 新增 `memory_ops.py auto` 强制防呆机制，确保记忆写入100%执行。

## 核心原则

**记忆写入是基本要求，不可妥协，不可遗忘。**

## 记忆类型

| 类型 | 用途 | 强制写入时机 |
|------|------|-------------|
| **hot** | 会话级记忆，当前对话 | **每次交互必须写** |
| **cold** | 用户级记忆，偏好习惯 | 用户透露重要信息时 |
| **agent** | Agent级记忆，经验沉淀 | 学到新经验时 |

---

## 🔥 强制记忆写入流程（V8.0）

### 步骤1: 回复前 - 预写入计划

```bash
python .opencode/skills/shuohan/memory_ops.py auto pre "计划做什么的简要描述"
```

**必须在生成回复前执行！这会创建一个状态标记。**

### 步骤2: 生成并发送回复

正常思考、查询、组织语言、回复用户。

### 步骤3: 回复后 - 确认完成写入

```bash
python .opencode/skills/shuohan/memory_ops.py auto post "实际完成的简要描述"
```

**必须在回复后第一时间执行！这会实际写入记忆并清除状态标记。**

### 验证机制

```bash
# 检查是否有未完成的记忆
python .opencode/skills/shuohan/memory_ops.py auto check
```

---

## 完整示例

用户提问后，**立即执行**：

```bash
# 步骤1: 预写入
python .opencode/skills/shuohan/memory_ops.py auto pre "用户询问明天廊坊天气"
```

生成回复...（查询天气、组织语言、发送回复）

```bash
# 步骤3: 完成写入
python .opencode/skills/shuohan/memory_ops.py auto post "已回复廊坊明天天气：多云、12℃"
```

---

## 防呆机制说明

`memory_ops.py auto` 实现以下保护：

1. **状态追踪**: `pre` 会创建状态文件，表示有待写入的记忆
2. **强制确认**: `post` 必须调用，否则状态文件会一直存在
3. **自动检查**: 可以通过 `check` 命令发现遗漏
4. **内容合并**: 自动将"计划"和"实际"合并写入，确保完整性

**如果忘记执行 post**，状态文件会一直存在，下次执行 `pre` 或 `check` 时会立即发现。

---

## 旧版命令（兼容保留）

### 读取记忆

```bash
python .opencode/skills/shuohan/memory_ops.py read hot
python .opencode/skills/shuohan/memory_ops.py read cold
python .opencode/skills/shuohan/memory_ops.py read agent
```

### 直接写入记忆（不推荐，请使用 memory_ops.py auto）

```bash
python .opencode/skills/shuohan/memory_ops.py write hot "内容"
python .opencode/skills/shuohan/memory_ops.py write cold "内容"
python .opencode/skills/shuohan/memory_ops.py write agent "内容"
```

### 搜索记忆

```bash
python .opencode/skills/shuohan/memory_ops.py search "关键词"
```

---

## 执行检查清单

每次交互后确认：

- [ ] 是否执行了 `pre` 预写入？
- [ ] 是否执行了 `post` 完成写入？
- [ ] 是否根据情况写入 `cold`？
- [ ] 是否根据情况写入 `agent`？

**任何一项未完成 = 流程失败**
