"""
严格选股程序 - 多周期版本
基于通达信严格选股指标：MA金叉倍量阳线确认信号（严格版）
支持周期：1分钟、5分钟、15分钟、30分钟、60分钟、日、周、月

核心逻辑（完全对齐严格选股.txt）：
  1. MA20金叉MA30
  2. 金叉后出现阴线（20天窗口内）
  3. 最后一根阴线后出现倍量阳线（量能>=最后阴线的2倍，且>金叉日量）
  4. 倍量阳线后1-5天内出现确认阳线，收盘价>=倍量阳线收盘价*0.9993
  5. 确认阳线量能 > 金叉到确认阳之间所有阳线量能（排除倍量阳）
  6. 整个过程中不能出现死叉
  7. 阴线缩量判断（普通/严格两级）
  8. 放量适度（2-6倍，超过6倍标记为爆量）
  9. 金叉日量能大于前7日最大阴线量能（严格买入条件）
  10. 只取首次确认阳线（首次确认）

用法：
  python 严格选股_多周期.py

注意：请先运行 "python 更新股票列表.py" 或 "python 更新股票列表_同花顺.py" 生成 stock_list.md 文件
"""

import os
import urllib.request
import json
import sys
import re
import time
from typing import Dict, List, Tuple, Optional

# 禁用代理（避免代理软件干扰国内API请求）
for _key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if _key in os.environ:
        del os.environ[_key]

# 创建无代理的opener（全局复用）
_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)


