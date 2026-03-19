# ML模型分析报告

> **训练日期**: 2026-03-19（模型最近一次训练的日期，每周一自动更新）  
> **样本数**: 48（已回填实际涨跌结果的历史信号数量）  
> **训练集准确率**: 100.00%  |  **测试集准确率**: 90.00%

## 按周期达标率
| 周期 | 总信号 | 达标数 | 达标率 |
|------|--------|--------|--------|
| 30分钟 | 8 | 0 | 0.0% |
| 5分钟 | 34 | 3 | 8.8% |
| 日线 | 6 | 0 | 0.0% |

## 按信号类型达标率
信号类型说明：**筑底**=底部企稳反弹、**突破**=放量突破压力位、**严格**=金叉严格条件全满足、**普通**=金叉基本条件满足

| 信号类型 | 总信号 | 达标数 | 达标率 |
|----------|--------|--------|--------|
| 突破 | 5 | 0 | 0.0% |
| 严格 | 15 | 0 | 0.0% |
| 普通 | 28 | 3 | 10.7% |

## 特征重要性 TOP20
越靠前的特征对模型预测影响越大，可理解为「决定上涨概率的关键因子」。

| 排名 | 特征名 | 重要性得分 |
|------|--------|------------|
| 1 | `an_success_rate_dim_reach_prob` | 0.0887 |
| 2 | `sc_volume` | 0.0664 |
| 3 | `an_capital_big_net_in` | 0.0640 |
| 4 | `an_quote_volume` | 0.0452 |
| 5 | `an_trend_detail_macd` | 0.0424 |
| 6 | `an_quote_turnover_rate` | 0.0386 |
| 7 | `an_trend_detail_vol_price` | 0.0350 |
| 8 | `sc_first_double_vol` | 0.0341 |
| 9 | `an_technical_stop_loss` | 0.0339 |
| 10 | `an_technical_expected_gain_pct` | 0.0335 |
| 11 | `an_technical_method_targets_斐波那契` | 0.0326 |
| 12 | `an_market_pos_relative_strength` | 0.0326 |
| 13 | `an_success_rate_score` | 0.0310 |
| 14 | `an_trend_macd_strength` | 0.0296 |
| 15 | `an_market_pos_vol_ratio` | 0.0259 |
| 16 | `sc_gap_days` | 0.0257 |
| 17 | `an_capital_super_net_in` | 0.0237 |
| 18 | `an_success_rate_dim_momentum` | 0.0237 |
| 19 | `sc_close` | 0.0219 |
| 20 | `an_quote_amount` | 0.0210 |

## 高达标 vs 低达标信号特征对比
对比达标(1)和未达标(0)样本的特征均值，差异大的特征是区分好坏信号的关键。

| 特征名 | 达标均值 | 未达标均值 | 差异 |
|--------|----------|------------|------|
| `an_success_rate_dim_reach_prob` | 53.667 | 70.562 | -16.896 ↓未达标更高 |
| `sc_volume` | 1954234.000 | 11708233.800 | -9753999.800 ↓未达标更高 |
| `an_capital_big_net_in` | -102.330 | 222.806 | -325.136 ↓未达标更高 |
| `an_quote_volume` | 654668.333 | 350378.244 | +304290.089 ↑达标更高 |
| `an_trend_detail_macd` | 50.333 | 67.289 | -16.956 ↓未达标更高 |
| `an_quote_turnover_rate` | 11.383 | 6.137 | +5.246 ↑达标更高 |
| `an_trend_detail_vol_price` | 79.333 | 57.422 | +21.911 ↑达标更高 |
| `sc_first_double_vol` | 1426071.667 | 9153988.778 | -7727917.111 ↓未达标更高 |
| `an_technical_stop_loss` | 28.867 | 29.403 | -0.536 ↓未达标更高 |
| `an_technical_expected_gain_pct` | 10.267 | 11.000 | -0.733 ↓未达标更高 |
| `an_technical_method_targets_斐波那契` | 35.973 | 38.130 | -2.156 ↓未达标更高 |
| `an_market_pos_relative_strength` | 5.980 | 2.967 | +3.013 ↑达标更高 |
| `an_success_rate_score` | 49.467 | 52.118 | -2.651 ↓未达标更高 |
| `an_trend_macd_strength` | 48.000 | 67.778 | -19.778 ↓未达标更高 |
| `an_market_pos_vol_ratio` | 2.013 | 1.061 | +0.952 ↑达标更高 |

## 结论摘要
- 最关键的3个特征: `an_success_rate_dim_reach_prob` / `sc_volume` / `an_capital_big_net_in`
- 整体达标率: 6.2%（基准线，ML预测高于此值才有参考意义）
- 测试集准确率 90.00%，模型有效