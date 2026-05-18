"""
特征重要性分析 - 本地手动运行，查看两个ML模型的关键因子

模型：
  1. 短线达标模型：预测5个交易日内是否触达目标价
  2. 早期潜力模型：预测5个交易日内最大涨幅是否 >= 8%
"""

import os
import sys
import logging

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_learner as sl


def _print_model_importance(title: str, model_file: str) -> None:
    try:
        import joblib
    except ImportError:
        print("请安装: pip install joblib")
        return

    if not os.path.exists(model_file):
        print(f"\n[FAIL] {title}不存在: {model_file}")
        print("   请先运行 weekly_train.py 积累足够数据后训练")
        return

    model_data = joblib.load(model_file)
    importance = model_data.get('importance', [])

    print("\n" + "=" * 70)
    print(f"[FEATURE] {title} 特征重要性排名（共 {len(importance)} 个特征）")
    print(f"   训练日期: {model_data.get('train_date', '?')}")
    print(f"   样本数量: {model_data.get('sample_count', '?')}")
    if 'positive_rate' in model_data:
        print(f"   正样本比例: {model_data.get('positive_rate', 0):.2%}")
        print(f"   标签定义: {model_data.get('label_desc', '?')}")
    print(f"   训练准确率: {model_data.get('train_acc', 0):.2%}")
    print(f"   测试准确率: {model_data.get('test_acc', 0):.2%}")
    print("=" * 70)

    for i, (name, imp) in enumerate(importance[:30], 1):
        bar = "#" * int(imp * 200)
        print(f"{i:3d}. {name:<45} {imp:.4f}  {bar}")

    sc_total = sum(imp for name, imp in importance if name.startswith('sc_'))
    an_total = sum(imp for name, imp in importance if name.startswith('an_'))
    ot_total = sum(imp for name, imp in importance if not name.startswith(('sc_', 'an_')))

    print("\n" + "=" * 70)
    print("[SOURCE] 各阶段重要性汇总")
    print("=" * 70)
    print(f"  选股阶段 (sc_*): {sc_total:.2%}")
    print(f"  分析阶段 (an_*): {an_total:.2%}")
    print(f"  其他字段:         {ot_total:.2%}")


def main():
    _print_model_importance("短线达标模型", sl.MODEL_FILE)
    _print_model_importance("十倍/百倍早期潜力模型", sl.POTENTIAL_MODEL_FILE)

    stats = sl.get_stats()
    print(f"\n[DATA] 当前数据状态")
    print(f"  总记录: {stats['total']}  已标记: {stats['labeled']}  未标记: {stats['unlabeled']}")
    print(f"  达标准确率: {stats['accuracy']:.2%}")
    print(f"  按周期: {stats['by_period']}")
    print(f"  短线模型: {'存在' if stats['model_exists'] else '不存在'}")
    print(f"  潜力模型: {'存在' if stats.get('potential_model_exists') else '不存在'}")


if __name__ == "__main__":
    main()