class StrictStockScreener:
    """严格选股器 - 多周期支持，核心逻辑对齐通达信严格选股.txt"""

    PERIOD_MAP = {
        '1': ('1min', '1分钟线', 1),
        '2': ('5min', '5分钟线', 5),
        '3': ('15min', '15分钟线', 15),
        '4': ('30min', '30分钟线', 30),
        '5': ('60min', '60分钟线', 60),
        '6': ('240min', '日线', 240),
        '7': ('weekly', '周线', 240),
        '8': ('monthly', '月线', 240),
    }

    def __init__(self, period: str = '240min', period_name: str = '日线'):
        self.period = period
        self.period_name = period_name
        self.scale = self._get_scale()
        self.ma_short = 20  # MA3 in 通达信
        self.ma_long = 30   # MA4 in 通达信

    def _get_scale(self):
        """获取新浪scale参数"""
        for key, (period, name, scale) in self.PERIOD_MAP.items():
            if period == self.period:
                return scale
        return 240

    @staticmethod
    def get_market_prefix(code: str) -> str:
        """根据股票代码判断市场前缀"""
        if code.startswith(('6', '9')):
            return 'sh'
        elif code.startswith(('0', '3')):
            return 'sz'
        return 'sh'

    def fetch_kline(self, code: str, days: int = 1500) -> List[Dict]:
        """从新浪获取K线数据（使用无代理连接）"""
        prefix = self.get_market_prefix(code)
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/"
            "CN_MarketDataService.getKLineData"
            f"?symbol={prefix}{code}&scale={self.scale}&ma=no&datalen={days}"
        )
        try:
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with _opener.open(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                if not isinstance(data, list):
                    return []
                return data
        except Exception:
            return []

    def _prepare_data(self, raw: List[Dict]) -> Optional[List[Dict]]:
        """清洗并预计算K线数据：MA、金叉、死叉、阴阳线"""
        data = []
        for d in raw:
            try:
                data.append({
                    "date": d["day"],
                    "open": float(d["open"]),
                    "high": float(d["high"]),
                    "low": float(d["low"]),
                    "close": float(d["close"]),
                    "volume": float(d["volume"])
                })
            except (KeyError, ValueError, TypeError):
                continue

        if len(data) < self.ma_long + 30:
            return None

        # 确保按时间正序
        data.sort(key=lambda x: x["date"])
        n = len(data)

        # 预计算MA
        closes = [d['close'] for d in data]
        for i in range(n):
            data[i]['ma20'] = (sum(closes[i - 19:i + 1]) / 20) if i >= 19 else None
            data[i]['ma30'] = (sum(closes[i - 29:i + 1]) / 30) if i >= 29 else None

        # 预计算阴阳线
        for i in range(n):
            data[i]['is_yang'] = data[i]['close'] > data[i]['open']
            data[i]['is_yin'] = data[i]['close'] < data[i]['open']

        # 预计算金叉死叉
        data[0]['gold_cross'] = False
        data[0]['dead_cross'] = False
        for i in range(1, n):
            prev, curr = data[i - 1], data[i]
            if (prev['ma20'] is None or prev['ma30'] is None or
                    curr['ma20'] is None or curr['ma30'] is None):
                data[i]['gold_cross'] = False
                data[i]['dead_cross'] = False
            else:
                data[i]['gold_cross'] = (prev['ma20'] < prev['ma30'] and curr['ma20'] > curr['ma30'])
                data[i]['dead_cross'] = (prev['ma20'] > prev['ma30'] and curr['ma20'] < curr['ma30'])

        return data

    def _check_signal_at(self, data: List[Dict], idx: int) -> Tuple[bool, bool, Dict]:
        """
        在指定位置idx检查是否有买入信号（完全对齐通达信严格选股.txt逻辑）

        通达信指标是对每根K线独立计算，以下用Python对齐实现。

        返回: (普通买入, 严格买入, 详情)
        """
        n = len(data)
        curr = data[idx]

        # ===== 基础判断 =====
        # 当前必须是阳线
        if not curr['is_yang']:
            return False, False, {}

        # ===== 第一步：找最近的金叉日 =====
        gold_cross_idx = -1
        for j in range(idx - 1, 29, -1):
            if data[j].get('gold_cross', False):
                gold_cross_idx = j
                break

        if gold_cross_idx == -1:
            return False, False, {}

        dist_gold = idx - gold_cross_idx  # 距金叉天数

        # ===== 第二步：死叉检测 =====
        # 金叉后不能出现死叉（找最近的死叉，其距离必须>距金叉天数）
        dead_cross_idx = -1
        for j in range(idx - 1, 29, -1):
            if data[j].get('dead_cross', False):
                dead_cross_idx = j
                break

        if dead_cross_idx != -1:
            dist_dead = idx - dead_cross_idx
            # 金叉后无死叉：距金叉天数 < 距死叉天数
            if dist_gold >= dist_dead:
                return False, False, {}

        # ===== 第三步：金叉后寻找最后一根阴线的成交量（回看20天）=====
        # 对齐通达信逻辑：从远到近扫描，最近的阴线覆盖之前的
        # 条件：N天前是阴线，且N < 距金叉天数（确保在本次金叉之后）
        yin_vol = 0
        for offset in range(20, 0, -1):  # 从20到1
            check_idx = idx - offset
            if check_idx < 0:
                continue
            if offset < dist_gold and data[check_idx]['is_yin']:
                yin_vol = data[check_idx]['volume']
                # 不break，让更近的覆盖

        has_yin = yin_vol > 0
        if not has_yin:
            return False, False, {}

        # ===== 第四步：金叉日量能 =====
        gold_day_vol = data[gold_cross_idx]['volume']

        # ===== 新增条件：金叉日量能要比前7日的阴线量能高 =====
        max_yin_vol_before_gold = 0
        for offset in range(1, 8):
            check_idx = gold_cross_idx - offset
            if check_idx >= 0 and data[check_idx]['is_yin']:
                max_yin_vol_before_gold = max(max_yin_vol_before_gold, data[check_idx]['volume'])

        gold_vol_enough = gold_day_vol > max_yin_vol_before_gold

        # ===== 第五步：检查当前位置之前是否存在倍量阳线 =====
        # 倍量阳条件：距金叉天数>0 且 <=20，是阳线，量>=阴线量*2，量>金叉日量
        # 然后找"首根倍量阳线"（金叉后第一根满足条件的）
        #
        # 通达信逻辑：
        #   倍量阳:=距金叉天数>0 AND 距金叉天数<=20 AND 阳线 AND VOL>=阴线量*2 AND VOL>金叉日量 AND 有阴线;
        #   首倍量:=倍量阳 AND (REF(倍量阳,1)=0 AND ... AND REF(倍量阳,10)=0);
        #   距首倍:=BARSLAST(首倍量);

        # 先标记区间内所有满足倍量阳条件的K线
        double_vol_yang_flags = []
        for k in range(gold_cross_idx + 1, idx + 1):
            k_dist_gold = k - gold_cross_idx
            if (k_dist_gold > 0 and k_dist_gold <= 20 and
                    data[k]['is_yang'] and
                    data[k]['volume'] >= yin_vol * 2 and
                    data[k]['volume'] > gold_day_vol and
                    has_yin):
                double_vol_yang_flags.append(k)

        # 找首根倍量阳线（前10根K线都不是倍量阳的那根）
        first_double_idx = -1
        for k in double_vol_yang_flags:
            is_first = True
            for prev_k in range(max(k - 10, gold_cross_idx + 1), k):
                if prev_k in double_vol_yang_flags:
                    is_first = False
                    break
            if is_first:
                first_double_idx = k
                break

        if first_double_idx == -1:
            return False, False, {}

        dist_first_double = idx - first_double_idx  # 距首倍
        first_double_price = data[first_double_idx]['close']  # 首倍价
        first_double_vol = data[first_double_idx]['volume']   # 首倍量能

        # ===== 放量适度（2-6倍） =====
        vol_moderate = first_double_vol < yin_vol * 6
        vol_explode = first_double_vol >= yin_vol * 6

        # ===== 阴线缩量判断 =====
        # 间隔天数 = 距金叉天数 - 距首倍
        gap_days = dist_gold - dist_first_double

        # 普通阴线缩量：金叉到倍量阳之间最大阴线量 < 金叉日量*2
        max_yin_vol_between = 0
        for k in range(gold_cross_idx + 1, first_double_idx):
            if data[k]['is_yin']:
                max_yin_vol_between = max(max_yin_vol_between, data[k]['volume'])

        normal_shrink = max_yin_vol_between > 0 and max_yin_vol_between < gold_day_vol * 2

        # 严格缩量：金叉到确认阳线之间的所有阴线量都 < 金叉日量
        # 第一部分：金叉到倍量阳之间的阴线
        strict_shrink = True
        for k in range(gold_cross_idx + 1, first_double_idx):
            if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                strict_shrink = False
                break

        # 第二部分：倍量阳到确认阳之间的阴线
        if strict_shrink:
            for k in range(first_double_idx + 1, idx):
                if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                    strict_shrink = False
                    break

        # ===== 确认阳线判断 =====
        # 条件1：距首倍 >= 1 且 <= 5
        if dist_first_double < 1 or dist_first_double > 5:
            return False, False, {}

        # 条件2：距首倍 < 距金叉天数（首倍在本次金叉之后）
        if dist_first_double >= dist_gold:
            return False, False, {}

        # 条件3：阳线 且 收盘价*10000 >= 首倍价*9993
        if curr['close'] * 10000 < first_double_price * 9993:
            return False, False, {}

        # 条件4：确认量能达标 - 量能 > 金叉到确认阳线之间所有阳线量能（排除倍量阳）
        max_yang_vol_except_double = 0
        for k in range(gold_cross_idx + 1, idx):
            if k == first_double_idx:
                continue
            if data[k]['is_yang'] and data[k]['volume'] > max_yang_vol_except_double:
                max_yang_vol_except_double = data[k]['volume']

        if curr['volume'] <= max_yang_vol_except_double:
            return False, False, {}

        # ===== 首次确认（本次金叉后第一次满足确认阳线条件） =====
        # COUNT(确认阳,距金叉天数+1)=1
        # 检查在当前位置之前（金叉之后）是否已经有确认阳线出现过
        confirm_count = 0
        for check_i in range(gold_cross_idx + 1, idx):
            # 对这个位置也做一遍确认阳线判断（简化版：只检查核心条件）
            if not data[check_i]['is_yang']:
                continue

            # 该位置的距首倍
            check_dist = check_i - first_double_idx
            if check_dist < 1 or check_dist > 5:
                continue
            if check_dist >= (check_i - gold_cross_idx):
                continue

            # 收盘价条件
            if data[check_i]['close'] * 10000 < first_double_price * 9993:
                continue

            # 量能达标
            check_max_yang = 0
            for kk in range(gold_cross_idx + 1, check_i):
                if kk == first_double_idx:
                    continue
                if data[kk]['is_yang'] and data[kk]['volume'] > check_max_yang:
                    check_max_yang = data[kk]['volume']

            if data[check_i]['volume'] <= check_max_yang:
                continue

            confirm_count += 1

        # 加上当前这根
        confirm_count += 1

        if confirm_count != 1:
            return False, False, {}

        # ===== 综合信号判断 =====
        details = {
            'date': curr['date'],
            'close': curr['close'],
            'ma20': curr['ma20'],
            'ma30': curr['ma30'],
            'volume': curr['volume'],
            'gold_cross_date': data[gold_cross_idx]['date'],
            'days_since_gold': dist_gold,
            'days_since_first_double': dist_first_double,
            'first_double_price': first_double_price,
            'first_double_vol': first_double_vol,
            'gold_day_vol': gold_day_vol,
            'yin_vol': yin_vol,
            'gap_days': gap_days,
        }

        # 普通买入: 阴线缩量 + 放量适度 + 金叉后无死叉
        normal_buy = normal_shrink and vol_moderate

        # 严格买入: 严格缩量 + 放量适度 + 间隔>0 + 金叉量够大
        strict_buy = strict_shrink and vol_moderate and gap_days > 0 and gold_vol_enough

        details['signal_type'] = '严格' if strict_buy else ('普通' if normal_buy else '无')
        details['vol_explode'] = vol_explode

        return normal_buy, strict_buy, details

    def check_buy_signals(self, code: str) -> Tuple[bool, bool, Dict]:
        """
        检查买入信号（选股模式：只检查最新K线）
        返回: (普通买入信号, 严格买入信号, 详情)
        """
        raw = self.fetch_kline(code, days=1500)
        if not raw:
            return False, False, {}

        data = self._prepare_data(raw)
        if data is None:
            return False, False, {}

        # 选股模式：只检查最新一根K线
        return self._check_signal_at(data, len(data) - 1)

    def load_stock_list(self) -> List[Tuple[str, str]]:
        """从MD文件加载股票列表（含基本面过滤）"""
        md_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_list.md')

        if not os.path.exists(md_file):
            print(f"错误: 找不到股票列表文件 {md_file}")
            print("请先运行: python 更新股票列表.py 或 python 更新股票列表_同花顺.py")
            sys.exit(1)

        stocks = []
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    match = re.match(r'\|\s*(\d{6})\s*\|\s*([^|]+)\s*\|', line)
                    if match:
                        code = match.group(1)
                        name = match.group(2).strip()

                        # ===== 基本面过滤（对齐通达信） =====
                        # 1. 只要60/00/30开头的股票
                        if not code.startswith(('60', '00', '30')):
                            continue

                        # 2. 排除ST/*ST股票
                        if 'ST' in name or '*ST' in name:
                            continue

                        # 3. 排除退市股票
                        if '退' in name:
                            continue

                        stocks.append((code, name))

            print(f"加载 {len(stocks)} 只股票（已过滤ST/退市/非主板创业板）")
        except Exception as e:
            print(f"读取股票列表失败: {e}")
            sys.exit(1)

        return stocks

    def screen_all_stocks(self, stock_list: List[Tuple[str, str]]):
        """批量选股"""
        total = len(stock_list)
        print(f"\n{'=' * 80}")
        print(f"  严格选股程序 - 周期: {self.period_name}")
        print(f"  待分析: {total} 只股票")
        print(f"{'=' * 80}\n")

        normal_results = []
        strict_results = []
        error_count = 0
        start_time = time.time()

        for i, (code, name) in enumerate(stock_list, 1):
            # 进度显示
            elapsed = time.time() - start_time
            if i > 1:
                eta = elapsed / (i - 1) * (total - i + 1)
                eta_str = f"预计剩余 {int(eta)}s"
            else:
                eta_str = ""

            print(f"\r[{i}/{total}] {code} {name:<10} {eta_str:<20}", end='', flush=True)

            try:
                normal_signal, strict_signal, details = self.check_buy_signals(code)

                if strict_signal:
                    print(f"\r[{i}/{total}] {code} {name:<10} >>> 严格买入信号! <<<")
                    strict_results.append((code, name, details))
                elif normal_signal:
                    print(f"\r[{i}/{total}] {code} {name:<10} >>> 普通买入信号 <<<")
                    normal_results.append((code, name, details))
            except Exception:
                error_count += 1

            # 请求间隔，避免被封
            time.sleep(0.05)

        elapsed_total = time.time() - start_time
        print(f"\r{'=' * 80}")
        print(f"  选股完成！ 用时 {elapsed_total:.1f}s")
        print(f"  严格买入: {len(strict_results)} 只")
        print(f"  普通买入: {len(normal_results)} 只")
        if error_count > 0:
            print(f"  请求失败: {error_count} 只")
        print(f"{'=' * 80}\n")

        return normal_results, strict_results


def show_menu():
    """显示周期选择菜单"""
    print()
    print("=" * 50)
    print("      严格选股程序 - MA金叉倍量阳线确认信号")
    print("=" * 50)
    print()
    print("  请选择K线周期：")
    print()
    print("  1. 1分钟线")
    print("  2. 5分钟线")
    print("  3. 15分钟线")
    print("  4. 30分钟线")
    print("  5. 60分钟线")
    print("  6. 日线")
    print("  7. 周线")
    print("  8. 月线")
    print()
    print("=" * 50)


def print_results(title: str, results: List[Tuple[str, str, Dict]], period_name: str):
    """格式化输出结果"""
    if not results:
        return

    print(f"\n{'=' * 80}")
    print(f"  {title} ({period_name})  共 {len(results)} 只")
    print(f"{'=' * 80}")
    print(f"  {'代码':<8} {'名称':<10} {'信号日期':<20} {'收盘价':>8} "
          f"{'MA20':>8} {'MA30':>8} {'金叉日期':<12} {'距金叉':>5} {'距倍量':>5}")
    print(f"  {'-' * 76}")

    for code, name, d in results:
        ma20_str = f"{d['ma20']:.2f}" if d.get('ma20') else "N/A"
        ma30_str = f"{d['ma30']:.2f}" if d.get('ma30') else "N/A"
        vol_tag = " [爆量]" if d.get('vol_explode') else ""
        print(f"  {code:<8} {name:<10} {d['date']:<20} {d['close']:>8.2f} "
              f"{ma20_str:>8} {ma30_str:>8} {d.get('gold_cross_date', ''):>12} "
              f"{d.get('days_since_gold', ''):>5} {d.get('days_since_first_double', ''):>5}{vol_tag}")

    print(f"  {'-' * 76}")


def main():
    show_menu()

    while True:
        choice = input("\n请输入选项 (1-8): ").strip()
        if choice in StrictStockScreener.PERIOD_MAP:
            break
        print("无效选项，请重新输入！")

    period, period_name, scale = StrictStockScreener.PERIOD_MAP[choice]
    print(f"\n已选择: {period_name}")

    screener = StrictStockScreener(period=period, period_name=period_name)
    stock_list = screener.load_stock_list()

    if not stock_list:
        print("股票列表为空")
        return

    normal_results, strict_results = screener.screen_all_stocks(stock_list)

    # 输出严格买入（优先级高）
    print_results("严格买入信号", strict_results, period_name)

    # 输出普通买入
    print_results("普通买入信号", normal_results, period_name)

    if not normal_results and not strict_results:
        print("\n没有找到符合买入条件的股票")

    # 汇总
    if normal_results or strict_results:
        print(f"\n{'=' * 80}")
        print(f"  汇总: 严格 {len(strict_results)} 只 + 普通 {len(normal_results)} 只 "
              f"= 共 {len(strict_results) + len(normal_results)} 只")
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
