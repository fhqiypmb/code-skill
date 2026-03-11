"""
每周训练脚本 - 更新实际结果并重新训练
建议每周五收盘后运行
"""

from shadow_learner import ShadowLearner

def weekly_training():
    """每周训练"""
    
    print("="*60)
    print("🔄 影子学习器 - 每周训练")
    print("="*60)
    
    learner = ShadowLearner()
    
    # 1. 查看当前状态
    stats = learner.get_stats()
    print(f"\n当前状态:")
    print(f"  总记录: {stats['total_records']}")
    print(f"  已标记: {stats['labeled_records']}")
    print(f"  你的准确率: {stats['overall_accuracy']:.2%}")
    
    # 2. 更新实际结果
    print(f"\n步骤1: 更新实际结果...")
    updated = learner.update_outcomes(days_later=5)
    
    if updated == 0:
        print("没有新数据需要更新")
        return
    
    # 3. 重新训练
    print(f"\n步骤2: 重新训练模型...")
    model = learner.train()
    
    if model:
        print(f"\n✅ 训练完成！")
        
        # 4. 显示最新统计
        stats = learner.get_stats()
        print(f"\n最新统计:")
        print(f"  总记录: {stats['total_records']}")
        print(f"  已标记: {stats['labeled_records']}")
        print(f"  你的准确率: {stats['overall_accuracy']:.2%}")

if __name__ == "__main__":
    weekly_training()