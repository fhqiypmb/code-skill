# -*- coding: utf-8 -*-
"""
市场环境特征有效性验证脚本
=============================

【这个脚本是干嘛的】
验证 monitor.py 埋进 shadow_data.json 的"市场环境特征"(mk_* 字段)到底有没有用。
即：当大盘健康 / 警告 / 禁止时，信号的实际命中率有没有显著差异。
若差异明显 → 这组特征值得保留并进入模型训练；若没差异 → 说明大盘状态对短线胜率影响有限。

【背景 / 为什么需要它】
- market_env.py 的 env_to_ml_features() 输出 6 个大盘特征(mk_overall_code / mk_sh_below ...)，
  由 monitor.py 在记录信号时埋进 details，最终落到 shadow_data.json 的 sc_mk_* 字段。
- 这些特征是"独立信号源"(来自大盘客观状态，不来自人工选股打分)，理论上能提升模型。
- 但特征是后期才接上的，老的历史样本没有它；本脚本就是用来观察新样本攒够后的真实效果。

【运行条件 —— 重要】
回填需要信号发出后满 5 个交易日(OUTCOME_DAYS=5)才有结果。
所以必须等"既带 sc_mk_* 特征、又已回填 reached_target/max_gain_pct"的样本积累起来才有意义。
- 若脚本输出"已回填结果: 0"，说明带特征的样本还太新，没到第5个交易日，请过几天再跑。
- 建议等可用样本攒到 50~100 条以上再下结论(样本太少看不出统计差异)。

【怎么判读结果】
- 看不同市场环境(健康/警告/禁止)分组下的命中率差异：
  * 若"健康"明显高于"禁止"(比如差 10+ 个百分点) → 特征有效，保留并喂进训练。
  * 若几组命中率差不多 → 大盘状态对短线影响有限，可考虑不投入。
- "按上证连续跌破MA60根数"那一节是更细的视角，同理看跌破越多命中率是否越低。

【怎么运行】
    cd stocks/ml && python verify_market_env.py
（纯只读，不改任何数据、不训练，随便跑）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shadow_data.json')

d = json.load(open(DATA, encoding='utf-8'))

# 带市场特征的样本
with_mk = [r for r in d if 'sc_mk_overall_code' in r]
# 其中已回填结果的
labeled = [r for r in with_mk if r.get('reached_target') is not None]
# 带 max_gain_pct 的（潜力/涨幅口径）
gain_ok = [r for r in with_mk if r.get('max_gain_pct') is not None]

print(f"带市场特征样本: {len(with_mk)}")
print(f"  其中已回填达标结果: {len(labeled)}")
print(f"  其中已回填涨幅结果: {len(gain_ok)}")
print()

CODE_NAME = {0: '健康', 1: '警告', 2: '禁止', -1: '缺失'}


def bucket(records, key_fn, label):
    print(f"=== 按市场环境分组：{label} ===")
    groups = {}
    for r in records:
        code = int(r.get('sc_mk_overall_code', -1))
        groups.setdefault(code, []).append(r)
    for code in sorted(groups):
        g = groups[code]
        hits = [key_fn(r) for r in g]
        rate = sum(hits) / len(hits) if hits else 0
        print(f"  {CODE_NAME.get(code, code):4s}(code={code}): {len(g):3d}条, 命中{sum(hits):3d}, 命中率 {rate:.1%}")
    print()


if labeled:
    bucket(labeled, lambda r: int(r.get('reached_target', 0)), "达标率(触达目标价)")

if gain_ok:
    bucket(gain_ok, lambda r: 1 if float(r.get('max_gain_pct') or 0) >= 8.0 else 0, "高弹性率(5日内≥8%)")

# 额外：按上证连续跌破根数粗分
if gain_ok:
    print("=== 按上证连续跌破MA60根数 ===")
    def sh_band(r):
        v = int(r.get('sc_mk_sh_below', -1))
        if v < 0: return '缺失'
        if v == 0: return '0根(站上)'
        if v <= 5: return '1-5根'
        return '>=6根'
    bands = {}
    for r in gain_ok:
        bands.setdefault(sh_band(r), []).append(r)
    for b in ['0根(站上)', '1-5根', '>=6根', '缺失']:
        if b in bands:
            g = bands[b]
            hits = [1 if float(r.get('max_gain_pct') or 0) >= 8.0 else 0 for r in g]
            print(f"  {b:10s}: {len(g):3d}条, 高弹性率 {sum(hits)/len(hits):.1%}")
