# ML双模型改造回滚记录 (2026-05-18)

## 回滚方法

如需回滚到本次提交之前的状态：

```bash
# 方法1：创建一个反向提交（安全，保留历史）
git revert 772628a

# 方法2：硬回滚（直接回到上一个提交，会丢弃之后的所有提交）
git reset --hard 44f90a0
git push --force
```

> **推荐方法1**，不会丢失git历史。方法2是破坏性操作，会改写远端历史，谨慎使用。

---

## 提交信息

- **提交哈希**: `772628a`
- **完整提交哈希**: `772628ab436c2c5c18a97633a6a73cdb9e5edfee`
- **上一提交**: `44f90a`
- **完整上一提交**: `44f90a02a380a73ca2e25426d421c8d636c7ec16`
- **分支**: master
- **提交信息**: `引入ML潜力模型辅助短线信号`

---

## 改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `.github/workflows/stock-monitor.yml` | 修改 | 每周训练提交文件从 `shadow_regressor.pkl` 改为 `shadow_potential_model.pkl` |
| `stocks/ml/shadow_learner.py` | 修改 | 删除旧回归模型逻辑，新增十倍/百倍早期潜力分类模型 |
| `stocks/ml/shadow_model.pkl` | 修改 | 用最新回填数据重新训练的短线达标分类模型 |
| `stocks/ml/shadow_potential_model.pkl` | 新增 | 新增早期潜力模型，预测5日内最大涨幅是否达到高弹性阈值 |
| `stocks/ml/shadow_regressor.pkl` | 删除 | 删除旧最大涨幅回归模型，代码不再引用 |
| `stocks/ml/shadow_data.json` | 修改 | 回填85条历史信号结果，新增/更新模型预测字段 |
| `stocks/ml/model_report.md` | 修改 | 报告从“分类+回归”改为“短线达标+早期潜力” |
| `stocks/ml/weekly_train.py` | 修改 | 训练状态显示短线模型和潜力模型是否存在 |
| `stocks/ml/analyze_features.py` | 修改 | 同时分析短线达标模型和早期潜力模型的特征重要性 |
| `stocks/ml/weekly_report/weekly_ml_report.py` | 修改 | 周报字段从“ML预测涨幅”改为“ML潜力概率” |
| `stocks/stock_monitor/monitor.py` | 修改 | 钉钉推送从 `达标+预测涨幅` 改为 `达标+潜力` |
| `stocks/stock_analyzer.py` | 修改 | 独立运行时的ML输出从 `gain` 改为 `potential` |
| `stocks/严格选股_多周期.py` | 修改 | 本地严格选股输出从 `涨幅` 改为 `潜力` |
| `stocks/test.py` | 删除 | 删除无业务引用的依赖安装测试脚本 |
| `stocks/test2.py` | 删除 | 删除无业务引用的早期金叉回测测试脚本 |

---

## 核心改动说明

### 1. 删除旧回归模型

旧逻辑：

```text
shadow_regressor.pkl
RandomForestRegressor
predict_gain()
ml_predict_gain
```

旧模型用于预测未来5个交易日内最大涨幅百分比，但测试表现较弱，上一版报告中 R² 为负，实际参考价值有限。

新逻辑中已删除：

```python
REGRESSOR_FILE
_train_regressor()
predict_gain()
```

并删除模型文件：

```text
stocks/ml/shadow_regressor.pkl
```

---

### 2. 新增十倍/百倍早期潜力模型

新增模型文件：

```text
stocks/ml/shadow_potential_model.pkl
```

新增核心函数：

```python
_train_potential_model()
predict_potential()
```

当前数据源缺少市值、估值、营收、利润、ROE、现金流、研发、机构持仓、股东户数等长期基本面因子，因此不能直接学习真实十倍/百倍概率。

本次先用可回测、可验证的代理标签：

```text
信号发出后5个交易日内最大涨幅 >= 8.0%
```

代码常量：

```python
POTENTIAL_GAIN_THRESHOLD_PCT = 8.0
```

模型含义：

```text
不是直接预测真实十倍/百倍，而是预测“早期高弹性爆发概率”。
```

---

### 3. 两个模型的分工

| 模型 | 文件 | 输出字段 | 作用 |
|------|------|----------|------|
| 短线达标模型 | `shadow_model.pkl` | `ml_predict_prob` | 预测5日内是否触达目标价，偏确定性 |
| 早期潜力模型 | `shadow_potential_model.pkl` | `ml_predict_potential` | 预测5日内是否具备>=8%高弹性，偏空间 |

实战逻辑：

```text
短线达标概率高 + 潜力概率高：优先信号，确定性和空间兼具
短线达标概率高 + 潜力概率低：适合短线快进快出
短线达标概率一般 + 潜力概率高：低仓观察，等二次确认
短线达标概率低 + 潜力概率低：忽略
```

---

### 4. 钉钉推送字段调整

旧推送：

```text
🤖达标xx%  📈涨幅xx%
```

新推送：

```text
🤖达标xx%  🌱潜力xx%
```

影响文件：

```text
stocks/stock_monitor/monitor.py
stocks/严格选股_多周期.py
stocks/stock_analyzer.py
stocks/ml/weekly_report/weekly_ml_report.py
```

GitHub Action 每天自动筛选股票时，会调用：

```python
record_and_predict()
```

返回：

```python
{
    'prob': 短线达标概率,
    'potential': 早期潜力概率,
}
```

---

### 5. 每周训练流程保持不变

`weekly_train.py` 仍然是 GitHub Action 的训练入口：

