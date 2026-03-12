"""
特征重要性分析 - 本地手动运行，查看哪些指标最影响达标率
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_learner as sl

MODEL_FILE = sl.MODEL_FILE


def main():
    try:
        import joblib
    except ImportError:
        print("请安装: pip install joblib")
        return

    if not os.path.exists(MODEL_FILE):
        print(f"❌ 模型不存在: {MODEL_FILE}")
        print("   请先运行 weekly_train.py 积累足够数据后训练")
        return

    model_data = joblib.load(MODEL_FILE)
    importance = model_data['importance']

    print("\n" + "=" * 70)
    print(f"📊 特征重要性排名（共 {len(importance)} 个特征）")
    print(f"   训练日期: {model_data.get('train_date', '?')}")
    print(f"   样本数量: {model_data.get('sample_count', '?')}")
    print(f"   训练准确率: {model_data.get('train_acc', 0):.2%}")
    print(f"   测试准确率: {model_data.get('test_acc', 0):.2%}")
    print("=" * 70)

    for i, (name, imp) in enumerate(importance[:30], 1):
        bar = "█" * int(imp * 200)
        print(f"{i:3d}. {name:<45} {imp:.4f}  {bar}")

    # 按来源分类
    sc_total = sum(imp for name, imp in importance if name.startswith('sc_'))
    an_total = sum(imp for name, imp in importance if name.startswith('an_'))
    ot_total = sum(imp for name, imp in importance if not name.startswith(('sc_', 'an_')))

    print("\n" + "=" * 70)
    print("📊 各阶段重要性汇总")
    print("=" * 70)
    print(f"  选股阶段 (sc_*): {sc_total:.2%}")
    print(f"  分析阶段 (an_*): {an_total:.2%}")
    print(f"  其他字段:         {ot_total:.2%}")

    # 当前数据统计
    stats = sl.get_stats()
    print(f"\n📁 当前数据状态")
    print(f"  总记录: {stats['total']}  已标记: {stats['labeled']}  未标记: {stats['unlabeled']}")
    print(f"  达标准确率: {stats['accuracy']:.2%}")
    print(f"  按周期: {stats['by_period']}")


if __name__ == "__main__":
    main()
