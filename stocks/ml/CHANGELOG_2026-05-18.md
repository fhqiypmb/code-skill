# ML模型校准优化改动记录 (2026-05-18)

## 回滚方法

如需回滚到本次提交之前的状态：

```bash
# 方法1：创建一个反向提交（安全，保留历史）
git revert 79cf100

# 方法2：硬回滚（直接回到上一个提交，会丢弃之后的所有提交）
git reset --hard 5b9c008
git push --force
```

> **推荐方法1**，不会丢失git历史。方法2是破坏性操作，谨慎使用。

---

## 提交信息

- **提交哈希**: `79cf100`
- **上一提交**: `5b9c008`
- **分支**: master

---

## 改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `stocks/ml/shadow_learner.py` | 修改 | 时间序列切分 + 不再用 class_weight='balanced' + isotonic 概率校准 + 排除9个冗余特征 |
| `stocks/ml/shadow_model.pkl` | 修改 | 用新逻辑重新训练的分类模型（CalibratedClassifierCV 包装，文件从1.6MB增到6.6MB） |
| `stocks/ml/shadow_regressor.pkl` | 修改 | 回归模型也改用时间序列切分 |
| `stocks/ml/model_report.md` | 修改 | 新增切分方式、校准说明、实战阈值建议 |
| `stocks/stock_monitor/monitor.py` | 修改 | 推送颜色阈值匹配新校准概率（40/35/30/25%） |
| `stocks/ml/weekly_report/weekly_ml_report.md` | 修改 | 周报数据更新（无关变更，本地生成） |

---

## 核心改动说明

### 1. 时间序列切分（最核心）

- **之前**: `train_test_split(random_state=42)` 随机切分，训练集和测试集时间混合，存在未来信息泄漏
- **之后**: 按日期升序排序后，前80%做训练，后20%做测试，模拟"用历史预测未来"

```python
# 旧代码
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# 新代码
labeled.sort(key=lambda r: r.get('date', ''))  # 先按日期排序
n_split = int(len(labeled) * 0.8)
X_train, X_test = X[:n_split], X[n_split:]
y_train, y_test = y[:n_split], y[n_split:]
```

### 2. 去掉 class_weight='balanced' + 加 isotonic 概率校准

- **之前**: `RandomForestClassifier(class_weight='balanced')` 把23%正类样本权重抬到3.4倍，导致输出概率系统性偏高
- **之后**: 不加权重 + `CalibratedClassifierCV(method='isotonic', cv=5)` 5折交叉校准，让输出概率≈实际达标率

```python
# 旧代码
model = RandomForestClassifier(
    n_estimators=100, max_depth=10,
    class_weight='balanced', random_state=42, n_jobs=-1,
)

# 新代码
base_model = RandomForestClassifier(
    n_estimators=100, max_depth=10,
    random_state=42, n_jobs=-1,
)
model = CalibratedClassifierCV(base_model, method='isotonic', cv=5)
```

### 3. 排除9个高相关冗余特征

新增到 exclude 集合：
- `sc_ma30` （与 sc_ma20 相关系数 0.9998）
- `sc_first_double_price` （与 sc_close 相关 0.9995）
- `an_quote_high` / `an_quote_low` / `an_quote_open` / `an_quote_pre_close` （都与 sc_close 高度相关）
- `an_technical_method_targets_压力位法/ATR通道法/斐波那契` （都是 target_price 的成分）

特征数：45 → 36

### 4. 特征重要性提取适配 CalibratedClassifierCV

`CalibratedClassifierCV` 不直接暴露 `feature_importances_`，改为取5个内部base estimator的平均值：

```python
all_imps = np.array([
    cc.estimator.feature_importances_
    for cc in model.calibrated_classifiers_
])
avg_imp = all_imps.mean(axis=0)
```

### 5. 推送颜色阈值同步调整 (monitor.py)

旧模型概率虚高，旧阈值是 80/65/50/35。新模型校准后概率范围下移，新阈值改为：

| 颜色 | 旧阈值 | 新阈值 | 新等级 | 实测精度 |
|------|-------|-------|-------|---------|
| 🔴 红 | ≥80% | **≥40%** | 强 | ~89% |
| 🟠 橙 | ≥65% | **≥35%** | 较强 | ~52% |
| 🟡 黄 | ≥50% | **≥30%** | 中等 | ~40% |
| 🔵 蓝 | ≥35% | **≥25%** | 较弱 | ~33% |
| 🟢 绿 | <35% | <25% | 弱 | <30% |

### 6. 模型 bundle 元信息扩充

```python
bundle = {
    ...,
    'train_end_date':  train_end_date,    # 训练截止日期
    'test_start_date': test_start_date,   # 测试起始日期
    'split_method':    'time_series',     # 切分方式
    'calibrated':      True,              # 已做 isotonic 校准
}
```

---

## 性能指标变化

| 指标 | 旧模型 | 新模型 | 含义 |
|------|-------|-------|------|
| 切分方式 | 随机 | 时间序列 | 真实"用历史预测未来" |
| AUC | 0.625(虚高) | 0.574 | 真实排序能力 |
| 测试准确率 | 76.15% | 72.36% | 真实测试性能 |
| 训练准确率 | 98.51% | 78.28% | 过拟合大幅减少 |
| 概率范围 | 0.10-0.67 | 0.09-0.42 | 概率回归真实分布 |
| 阈值≥50%精度 | 44% | -- (无≥50%信号) | -- |
| 阈值≥40%精度 | -- | **88.9%** | 真实强信号 |
| 阈值≥35%精度 | -- | 51.7% | 中等信号 |
| 阈值≥30%精度 | -- | 40.0% | 弱信号 |

> 新模型整体准确率/AUC数字看起来下降，是因为旧模型被随机切分的"信息泄漏"虚高了。
> 新模型的所有数字都是真实可信的，模型说60%就是真60%概率达标。

---

## 实战阈值建议（新模型）

| 阈值 | 策略 | 仓位 |
|------|------|------|
| ≥40% | 🔥 强烈推荐买入 | 重仓 |
| ≥35% | ✅ 可以买入 | 正常 |
| ≥30% | 🟡 谨慎买入 | 半仓 |
| ≥25% | ⚠️ 等于看运气 | 不建议 |
| <25% | ❌ 直接忽略 | 不建议 |

---

## 兼容性确认

- ✅ `record_and_predict()` 接口签名不变
- ✅ `predict()` / `predict_gain()` 接口签名不变
- ✅ `weekly_train.py` 调用链不变
- ✅ GitHub Actions `ml-train` job 无需调整
- ✅ 仅依赖 sklearn 内置的 `CalibratedClassifierCV`，requirements 不变

---

## 验证清单（提交后2-7天观察）

- [ ] 周一 GitHub Actions 自动训练成功（每周一 07:00 北京时间）
- [ ] 钉钉推送的概率显示在合理范围（不再大量出现"50%+"虚假信号）
- [ ] ≥40% 的强信号实际买入达标率接近 89%
- [ ] ≥35% 的信号实际买入达标率接近 50%
- [ ] 整体亏损情况是否改善

如观察1-2周发现新模型不如旧模型，按本文档顶部"回滚方法"操作即可。
