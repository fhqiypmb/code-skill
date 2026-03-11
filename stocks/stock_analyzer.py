"""
个股分析模块 - 目标导向版（短期涨幅 ≥10%）

核心逻辑：
  1. 技术目标价计算（压力位 / ATR通道 / 斐波那契扩展，三法取中）
  2. 止损价：金叉确认阳线低点（即选股买入区低点）
  3. 空间判断：目标涨幅 ≥10% 才标记达标，这是最低卖出考量线
  4. 趋势强度评分（均线排列 + 量价配合 + MACD动量）
  5. 市场位置评分（个股相对强度50% + 换手率强度50%）
     - 相对强度：个股涨幅 vs 对应基准指数（上证/深证/创业板/科创板）
     - 换手率强度：今日成交量 vs 近20日均量（量比）
  6. 主力资金方向（辅助）
  7. 成功率评分（优中选优，5个维度）
     - 突破质量（25%）：金叉信号放量倍数、均线开口、站上MA20
     - 趋势动能（25%）：MACD方向、均线排列完整度、近期涨速
     - 相对强度（20%）：跑赢基准指数幅度
     - 资金持续性（20%）：主力净买入 + 量比共振
     - 风险收益比（10%）：目标涨幅 / 止损幅度

输出重心：
  - 现价 / 目标价 / 止损价 / 预期涨幅
  - 成功率等级（S/A/B/C/D）及各维度得分
  - 趋势强度（强 / 中 / 弱）
  - 市场位置（相对强度 + 量比）
  - 综合建议（达标 / 空间不足 / 趋势偏弱）
"""

import sys
import time
import logging
import concurrent.futures
from typing import Dict, List, Tuple, Optional, TypedDict

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import data_source
from data_source import KLineBar, QuoteInfo, CapitalFlow

logger = logging.getLogger(__name__)


# ==================== TypedDict ====================

class TechnicalTarget(TypedDict):
    current_price: float
    target_price: float
    stop_loss: float
    expected_gain_pct: float
    stop_loss_pct: float
    space_ok: bool
    method_targets: Dict[str, float]
    atr: float


class TrendStrength(TypedDict):
    score: float
    level: str                 # 强 / 中 / 弱
    ma_align: bool
    vol_price_ok: bool
    macd_positive: bool
    detail: Dict[str, float]


class MarketPosition(TypedDict):
    score: float               # 市场位置总分 [0, 100]
    level: str                 # 强 / 中 / 弱
    relative_strength: float   # 个股涨幅 - 基准指数涨幅（%，近5日）
    rs_score: float            # 相对强度评分 [0, 100]
    vol_ratio: float           # 量比（今日量 / 近20日均量）
    vr_score: float            # 量比评分 [0, 100]
    benchmark: str             # 基准指数代码
    benchmark_name: str        # 基准指数名称


class SuccessRate(TypedDict):
    score: float               # 综合成功率 [0, 100]
    grade: str                 # S / A / B / C / D
    dim_breakout: float        # 突破质量得分 [0,100]
    dim_momentum: float        # 趋势动能得分 [0,100]
    dim_rs: float              # 相对强度得分 [0,100]
    dim_capital: float         # 资金持续性得分 [0,100]
    dim_rr: float              # 风险收益比得分 [0,100]


class AnalysisResult(TypedDict):
    code: str
    name: str
    industry: str
    concepts: List[str]
    quote: QuoteInfo
    capital: CapitalFlow
    technical: TechnicalTarget
    trend: TrendStrength
    market_pos: MarketPosition
    success_rate: SuccessRate
    capital_confirmed: bool    # 今日主力是否净买入
    verdict: str               # 达标 / 空间不足 / 趋势偏弱
    signal_type: str


# ==================== 1. 技术目标价计算 ====================

