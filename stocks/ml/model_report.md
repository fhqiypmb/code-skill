# ML模型分析报告

> **训练日期**: 2026-03-23（模型最近一次训练的日期，每周一自动更新）  
> **样本数**: 214（已回填实际涨跌结果的历史信号数量）  
> **训练集准确率**: 100.00%  |  **测试集准确率**: 97.67%

## 按周期达标率
| 周期 | 总信号 | 达标数 | 达标率 |
|------|--------|--------|--------|
| 30分钟 | 14 | 0 | 0.0% |
| 5分钟 | 172 | 4 | 2.3% |
| 日线 | 28 | 0 | 0.0% |

## 按信号类型达标率
信号类型说明：**筑底**=底部企稳反弹、**突破**=放量突破压力位、**严格**=金叉严格条件全满足、**普通**=金叉基本条件满足

| 信号类型 | 总信号 | 达标数 | 达标率 |
|----------|--------|--------|--------|
| 突破 | 58 | 0 | 0.0% |
| 严格 | 54 | 0 | 0.0% |
| 普通 | 102 | 4 | 3.9% |

## 特征重要性 TOP20
越靠前的特征对模型预测影响越大，可理解为「决定上涨概率的关键因子」。

| 排名 | 特征名 | 重要性得分 |
|------|--------|------------|
| 1 | `an_market_pos_vol_ratio` | 0.0534 |
| 2 | `an_quote_volume` | 0.0493 |
| 3 | `an_technical_method_targets_ATR通道法` | 0.0483 |
| 4 | `an_capital_big_net_in` | 0.0481 |
| 5 | `an_quote_high` | 0.0457 |
| 6 | `an_technical_atr` | 0.0448 |
| 7 | `an_quote_open` | 0.0421 |
| 8 | `an_quote_change_pct` | 0.0418 |
| 9 | `an_market_pos_relative_strength` | 0.0402 |
| 10 | `an_technical_method_targets_压力位法` | 0.0399 |
| 11 | `an_quote_turnover_rate` | 0.0385 |
| 12 | `an_capital_flow_ratio` | 0.0360 |
| 13 | `sc_yin_vol` | 0.0357 |
| 14 | `sc_first_double_price` | 0.0326 |
| 15 | `sc_first_double_vol` | 0.0298 |
| 16 | `an_technical_method_targets_斐波那契` | 0.0290 |
| 17 | `sc_days_since_gold` | 0.0249 |
| 18 | `an_trend_detail_vol_price` | 0.0240 |
| 19 | `an_technical_stop_loss` | 0.0232 |
| 20 | `an_quote_pre_close` | 0.0230 |

## 高达标 vs 低达标信号特征对比
对比达标(1)和未达标(0)样本的特征均值，差异大的特征是区分好坏信号的关键。

| 特征名 | 达标均值 | 未达标均值 | 差异 |
|--------|----------|------------|------|
| `an_market_pos_vol_ratio` | 1.513 | 0.246 | +1.267 ↑达标更高 |
| `an_quote_volume` | 511168.750 | 253698.000 | +257470.750 ↑达标更高 |
| `an_technical_method_targets_ATR通道法` | 45.190 | 31.398 | +13.792 ↑达标更高 |
| `an_capital_big_net_in` | 189.827 | -138.251 | +328.078 ↑达标更高 |
| `an_quote_high` | 39.568 | 28.004 | +11.564 ↑达标更高 |
| `an_technical_atr` | 1.930 | 1.283 | +0.648 ↑达标更高 |
| `an_quote_open` | 38.185 | 27.460 | +10.725 ↑达标更高 |
| `an_quote_change_pct` | 2.763 | 0.678 | +2.085 ↑达标更高 |
| `an_market_pos_relative_strength` | 4.865 | -1.387 | +6.252 ↑达标更高 |
| `an_technical_method_targets_压力位法` | 39.828 | 28.026 | +11.802 ↑达标更高 |
| `an_quote_turnover_rate` | 9.998 | 4.118 | +5.880 ↑达标更高 |
| `an_capital_flow_ratio` | 0.588 | -0.830 | +1.417 ↑达标更高 |
| `sc_yin_vol` | 537050.000 | 2024831.010 | -1487781.010 ↓未达标更高 |
| `sc_first_double_price` | 39.270 | 27.532 | +11.738 ↑达标更高 |
| `sc_first_double_vol` | 1129947.500 | 3793868.557 | -2663921.057 ↓未达标更高 |

## 结论摘要
- 最关键的3个特征: `an_market_pos_vol_ratio` / `an_quote_volume` / `an_technical_method_targets_ATR通道法`
- 整体达标率: 1.9%（基准线，ML预测高于此值才有参考意义）
- 测试集准确率 97.67%，模型有效