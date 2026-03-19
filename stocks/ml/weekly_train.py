"""
每周训练脚本 - 由 GitHub Actions 自动调用，也可本地手动运行
流程：1.查看状态 → 2.回填实际结果 → 3.训练（样本够的话）
"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# 确保能找到 shadow_learner
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shadow_learner as sl


def main():
    print("=" * 60)
    print("  影子学习器 - 每周训练")
    print("=" * 60)

    # 1. 当前状态
    stats = sl.get_stats()
    print(f"\n【当前状态】")
    print(f"  总记录:   {stats['total']}")
    print(f"  已标记:   {stats['labeled']}")
    print(f"  未标记:   {stats['unlabeled']}")
    print(f"  达标准确率: {stats['accuracy']:.2%}")
    print(f"  按周期:   {stats['by_period']}")
    print(f"  模型文件: {'存在' if stats['model_exists'] else '不存在'}")

    # 2. 回填实际结果
    print(f"\n【回填实际结果（信号发出 {sl.OUTCOME_DAYS} 天后）】")
    updated = sl.update_outcomes()
    print(f"  本次回填: {updated} 条")

    # 3. 训练
    stats = sl.get_stats()
    print(f"\n【训练】已标记样本: {stats['labeled']} 条")
    if stats['labeled'] >= sl.MIN_TRAIN_SAMPLES:
        print(f"  样本充足，开始训练...")
        model = sl.train()
        if model:
            print(f"  [OK] 训练完成")
        else:
            print(f"  [FAIL] 训练失败")
    else:
        print(f"  样本不足（需要 {sl.MIN_TRAIN_SAMPLES} 条），跳过训练")
        print(f"  继续积累信号数据，等待自动回填后再训练")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
