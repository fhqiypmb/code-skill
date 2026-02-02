"""
股票金叉倍量信号分析程序（单次查询版本）
根据 MA20金叉MA30倍量阳线确认信号 进行分析
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys

class StockSignalAnalyzer:
    def __init__(self):
        pass

    def get_stock_data(self, stock_code, days=100):
        """
        获取股票历史数据
        """
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

            df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                   start_date=start_date, end_date=end_date, adjust="qfq")

            if df is None or len(df) == 0:
                return None

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
            print(f"❌ 获取股票 {stock_code} 数据失败: {e}")
            return None

    def calculate_signal(self, df):
        """
        根据选股指标逻辑计算信号
        """
        if df is None or len(df) < 50:
            return False, "", {}

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

        details = {
            'ma20': latest['ma20'],
            'ma30': latest['ma30'],
            'close': latest['close'],
            'date': latest['date'],
            '距金叉天数': latest['距金叉天数']
        }

        if has_signal:
            info = f"MA20:{latest['ma20']:.2f} MA30:{latest['ma30']:.2f} 收盘:{latest['close']:.2f}"
            return True, info, details

        return False, "", details

    def analyze(self, stock_code):
        """
        分析单个股票
        """
        print(f"\n{'='*60}")
        print(f"正在分析股票: {stock_code}")
        print(f"{'='*60}\n")

        df = self.get_stock_data(stock_code)
        if df is None:
            print("获取数据失败")
            return

        has_signal, info, details = self.calculate_signal(df)

        print(f"最新日期: {details['date']}")
        print(f"MA20: {details['ma20']:.2f}")
        print(f"MA30: {details['ma30']:.2f}")
        print(f"收盘价: {details['close']:.2f}")
        print(f"距金叉天数: {int(details['距金叉天数'])}")
        print()

        if has_signal:
            print(f"{'*'*60}")
            print(f"【买入信号】")
            print(f"{info}")
            print(f"{'*'*60}")
        else:
            print("暂无买入信号")

        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python 股票信号分析.py <股票代码>")
        print("示例: python 股票信号分析.py 603658")
        sys.exit(1)

    stock_code = sys.argv[1]
    analyzer = StockSignalAnalyzer()
    analyzer.analyze(stock_code)
