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

# ==================== 全局控制变量 ====================
# 控制状态: 'running'(运行中), 'paused'(暂停), 'stopped'(已停止)
_control_state = 'running'
_control_lock = threading.Lock()
_pause_event = threading.Event()  # set=运行中, clear=暂停
_pause_event.set()
_stop_event = threading.Event()   # set=已停止
_pause_start_time = 0.0  # 暂停开始时间
_total_paused_time = 0.0  # 累计暂停时长
_pause_time_lock = threading.Lock()

def set_control_state(state: str):
    """设置控制状态"""
    global _control_state, _pause_start_time, _total_paused_time
    with _control_lock:
        old_state = _control_state
        _control_state = state
        if state == 'paused':
            _pause_event.clear()
            with _pause_time_lock:
                _pause_start_time = time.time()
        elif state == 'running':
            _pause_event.set()
            if old_state == 'paused':
                with _pause_time_lock:
                    _total_paused_time += time.time() - _pause_start_time
        elif state == 'stopped':
            _stop_event.set()
            _pause_event.set()  # 解除暂停等待，让线程能退出

def get_control_state() -> str:
    """获取当前控制状态"""
    with _control_lock:
        return _control_state

def get_total_paused_time() -> float:
    """获取累计暂停时长（秒）"""
    with _pause_time_lock:
        extra = 0.0
        if get_control_state() == 'paused':
            extra = time.time() - _pause_start_time
        return _total_paused_time + extra

def reset_control():
    """重置控制状态（每次选股前调用）"""
    global _control_state, _pause_start_time, _total_paused_time
    with _control_lock:
        _control_state = 'running'
    _pause_event.set()
    _stop_event.clear()
    with _pause_time_lock:
        _pause_start_time = 0.0
        _total_paused_time = 0.0

def check_control():
    """检查控制状态，如果是暂停则等待，如果是停止则抛出StopIteration"""
    if _stop_event.is_set():
        raise StopIteration("用户停止")
    # 如果暂停，阻塞等待直到恢复或停止
    _pause_event.wait()
    if _stop_event.is_set():
        raise StopIteration("用户停止")

# ==================== 键盘监听线程 ====================
def keyboard_listener():
    """键盘监听线程，监听空格键暂停/继续，Q键停止"""
    try:
        import msvcrt  # Windows only
        while not _stop_event.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b' ':  # 空格键 - 暂停/继续
                    state = get_control_state()
                    if state == 'running':
                        set_control_state('paused')
                        with _print_lock:
                            print("\n")
                            print("  " + "=" * 50)
                            print("  ⏸  已暂停")
                            print("  按 [空格] 继续  |  按 [Q] 停止并输出结果")
                            print("  " + "=" * 50)
                    elif state == 'paused':
                        set_control_state('running')
                        with _print_lock:
                            print("  ▶  继续执行...\n")
                elif key in (b'q', b'Q'):  # Q键 - 停止
                    set_control_state('stopped')
                    with _print_lock:
                        print("\n  ⏹  用户停止，正在输出已收集的结果...")
                    break
                elif key == b'\x1b':  # ESC键 - 也可以停止
                    set_control_state('stopped')
                    with _print_lock:
                        print("\n  ⏹  用户停止，正在输出已收集的结果...")
                    break
            time.sleep(0.05)
    except ImportError:
        pass  # 非Windows平台，忽略
    except Exception:
        pass

