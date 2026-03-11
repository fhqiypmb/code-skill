<div align="center">

# 🎯 机器学习影子学习器
## 超简单使用说明

`Python 3.8+` · `机器学习就绪`

</div>

---

## 📁 文件说明

### 🔧 原有文件（不用动）

| 文件 | 用途 |
|------|------|
| `严格选股_多周期.py` | 主选股程序 |
| `stock_analyzer.py` | 股票分析器 |
| `data_source.py` | 数据源模块 |
| `stock_list.md` | 股票列表 |

### ✨ 新增文件（已创建）

| 文件 | 用途 |
|------|------|
| `shadow_learner.py` | 影子学习器核心 |
| `enhanced_screener.py` | 增强版选股器 |
| `weekly_train.py` | 每周训练脚本 |

### 🗄️ 自动生成的文件

| 文件 | 说明 |
|------|------|
| `shadow_data.pkl` | 📊 数据文件（存选股记录） |
| `shadow_model.pkl` | 🤖 模型文件（训练后生成） |

---

## 📅 每日使用

```bash
python enhanced_screener.py
```

**操作和原来完全一样：**

1️⃣ 选模式（`1` - 单独测试 / `2` - 批量筛选）  
2️⃣ 选周期（`1-8`）  
3️⃣ 正常选股

> 💡 **提示**：程序会自动记录信号到 `shadow_data.pkl`

---

## 📊 查看数据量

```bash
python -c "import pickle; data=pickle.load(open('shadow_data.pkl','rb')); print(f'已收集 {len(data)} 条')"
```

---

## 🎓 每周训练

```bash
python weekly_train.py
```

**等收集够 50 条后运行，会：**

| 步骤 | 说明 |
|------|------|
| 1️⃣ | 更新实际走势 |
| 2️⃣ | 训练模型 |
| 3️⃣ | 生成 `shadow_model.pkl` |

---

## ✅ 就这么简单！

| 时间 | 操作 |
|------|------|
| 每天 | `python enhanced_screener.py` |
| 每周五 | `python weekly_train.py` |
| 随时 | 查看数据量命令 |

---

<div align="center">

**Made with ❤️ for Smart Trading**

</div>
