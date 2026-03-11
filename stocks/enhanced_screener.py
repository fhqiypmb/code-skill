"""
增强版选股器 - 集成影子学习器
完全不改动原系统，只是添加ML建议
"""

import sys
from 严格选股_多周期 import StrictStockScreener, show_mode_menu, show_period_menu
from shadow_learner import record_signal_from_screener, get_shadow
import threading
import time

def enhanced_screen_all_stocks(screener, stock_list):
    """增强版批量选股"""
    
    shadow = get_shadow()
    
    def on_signal(code, name, signal_type, details):
        """当扫到信号时，立即分析基本面并记录"""
        
        # 在另一个线程中分析，不影响选股速度
        def analyze_in_background():
            try:
                result, ml_result = record_signal_from_screener(code, name, signal_type)
                
                # 打印ML建议
                if result and ml_result:
                    print(f"\n  【{code} {name} 基本面分析】")
                    print(f"  你的评分: {result['success_rate']['score']:.1f}")
                    print(f"  ML置信度: {ml_result['confidence']:.1%} ({ml_result['level']})")
                    print(f"  ML建议: {ml_result['advice']}")
                    print()
            except Exception as e:
                print(f"  ⚠️ {code} 分析失败: {e}")
        
        threading.Thread(target=analyze_in_background, daemon=True).start()
    
    # 调用原选股器，传入回调
    return screener.screen_all_stocks(stock_list, on_signal=on_signal)


def main():
    """主函数"""
    
    print("=" * 60)
    print("  严格选股程序 - 机器学习增强版")
    print("=" * 60)
    print("  【功能】")
    print("  1. 原选股逻辑完全不变")
    print("  2. 自动记录信号供机器学习学习")
    print("  3. 实时显示ML置信度建议")
    print("=" * 60)
    
    # 显示原菜单
    show_mode_menu()
    
    while True:
        mode = input("\n请输入选项 (1-2): ").strip()
        if mode in ('1', '2'):
            break
        print("无效选项，请重新输入！")
    
    show_period_menu()
    
    while True:
        choice = input("\n请输入选项 (1-8): ").strip()
        if choice in StrictStockScreener.PERIOD_MAP:
            break
        print("无效选项，请重新输入！")
    
    period, period_name, scale = StrictStockScreener.PERIOD_MAP[choice]
    print(f"\n已选择: {period_name}")
    
    # 创建选股器
    is_minute = period in ('1min', '5min', '15min', '30min', '60min')
    max_workers = 8 if is_minute else 12  # 你的原配置
    screener = StrictStockScreener(period=period, period_name=period_name,
                                   max_workers=max_workers)
    
    if mode == '1':
        # 单独测试模式
        code = input("\n请输入股票代码（6位数字）: ").strip()
        if len(code) == 6 and code.isdigit():
            # 先选股
            normal_signal, strict_signal, details, last_bar = screener.check_one_stock(code)
            
            # 获取股票名称
            stock_list = screener.load_stock_list()
            name = dict(stock_list).get(code, code)
            
            # 如果有信号，分析基本面
            if normal_signal or strict_signal:
                sig_type = details.get('signal_type', '普通')
                result, ml_result = record_signal_from_screener(code, name, sig_type)
                
                # 显示结果
                if result:
                    from stock_analyzer import format_analysis_report
                    print(format_analysis_report(result))
                    
                    if ml_result:
                        print(f"\n  【机器学习验证】")
                        print(f"  置信度: {ml_result['confidence']:.1%} ({ml_result['level']})")
                        print(f"  建议: {ml_result['advice']}")
            else:
                print(f"\n{code} 无买入信号")
    
    else:
        # 批量筛选模式
        stock_list = screener.load_stock_list()
        
        if not stock_list:
            print("股票列表为空")
            return
        
        print(f"\n开始批量选股，共 {len(stock_list)} 只股票...")
        
        # 用增强版选股
        normal_results, strict_results = enhanced_screen_all_stocks(screener, stock_list)
        
        # 显示结果
        all_results = strict_results + normal_results
        if not all_results:
            print("\n没有找到符合买入条件的股票")
        else:
            print(f"\n{'=' * 80}")
            print(f"  选股完成！共 {len(all_results)} 只信号")
            print(f"{'=' * 80}")
            
            # 显示ML统计
            shadow = get_shadow()
            stats = shadow.get_stats()
            if stats['model_trained']:
                print(f"\n📊 机器学习统计:")
                print(f"  已记录信号: {stats['total_records']}")
                print(f"  已标记结果: {stats['labeled_records']}")
                print(f"  你的准确率: {stats['overall_accuracy']:.2%}")
                
                if stats['signal_stats']:
                    print(f"\n  按信号类型准确率:")
                    for st, data in stats['signal_stats'].items():
                        if data['total'] > 0:
                            acc = data['correct']/data['total'] if data['total']>0 else 0
                            print(f"    {st}: {acc:.2%} ({data['correct']}/{data['total']})")
            
            # 建议下次训练
            if stats['labeled_records'] < 50:
                print(f"\n💡 建议: 收集够50条标记数据后运行训练")
                print(f"   python weekly_train.py")


if __name__ == "__main__":
    main()