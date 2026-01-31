"""
股票金叉倍量信号监控程序
根据 MA20金叉MA30倍量阳线确认信号 进行实时监控
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import os

class StockSignalMonitor:
    def __init__(self, stock_codes):
        """
        初始化监控器
        :param stock_codes: 股票代码列表，例如 ['000001', '600000', '300001']
        """
        self.stock_codes = stock_codes
        self.last_signals = {}  # 记录上次的信号状态，避免重复提示

    def get_stock_data(self, stock_code, days=100):
        """
        获取股票历史数据
        :param stock_code: 股票代码
        :param days: 获取的天数
        :return: DataFrame
        """
        try:
            # 判断股票类型（沪深）
            if stock_code.startswith('6'):
                symbol = f"sh{stock_code}"
            else:
                symbol = f"sz{stock_code}"

            # 获取历史数据
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                   start_date=start_date, end_date=end_date, adjust="qfq")

            if df is None or len(df) == 0:
                return None

            # 重命名列名
            df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume'
            }, inplace=True)

            return df

        except Exception as e:
            print(f"获取股票 {stock_code} 数据失败: {e}")
            return None

    def calculate_signal(self, df):
        """
        根据选股指标逻辑计算信号
        """
        if df is None or len(df) < 50:
            return False, ""

        # 基础计算
        df['ma20'] = df['close'].rolling(window=20).mean()
        df['ma30'] = df['close'].rolling(window=30).mean()

        # 阳线阴线判断
        df['阳线'] = (df['close'] > df['open']).astype(int)
        df['阴线'] = (df['close'] < df['open']).astype(int)

        # 金叉检测
        df['金叉'] = ((df['ma20'] > df['ma30']) & (df['ma20'].shift(1) <= df['ma30'].shift(1))).astype(int)

        # 死叉检测
        df['死叉'] = ((df['ma30'] > df['ma20']) & (df['ma30'].shift(1) <= df['ma20'].shift(1))).astype(int)

        # 计算距离金叉和死叉的天数
        for i in range(len(df)):
            # 距金叉天数
            gold_cross_indices = df.index[df['金叉'] == 1]
            if len(gold_cross_indices) > 0:
                last_gold = gold_cross_indices[gold_cross_indices <= i]
                if len(last_gold) > 0:
                    df.loc[i, '距金叉天数'] = i - last_gold[-1]
                else:
                    df.loc[i, '距金叉天数'] = 999
            else:
                df.loc[i, '距金叉天数'] = 999

            # 距死叉天数
            dead_cross_indices = df.index[df['死叉'] == 1]
            if len(dead_cross_indices) > 0:
                last_dead = dead_cross_indices[dead_cross_indices <= i]
                if len(last_dead) > 0:
                    df.loc[i, '距死叉天数'] = i - last_dead[-1]
                else:
                    df.loc[i, '距死叉天数'] = 999
            else:
                df.loc[i, '距死叉天数'] = 999

        df['距金叉天数'] = df['距金叉天数'].fillna(999)
        df['距死叉天数'] = df['距死叉天数'].fillna(999)

        # 金叉后无死叉
        df['金叉后无死叉'] = ((df['距金叉天数'] < df['距死叉天数']) | (df['距死叉天数'] > 15)).astype(int)

        # 寻找金叉后10天内的阴线和倍量阳线
        signals = []

        for idx in range(len(df)):
            if idx < 10:
                signals.append(False)
                continue

            current_row = df.iloc[idx]
            距金叉 = current_row['距金叉天数']

            # 如果距离金叉太远，跳过
            if 距金叉 > 10 or 距金叉 == 0:
                signals.append(False)
                continue

            # 寻找最后一根阴线的成交量
            最后阴线量 = 0
            for back in range(1, 11):
                if idx - back < 0:
                    break
                check_row = df.iloc[idx - back]
                if check_row['距金叉天数'] <= 10 and check_row['距金叉天数'] > 0 and check_row['阴线'] == 1:
                    最后阴线量 = check_row['volume']
                    break

            if 最后阴线量 == 0:
                signals.append(False)
                continue

            # 检查是否有倍量阳线
            倍量阳线价格 = 0
            for back in range(1, 6):
                if idx - back < 0:
                    break
                check_row = df.iloc[idx - back]
                距金叉_check = check_row['距金叉天数']
                if (距金叉_check > 0 and 距金叉_check <= 10 and
                    check_row['阳线'] == 1 and
                    check_row['volume'] >= 最后阴线量 * 2):
                    倍量阳线价格 = check_row['close']
                    break

            if 倍量阳线价格 == 0:
                signals.append(False)
                continue

            # 检查确认阳线
            if (current_row['阳线'] == 1 and
                current_row['close'] >= 倍量阳线价格 * 0.995 and
                current_row['金叉后无死叉'] == 1):
                signals.append(True)
            else:
                signals.append(False)

        df['买入信号'] = signals

        # 返回最新一天的信号
        latest = df.iloc[-1]
        has_signal = latest['买入信号']

        if has_signal:
            info = f"MA20:{latest['ma20']:.2f} MA30:{latest['ma30']:.2f} 收盘:{latest['close']:.2f}"
            return True, info

        return False, ""

    def check_stocks(self):
        """
        检查所有股票
        """
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始检查股票信号...")
        print(f"{'='*60}")

        for stock_code in self.stock_codes:
            try:
                print(f"\n正在检查: {stock_code}", end=" ")

                df = self.get_stock_data(stock_code)
                if df is None:
                    print("❌ 获取数据失败")
                    continue

                has_signal, info = self.calculate_signal(df)

                # 检查是否是新信号
                is_new_signal = False
                if stock_code not in self.last_signals:
                    is_new_signal = has_signal
                elif has_signal and not self.last_signals[stock_code]:
                    is_new_signal = True

                self.last_signals[stock_code] = has_signal

                if has_signal:
                    if is_new_signal:
                        print(f"\n{'*'*60}")
                        print(f"🔔 【新信号提示】股票: {stock_code}")
                        print(f"📊 {info}")
                        print(f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"{'*'*60}")

                        # 响铃提示（Windows）
                        print('\a')
                    else:
                        print(f"✅ 信号持续中 - {info}")
                else:
                    print("⚪ 无信号")

            except Exception as e:
                print(f"❌ 检查失败: {e}")

        print(f"\n{'='*60}")
        print(f"本次检查完成")
        print(f"{'='*60}\n")

    def run(self, interval=60):
        """
        运行监控
        :param interval: 检查间隔（秒）
        """
        print("="*60)
        print("股票金叉倍量信号监控程序")
        print("="*60)
        print(f"监控股票: {', '.join(self.stock_codes)}")
        print(f"检查间隔: {interval}秒")
        print("按 Ctrl+C 停止监控")
        print("="*60)

        try:
            while True:
                self.check_stocks()
                print(f"等待 {interval} 秒后进行下次检查...")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n监控已停止")


if __name__ == "__main__":
    # 配置要监控的股票代码列表
    # 格式：6位数字代码
    stock_list = [
        "000001",  # 平安银行
        "600000",  # 浦发银行
        "000002",  # 万科A
        # 在这里添加更多股票代码...
    ]

    print("\n请输入要监控的股票代码（多个代码用逗号分隔，直接回车使用默认列表）:")
    print(f"默认列表: {', '.join(stock_list)}")
    user_input = input("股票代码: ").strip()

    if user_input:
        stock_list = [code.strip() for code in user_input.split(',')]

    print("\n请输入检查间隔（秒，默认60秒）:")
    interval_input = input("间隔秒数: ").strip()
    interval = int(interval_input) if interval_input.isdigit() else 60

    # 创建监控器并运行
    monitor = StockSignalMonitor(stock_list)
    monitor.run(interval=interval)
