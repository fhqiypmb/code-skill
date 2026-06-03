"""
市场环境过滤模块

设计文档: market_env_design.md (市场环境过滤设计.md)

核心思路:
  - 每轮汇总顶部插入一段"市场环境提示"
  - 不拦截扫描、不修改打分、不影响 ML
  - 健康时不显示;跌破1-5根 显示警告;持续>=6根 显示禁止

监控对象:
  主指数: 上证(000001) 深证(399001) — 任一禁止则全局禁止
  辅指数: 创业板(399006) 科创50(000688) 北证50(899050) — 仅文字提示

判断: 30min K 线 收盘价 vs MA60(滚动)
  连续 N 根低于MA60 → N=0 健康 / 1-5 警告 / >=6 禁止

数据源: 新浪 (东方财富 push2his 对指数 30min K 不稳定)
"""

import os
import sys
import json
import ssl
import urllib.request
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)

ssl._create_default_https_context = ssl._create_unverified_context

# 复用 data_source 限流器(避免新增请求源管理)
_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

try:
    from data_source import _sina_limiter, _http_get, _record_throttle  # type: ignore
except Exception:  # 单测/独立运行时降级
    _sina_limiter = None
    _http_get = None
    _record_throttle = None


# ==================== 配置 ====================

class IndexCfg(TypedDict):
    name: str       # 显示名称
    code: str       # 6 位代码
    symbol: str     # 新浪 symbol(带前缀)
    is_main: bool   # 是否主指数(影响禁止决策)


INDEX_CONFIG: List[IndexCfg] = [
    {'name': '上证',   'code': '000001', 'symbol': 'sh000001', 'is_main': True},
    {'name': '深证',   'code': '399001', 'symbol': 'sz399001', 'is_main': True},
    {'name': '创业',   'code': '399006', 'symbol': 'sz399006', 'is_main': False},
    {'name': '科创',   'code': '000688', 'symbol': 'sh000688', 'is_main': False},
    {'name': '北证',   'code': '899050', 'symbol': 'bj899050', 'is_main': False},
]

# 拉 120 根: MA60 需 60 根, 数 consec_below 留 60 根缓冲(够极端弱市数到 50+ 根)
KLINE_COUNT = 120
MA_PERIOD = 60
FORBID_THRESHOLD = 6  # 连续>=6根禁止买入


# ==================== 缓存 ====================

# 按 30min 槽位缓存(同一槽位内复用),槽切换后自动失效
_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()


def _current_slot() -> str:
    """当前 30min 槽位标识,如 '2026-05-28-10-30' 表示 10:00-10:30 这根 K"""
    now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    half = '30' if now.minute >= 30 else '00'
    return f"{now.strftime('%Y-%m-%d-%H')}-{half}"


# ==================== K 线获取 ====================

def _fetch_30min_klines(symbol: str, count: int = KLINE_COUNT) -> List[dict]:
    """
    通过新浪拉指数 30min K 线
    返回: [{'day': '2026-05-28 15:00:00', 'open': float, 'close': float, ...}, ...]
    失败返回 []
    """
    url = (
        f"https://quotes.sina.cn/cn/api/json_v2.php/"
        f"CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=30&ma=no&datalen={count}"
    )
    try:
        if _sina_limiter is not None:
            _sina_limiter.wait()

        if _http_get is not None:
            raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, retry=2)
            text = raw.decode('utf-8', errors='replace')
        else:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            })
            text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='replace')

        if text.strip() in ('null', '[]', ''):
            return []

        data = json.loads(text)
        if not isinstance(data, list):
            return []

        result: List[dict] = []
        for d in data:
            try:
                result.append({
                    'day':   d.get('day', ''),
                    'open':  float(d.get('open',  0) or 0),
                    'close': float(d.get('close', 0) or 0),
                    'high':  float(d.get('high',  0) or 0),
                    'low':   float(d.get('low',   0) or 0),
                    'volume': float(d.get('volume', 0) or 0),
                })
            except (ValueError, TypeError):
                continue
        return result
    except Exception as e:
        logger.warning(f"拉取指数 {symbol} 30min K 失败: {e}")
        if _record_throttle is not None:
            _record_throttle('market_env_sina')
        return []