```bash
python stocks/ml/weekly_train.py
```

流程仍是：

```text
1. 查看状态
2. 回填实际结果
3. 训练模型
4. 生成报告
```

区别是训练阶段现在生成两个分类模型：

```text
shadow_model.pkl
shadow_potential_model.pkl
```

GitHub Action 提交文件已同步改为：

```bash
git add stocks/ml/shadow_data.json stocks/ml/shadow_model.pkl stocks/ml/shadow_potential_model.pkl stocks/ml/model_report.md || true
```

---

## 本次训练结果

### 数据状态

| 指标 | 数值 |
|------|------|
| 总记录 | 2168 |
| 已标记 | 1927 |
| 未标记 | 241 |
| 本次回填 | 85条 |
| 短线达标率 | 22.78% |
| 高弹性基准率 | 25.06% |

### 短线达标模型

| 指标 | 数值 |
|------|------|
| 样本数 | 1927 |
| 训练准确率 | 82.61% |
| 测试准确率 | 76.17% |
| 训练截止 | 2026-05-06 |
| 测试起始 | 2026-05-06 |

概率桶表现：

| 概率区间 | 信号数 | 命中数 | 命中率 |
|----------|--------|--------|--------|
| >=40% | 20 | 4 | 20.0% |
| 35%-40% | 1 | 0 | 0.0% |
| 30%-35% | 17 | 6 | 35.3% |
| 25%-30% | 100 | 20 | 20.0% |
| <25% | 248 | 61 | 24.6% |

> 注意：本轮短线模型高概率桶表现一般，需要观察后续自动积累样本后的稳定性。

### 早期潜力模型

| 指标 | 数值 |
|------|------|
| 样本数 | 1927 |
| 标签 | 5日内最大涨幅 >= 8.0% |
| 正样本比例 | 25.06% |
| 训练准确率 | 91.56% |
| 测试准确率 | 69.69% |

潜力概率桶表现：

| 潜力概率区间 | 信号数 | 高弹性命中数 | 命中率 |
|--------------|--------|--------------|--------|
| >=60% | 35 | 19 | 54.3% |
| 50%-60% | 22 | 7 | 31.8% |
| 40%-50% | 64 | 20 | 31.2% |
| 30%-40% | 82 | 27 | 32.9% |
| <30% | 183 | 39 | 21.3% |

> 当前更值得重点观察的是 `ml_predict_potential >= 60%` 的信号。

---

## 特征重要性摘要

### 短线达标模型 TOP5

| 排名 | 特征 | 含义 |
|------|------|------|
| 1 | `an_quote_change_pct` | 当前涨跌幅 |
| 2 | `an_market_pos_vol_ratio` | 量比/成交活跃度 |
| 3 | `an_market_pos_relative_strength` | 相对市场强度 |
| 4 | `an_capital_flow_ratio` | 主力资金流入强度 |
| 5 | `sc_gold_day_vol` | 金叉日成交量 |

### 早期潜力模型 TOP5

| 排名 | 特征 | 含义 |
|------|------|------|
| 1 | `an_quote_turnover_rate` | 换手率 |
| 2 | `an_market_pos_relative_strength` | 相对市场强度 |
| 3 | `an_quote_change_pct` | 当前涨跌幅 |
| 4 | `an_technical_atr` | 波动率 |
| 5 | `sc_ma20` | 技术位置 |

两个模型的特征来源占比接近：

```text
短线达标模型：选股阶段约33%，分析阶段约67%
早期潜力模型：选股阶段约33%，分析阶段约67%
```

---

## 兼容性确认

- ✅ GitHub Action 自动筛股可继续运行
- ✅ GitHub Action 每周训练可继续运行
- ✅ 本地严格选股可继续运行
- ✅ `stock_analyzer.py` 独立运行可继续使用ML预测
- ✅ `chip_analyzer.py` 不受影响，仍作为独立筹码分析工具保留
- ✅ `weekly_report.py` 已适配潜力概率字段
- ✅ `analyze_features.py` 已适配两个模型
- ✅ `test.py` / `test2.py` 删除后无引用影响

---

## 验证清单

已完成：

- [x] `python -m py_compile` 检查核心脚本无语法错误
- [x] `python stocks/ml/analyze_features.py` 可正常输出两个模型特征重要性
- [x] `weekly_train.py` 已成功训练并生成两个模型
- [x] `shadow_regressor.pkl` 已删除
- [x] `shadow_potential_model.pkl` 已生成
- [x] GitHub Action 提交文件列表已更新
- [x] 本地工作区已提交并推送

后续观察：

- [ ] 下一次 GitHub Actions `monitor` 能正常推送 `达标 + 潜力`
- [ ] 下一次 GitHub Actions `ml-train` 能正常提交 `shadow_potential_model.pkl`
- [ ] 观察 `ml_predict_potential >= 60%` 信号的实际表现
- [ ] 如潜力模型效果不稳定，考虑调整代理标签阈值，比如 6%、10%、或20日窗口
- [ ] 如后续接入市值/估值/财务数据，再升级为真正的中长期十倍股模型

---

## 回滚后需要注意

如果使用 `git revert 772628a` 回滚：

1. `shadow_regressor.pkl` 会恢复。
2. `shadow_potential_model.pkl` 会被删除。
3. 钉钉推送会恢复为 `达标 + 预测涨幅`。
4. GitHub Action 会恢复提交旧回归模型。
5. `test.py` 和 `test2.py` 会恢复。

如果之后已经有新的自动训练或信号提交，优先使用 `git revert`，不要直接 `reset --hard + push --force`。
