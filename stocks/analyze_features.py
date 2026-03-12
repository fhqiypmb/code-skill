"""
分析特征重要性 - 查看哪些指标最有用
"""

from shadow_learner import get_shadow
import joblib

def main():
    shadow = get_shadow()
    
    if not shadow.model:
        print("❌ 模型不存在，请先运行 weekly_train.py 训练")
        return
    
    # 加载模型数据
    model_data = joblib.load(shadow.model_file)
    importance = model_data['importance']
    
    print("\n" + "="*70)
    print("📊 特征重要性排名（完整版）")
    print("="*70)
    
    for i, (name, imp) in enumerate(importance[:30]):
        print(f"{i+1:3d}. {name:<40} {imp:.4f}")
    
    # 按来源分类统计
    screener_total = 0
    analyzer_total = 0
    other_total = 0
    
    for name, imp in importance:
        if name.startswith('screener_'):
            screener_total += imp
        elif name.startswith('analyzer_'):
            analyzer_total += imp
        else:
            other_total += imp
    
    print(f"\n{'='*70}")
    print("📊 各阶段重要性对比")
    print(f"{'='*70}")
    print(f"选股阶段总重要性: {screener_total:.2%}")
    print(f"分析阶段总重要性: {analyzer_total:.2%}")
    print(f"其他数据总重要性: {other_total:.2%}")
    
    if screener_total > analyzer_total:
        print(f"\n✅ 结论：选股阶段的指标更重要")
    else:
        print(f"\n✅ 结论：分析阶段的指标更重要")

if __name__ == "__main__":
    main()