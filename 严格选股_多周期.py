"""
严格选股程序 - 多周期版本
基于通达信严格选股指标：MA金叉倍量阳线确认信号（严格版）
支持周期：1分钟、5分钟、15分钟、30分钟、60分钟、日、周、月

核心逻辑（完全对齐金叉.txt）：
  1. MA20金叉MA30
  2. 金叉后出现阴线（20天窗口内）
  3. 最后一根阴线后出现倍量阳线（量能>=最后阴线的2倍，且>金叉日量）
  4. 倍量阳线后1-5天内出现确认阳线，收盘价>=倍量阳线收盘价*容差（按周期动态调整）
  5. 确认阳线量能 > 金叉到确认阳之间所有阳线量能（排除倍量阳）
  6. 整个过程中不能出现死叉
  7. 阴线缩量判断（普通/严格两级）
  8. 放量适度（2-6倍，超过6倍标记为爆量）
  9. 金叉日量能大于前7日最大阴线量能（严格买入条件）
  10. 只取首次确认阳线（首次确认）

数据源：新浪财经、东方财富、腾讯财经、同花顺（多源并行，自动负载分散）

用法：
  python 严格选股_多周期.py

注意：请先运行 "python 更新股票列表.py" 生成 stock_list.md 文件
"""

import os
import urllib.request
import json
import sys
import re
import ssl
import time
import threading
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# 禁用代理（避免代理软件干扰国内API请求）
for _key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if _key in os.environ:
        del os.environ[_key]

# 忽略SSL证书验证
ssl._create_default_https_context = ssl._create_unverified_context

# 创建无代理的opener（全局复用）
_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

# 线程安全的打印锁
_print_lock = threading.Lock()


# ==================== 多数据源K线获取 ====================

class KlineSource:
    """K线数据源基类"""

    @staticmethod
    def get_market_prefix(code: str) -> str:
        if code.startswith(('6', '9')):
            return 'sh'
        return 'sz'

    @staticmethod
    def _request(url: str, headers: dict, timeout: int = 12) -> bytes:
        req = urllib.request.Request(url, headers=headers)
        with _opener.open(req, timeout=timeout) as r:
            return r.read()