# ==================== 新浪限流测试 ====================
def test_sina_rate_limit():
    """测试新浪数据源限流情况"""
    print("\n" + "=" * 60)
    print("  新浪数据源限流测试")
    print("=" * 60)
    print("  测试方法：连续请求10次，观察响应情况")
    print()

    test_code = '000001'
    period = '240min'
    success_count = 0
    throttle_count = 0
    errors = []

    for i in range(10):
        try:
            start = time.time()
            data = SinaKline.fetch(test_code, period, 50)
            elapsed = time.time() - start
            if data and len(data) > 10:
                success_count += 1
                status = "✓ 成功"
            else:
                status = "✗ 空数据"
            print(f"  请求 {i+1:2d}/10: {status} ({elapsed:.2f}s)")
        except Exception as e:
            err_str = str(e)
            errors.append(err_str)
            if '456' in err_str or '403' in err_str or '429' in err_str:
                throttle_count += 1
                status = "✗ 限流"
            else:
                status = f"✗ 错误"
            print(f"  请求 {i+1:2d}/10: {status} - {err_str[:40]}")
        time.sleep(0.1)  # 100ms间隔

    print()
    print(f"  成功: {success_count}/10")
    print(f"  限流: {throttle_count}/10")
    if errors:
        print(f"  错误类型: {set(errors)}")
    print("=" * 60)
    print()
    return success_count == 10


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
# 分钟线：仅新浪（东方财富频繁限流已去除，腾讯/同花顺不支持分钟K线）
# 日线/周线/月线：新浪、同花顺（东方财富频繁限流已去除，腾讯DNS不通已去除）
_SOURCES_MINUTE = [SinaKline]
_SOURCES_DAILY = [SinaKline, THSKline]


# 限流计数器（线程安全）
_throttle_counts = {}  # {数据源名: 次数}
_throttle_lock = threading.Lock()


def _record_throttle(src_name: str):
    with _throttle_lock:
        _throttle_counts[src_name] = _throttle_counts.get(src_name, 0) + 1


def get_throttle_summary() -> str:
    """返回限流统计摘要，无限流返回空字符串"""
    with _throttle_lock:
        if not _throttle_counts:
            return ""
        parts = [f"{name} {cnt}次" for name, cnt in _throttle_counts.items()]
        return "数据源限流: " + ", ".join(parts)


def reset_throttle_counts():
    with _throttle_lock:
        _throttle_counts.clear()


class SourceRateLimiter:
    """每个数据源独立的速率限制器（令牌桶算法）"""

    def __init__(self, max_per_sec: float = 3.0):
        self._limiters = {}  # {源名: {'lock': Lock, 'last_time': float, 'interval': float, 'backoff': float}}
        self._global_lock = threading.Lock()
        self._interval = 1.0 / max_per_sec  # 每次请求的最小间隔

    def _get_limiter(self, src_name: str) -> dict:
        if src_name not in self._limiters:
            with self._global_lock:
                if src_name not in self._limiters:
                    self._limiters[src_name] = {
                        'lock': threading.Lock(),
                        'last_time': 0.0,
                        'backoff': 0.0,  # 被限流后的额外退避时间
                    }
        return self._limiters[src_name]

    def wait(self, src_name: str):
        """请求前调用，会阻塞直到满足速率限制"""
        lim = self._get_limiter(src_name)
        with lim['lock']:
            now = time.time()
            wait_interval = self._interval + lim['backoff']
            elapsed = now - lim['last_time']
            if elapsed < wait_interval:
                time.sleep(wait_interval - elapsed)
            lim['last_time'] = time.time()

    def report_throttled(self, src_name: str):
        """报告某数据源被限流，增加退避时间（最大8秒）"""
        lim = self._get_limiter(src_name)
        with lim['lock']:
            if lim['backoff'] < 0.5:
                lim['backoff'] = 2.0
            else:
                lim['backoff'] = min(lim['backoff'] * 2, 8.0)

    def report_success(self, src_name: str):
        """请求成功，逐步减少退避时间"""
        lim = self._get_limiter(src_name)
        with lim['lock']:
            if lim['backoff'] > 0:
                lim['backoff'] = max(lim['backoff'] * 0.5, 0.0)
                if lim['backoff'] < 0.1:
                    lim['backoff'] = 0.0


# 全局速率限制器：每个数据源每秒最多2次请求（新浪单源需更保守）
_rate_limiter = SourceRateLimiter(max_per_sec=2.0)