# ==================== 状态判断 ====================

def _drop_unfinished_bar(klines: List[dict]) -> List[dict]:
    """
    剔除最后一根未收盘的 K 线,确保判断基于已落定的数据。

    30min K 收盘点: 10:00 / 10:30 / 11:00 / 11:30 / 13:30 / 14:00 / 14:30 / 15:00
    若最后一根的 day 时间戳 > 当前北京时间(还没到这一根的收盘时刻),则剔除。
    """
    if not klines:
        return klines
    last_day = klines[-1].get('day', '')
    if not last_day:
        return klines
    try:
        # 新浪 day 格式: '2026-05-28 15:00:00'
        bar_dt = datetime.strptime(last_day, '%Y-%m-%d %H:%M:%S')
        beijing_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        if bar_dt > beijing_now:
            return klines[:-1]
    except (ValueError, TypeError):
        pass
    return klines


def _calc_consec_below(klines: List[dict]) -> dict:
    """
    计算 30min K 序列的市场状态。

    Returns:
        {
            'level':        'healthy' | 'warning' | 'forbidden' | 'unknown',
            'consec_below': int,      # 从最新一根往前连续低于 MA60 的根数
            'close':        float,    # 最新一根收盘价
            'ma60':         float,    # 最新一根对应的 MA60
            'diff_pct':     float,    # (close - ma60) / ma60 * 100
            'has_data':     bool,
        }
    """
    empty = {
        'level': 'unknown', 'consec_below': 0,
        'close': 0.0, 'ma60': 0.0, 'diff_pct': 0.0,
        'has_data': False,
    }

    if not klines or len(klines) < MA_PERIOD + 1:
        return empty

    closes = [k['close'] for k in klines if k.get('close', 0) > 0]
    if len(closes) < MA_PERIOD + 1:
        return empty

    # 滚动 MA60: ma60_series[i] = closes[i-59:i+1] 的均值
    ma60_series: List[Optional[float]] = [None] * len(closes)
    for i in range(MA_PERIOD - 1, len(closes)):
        ma60_series[i] = sum(closes[i - MA_PERIOD + 1: i + 1]) / MA_PERIOD

    # 从最后一根往前数,连续多少根 close < 自身的 MA60
    consec = 0
    for i in range(len(closes) - 1, -1, -1):
        ma_i = ma60_series[i]
        if ma_i is None:
            break
        if closes[i] < ma_i:
            consec += 1
        else:
            break

    last_close = closes[-1]
    last_ma = ma60_series[-1] or 0.0
    diff_pct = ((last_close - last_ma) / last_ma * 100) if last_ma > 0 else 0.0

    if consec == 0:
        level = 'healthy'
    elif consec >= FORBID_THRESHOLD:
        level = 'forbidden'
    else:
        level = 'warning'

    return {
        'level':        level,
        'consec_below': consec,
        'close':        round(last_close, 2),
        'ma60':         round(last_ma, 2),
        'diff_pct':     round(diff_pct, 2),
        'has_data':     True,
    }


def calc_index_status(symbol: str) -> dict:
    """单指数完整状态(拉数据 + 剔未收盘 + 计算)"""
    klines = _fetch_30min_klines(symbol)
    klines = _drop_unfinished_bar(klines)
    return _calc_consec_below(klines)


# ==================== 全市场决策 ====================

