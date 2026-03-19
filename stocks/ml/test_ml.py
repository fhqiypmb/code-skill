"""
ML系统测试脚本 - 本地 & GitHub Action 都可用
用法: python stocks/ml/test_ml.py
"""
import os, sys, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_learner as sl

today = datetime.now().strftime('%Y-%m-%d')

print("=" * 55)
print("  ML系统测试")
print("=" * 55)

# ── 1. 写入一条假信号（测试记录+去重） ──────────────────
fake_details = {
    'close': 20.70,
    'gold_cross_date': today,
    'date': today,
    'macd': 0.12,
    'rsi': 62.5,
    'volume_ratio': 1.8,
}
fake_analysis = {
    'verdict': '达标',
    'industry': '服装家纺',
    'success_rate': {'score': 0.62, 'grade': 'B',
                     'dim_breakout': 53, 'dim_momentum': 79,
                     'dim_rs': 25, 'dim_capital': 5,
                     'dim_rr': 20, 'dim_reach_prob': 82},
    'technical':   {'target_price': 21.73, 'stop_loss': 18.22,
                    'expected_gain_pct': 5.0, 'stop_loss_pct': -12.0},
    'market_pos':  {'relative_strength': 1.9, 'vol_ratio': 0.52},
    'capital':     {'main_net_in': 94.0, 'flow_ratio': 4.54},
    'concepts':    ['网红经济', '抖音小店', '电商'],
}

print("\n[1] 写入测试信号...")
r1 = sl.record_signal('603365', '水星家纺', '30分钟', '严格',
                      fake_details, fake_analysis)
print(f"    结果: {'[OK] 写入成功' if r1 else '跳过(重复)'}")

print("\n[2] 再次写入相同信号（测试去重）...")
count_before = len(sl._load_data())
r2 = sl.record_signal('603365', '水星家纺', '30分钟', '严格',
                      fake_details, fake_analysis)
count_after = len(sl._load_data())
if count_after == count_before:
    print(f"    结果: [OK] 去重跳过(正确)")
else:
    print(f"    结果: [FAIL] 未去重，记录数从{count_before}增加到{count_after}")

print("\n[3] 不同周期同一股票（应允许写入）...")
r3 = sl.record_signal('603365', '水星家纺', '日线', '严格',
                      fake_details, fake_analysis)
print(f"    结果: {'[OK] 写入成功' if r3 else '跳过(重复)'}")

# ── 2. 读取并验证JSON ────────────────────────────────────
print("\n[4] 验证JSON文件...")
if os.path.exists(sl.DATA_FILE):
    with open(sl.DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f"    文件路径: {sl.DATA_FILE}")
    print(f"    总记录数: {len(data)}")
    last = data[-1]
    print(f"    最新一条: {last['date']} {last['code']} {last['name']} "
          f"[{last['period']}][{last['signal_type']}]")
    print(f"    字段数量: {len(last)}")
    print(f"    verdict : {last.get('verdict')}")
    print(f"    sr_score: {last.get('sr_score')}")
    print(f"    reached_target: {last.get('reached_target')} (待回填)")
else:
    print("    ✗ JSON文件不存在！")

# ── 3. 统计 ──────────────────────────────────────────────
print("\n[5] 统计信息...")
stats = sl.get_stats()
for k, v in stats.items():
    print(f"    {k}: {v}")

# ── 4. 测试回填（K 线接口 + 实际回填逻辑）──────────────
print("\n[6] 测试回填...")
try:
    stocks_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if stocks_dir not in sys.path:
        sys.path.insert(0, stocks_dir)
    import data_source

    # 先验证 K 线接口本身
    code = '002177'
    klines = data_source.fetch_kline(code, period='240min', limit=10)
    if klines:
        print(f"    [OK] fetch_kline: {len(klines)} 条, last={klines[-1]['day']} close={klines[-1]['close']}")
    else:
        print(f"    [FAIL] fetch_kline 返回空，回填将无法执行")

    # 再跑实际回填逻辑，看能回填几条
    updated = sl.update_outcomes()
    stats2 = sl.get_stats()
    print(f"    [OK] 回填完成: {updated} 条，累计已标记: {stats2['labeled']} 条")
except Exception as e:
    print(f"    [ERROR] {type(e).__name__}: {e}")

print("\n" + "=" * 55)
print("[DONE] 测试完成")
print("=" * 55)