def fetch_kline_with_fallback(code: str, period: str, source_idx: int = 0,
                              datalen: int = 1500) -> List[Dict]:
    """
    从指定数据源获取K线，失败自动切换下一个数据源。
    source_idx 用于在多线程中分散到不同数据源。
    每个数据源请求前会受速率限制，被限流后自动指数退避。
    """
    is_minute = period in ('1min', '5min', '15min', '30min', '60min')
    sources = _SOURCES_MINUTE if is_minute else _SOURCES_DAILY

    order = [sources[(source_idx + i) % len(sources)] for i in range(len(sources))]

    for src in order:
        src_name = src.__name__
        try:
            _rate_limiter.wait(src_name)  # 等待速率限制
            data = src.fetch(code, period, datalen)
            if data and len(data) > 30:
                _rate_limiter.report_success(src_name)  # 成功，减少退避
                return data
        except Exception as e:
            err_str = str(e)
            # 检测限流：HTTP 456(新浪)、连接断开(东财)、403等
            if '456' in err_str or 'RemoteDisconnected' in err_str or '403' in err_str or '429' in err_str:
                _record_throttle(src_name)
                _rate_limiter.report_throttled(src_name)  # 限流，增加退避
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
        """清洗并预计算K线数据：MA、金叉、死叉、阴阳线、MA5止跌、底部企稳"""
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
            data[i]['ma5'] = (sum(closes[i - 4:i + 1]) / 5) if i >= 4 else None

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
                # TDX CROSS(A,B) = prev_A <= prev_B AND curr_A > curr_B
                data[i]['gold_cross'] = (prev['ma20'] <= prev['ma30'] and curr['ma20'] > curr['ma30'])
                data[i]['dead_cross'] = (prev['ma20'] >= prev['ma30'] and curr['ma20'] < curr['ma30'])

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
        # TDX BARSLAST(金叉日) 在金叉当天返回0，所以搜索范围包含idx自身
        gold_cross_idx = -1
        for j in range(idx, 29, -1):
            if data[j].get('gold_cross', False):
                gold_cross_idx = j
                break

        if gold_cross_idx == -1:
            return False, False, {}

        dist_gold = idx - gold_cross_idx

        # ===== 第二步：死叉检测 =====
        # TDX BARSLAST(死叉日) 同理，搜索范围包含idx自身
        dead_cross_idx = -1
        for j in range(idx, 29, -1):
            if data[j].get('dead_cross', False):
                dead_cross_idx = j
                break

        if dead_cross_idx != -1:
            dist_dead = idx - dead_cross_idx
            # TDX: 金叉后无死叉:=距金叉天数<距死叉天数
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

        # ===== 辅助函数：在任意位置pos计算倍量阳标记列表和首倍量位置 =====
        def find_first_double_at(pos):
            """
            在pos位置计算：倍量阳标记 → 首倍量位置
            完全对齐TDX逐K线独立计算逻辑
            返回: first_double_idx 或 -1
            """
            pos_dist_gold = pos - gold_cross_idx
            dv_flags = []
            for k in range(gold_cross_idx + 1, pos + 1):
                kd = k - gold_cross_idx
                kv = calc_yin_vol_at(k)
                if (kd > 0 and kd <= 20 and
                        data[k]['is_yang'] and
                        kv > 0 and
                        data[k]['volume'] >= kv * 2 and
                        data[k]['volume'] > gold_day_vol):
                    dv_flags.append(k)

            # TDX 首倍量: 倍量阳 AND 前10根都不是倍量阳
            # BARSLAST(首倍量) 取最近的首倍量
            fd_idx = -1
            for k in dv_flags:
                is_first = True
                for prev_k in range(max(k - 10, gold_cross_idx + 1), k):
                    if prev_k in dv_flags:
                        is_first = False
                        break
                if is_first:
                    fd_idx = k  # 不break，让更近的覆盖（对齐BARSLAST）
            return fd_idx

        # ===== 辅助函数：在任意位置pos判断是否为确认阳 =====
        def is_confirm_yang_at(pos):
            """
            在pos位置独立计算确认阳条件（对齐TDX逐K线独立计算）
            返回: True/False
            """
            if not data[pos]['is_yang']:
                return False

            fd_idx = find_first_double_at(pos)
            if fd_idx == -1:
                return False

            pos_dist_fd = pos - fd_idx
            pos_dist_gold = pos - gold_cross_idx
            fd_price = data[fd_idx]['close']

            # 距首倍>=1 AND 距首倍<=5 AND 距首倍<距金叉天数
            if pos_dist_fd < 1 or pos_dist_fd > 5:
                return False
            if pos_dist_fd >= pos_dist_gold:
                return False

            # 收盘价容差
            if data[pos]['close'] * 10000 < fd_price * self.tolerance:
                return False

            # 确认量能达标（QRY: N<距金叉天数 AND N<>距首倍）
            max_yang_vol = 0
            for n in range(1, 21):
                kk = pos - n
                if kk < 0:
                    continue
                if pos_dist_gold <= n:  # N < 距金叉天数
                    continue
                if kk == fd_idx:  # N <> 距首倍
                    continue
                if data[kk]['is_yang']:
                    max_yang_vol = max(max_yang_vol, data[kk]['volume'])

            if data[pos]['volume'] <= max_yang_vol:
                return False

            return True

        # ===== 在当前位置idx计算首倍量 =====
        first_double_idx = find_first_double_at(idx)
        if first_double_idx == -1:
            return False, False, {}

        dist_first_double = idx - first_double_idx
        first_double_price = data[first_double_idx]['close']
        first_double_vol = data[first_double_idx]['volume']

        # ===== 放量适度（2-6倍） =====
        # TDX: 首倍量能 < 阴线量*6，这里阴线量是当前K线(idx)的阴线量
        vol_moderate = first_double_vol < yin_vol * 6
        vol_explode = first_double_vol >= yin_vol * 6

        # ===== 阴线缩量判断 =====
        gap_days = dist_gold - dist_first_double

        # 普通阴线缩量（对齐通达信YXM：从首倍量位置往金叉方向看，最多20根）
        max_yin_vol_between = 0
        for n in range(1, 21):
            k = first_double_idx - n
            if k < 0:
                continue
            if n == 1:
                if not (1 <= gap_days):  # YXM1用<=
                    continue
            else:
                if not (n < gap_days):  # YXM2~20用<
                    continue
            if data[k]['is_yin']:
                max_yin_vol_between = max(max_yin_vol_between, data[k]['volume'])

        normal_shrink = max_yin_vol_between > 0 and max_yin_vol_between < gold_day_vol * 2

        # 严格缩量（对齐通达信YJ范围）
        strict_shrink = True
        # 第一部分：YJ1~YJ20
        for n in range(1, 21):
            k = first_double_idx - n
            if k < 0:
                continue
            if n == 1:
                if not (1 <= gap_days):
                    continue
            else:
                if not (n < gap_days):
                    continue
            if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                strict_shrink = False
                break
        # 第二部分：YZ1~YZ5
        if strict_shrink:
            for n in range(1, 6):
                k = idx - n
                if k < 0:
                    continue
                if not (n < dist_first_double):  # N<距首倍
                    continue
                if data[k]['is_yin'] and data[k]['volume'] >= gold_day_vol:
                    strict_shrink = False
                    break

        # ===== 确认阳线判断（当前K线idx）=====
        if not is_confirm_yang_at(idx):
            return False, False, {}

        # ===== 首次确认（对齐通达信 COUNT(确认阳, 距金叉天数+1)=1）=====
        # TDX中每根K线的确认阳都是独立计算的（阴线量、倍量阳、首倍量、首倍价、QRY都重算）
        confirm_count = 0
        for check_i in range(gold_cross_idx + 1, idx):
            if is_confirm_yang_at(check_i):
                confirm_count += 1

        confirm_count += 1  # 加上当前K线(idx)自身
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

        # ===== MA5止跌：MA5 >= 20天前的MA5 =====
        ma5_rising = False
        if idx >= 24 and curr.get('ma5') is not None and data[idx - 20].get('ma5') is not None:
            ma5_rising = curr['ma5'] >= data[idx - 20]['ma5']

        # ===== 底部企稳：30日最低价 >= 120日最低价 =====
        bottom_stable = False
        if idx >= 119:
            low_30 = min(data[k]['low'] for k in range(idx - 29, idx + 1))
            low_120 = min(data[k]['low'] for k in range(idx - 119, idx + 1))
            bottom_stable = low_30 >= low_120

        strict_buy = (strict_shrink and vol_moderate and gap_days > 0
                      and gold_vol_enough and ma5_rising and bottom_stable)

        details['ma5_rising'] = ma5_rising
        details['bottom_stable'] = bottom_stable
        details['signal_type'] = '严格' if strict_buy else ('普通' if normal_buy else '无')
        details['vol_explode'] = vol_explode

        return normal_buy, strict_buy, details

    def check_one_stock(self, code: str, source_idx: int = 0) -> Tuple[bool, bool, Dict, str]:
        """检查单只股票的买入信号，返回(普通买入, 严格买入, 详情, 最后一根K线时间)"""
        raw = fetch_kline_with_fallback(code, self.period, source_idx)
        if not raw:
            return False, False, {}, None

        data = self._prepare_data(raw)
        if data is None:
            return False, False, {}, None

        normal_buy, strict_buy, details = self._check_signal_at(data, len(data) - 1)
        last_bar_time = data[-1]['date'] if data else None
        return normal_buy, strict_buy, details, last_bar_time

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

    def _check_sources(self):
        """开始前测试各数据源是否可用"""
        is_minute = self.period in ('1min', '5min', '15min', '30min', '60min')
        sources = _SOURCES_MINUTE if is_minute else _SOURCES_DAILY
        test_code = '000001'  # 用平安银行测试
        ok_list = []
        fail_list = []
        for src in sources:
            try:
                data = src.fetch(test_code, self.period, 50)
                if data and len(data) > 10:
                    ok_list.append(src.__name__)
                else:
                    fail_list.append((src.__name__, '返回数据为空'))
            except Exception as e:
                err = str(e)
                if '456' in err:
                    fail_list.append((src.__name__, '限流(456)'))
                elif 'RemoteDisconnected' in err:
                    fail_list.append((src.__name__, '连接被断开'))
                elif '403' in err:
                    fail_list.append((src.__name__, '拒绝访问(403)'))
                else:
                    fail_list.append((src.__name__, err[:40]))
        return ok_list, fail_list

    def screen_all_stocks(self, stock_list: List[Tuple[str, str]]):
        """并行批量选股 - 多数据源分散请求"""
        total = len(stock_list)
        is_minute = self.period in ('1min', '5min', '15min', '30min', '60min')
        num_sources = len(_SOURCES_MINUTE) if is_minute else len(_SOURCES_DAILY)

        print(f"\n{'=' * 80}")
        print(f"  严格选股程序 - 周期: {self.period_name}")
        print(f"  待分析: {total} 只股票")
        print(f"  并行线程: {self.max_workers}  数据源: {num_sources}个")
        print(f"  速率限制: 每数据源 ≤3次/秒，限流自动退避(2-8s)")

        # 测试数据源可用性
        ok_list, fail_list = self._check_sources()
        if ok_list:
            print(f"  ✔ 可用: {', '.join(ok_list)}")
        if fail_list:
            for name, reason in fail_list:
                print(f"  ✘ 不可用: {name} - {reason}")
        if not ok_list:
            print(f"  ⚠ 所有数据源均不可用，无法选股！")
            print(f"{'=' * 80}\n")
            return [], []

        print(f"{'=' * 80}\n")

        normal_results = []
        strict_results = []
        error_count = 0
        completed = 0
        start_time = time.time()
        results_lock = threading.Lock()
        reference_time = None  # 基准时间（第一只成功获取数据的股票的最后一根K线时间）
        time_mismatch_count = 0  # 时间不一致计数

        def process_stock(args):
            idx, code, name = args
            source_idx = idx % num_sources
            try:
                # 任务开始前检查控制状态（暂停时阻塞，停止时跳过）
                check_control()
                normal_signal, strict_signal, details, last_bar = self.check_one_stock(code, source_idx)
                return (code, name, normal_signal, strict_signal, details, last_bar, None)
            except StopIteration:
                return (code, name, False, False, {}, None, '__stopped__')
            except Exception as e:
                return (code, name, False, False, {}, None, str(e))

        tasks = [(i, code, name) for i, (code, name) in enumerate(stock_list)]

        # 重置控制状态
        reset_control()
        stopped_early = False

        # 启动键盘监听线程
        keyboard_thread = threading.Thread(target=keyboard_listener, daemon=True)
        keyboard_thread.start()

        print(f"  提示: 按 [空格] 暂停/继续  |  按 [Q] 或 [ESC] 停止并输出结果\n")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(process_stock, task): task for task in tasks}

            for future in as_completed(futures):
                # 检查控制状态
                try:
                    check_control()
                except StopIteration:
                    stopped_early = True
                    # 取消尚未开始的任务
                    for f in futures:
                        f.cancel()
                    break

                code, name, normal_signal, strict_signal, details, last_bar, err = future.result()

                # 跳过被停止的任务
                if err == '__stopped__':
                    continue

                with results_lock:
                    completed += 1
                    if err:
                        error_count += 1

                    # 设置基准时间（第一只成功获取数据的股票）
                    if reference_time is None and last_bar is not None:
                        reference_time = last_bar
                        print(f"\n  基准时间: {reference_time}")

                    # 检查信号时间是否与基准时间一致
                    signal_time = details.get('date') if details else None
                    time_mismatch = False
                    if reference_time and signal_time and signal_time != reference_time:
                        time_mismatch = True
                        time_mismatch_count += 1

                    # 计算速度时扣除暂停时间
                    elapsed = time.time() - start_time - get_total_paused_time()
                    if completed > 1 and elapsed > 0:
                        speed = completed / elapsed
                        eta = (total - completed) / speed
                        eta_str = f"预计剩余 {int(eta)}s ({speed:.1f}只/s)"
                    else:
                        eta_str = ""

                    if strict_signal:
                        strict_results.append((code, name, details))
                        mismatch_tag = " [日期为前一天]" if time_mismatch else ""
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f">>> 严格买入信号! <<< {mismatch_tag} {eta_str}")
                    elif normal_signal:
                        normal_results.append((code, name, details))
                        mismatch_tag = " [日期为前一天]" if time_mismatch else ""
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f">>> 普通买入信号 <<< {mismatch_tag} {eta_str}")
                    else:
                        with _print_lock:
                            print(f"\r[{completed}/{total}] {code} {name:<10} "
                                  f"{eta_str:<40}", end='', flush=True)

        elapsed_total = time.time() - start_time
        paused_total = get_total_paused_time()
        active_time = elapsed_total - paused_total
        speed = completed / active_time if active_time > 0 else 0

        # 注：每只股票只检查其最后一根K线（data[-1]）
        # 信号本身就是最后一根K线的信号，无需额外过滤

        print(f"\r{'=' * 80}")
        if stopped_early:
            print(f"  选股被用户停止  已完成 {completed}/{total} 只")
        else:
            print(f"  选股完成！")
        time_info = f"用时 {active_time:.1f}s  速度 {speed:.1f}只/s"
        if paused_total > 1:
            time_info += f"  (暂停 {paused_total:.1f}s)"
        print(f"  {time_info}")
        if reference_time:
            print(f"  基准时间: {reference_time}")
        if time_mismatch_count > 0:
            print(f"  ⚠ 日期不一致: {time_mismatch_count} 只 (数据非最新)")
        print(f"  严格买入: {len(strict_results)} 只")
        print(f"  普通买入: {len(normal_results)} 只")
        if error_count > 0:
            print(f"  请求失败: {error_count} 只")
        throttle_info = get_throttle_summary()
        if throttle_info:
            print(f"  {throttle_info}")
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
    print("-" * 50)
    print("  运行时控制：")
    print("    空格  - 暂停 / 继续")
    print("    Q     - 停止并输出已收集的结果")
    print("    ESC   - 停止并输出已收集的结果")
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

    # 分钟线只有新浪一个源，降低并发避免限流；日线有两个源可以稍高
    is_minute = period in ('1min', '5min', '15min', '30min', '60min')
    max_workers = 4 if is_minute else 6
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
