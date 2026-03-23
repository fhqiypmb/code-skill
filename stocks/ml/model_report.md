# ML模型分析报告

> **训练日期**: 2026-03-22（模型最近一次训练的日期，每周一自动更新）  
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
| 1 | `an_market_pos_vol_ratio` | 0.0741 |
| 2 | `an_success_rate_score` | 0.0647 |
| 3 | `an_technical_atr` | 0.0428 |
| 4 | `an_success_rate_dim_reach_prob` | 0.0411 |
| 5 | `an_market_pos_vr_score` | 0.0327 |
| 6 | `an_technical_stop_loss` | 0.0322 |
| 7 | `an_quote_change_pct` | 0.0318 |
| 8 | `sc_first_double_vol` | 0.0316 |
| 9 | `an_capital_big_net_in` | 0.0306 |
| 10 | `an_quote_price` | 0.0300 |
| 11 | `an_market_pos_relative_strength` | 0.0298 |
| 12 | `sc_volume` | 0.0289 |
| 13 | `an_technical_expected_gain_pct` | 0.0289 |
| 14 | `an_quote_turnover_rate` | 0.0280 |
| 15 | `an_technical_target_price` | 0.0279 |
| 16 | `an_trend_score` | 0.0276 |
| 17 | `sc_close` | 0.0272 |
| 18 | `sr_score` | 0.0266 |
| 19 | `sc_yin_vol` | 0.0216 |
| 20 | `sc_first_double_price` | 0.0208 |

## 高达标 vs 低达标信号特征对比
对比达标(1)和未达标(0)样本的特征均值，差异大的特征是区分好坏信号的关键。

| 特征名 | 达标均值 | 未达标均值 | 差异 |
|--------|----------|------------|------|
| `an_market_pos_vol_ratio` | 1.513 | 0.246 | +1.267 ↑达标更高 |
| `an_success_rate_score` | 54.250 | 43.518 | +10.732 ↑达标更高 |
| `an_technical_atr` | 1.930 | 1.283 | +0.648 ↑达标更高 |
| `an_success_rate_dim_reach_prob` | 60.375 | 59.569 | +0.806 ↑达标更高 |
| `an_market_pos_vr_score` | 47.500 | 14.738 | +32.762 ↑达标更高 |
| `an_technical_stop_loss` | 34.585 | 24.233 | +10.352 ↑达标更高 |
| `an_quote_change_pct` | 2.763 | 0.678 | +2.085 ↑达标更高 |
| `sc_first_double_vol` | 1129947.500 | 3793868.557 | -2663921.057 ↓未达标更高 |
| `an_capital_big_net_in` | 189.827 | -138.251 | +328.078 ↑达标更高 |
| `an_quote_price` | 39.395 | 27.649 | +11.746 ↑达标更高 |
| `an_market_pos_relative_strength` | 4.865 | -1.387 | +6.252 ↑达标更高 |
| `sc_volume` | 1537775.500 | 4207864.848 | -2670089.348 ↓未达标更高 |
| `an_technical_expected_gain_pct` | 11.825 | 9.559 | +2.266 ↑达标更高 |
| `an_quote_turnover_rate` | 9.998 | 4.118 | +5.880 ↑达标更高 |
| `an_technical_target_price` | 44.043 | 30.639 | +13.404 ↑达标更高 |

## 结论摘要
- 最关键的3个特征: `an_market_pos_vol_ratio` / `an_success_rate_score` / `an_technical_atr`
- 整体达标率: 1.9%（基准线，ML预测高于此值才有参考意义）
- 测试集准确率 97.67%，模型有效