def check_market_environment(use_cache: bool = True) -> dict:
    """
    检查 5 个指数,得出全市场环境。

    Returns:
        {
            'overall':  'healthy' | 'warning' | 'forbidden',
            'indices':  {'上证': {...}, '深证': {...}, ...},
            'has_main_data': bool,  # 主指数是否至少有一个拿到数据
        }

    决策规则:
        主指数(上证/深证)任一 forbidden → overall=forbidden
        主指数任一 warning → overall=warning
        主指数全 healthy → overall=healthy
        若主指数全部数据缺失 → has_main_data=False, overall=healthy(降级:不阻塞推送)
    """
    slot = _current_slot()

    if use_cache:
        with _cache_lock:
            cached = _cache.get(slot)
            if cached is not None:
                return cached
            # 不同槽:清掉旧数据
            _cache.clear()

    indices_status: Dict[str, dict] = {}
    for cfg in INDEX_CONFIG:
        st = calc_index_status(cfg['symbol'])
        st['is_main'] = cfg['is_main']
        indices_status[cfg['name']] = st

    main_has_data = any(
        s['has_data'] for s in indices_status.values() if s['is_main']
    )

    main_levels = [
        s['level'] for s in indices_status.values()
        if s['is_main'] and s['has_data']
    ]

    if not main_has_data:
        overall = 'healthy'  # 主数据全缺,降级为不显示警告(避免误报)
    elif 'forbidden' in main_levels:
        overall = 'forbidden'
    elif 'warning' in main_levels:
        overall = 'warning'
    else:
        overall = 'healthy'

    result = {
        'overall':       overall,
        'indices':       indices_status,
        'has_main_data': main_has_data,
        'slot':          slot,
    }

    if use_cache:
        with _cache_lock:
            _cache[slot] = result

    return result


# ==================== Markdown 生成 ====================

_LEVEL_ICON = {
    'healthy':   '✅',
    'warning':   '⚠️',
    'forbidden': '🚫',
    'unknown':   '❓',
}


def _fmt_index_line(name: str, st: dict) -> str:
    """单个指数的一行文字"""
    if not st['has_data']:
        return f"{name} ❓ 数据缺失"
    icon = _LEVEL_ICON[st['level']]
    if st['level'] == 'healthy':
        return f"{name} {icon} 健康"
    suffix = (
        f"持续 {st['consec_below']} 根" if st['level'] == 'forbidden'
        else f"跌破 {st['consec_below']} 根"
    )
    return (
        f"{name} {icon} {suffix}"
        f"(收盘 {st['close']} / MA60 {st['ma60']}, 差 {st['diff_pct']:+.2f}%)"
    )


def _fmt_aux_line(indices: Dict[str, dict]) -> str:
    """辅指数紧凑一行: '辅: 创业 ⚠️ 跌破3根 ｜ 科创 ✅ ｜ 北证 ✅'"""
    parts = []
    for cfg in INDEX_CONFIG:
        if cfg['is_main']:
            continue
        name = cfg['name']
        st = indices.get(name, {})
        if not st.get('has_data'):
            continue  # 辅指数缺数据直接省略
        icon = _LEVEL_ICON[st['level']]
        if st['level'] == 'healthy':
            parts.append(f"{name} {icon}")
        else:
            verb = '持续' if st['level'] == 'forbidden' else '跌破'
            parts.append(f"{name} {icon} {verb} {st['consec_below']} 根")
    return "辅: " + " ｜ ".join(parts) if parts else ""


