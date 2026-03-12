"""
每周训练脚本 - 更新实际结果并重新训练
"""

from shadow_learner import get_shadow

def main():
    print("=" * 60)
    print("🔄 影子学习器 - 每周训练")
    print("=" * 60)
    
    shadow = get_shadow()
    
    # 1. 查看当前状态
    stats = shadow.get_stats()
    print(f"\n当前状态:")
    print(f"  总记录: {stats['total_records']}")
    print(f"  已标记: {stats['labeled_records']}")
    print(f"  你的准确率: {stats['overall_accuracy']:.2%}")
    print(f"  存储字段数: {stats['feature_count']}")
    
    # 2. 更新实际结果
    print(f"\n步骤1: 更新实际结果...")
    updated = shadow.update_outcomes(days_later=5)
    print(f"  更新了 {updated} 条记录")
    
    # 3. 再次查看状态
    stats = shadow.get_stats()
    print(f"\n更新后状态:")
    print(f"  总记录: {stats['total_records']}")
    print(f"  已标记: {stats['labeled_records']}")
    print(f"  你的准确率: {stats['overall_accuracy']:.2%}")
    
    # 4. 如果已标记数据够50条，开始训练
    if stats['labeled_records'] >= 50:
        print(f"\n🎯 已标记数据足够（{stats['labeled_records']}条），开始训练...")
        model = shadow.train()
        if model:
            print(f"\n✅ 训练完成！")
            print(f"   模型已保存到: shadow_model.pkl")
    else:
        print(f"\n⚠️ 已标记数据不足，需要至少50条，当前{stats['labeled_records']}条")
        print(f"   请过几天再运行，等股票走完5天行情")

if __name__ == "__main__":
    main()