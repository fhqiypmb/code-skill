"""
增强版选股器 - 集成影子学习器
"""

import sys
from 严格选股_多周期 import StrictStockScreener, show_mode_menu, show_period_menu
from shadow_learner import record_signal_from_screener
import threading
from stock_analyzer import format_analysis_report
import time

def enhanced_screen_all_stocks(screener, stock_list):
    """增强版批量选股"""
    
    def on_signal(code, name, signal_type, details):
        """当扫到信号时，立即分析基本面并记录"""
        
        def analyze_in_background():
            try:
                details['signal_type'] = signal_type
                result = record_signal_from_screener(code, name, details)
                if result:
                    # 获取完整报告
                    report = format_analysis_report(result)
                    
                    # 加锁打印，避免和选股进度混在一起
                    print_lock = threading.Lock()
                    with print_lock:
                        print("\n" + "="*70)
                        print(f"  {code} {name} 基本面分析")
                        print("="*70)
                        print(report)
                        print("="*70 + "\n")
            except Exception as e:
                print(f"  ⚠️ {code} 分析失败: {e}")
        
        threading.Thread(target=analyze_in_background, daemon=True).start()
    
    return screener.screen_all_stocks(stock_list, on_signal=on_signal)


def main():
    """主函数"""
    
    print("=" * 60)
    print("  严格选股程序 - 机器学习增强版")
    print("=" * 60)
    
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
    
    screener = StrictStockScreener(period=period, period_name=period_name)
    
    if mode == '1':
        # 单独测试模式
        code = input("\n请输入股票代码: ").strip()
        if len(code) == 6:
            normal, strict, details, last_bar = screener.check_one_stock(code)
            stock_list = screener.load_stock_list()
            name = dict(stock_list).get(code, code)
            
            if normal or strict:
                result = record_signal_from_screener(code, name, details)
                if result:
                    print(format_analysis_report(result))
            else:
                print(f"\n{code} 无买入信号")
    else:
        # 批量筛选模式
        stock_list = screener.load_stock_list()
        if not stock_list:
            print("股票列表为空")
            return
        
        print(f"\n开始批量选股，共 {len(stock_list)} 只股票...")
        enhanced_screen_all_stocks(screener, stock_list)


if __name__ == "__main__":
    main()