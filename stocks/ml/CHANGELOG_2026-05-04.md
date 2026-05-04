# ML双模型改动记录 (2026-05-04)

## 回滚方法

如需回滚到本次提交之前的状态：

```bash
# 方法1：创建一个反向提交（安全，保留历史）
git revert 31dd993

# 方法2：硬回滚（直接回到上一个提交，会丢弃之后的所有提交）
git reset --hard 0ea2b38
git push --force
```

> **推荐方法1**，不会丢失git历史。方法2是破坏性操作，谨慎使用。

---

## 提交信息

- **提交哈希**: `31dd993`
- **上一提交**: `0ea2b38`
- **分支**: master

---

## 改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `stocks/ml/shadow_learner.py` | 修改 | 新增回归模型；`record_and_predict`返回`{prob, gain}`；回填逻辑改为5个交易日最高价；新增`max_gain_pct`；排除未来数据特征；报告分四章 |
| `stocks/ml/shadow_regressor.pkl` | 新增 | 回归模型文件 |
| `stocks/ml/shadow_model.pkl` | 修改 | 分类模型重新训练 |
| `stocks/ml/shadow_data.json` | 修改 | 重新回填标签（5交易日最高价+max_gain_pct），删除5月4日测试数据 |
| `stocks/ml/model_report.md` | 修改 | 报告分为四章：样本概况/分类模型/回归模型/结论 |
| `stocks/ml/weekly_train.py` | 未改 | 无需改动，调用sl.train()内部已自动训练回归模型 |
| `stocks/ml/test_ml.py` | 删除 | 已废弃 |
| `stocks/stock_monitor/monitor.py` | 修改 | 适配新返回格式，单推和汇总都显示达标概率+预测涨幅 |
| `stocks/stock_analyzer.py` | 修改 | 适配新返回格式 |
| `stocks/严格选股_多周期.py` | 修改 | 两处调用适配新返回格式 |
| `.github/workflows/stock-monitor.yml` | 修改 | commit步骤新增`shadow_regressor.pkl`；删除test-ml job和对应input选项 |
| `stocks/stock_monitor/signals/2026-05-04.json` | 删除 | 测试数据清理 |

---

## 核心改动说明

### 1. 回填逻辑修正
- **之前**: 只看第5天收盘价是否达标，且"5天"定义混乱（自然日/交易日不一致）
- **之后**: 看5个**交易日**内的**最高价**是否触达目标价

### 2. 新增回归模型
- **分类模型** (RandomForestClassifier): 预测达标概率 → `shadow_model.pkl`
- **回归模型** (RandomForestRegressor): 预测最大涨幅% → `shadow_regressor.pkl`
- 两个模型共用同一套样本和特征，独立训练独立预测

### 3. record_and_predict 返回格式变更
```python
# 之前：返回 float 或 None
ml_prob = record_and_predict(...)  # 38.5 或 None

# 之后：返回 dict
ml_result = record_and_predict(...)  # {'prob': 38.5, 'gain': 5.2}
```

### 4. 数据泄漏修复
排除特征列表 `_EXCLUDE_FEATURES` 新增：
- `max_high` — 未来最高价
- `max_gain_pct` — 未来最大涨幅

### 5. 达标率变化
- 之前整体达标率: **7.3%**（只看第5天收盘价）
- 之后整体达标率: **18.9%**（看5个交易日内最高价）