def _calc_atr(klines: List[KLineBar], period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.0
    trs: List[float] = []
    bars = klines[-(period + 1):]
    for i in range(1, len(bars)):
        high = float(bars[i]['high'])
        low  = float(bars[i]['low'])
        prev_close = float(bars[i - 1]['close'])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _resistance_target(klines: List[KLineBar], current: float, lookback: int = 60) -> float:
    """压力位法：近 lookback 根 K 线中高于当前价的最低高点"""
    if not klines:
        return current * 1.10
    recent = klines[-lookback:] if len(klines) >= lookback else klines
    highs = [float(k['high']) for k in recent]
    resistances = [h for h in highs if h > current * 1.01]
    return min(resistances) if resistances else max(highs)


def _atr_channel_target(current: float, atr: float, multiplier: float = 3.0) -> float:
    """ATR 通道法：当前价 + N 倍 ATR"""
    return current + atr * multiplier if atr > 0 else current * 1.12


def _fib_extension_target(klines: List[KLineBar], current: float, lookback: int = 60) -> float:
    """斐波那契扩展法：波段低点起 1.618 扩展"""
    if len(klines) < 10:
        return current * 1.10
    recent = klines[-lookback:] if len(klines) >= lookback else klines
    swing_low = min(float(k['low']) for k in recent)
    wave = current - swing_low
    return swing_low + wave * 1.618 if wave > 0 else current * 1.10


def calc_target_price(klines: List[KLineBar], current_price: float) -> TechnicalTarget:
    """三法取中位数计算目标价，限制在 [current*1.05, current*1.40]"""
    if not klines or current_price <= 0:
        return {
            'current_price': current_price,
            'target_price': round(current_price * 1.10, 2),
            'stop_loss': round(current_price * 0.95, 2),
            'expected_gain_pct': 10.0,
            'stop_loss_pct': -5.0,
            'space_ok': True,
            'method_targets': {},
            'atr': 0.0,
        }

    atr = _calc_atr(klines)
    t_r = _resistance_target(klines, current_price)
    t_a = _atr_channel_target(current_price, atr)
    t_f = _fib_extension_target(klines, current_price)

    target = sorted([t_r, t_a, t_f])[1]
    target = max(target, current_price * 1.05)
    target = min(target, current_price * 1.40)
    target = round(target, 2)

    recent5 = klines[-5:] if len(klines) >= 5 else klines
    stop = max(min(float(k['low']) for k in recent5), current_price * 0.85)
    stop = round(stop, 2)

    expected_gain = round((target - current_price) / current_price * 100, 1)
    stop_loss_pct = round((stop - current_price) / current_price * 100, 1)

    return {
        'current_price': current_price,
        'target_price': target,
        'stop_loss': stop,
        'expected_gain_pct': expected_gain,
        'stop_loss_pct': stop_loss_pct,
        'space_ok': expected_gain >= 10.0,
        'method_targets': {
            '压力位法': round(t_r, 2),
            'ATR通道法': round(t_a, 2),
            '斐波那契': round(t_f, 2),
        },
        'atr': round(atr, 3),
    }


# ==================== 2. 趋势强度评分 ====================

def _ma(closes: List[float], n: int) -> Optional[float]:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def calc_trend_strength(klines: List[KLineBar]) -> TrendStrength:
    """均线排列(40%) + 量价配合(35%) + MACD动量(25%)"""
    if not klines or len(klines) < 35:
        return {
            'score': 50.0, 'level': '中',
            'ma_align': False, 'vol_price_ok': False, 'macd_positive': False,
            'detail': {'ma_align': 50.0, 'vol_price': 50.0, 'macd': 50.0},
        }

    closes  = [float(k['close']) for k in klines]
    volumes = [float(k['volume']) for k in klines]

    # ---- 均线排列 ----
    ma5  = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma30 = _ma(closes, 30)

    ma_score = 30.0
    ma_align = False
    if ma5 and ma10 and ma20 and ma30:
        if ma5 > ma10 > ma20 > ma30:
            ma_score, ma_align = 100.0, True
        elif ma5 > ma10 > ma20:
            ma_score, ma_align = 80.0, True
        elif ma5 > ma10:
            ma_score = 60.0
        elif ma5 < ma10 < ma20:
            ma_score = 10.0
    if ma20 and closes[-1] > ma20:
        ma_score = min(ma_score + 10, 100.0)

    # ---- 量价配合 ----
    recent10 = klines[-10:] if len(klines) >= 10 else klines
    up_bars   = [k for k in recent10 if float(k['close']) > float(k['open'])]
    down_bars = [k for k in recent10 if float(k['close']) < float(k['open'])]
    avg_up_v  = sum(float(k['volume']) for k in up_bars) / len(up_bars) if up_bars else 0
    avg_dn_v  = sum(float(k['volume']) for k in down_bars) / len(down_bars) if down_bars else 1
    vp_ratio  = avg_up_v / avg_dn_v if avg_dn_v > 0 else 1.0

    avg_vol_5  = sum(volumes[-5:]) / 5   if len(volumes) >= 5  else volumes[-1]
    avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else avg_vol_5
    vol_trend  = avg_vol_5 / avg_vol_20  if avg_vol_20 > 0 else 1.0

    vp_score = 50.0
    if vp_ratio >= 2.0:   vp_score += 35
    elif vp_ratio >= 1.5: vp_score += 20
    elif vp_ratio >= 1.2: vp_score += 10
    elif vp_ratio < 0.8:  vp_score -= 20
    if vol_trend >= 1.5:   vp_score += 15
    elif vol_trend >= 1.1: vp_score += 8
    elif vol_trend < 0.7:  vp_score -= 10
    vp_score = max(0.0, min(100.0, vp_score))
    vol_price_ok = vp_ratio >= 1.2

    # ---- MACD 动量 ----
    def _ema(data: List[float], period: int) -> List[float]:
        k = 2 / (period + 1)
        r = [data[0]]
        for v in data[1:]:
            r.append(v * k + r[-1] * (1 - k))
        return r

    macd_score = 50.0
    macd_positive = False
    if len(closes) >= 26:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = [a - b for a, b in zip(ema12, ema26)]
        dea = _ema(dif, 9) if len(dif) >= 9 else dif
        curr_dif, curr_dea = dif[-1], dea[-1]
        prev_dif = dif[-2] if len(dif) >= 2 else curr_dif
        if curr_dif > 0 and curr_dea > 0:
            macd_score, macd_positive = 80.0, True
        elif curr_dif > 0:
            macd_score, macd_positive = 65.0, True
        elif curr_dif <= 0 and curr_dea <= 0:
            macd_score = 25.0
        macd_score += 15 if curr_dif > prev_dif else -10
        macd_score = max(0.0, min(100.0, macd_score))

    total = round(ma_score * 0.40 + vp_score * 0.35 + macd_score * 0.25, 1)
    total = max(0.0, min(100.0, total))
    level = '强' if total >= 70 else ('中' if total >= 45 else '弱')

    return {
        'score': total, 'level': level,
        'ma_align': ma_align, 'vol_price_ok': vol_price_ok, 'macd_positive': macd_positive,
        'detail': {'ma_align': round(ma_score, 1), 'vol_price': round(vp_score, 1), 'macd': round(macd_score, 1)},
    }


# ==================== 3. 市场位置（相对强度 + 量比） ====================

# 基准指数映射：按股票代码前缀匹配
_BENCHMARK_MAP: Dict[str, Tuple[str, str]] = {
    '30': ('399006', '创业板指'),
    '68': ('000688', '科创50'),
    '00': ('399001', '深证成指'),
}
_DEFAULT_BENCHMARK: Tuple[str, str] = ('000001', '上证指数')


def _get_benchmark(code: str) -> Tuple[str, str]:
    for prefix, info in _BENCHMARK_MAP.items():
        if code.startswith(prefix):
            return info
    return _DEFAULT_BENCHMARK


def _score_relative_strength(klines: List[KLineBar],
                              benchmark_code: str) -> Tuple[float, float]:
    """
    个股相对强度评分。
    用个股近5日涨幅 - 基准指数近5日涨幅，差值即相对强度。
    返回 (评分[0,100], 相对强度%)
    """
    if not klines or len(klines) < 6:
        return 50.0, 0.0
    try:
        idx_klines = data_source.fetch_index_kline(benchmark_code, 15)
        if not idx_klines or len(idx_klines) < 6:
            return 50.0, 0.0
    except Exception:
        return 50.0, 0.0

    stock_closes = [float(k['close']) for k in klines]
    stock_chg5 = (stock_closes[-1] - stock_closes[-6]) / stock_closes[-6] * 100

    idx_closes = [k['close'] for k in idx_klines]
    idx_chg5 = (idx_closes[-1] - idx_closes[-6]) / idx_closes[-6] * 100

    rs = round(stock_chg5 - idx_chg5, 2)

    if rs >= 10:    score = 100.0
    elif rs >= 5:   score = 85.0
    elif rs >= 2:   score = 70.0
    elif rs >= 0:   score = 55.0
    elif rs >= -2:  score = 40.0
    elif rs >= -5:  score = 25.0
    else:           score = 10.0

    return round(score, 1), rs


def _score_vol_ratio(klines: List[KLineBar], today_volume: float) -> Tuple[float, float]:
    """
    量比评分（换手率强度近似）。
    量比 = 今日成交量 / 近20日日均成交量。
    返回 (评分[0,100], 量比)
    """
    if not klines or len(klines) < 5:
        return 50.0, 1.0

    hist = klines[-20:] if len(klines) >= 20 else klines
    avg_vol = sum(float(k['volume']) for k in hist) / len(hist)
    if avg_vol <= 0:
        return 50.0, 1.0

    vol = today_volume if today_volume > 0 else float(klines[-1]['volume'])
    vr = round(vol / avg_vol, 2)

    if vr >= 3.0:    score = 95.0
    elif vr >= 2.0:  score = 80.0
    elif vr >= 1.5:  score = 68.0
    elif vr >= 1.0:  score = 55.0
    elif vr >= 0.7:  score = 38.0
    elif vr >= 0.5:  score = 22.0
    else:            score = 10.0

    return round(score, 1), vr


def calc_market_position(code: str, klines: List[KLineBar],
                          today_volume: float) -> MarketPosition:
    """市场位置综合评分：相对强度(50%) + 量比(50%)"""
    benchmark_code, benchmark_name = _get_benchmark(code)
    rs_score, rs_val = _score_relative_strength(klines, benchmark_code)
    vr_score, vr_val = _score_vol_ratio(klines, today_volume)

    total = round(rs_score * 0.5 + vr_score * 0.5, 1)
    level = '强' if total >= 70 else ('中' if total >= 45 else '弱')

    return {
        'score':             total,
        'level':             level,
        'relative_strength': rs_val,
        'rs_score':          rs_score,
        'vol_ratio':         vr_val,
        'vr_score':          vr_score,
        'benchmark':         benchmark_code,
        'benchmark_name':    benchmark_name,
    }


# ==================== 4. 成功率评分（优中选优） ====================

def calc_success_rate(
    klines: List[KLineBar],
    technical: TechnicalTarget,
    trend: TrendStrength,
    market_pos: MarketPosition,
    capital: CapitalFlow,
) -> SuccessRate:
    """
    5维度成功率评分 [0,100]，用于达标股票间的优先级排序。

    维度权重：
      突破质量  25% —— 信号本身的可靠性
      趋势动能  25% —— 趋势能否持续推动到目标价
      相对强度  20% —— 主力是否专注拉升这只
      资金持续性 20% —— 资金是否持续流入
      风险收益比 10% —— 同样涨10%，亏损空间越小越好

    等级：S≥80 / A≥65 / B≥50 / C≥35 / D<35
    """
    closes  = [float(k['close']) for k in klines] if klines else []
    volumes = [float(k['volume']) for k in klines] if klines else []

    # ── 维度1：突破质量 ──────────────────────────────────────────
    # 衡量金叉信号发出时的力度：放量倍数、均线多头、价格站上MA20
    bk_score = 40.0  # 基础分

    # 近5日成交量 vs 近20日均量（放量倍数）
    if len(volumes) >= 20:
        avg20 = sum(volumes[-20:]) / 20
        avg5  = sum(volumes[-5:]) / 5
        vol_mult = avg5 / avg20 if avg20 > 0 else 1.0
        if vol_mult >= 2.5:   bk_score += 35
        elif vol_mult >= 1.8: bk_score += 25
        elif vol_mult >= 1.3: bk_score += 15
        elif vol_mult >= 1.0: bk_score += 5
        else:                 bk_score -= 10

    # 均线多头加分
    if trend.get('ma_align'):
        bk_score += 20

    # 价格站上MA20（已在趋势里计算，直接复用）
    ma20_score = trend.get('detail', {}).get('ma_align', 50.0)
    if ma20_score >= 80:
        bk_score += 5

    bk_score = max(0.0, min(100.0, bk_score))

    # ── 维度2：趋势动能 ──────────────────────────────────────────
    # 直接复用趋势强度总分，但对MACD正向额外加权
    mo_score = trend.get('score', 50.0)
    if trend.get('macd_positive'):
        mo_score = min(mo_score + 10, 100.0)

    # 近10日价格动量（稳步上涨 vs 剧烈震荡）
    if len(closes) >= 10:
        chg10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
        # 稳健涨幅5~15%加分，涨太多反而追高风险高
        if 5 <= chg10 <= 15:    mo_score = min(mo_score + 10, 100.0)
        elif chg10 > 25:        mo_score = max(mo_score - 15, 0.0)
        elif chg10 < 0:         mo_score = max(mo_score - 10, 0.0)

    mo_score = max(0.0, min(100.0, mo_score))

    # ── 维度3：相对强度 ──────────────────────────────────────────
    # 直接复用市场位置里的相对强度评分
    rs_score = market_pos.get('rs_score', 50.0)

    # ── 维度4：资金持续性 ─────────────────────────────────────────
    # 主力净买入 + 量比共振
    main_in    = capital.get('main_net_in', 0.0)
    flow_ratio = capital.get('flow_ratio', 0.0)
    vr         = market_pos.get('vol_ratio', 1.0)

    cap_score = 40.0
    # 主力净买入方向
    if main_in > 5000:    cap_score += 35
    elif main_in > 2000:  cap_score += 25
    elif main_in > 500:   cap_score += 15
    elif main_in > 0:     cap_score += 5
    elif main_in < -2000: cap_score -= 25
    elif main_in < 0:     cap_score -= 10

    # 净流入占成交额比例
    if flow_ratio > 5:    cap_score += 15
    elif flow_ratio > 2:  cap_score += 8
    elif flow_ratio < -2: cap_score -= 10

    # 量比共振（量比高 + 主力净买入 = 资金持续涌入）
    if vr >= 2.0 and main_in > 0:  cap_score += 10
    elif vr < 0.7:                 cap_score -= 8

    cap_score = max(0.0, min(100.0, cap_score))

    # ── 维度5：风险收益比 ─────────────────────────────────────────
    # 目标涨幅 / |止损幅度|，比值越高越划算
    gain   = technical.get('expected_gain_pct', 10.0)
    sl_pct = abs(technical.get('stop_loss_pct', -5.0))
    rr     = gain / sl_pct if sl_pct > 0 else 2.0

    if rr >= 4.0:    rr_score = 100.0
    elif rr >= 3.0:  rr_score = 85.0
    elif rr >= 2.0:  rr_score = 70.0
    elif rr >= 1.5:  rr_score = 55.0
    elif rr >= 1.0:  rr_score = 40.0
    else:            rr_score = 20.0

    # ── 综合加权 ─────────────────────────────────────────────────
    score = (
        bk_score * 0.25 +
        mo_score * 0.25 +
        rs_score * 0.20 +
        cap_score * 0.20 +
        rr_score * 0.10
    )
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 80:   grade = 'S'
    elif score >= 65: grade = 'A'
    elif score >= 50: grade = 'B'
    elif score >= 35: grade = 'C'
    else:             grade = 'D'

    return {
        'score':        score,
        'grade':        grade,
        'dim_breakout': round(bk_score, 1),
        'dim_momentum': round(mo_score, 1),
        'dim_rs':       round(rs_score, 1),
        'dim_capital':  round(cap_score, 1),
        'dim_rr':       round(rr_score, 1),
    }


# ==================== 5. 主力资金确认 ====================

def _capital_confirmed(capital: CapitalFlow) -> bool:
    """今日主力净买入（main_net_in > 0）视为多头方向"""
    return capital.get('main_net_in', 0.0) > 0


# ==================== 6. 综合分析入口 ====================

def analyze_stock(code: str, name: str = '', signal_type: str = '') -> AnalysisResult:
    """并发获取数据，计算目标价 / 趋势强度 / 市场位置 / 成功率 / 资金方向"""
    stock_info = data_source.fetch_stock_industry(code)
    if not name:
        name = stock_info.get('name', code)
    industry = stock_info.get('industry', '')

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        fut_quote    = executor.submit(data_source.fetch_realtime_quote, code)
        fut_concepts = executor.submit(data_source.fetch_stock_concepts, code)
        fut_klines   = executor.submit(data_source.fetch_kline, code, '240min', 120)
        fut_capital  = executor.submit(data_source.fetch_capital_flow, code)

        quote    = fut_quote.result()
        concepts = fut_concepts.result()
        klines   = fut_klines.result()
        capital  = fut_capital.result()

    current_price = quote.get('price', 0.0)
    if current_price <= 0 and klines:
        current_price = float(klines[-1]['close'])

    today_volume = float(quote.get('volume', 0))

    technical    = calc_target_price(klines, current_price)
    trend        = calc_trend_strength(klines)
    market_pos   = calc_market_position(code, klines, today_volume)
    success_rate = calc_success_rate(klines, technical, trend, market_pos, capital)
    cap_ok       = _capital_confirmed(capital)

    # 综合建议
    if not technical['space_ok']:
        verdict = '空间不足'
    elif trend['level'] == '弱':
        verdict = '趋势偏弱'
    else:
        verdict = '达标'

    return {
        'code': code, 'name': name, 'industry': industry, 'concepts': concepts,
        'quote': quote, 'capital': capital,
        'technical': technical, 'trend': trend, 'market_pos': market_pos,
        'success_rate': success_rate,
        'capital_confirmed': cap_ok, 'verdict': verdict, 'signal_type': signal_type,
    }


# ==================== 7. 格式化输出 ====================

_GRADE_TAG = {'S': '[S级]', 'A': '[A级]', 'B': '[B级]', 'C': '[C级]', 'D': '[D级]'}
_GRADE_DESC = {
    'S': '强烈推荐，多维度共振',
    'A': '推荐，大部分维度良好',
    'B': '一般，可关注',
    'C': '偏弱，谨慎',
    'D': '不建议',
}


def format_analysis_report(result: AnalysisResult) -> str:
    code      = result['code']
    name      = result['name']
    industry  = result.get('industry', '')
    concepts  = result.get('concepts', [])
    quote     = result.get('quote', {})
    capital   = result.get('capital', {})
    tech      = result.get('technical', {})
    trend     = result.get('trend', {})
    mkt_pos   = result.get('market_pos', {})
    sr        = result.get('success_rate', {})
    verdict   = result.get('verdict', '')

    lines: List[str] = ['']
    sep = '=' * 62
    lines.append(f"  {sep}")

    verdict_tag = {
        '达标':   '[★ 达标]',
        '空间不足': '[  空间不足]',
        '趋势偏弱': '[  趋势偏弱]',
    }.get(verdict, f'[{verdict}]')

    # 成功率等级放标题行
    grade     = sr.get('grade', '?')
    sr_score  = sr.get('score', 0.0)
    grade_tag = _GRADE_TAG.get(grade, f'[{grade}]')
    lines.append(f"  {code} {name}  {verdict_tag}  {grade_tag} {sr_score:.0f}分")
    lines.append(f"  {sep}")

    # 实时行情
    if quote:
        chg = quote.get('change_pct', 0)
        chg_str = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
        lines.append(
            f"  现价: {quote.get('price', 0):.2f}  涨跌: {chg_str}  "
            f"今开: {quote.get('open', 0):.2f}  "
            f"高: {quote.get('high', 0):.2f}  低: {quote.get('low', 0):.2f}"
        )

    # 目标价
    if tech:
        gain    = tech.get('expected_gain_pct', 0)
        sl_pct  = tech.get('stop_loss_pct', 0)
        space_tag = '  [空间达标]' if tech.get('space_ok') else '  [空间不足]'
        lines.append('')
        lines.append(
            f"  【目标价】 {tech.get('target_price', 0):.2f}"
            f"  预期涨幅: +{gain:.1f}%{space_tag}"
        )
        lines.append(
            f"  【止损价】 {tech.get('stop_loss', 0):.2f}"
            f"  止损幅度: {sl_pct:.1f}%"
            f"  ATR: {tech.get('atr', 0):.3f}"
        )
        methods = tech.get('method_targets', {})
        if methods:
            method_str = '  '.join(f"{k}: {v:.2f}" for k, v in methods.items())
            lines.append(f"  【测算明细】{method_str}")

    # 成功率
    if sr:
        grade    = sr.get('grade', '?')
        sr_score = sr.get('score', 0.0)
        desc     = _GRADE_DESC.get(grade, '')
        bar      = '█' * int(sr_score / 5) + '░' * (20 - int(sr_score / 5))
        lines.append('')
        lines.append(f"  【成功率】{bar} {sr_score:.0f}分  {grade_tag} {desc}")
        lines.append(
            f"    突破质量 {sr.get('dim_breakout', 0):.0f}  "
            f"趋势动能 {sr.get('dim_momentum', 0):.0f}  "
            f"相对强度 {sr.get('dim_rs', 0):.0f}  "
            f"资金持续 {sr.get('dim_capital', 0):.0f}  "
            f"风险收益 {sr.get('dim_rr', 0):.0f}"
        )

    # 趋势强度
    if trend:
        score  = trend.get('score', 0)
        level  = trend.get('level', '')
        detail = trend.get('detail', {})
        ma_tag  = 'OK' if trend.get('ma_align')      else '--'
        vp_tag  = 'OK' if trend.get('vol_price_ok')  else '--'
        mac_tag = 'OK' if trend.get('macd_positive') else '--'
        bar = '█' * int(score / 5) + '░' * (20 - int(score / 5))
        lines.append('')
        lines.append(f"  【趋势强度】{bar} {score:.1f}  ({level})")
        lines.append(
            f"    均线排列[{ma_tag}] {detail.get('ma_align', 0):.0f}分  "
            f"量价配合[{vp_tag}] {detail.get('vol_price', 0):.0f}分  "
            f"MACD动量[{mac_tag}] {detail.get('macd', 0):.0f}分"
        )

    # 市场位置
    if mkt_pos:
        mp_score = mkt_pos.get('score', 50.0)
        mp_level = mkt_pos.get('level', '')
        rs_val   = mkt_pos.get('relative_strength', 0.0)
        rs_score = mkt_pos.get('rs_score', 50.0)
        vr_val   = mkt_pos.get('vol_ratio', 1.0)
        vr_score = mkt_pos.get('vr_score', 50.0)
        bm_name  = mkt_pos.get('benchmark_name', '')
        rs_str   = f"+{rs_val:.2f}%" if rs_val >= 0 else f"{rs_val:.2f}%"
        bar = '█' * int(mp_score / 5) + '░' * (20 - int(mp_score / 5))
        lines.append('')
        lines.append(f"  【市场位置】{bar} {mp_score:.1f}  ({mp_level})")
        lines.append(
            f"    相对强度[vs {bm_name}] {rs_str}  {rs_score:.0f}分  "
            f"量比 {vr_val:.2f}x  {vr_score:.0f}分"
        )

    # 主力资金
    if capital:
        main_in = capital.get('main_net_in', 0)
        flow    = capital.get('flow_ratio', 0)
        dir_tag = f'今日主力净买入 +{main_in:.0f}万' if main_in > 0 else f'今日主力净卖出 {main_in:.0f}万'
        flow_tag = f'  占成交额 {flow:+.2f}%'
    else:
        dir_tag, flow_tag = '资金数据获取失败', ''
    lines.append(f"  【主力资金】{dir_tag}{flow_tag}")

    # 行业 & 概念
    lines.append('')
    lines.append(f"  【行业】{industry if industry else '未知'}")
    if concepts:
        lines.append(
            f"  【概念】{', '.join(concepts[:10])}"
            + (f" 等{len(concepts)}个" if len(concepts) > 10 else "")
        )

    lines.append(f"  {sep}")
    lines.append('')
    return "\n".join(lines)


# ==================== 8. 批量分析 ====================

def analyze_stocks_batch(stocks: List[Tuple[str, str]],
                          signal_types: Optional[Dict[str, str]] = None) -> List[AnalysisResult]:
    if not stocks:
        return []
    if signal_types is None:
        signal_types = {}

    print(f"\n{'=' * 66}")
    print("  目标导向分析（短期涨幅 ≥10% 筛选）")
    print(f"  待分析: {len(stocks)} 只")
    print(f"{'=' * 66}")

    results: List[AnalysisResult] = []
    for i, (code, name) in enumerate(stocks):
        print(f"\n  [{i+1}/{len(stocks)}] 正在分析 {code} {name} ...")
        try:
            r = analyze_stock(code, name, signal_type=signal_types.get(code, ''))
            results.append(r)
            print(format_analysis_report(r))
        except Exception as e:
            logger.error(f"分析失败 {code}: {e}")
            print(f"    分析失败: {e}")
            results.append({  # type: ignore[misc]
                'code': code, 'name': name, 'signal_type': signal_types.get(code, ''),
                'industry': '', 'concepts': [], 'quote': {}, 'capital': {},
                'technical': {}, 'trend': {}, 'market_pos': {},
                'capital_confirmed': False, 'verdict': '失败',
            })

        if i < len(stocks) - 1:
            time.sleep(0.3)

    ok     = [r for r in results if r.get('verdict') == '达标']
    not_ok = [r for r in results if r.get('verdict') != '达标']

    # 达标股票按成功率降序排列
    ok_sorted = sorted(ok, key=lambda r: r.get('success_rate', {}).get('score', 0), reverse=True)

    print(f"\n{'=' * 66}")
    print(f"  分析完成  达标: {len(ok)} 只  不达标: {len(not_ok)} 只")
    if ok_sorted:
        print(f"  {'─' * 60}")
        print(f"  达标股票（按成功率排序）:")
        for r in ok_sorted:
            t   = r.get('technical', {})
            tr  = r.get('trend', {})
            mp  = r.get('market_pos', {})
            sr  = r.get('success_rate', {})
            rs_val  = mp.get('relative_strength', 0.0)
            rs_str  = f"+{rs_val:.1f}%" if rs_val >= 0 else f"{rs_val:.1f}%"
            grade   = sr.get('grade', '?')
            sr_sc   = sr.get('score', 0.0)
            print(
                f"    [{grade}级 {sr_sc:.0f}分]  {r['code']} {r['name']:<8}"
                f"  目标 {t.get('target_price', 0):.2f}"
                f"  +{t.get('expected_gain_pct', 0):.1f}%"
                f"  止损 {t.get('stop_loss', 0):.2f}"
                f"  趋势[{tr.get('level', '?')}]"
                f"  RS {rs_str}"
                f"  量比 {mp.get('vol_ratio', 0):.2f}x"
            )
    print(f"{'=' * 66}\n")

    return results


# ==================== 9. 独立运行 ====================

def main() -> None:
    print("=" * 62)
    print("  个股分析工具（目标导向版，短期涨幅 ≥10%）")
    print("=" * 62)
    print("  输入股票代码（6位），多只用逗号分隔")
    print()
    codes_input = input("  请输入股票代码: ").strip()
    if not codes_input:
        return
    codes = [c.strip() for c in codes_input.replace('\uff0c', ',').split(',') if c.strip()]
    stocks = [(c, '') for c in codes if len(c) == 6 and c.isdigit()]
    if not stocks:
        print("  无有效代码")
        return
    analyze_stocks_batch(stocks)


if __name__ == "__main__":
    main()