def format_market_warning(env: dict) -> str:
    """
    将市场环境结果生成 markdown 提示块(健康时返回空串,不显示)。

    返回的字符串可直接 append 到 _format_round_summary 的 lines 里。
    """
    overall = env['overall']
    indices = env['indices']

    # 健康时 + 辅指数也全健康 → 完全不显示
    aux_has_problem = any(
        s.get('has_data') and s['level'] != 'healthy'
        for name, s in indices.items()
        if not s.get('is_main', False)
    )

    if overall == 'healthy' and not aux_has_problem:
        return ""

    lines: List[str] = []

    if overall == 'forbidden':
        lines.append("> 🚫 **禁止买入** | 主指数持续低于 30min MA60")
        for cfg in INDEX_CONFIG:
            if cfg['is_main']:
                lines.append("> - " + _fmt_index_line(cfg['name'], indices[cfg['name']]))
        aux = _fmt_aux_line(indices)
        if aux:
            lines.append("> - " + aux)
        lines.append(">")
        lines.append("> 📌 信号正常推送和记录,但**强烈建议不买入,等市场回到 MA60 上方再说**")

    elif overall == 'warning':
        lines.append("> ⚠️ **市场环境警告** | 主指数跌破 30min MA60")
        for cfg in INDEX_CONFIG:
            if cfg['is_main']:
                lines.append("> - " + _fmt_index_line(cfg['name'], indices[cfg['name']]))
        aux = _fmt_aux_line(indices)
        if aux:
            lines.append("> - " + aux)
        lines.append(">")
        lines.append("> 📌 信号正常推送,建议谨慎买入")

    else:
        # overall=healthy 但辅指数有问题 → 板块提示
        lines.append("> ℹ️ **板块提示** | 主指数健康,部分板块走弱")
        main_summary = " ｜ ".join(
            _fmt_index_line(cfg['name'], indices[cfg['name']])
            for cfg in INDEX_CONFIG if cfg['is_main']
        )
        lines.append(f"> - {main_summary}")
        aux = _fmt_aux_line(indices)
        if aux:
            lines.append("> - " + aux)
        lines.append(">")
        lines.append("> 📌 30/68 开头小盘股请谨慎")

    return "\n".join(lines)


# ==================== ML 埋点辅助 ====================

def env_to_ml_features(env: dict) -> Dict[str, object]:
    """
    把市场环境转成 ML 特征字段,埋到 details 里。
    shadow_learner 的 _flatten 只接受数值/bool 字段,所以这里全部用 int。

    mk_overall_code: 0=healthy / 1=warning / 2=forbidden
    """
    indices = env.get('indices', {})

    def _below(name: str) -> int:
        st = indices.get(name, {})
        return int(st.get('consec_below', 0)) if st.get('has_data') else -1

    overall_code_map = {'healthy': 0, 'warning': 1, 'forbidden': 2}
    overall_code = overall_code_map.get(env.get('overall', 'healthy'), 0)

    return {
        'mk_overall_code': overall_code,   # 0/1/2
        'mk_sh_below':     _below('上证'),  # -1 表示数据缺失
        'mk_sz_below':     _below('深证'),
        'mk_cyb_below':    _below('创业'),
        'mk_kc_below':     _below('科创'),
        'mk_bz_below':     _below('北证'),
    }


# ==================== 独立运行测试 ====================

if __name__ == "__main__":
    # Windows 控制台默认 GBK,强制 UTF-8 输出
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s')

    print("=" * 60)
    print("  市场环境检查")
    print("=" * 60)

    env = check_market_environment(use_cache=False)

    print(f"\n槽位: {env['slot']}")
    print(f"全局: {env['overall']}  (主指数有数据: {env['has_main_data']})")
    print()
    for name, st in env['indices'].items():
        tag = '主' if st['is_main'] else '辅'
        if st['has_data']:
            print(f"  [{tag}] {name:4s} {_LEVEL_ICON[st['level']]} "
                  f"连续<MA60: {st['consec_below']:2d} 根  "
                  f"收盘 {st['close']:>10.2f}  MA60 {st['ma60']:>10.2f}  "
                  f"差 {st['diff_pct']:+.2f}%")
        else:
            print(f"  [{tag}] {name:4s} ❓ 数据缺失")

    print("\n" + "=" * 60)
    print("  推送 Markdown 预览")
    print("=" * 60)
    md = format_market_warning(env)
    if md:
        print(md)
    else:
        print("(健康,不显示任何提示)")

    print("\n" + "=" * 60)
    print("  ML 埋点字段")
    print("=" * 60)
    print(env_to_ml_features(env))