class SinaKline(KlineSource):
    """新浪财经K线"""

    SCALE_MAP = {
        '1min': 1, '5min': 5, '15min': 15, '30min': 30,
        '60min': 60, '240min': 240, 'weekly': 240, 'monthly': 240,
    }

    @classmethod
    def fetch(cls, code: str, period: str, datalen: int = 1500) -> List[Dict]:
        prefix = cls.get_market_prefix(code)
        scale = cls.SCALE_MAP.get(period, 240)
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/"
            "CN_MarketDataService.getKLineData"
            f"?symbol={prefix}{code}&scale={scale}&ma=no&datalen={datalen}"
        )
        raw = cls._request(url, {
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            return []
        return [{"day": d["day"], "open": d["open"], "high": d["high"],
                 "low": d["low"], "close": d["close"], "volume": d["volume"]}
                for d in data]


class EastmoneyKline(KlineSource):
    """东方财富K线"""

    KLT_MAP = {
        '1min': 1, '5min': 5, '15min': 15, '30min': 30,
        '60min': 60, '240min': 101, 'weekly': 102, 'monthly': 103,
    }

    @classmethod
    def fetch(cls, code: str, period: str, datalen: int = 1500) -> List[Dict]:
        market = 1 if code.startswith('6') else 0
        klt = cls.KLT_MAP.get(period, 101)
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={market}.{code}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt={klt}&fqt=1&end=20500101&lmt={datalen}"
            f"&_={int(time.time()*1000)}"
        )
        raw = cls._request(url, {
            "Referer": "https://quote.eastmoney.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = json.loads(raw.decode("utf-8"))
        klines = resp.get('data', {}).get('klines', []) if resp.get('data') else []
        result = []
        for line in klines:
            parts = line.split(',')
            if len(parts) >= 6:
                result.append({
                    "day": parts[0], "open": parts[1], "high": parts[3],
                    "low": parts[4], "close": parts[2], "volume": parts[5],
                })
        return result


class TencentKline(KlineSource):
    """腾讯财经K线（日线/周线/月线）"""

    KTYPE_MAP = {
        '240min': 'day', 'weekly': 'week', 'monthly': 'month',
    }

    @classmethod
    def fetch(cls, code: str, period: str, datalen: int = 1500) -> List[Dict]:
        ktype = cls.KTYPE_MAP.get(period)
        if not ktype:
            return []  # 腾讯不支持分钟K线
        prefix = cls.get_market_prefix(code)
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
            f"param={prefix}{code},{ktype},,,{datalen},qfq"
        )
        raw = cls._request(url, {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = json.loads(raw.decode("utf-8"))
        kdata = resp.get('data', {}).get(f'{prefix}{code}', {})
        day_data = kdata.get(ktype, kdata.get(f'qfq{ktype}', []))
        result = []
        for d in day_data:
            if len(d) >= 6:
                result.append({
                    "day": d[0], "open": str(d[1]), "high": str(d[3]),
                    "low": str(d[4]), "close": str(d[2]), "volume": str(d[5]),
                })
        return result


class THSKline(KlineSource):
    """同花顺K线（仅日线/周线/月线）"""

    PERIOD_CODE_MAP = {
        '240min': '01',  # 日线
        'weekly': '11',  # 周线
        'monthly': '21', # 月线
    }

    @classmethod
    def fetch(cls, code: str, period: str, datalen: int = 1500) -> List[Dict]:
        pc = cls.PERIOD_CODE_MAP.get(period)
        if not pc:
            return []  # 同花顺不支持分钟K线
        url = f"https://d.10jqka.com.cn/v6/line/hs_{code}/{pc}/last.js"
        raw = cls._request(url, {
            "Referer": "https://stockpage.10jqka.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        text = raw.decode("utf-8")
        # 解析JSONP
        m = re.search(r'\((\{.*\})\)', text, re.DOTALL)
        if not m:
            return []
        resp = json.loads(m.group(1))
        raw_data = resp.get('data', '')
        if not raw_data:
            return []
        result = []
        for rec in raw_data.split(';'):
            parts = rec.split(',')
            if len(parts) >= 7 and parts[1]:
                result.append({
                    "day": parts[0], "open": parts[1], "high": parts[2],
                    "low": parts[3], "close": parts[4], "volume": parts[5],
                })
        return result


# 数据源列表
# 分钟线：新浪、东方财富（腾讯/同花顺不支持分钟K线）
# 日线/周线/月线：新浪、东方财富、腾讯、同花顺
_SOURCES_MINUTE = [SinaKline, EastmoneyKline]
_SOURCES_DAILY = [SinaKline, EastmoneyKline, TencentKline, THSKline]


def fetch_kline_with_fallback(code: str, period: str, source_idx: int = 0,
                              datalen: int = 1500) -> List[Dict]:
    """
    从指定数据源获取K线，失败自动切换下一个数据源。
    source_idx 用于在多线程中分散到不同数据源。
    """
    is_minute = period in ('1min', '5min', '15min', '30min', '60min')
    sources = _SOURCES_MINUTE if is_minute else _SOURCES_DAILY

    order = [sources[(source_idx + i) % len(sources)] for i in range(len(sources))]

    for src in order:
        try:
            data = src.fetch(code, period, datalen)
            if data and len(data) > 30:
                return data
        except Exception:
            continue
    return []


# ==================== 选股核心逻辑 ====================

class StrictStockScreener:
    """严格选股器 - 多周期支持，核心逻辑对齐通达信金叉.txt"""

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

    # 容差映射（对齐通达信PERIOD: 0-1min 1-5min 2-15min 3-30min 4-60min 5-日 6-周 7-月）
    TOLERANCE_MAP = {
        '1min': 9999,
        '5min': 9998,
        '15min': 9997,
        '30min': 9996,
        '60min': 9995,
        '240min': 9993,
        'weekly': 9990,
        'monthly': 9985,
    }

    def __init__(self, period: str = '240min', period_name: str = '日线',
                 max_workers: int = 8):
        self.period = period
        self.period_name = period_name
        self.tolerance = self.TOLERANCE_MAP.get(period, 9993)
        self.ma_short = 20  # MA3 in 通达信
        self.ma_long = 30   # MA4 in 通达信
        self.max_workers = max_workers

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
        在指定位置idx检查是否有买入信号（完全对齐通达信金叉.txt逻辑）
        返回: (普通买入, 严格买入, 详情)
        """
        n = len(data)
        curr = data[idx]

        # ===== 基础判断 =====
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

        dist_gold = idx - gold_cross_idx

        # ===== 第二步：死叉检测 =====
        dead_cross_idx = -1
        for j in range(idx - 1, 29, -1):
            if data[j].get('dead_cross', False):
                dead_cross_idx = j
                break

        if dead_cross_idx != -1:
            dist_dead = idx - dead_cross_idx
            if dist_gold >= dist_dead:
                return False, False, {}

        # ===== 第三步：计算阴线量的辅助函数（对齐通达信逐K线独立计算）=====
        def calc_yin_vol_at(pos):
            """在pos位置独立计算阴线量：从pos往前回看20根，找金叉后最近的阴线"""
            dist = pos - gold_cross_idx
            yv = 0
            for off in range(20, 0, -1):
                ci = pos - off
                if ci < 0:
                    continue
                if off < dist and data[ci]['is_yin']:
                    yv = data[ci]['volume']
            return yv

        # 当前K线的阴线量
        yin_vol = calc_yin_vol_at(idx)
        has_yin = yin_vol > 0
        if not has_yin:
            return False, False, {}

        # ===== 第四步：金叉日量能 =====
        gold_day_vol = data[gold_cross_idx]['volume']

        # 金叉日量能要比前7日的阴线量能高
        max_yin_vol_before_gold = 0
        for offset in range(1, 8):
            check_idx = gold_cross_idx - offset
            if check_idx >= 0 and data[check_idx]['is_yin']:
                max_yin_vol_before_gold = max(max_yin_vol_before_gold, data[check_idx]['volume'])

        gold_vol_enough = gold_day_vol > max_yin_vol_before_gold

        # ===== 第五步：检查倍量阳线（每根K线独立计算阴线量，对齐通达信）=====
        double_vol_yang_flags = []
        for k in range(gold_cross_idx + 1, idx + 1):
            k_dist_gold = k - gold_cross_idx
            k_yin_vol = calc_yin_vol_at(k)
            if (k_dist_gold > 0 and k_dist_gold <= 20 and
                    data[k]['is_yang'] and
                    k_yin_vol > 0 and
                    data[k]['volume'] >= k_yin_vol * 2 and
                    data[k]['volume'] > gold_day_vol):
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

        dist_first_double = idx - first_double_idx
        first_double_price = data[first_double_idx]['close']
        first_double_vol = data[first_double_idx]['volume']

        # ===== 放量适度（2-6倍） =====
        vol_moderate = first_double_vol < yin_vol * 6
        vol_explode = first_double_vol >= yin_vol * 6

        # ===== 阴线缩量判断 =====
        gap_days = dist_gold - dist_first_double

        # 普通阴线缩量（包含金叉日本身，对齐通达信YXM范围）
        max_yin_vol_between = 0
        for k in range(gold_cross_idx, first_double_idx):
            if data[k]['is_yin']:
                max_yin_vol_between = max(max_yin_vol_between, data[k]['volume'])

        normal_shrink = max_yin_vol_between > 0 and max_yin_vol_between < gold_day_vol * 2

        # 严格缩量（包含金叉日本身，对齐通达信YJ范围）
        strict_shrink = True
        for k in range(gold_cross_idx, first_double_idx):
            if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                strict_shrink = False
                break
        if strict_shrink:
            for k in range(first_double_idx + 1, idx):
                if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                    strict_shrink = False
                    break

        # ===== 确认阳线判断 =====
        if dist_first_double < 1 or dist_first_double > 5:
            return False, False, {}
        if dist_first_double >= dist_gold:
            return False, False, {}

        # 收盘价容差（按周期动态调整）
        if curr['close'] * 10000 < first_double_price * self.tolerance:
            return False, False, {}

        # 确认量能达标
        max_yang_vol_except_double = 0
        for k in range(gold_cross_idx + 1, idx):
            if k == first_double_idx:
                continue
            if data[k]['is_yang'] and data[k]['volume'] > max_yang_vol_except_double:
                max_yang_vol_except_double = data[k]['volume']

        if curr['volume'] <= max_yang_vol_except_double:
            return False, False, {}

        # ===== 首次确认 =====
        confirm_count = 0
        for check_i in range(gold_cross_idx + 1, idx):
            if not data[check_i]['is_yang']:
                continue
            check_dist = check_i - first_double_idx
            if check_dist < 1 or check_dist > 5:
                continue
            if check_dist >= (check_i - gold_cross_idx):
                continue
            if data[check_i]['close'] * 10000 < first_double_price * self.tolerance:
                continue
            check_max_yang = 0
            for kk in range(gold_cross_idx + 1, check_i):
                if kk == first_double_idx:
                    continue
                if data[kk]['is_yang'] and data[kk]['volume'] > check_max_yang:
                    check_max_yang = data[kk]['volume']
            if data[check_i]['volume'] <= check_max_yang:
                continue
            confirm_count += 1

        confirm_count += 1
        if confirm_count != 1:
            return False, False, {}

        # ===== 综合信号 =====
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

        normal_buy = normal_shrink and vol_moderate
        strict_buy = strict_shrink and vol_moderate and gap_days > 0 and gold_vol_enough

        details['signal_type'] = '严格' if strict_buy else ('普通' if normal_buy else '无')
        details['vol_explode'] = vol_explode

        return normal_buy, strict_buy, details

    def check_one_stock(self, code: str, source_idx: int = 0) -> Tuple[bool, bool, Dict]:
        """检查单只股票的买入信号"""
        raw = fetch_kline_with_fallback(code, self.period, source_idx)
        if not raw:
            return False, False, {}

        data = self._prepare_data(raw)
        if data is None:
            return False, False, {}

        return self._check_signal_at(data, len(data) - 1)

    def load_stock_list(self) -> List[Tuple[str, str]]:
        """从MD文件加载股票列表（含基本面过滤）"""
        md_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_list.md')

        if not os.path.exists(md_file):
            print(f"错误: 找不到股票列表文件 {md_file}")
            print("请先运行: python 更新股票列表.py")
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

                        if not code.startswith(('60', '00', '30')):
                            continue
                        if 'ST' in name or '*ST' in name:
                            continue
                        if '退' in name:
                            continue

                        stocks.append((code, name))

            print(f"加载 {len(stocks)} 只股票（已过滤ST/退市/非主板创业板）")
        except Exception as e:
            print(f"读取股票列表失败: {e}")
            sys.exit(1)

        return stocks

    def screen_all_stocks(self, stock_list: List[Tuple[str, str]]):
        """并行批量选股 - 多数据源分散请求"""
        total = len(stock_list)
        is_minute = self.period in ('1min', '5min', '15min', '30min', '60min')
        num_sources = len(_SOURCES_MINUTE) if is_minute else len(_SOURCES_DAILY)

        print(f"\n{'=' * 80}")
        print(f"  严格选股程序 - 周期: {self.period_name}")
        print(f"  待分析: {total} 只股票")
        print(f"  并行线程: {self.max_workers}  数据源: {num_sources}个")
        print(f"{'=' * 80}\n")

        normal_results = []
        strict_results = []
        error_count = 0
        completed = 0
        start_time = time.time()
        results_lock = threading.Lock()

        def process_stock(args):
            idx, code, name = args
            source_idx = idx % num_sources
            try:
                normal_signal, strict_signal, details = self.check_one_stock(code, source_idx)
                return (code, name, normal_signal, strict_signal, details, None)
            except Exception as e:
                return (code, name, False, False, {}, str(e))

        tasks = [(i, code, name) for i, (code, name) in enumerate(stock_list)]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(process_stock, task): task for task in tasks}

            for future in as_completed(futures):
                code, name, normal_signal, strict_signal, details, err = future.result()

                with results_lock:
                    completed += 1
                    if err:
                        error_count += 1

                    elapsed = time.time() - start_time
                    if completed > 1:
                        speed = completed / elapsed
                        eta = (total - completed) / speed
                        eta_str = f"预计剩余 {int(eta)}s ({speed:.1f}只/s)"
                    else:
                        eta_str = ""

                    if strict_signal:
                        strict_results.append((code, name, details))
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f">>> 严格买入信号! <<<  {eta_str}")
                    elif normal_signal:
                        normal_results.append((code, name, details))
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f">>> 普通买入信号 <<<  {eta_str}")
                    else:
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f"{eta_str:<40}", end='', flush=True)

        elapsed_total = time.time() - start_time
        speed = total / elapsed_total if elapsed_total > 0 else 0
        print(f"\r{'=' * 80}")
        print(f"  选股完成！ 用时 {elapsed_total:.1f}s  速度 {speed:.1f}只/s")
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

    results_sorted = sorted(results, key=lambda x: x[0])

    print(f"\n{'=' * 80}")
    print(f"  {title} ({period_name})  共 {len(results_sorted)} 只")
    print(f"{'=' * 80}")
    print(f"  {'代码':<8} {'名称':<10} {'信号日期':<20} {'收盘价':>8} "
          f"{'MA20':>8} {'MA30':>8} {'金叉日期':<12} {'距金叉':>5} {'距倍量':>5}")
    print(f"  {'-' * 76}")

    for code, name, d in results_sorted:
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

    max_workers = 8
    screener = StrictStockScreener(period=period, period_name=period_name,
                                   max_workers=max_workers)
    stock_list = screener.load_stock_list()

    if not stock_list:
        print("股票列表为空")
        return

    normal_results, strict_results = screener.screen_all_stocks(stock_list)

    print_results("严格买入信号", strict_results, period_name)
    print_results("普通买入信号", normal_results, period_name)

    if not normal_results and not strict_results:
        print("\n没有找到符合买入条件的股票")

    if normal_results or strict_results:
        print(f"\n{'=' * 80}")
        print(f"  汇总: 严格 {len(strict_results)} 只 + 普通 {len(normal_results)} 只 "
              f"= 共 {len(strict_results) + len(normal_results)} 只")
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
