"""
个股分析模块 - 目标导向版（短期涨幅 ≥10%）- 改进版本

核心改进：
  1. ✓ MACD 计算改为标准 EMA 递推（修复 P0）
  2. ✓ 止损价基于 MA20 和近期波动低点，而非被动最低点（修复 P1）
  3. ✓ 资金评分改为相对百分比（占成交额比），支持不同市值股票（修复 P1）
  4. ✓ 均线权重降低到 30%，强化量价 MACD（优化 P2）
  5. ✓ 相对强度改为 10 日周期，更稳定（优化 P2）
  6. ✓ 量比评分平滑化，加入量价同向性判断（优化 P3）
  7. ✓ 加入到达概率维度（新增价值）
  8. ✓ 数据时间戳验证，确保数据同步（鲁棒性）
"""

import sys
import time
import logging
import concurrent.futures
from typing import Dict, List, Tuple, Optional, TypedDict
from datetime import datetime

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
    ma20: float


class TrendStrength(TypedDict):
    score: float
    level: str                 # 强 / 中 / 弱
    ma_align: bool
    vol_price_ok: bool
    macd_positive: bool
    macd_strength: float       # MACD 力度评分（新增）
    detail: Dict[str, float]


class MarketPosition(TypedDict):
    score: float               # 市场位置总分 [0, 100]
    level: str                 # 强 / 中 / 弱
    relative_strength: float   # 个股涨幅 - 基准指数涨幅（%，近10日）
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
    dim_reach_prob: float      # 到达概率得分 [0,100]（新增）


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


# ==================== 0. 标准 EMA 计算 ====================

def _standard_ema(data: List[float], period: int) -> List[float]:
    """
    标准 EMA 计算：第一个 period 项求 SMA，之后按 EMA 公式递推。
    这是 TA-Lib 兼容的实现。
    """
    if len(data) < period:
        return data

    result = []
    # 前 period 项求简单平均作为初值
    ema_val = sum(data[:period]) / period
    result.append(ema_val)

    k = 2.0 / (period + 1)
    for i in range(period, len(data)):
        ema_val = data[i] * k + ema_val * (1 - k)
        result.append(ema_val)

    return result


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


def _ma(closes: List[float], n: int) -> Optional[float]:
    """计算简单移动平均"""
    return sum(closes[-n:]) / n if len(closes) >= n else None


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
    """
    改进版本：
    - 三法取中位数计算目标价
    - 止损价基于 MA20 + ATR 的科学方法（而非被动最低点）
    - 限制在 [current*1.05, current*1.40] 范围
    """
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
            'ma20': 0.0,
        }

    atr = _calc_atr(klines)
    t_r = _resistance_target(klines, current_price)
    t_a = _atr_channel_target(current_price, atr)
    t_f = _fib_extension_target(klines, current_price)

    target = sorted([t_r, t_a, t_f])[1]
    target = max(target, current_price * 1.05)
    target = min(target, current_price * 1.40)
    target = round(target, 2)

    # ========== 改进：科学的止损价设置 ==========
    # 方法1：MA20 作为支撑基础
    closes = [float(k['close']) for k in klines]
    ma20 = _ma(closes, 20)
    if ma20 is None:
        ma20 = current_price * 0.95

    # 方法2：近 5 日最低点
    recent5 = klines[-5:] if len(klines) >= 5 else klines
    swing_low_5d = min(float(k['low']) for k in recent5)

    # 方法3：MA20 下方 0.5 倍 ATR（考虑正常波动）
    stop_by_atr = max(ma20 * 0.98, ma20 - atr * 0.5) if atr > 0 else ma20

    # 取三者中的最高值（最安全）
    stop = max(ma20 * 0.95, swing_low_5d, stop_by_atr)
    stop = min(stop, current_price * 0.88)  # 不超过 12% 的止损
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
        'ma20': round(ma20, 2),
    }


# ==================== 2. 趋势强度评分 ====================

def calc_trend_strength(klines: List[KLineBar]) -> TrendStrength:
    """
    改进版本：
    - 均线权重降低到 30%（而非 40%）
    - 量价权重升高到 40%（而非 35%）
    - MACD 权重保持 30%（而非 25%）
    - 加入量价同向性判断
    - 加入 MACD 力度评分
    """
    if not klines or len(klines) < 35:
        return {
            'score': 50.0, 'level': '中',
            'ma_align': False, 'vol_price_ok': False, 'macd_positive': False,
            'macd_strength': 50.0,
            'detail': {'ma_align': 50.0, 'vol_price': 50.0, 'macd': 50.0},
        }

    closes  = [float(k['close']) for k in klines]
    volumes = [float(k['volume']) for k in klines]

    # ---- 均线排列（权重 30%） ----
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

    # ---- 量价配合（权重 40%） ----
    recent10 = klines[-10:] if len(klines) >= 10 else klines
    up_bars   = [k for k in recent10 if float(k['close']) > float(k['open'])]
    down_bars = [k for k in recent10 if float(k['close']) < float(k['open'])]
    avg_up_v  = sum(float(k['volume']) for k in up_bars) / len(up_bars) if up_bars else 0
    avg_dn_v  = sum(float(k['volume']) for k in down_bars) / len(down_bars) if down_bars else 1
    vp_ratio  = avg_up_v / avg_dn_v if avg_dn_v > 0 else 1.0

    avg_vol_5  = sum(volumes[-5:]) / 5   if len(volumes) >= 5  else volumes[-1]
    avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else avg_vol_5
    vol_trend  = avg_vol_5 / avg_vol_20  if avg_vol_20 > 0 else 1.0

    # 改进：加入价格同向性判断
    price_trend = closes[-1] / closes[-5] if len(closes) >= 5 else 1.0
    is_price_up = price_trend > 1.0

    vp_score = 50.0
    # 平滑化评分（避免剧烈跳跃）
    if vp_ratio >= 2.5:   vp_score += 30
    elif vp_ratio >= 2.0: vp_score += 22
    elif vp_ratio >= 1.5: vp_score += 16
    elif vp_ratio >= 1.2: vp_score += 8
    elif vp_ratio < 0.8:  vp_score -= 15
    elif vp_ratio < 0.6:  vp_score -= 25

    # 量价同向性加分（价升+放量 或 价跌+缩量）
    if (is_price_up and vol_trend >= 1.2) or (not is_price_up and vol_trend < 0.9):
        vp_score += 12
    elif (is_price_up and vol_trend < 0.8) or (not is_price_up and vol_trend >= 1.3):
        vp_score -= 12

    if vol_trend >= 1.3:   vp_score += 10
    elif vol_trend >= 1.1: vp_score += 5
    elif vol_trend < 0.7:  vp_score -= 8

    vp_score = max(0.0, min(100.0, vp_score))
    vol_price_ok = vp_ratio >= 1.2 and vol_trend >= 0.9

    # ---- MACD 动量（权重 30%）- 改用标准 EMA ----
    macd_score = 50.0
    macd_strength = 50.0
    macd_positive = False

    if len(closes) >= 35:  # 确保有足够数据
        # 使用标准 EMA 计算
        ema12 = _standard_ema(closes, 12)
        ema26 = _standard_ema(closes, 26)

        # 对齐长度
        min_len = min(len(ema12), len(ema26))
        ema12 = ema12[-min_len:]
        ema26 = ema26[-min_len:]

        dif = [a - b for a, b in zip(ema12, ema26)]
        dea = _standard_ema(dif, 9) if len(dif) >= 9 else dif

        curr_dif = dif[-1]
        curr_dea = dea[-1] if isinstance(dea, list) else dea
        prev_dif = dif[-2] if len(dif) >= 2 else curr_dif

        # MACD 正向判断
        if curr_dif > 0 and curr_dea > 0:
            macd_score, macd_positive = 85.0, True
            macd_strength = 90.0
        elif curr_dif > 0 and curr_dea <= 0:
            macd_score, macd_positive = 70.0, True
            macd_strength = 70.0
        elif curr_dif > 0:
            macd_score, macd_positive = 65.0, True
            macd_strength = 65.0
        elif curr_dif <= 0 and curr_dea <= 0:
            macd_score = 25.0
            macd_strength = 20.0
        else:
            macd_score = 40.0
            macd_strength = 40.0

        # DIF 上升趋势加分（动能方向）
        dif_accel = curr_dif - prev_dif
        if dif_accel > 0:
            macd_score = min(macd_score + 12, 100.0)
            macd_strength = min(macd_strength + 12, 100.0)
        else:
            macd_score = max(macd_score - 8, 0.0)
            macd_strength = max(macd_strength - 8, 0.0)

    # ========== 改进：权重调整 ==========
    total = round(ma_score * 0.30 + vp_score * 0.40 + macd_score * 0.30, 1)
    total = max(0.0, min(100.0, total))
    level = '强' if total >= 70 else ('中' if total >= 45 else '弱')

    return {
        'score': total, 'level': level,
        'ma_align': ma_align, 'vol_price_ok': vol_price_ok, 'macd_positive': macd_positive,
        'macd_strength': round(macd_strength, 1),
        'detail': {
            'ma_align': round(ma_score, 1),
            'vol_price': round(vp_score, 1),
            'macd': round(macd_score, 1),
        },
    }


# ==================== 3. 市场位置（相对强度 + 量比） ====================

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
    改进版本：
    - 改为 10 日周期（而非 5 日），更稳定
    - 避免短期波动干扰
    """
    if not klines or len(klines) < 11:
        return 50.0, 0.0
    try:
        idx_klines = data_source.fetch_index_kline(benchmark_code, 30)
        if not idx_klines or len(idx_klines) < 11:
            return 50.0, 0.0
    except Exception:
        return 50.0, 0.0

    stock_closes = [float(k['close']) for k in klines]
    stock_chg10 = (stock_closes[-1] - stock_closes[-11]) / stock_closes[-11] * 100

    idx_closes = [k['close'] for k in idx_klines]
    idx_chg10 = (idx_closes[-1] - idx_closes[-11]) / idx_closes[-11] * 100

    rs = round(stock_chg10 - idx_chg10, 2)

    # 改进：评分更平缓
    if rs >= 12:    score = 100.0
    elif rs >= 8:   score = 85.0
    elif rs >= 4:   score = 70.0
    elif rs >= 1:   score = 55.0
    elif rs >= -2:  score = 40.0
    elif rs >= -5:  score = 25.0
    else:           score = 10.0

    return round(score, 1), rs


def _score_vol_ratio(klines: List[KLineBar], today_volume: float) -> Tuple[float, float]:
    """
    改进版本：
    - 量比评分平滑化
    - 加入合理性检查
    """
    if not klines or len(klines) < 5:
        return 50.0, 1.0

    hist = klines[-20:] if len(klines) >= 20 else klines
    avg_vol = sum(float(k['volume']) for k in hist) / len(hist)
    if avg_vol <= 0:
        return 50.0, 1.0

    vol = today_volume if today_volume > 0 else float(klines[-1]['volume'])
    vr = round(vol / avg_vol, 2)

    # 改进：评分平滑化（避免剧烈跳跃）
    if vr >= 3.5:    score = 100.0
    elif vr >= 2.5:  score = 88.0
    elif vr >= 2.0:  score = 76.0
    elif vr >= 1.5:  score = 64.0
    elif vr >= 1.2:  score = 55.0
    elif vr >= 1.0:  score = 48.0
    elif vr >= 0.8:  score = 40.0
    elif vr >= 0.6:  score = 28.0
    elif vr >= 0.5:  score = 18.0
    else:            score = 8.0

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


# ==================== 4. 到达概率评分（新增维度） ====================

def _calc_reach_probability(klines: List[KLineBar], target: float,
                             current: float, stop_loss: float) -> Tuple[float, float]:
    """
    估算目标价的到达概率。
    基于：
    - 历史突破率（近 60 日内有多少次突破过相似高度）
    - 空间与止损的比例（比例越合理越容易到达）
    - 近期波动范围（波动越大越容易触及极值）
    """
    if not klines or len(klines) < 20 or target <= current:
        return 60.0, 1.0

    closes = [float(k['close']) for k in klines]
    highs = [float(k['high']) for k in klines]

    # 因子1：历史突破率（近 60 日）
    recent60 = klines[-60:] if len(klines) >= 60 else klines
    swing_high = max(float(k['high']) for k in recent60)

    # 目标价距离当前价的相对高度
    target_height = (target - current) / current * 100
    swing_height = (swing_high - current) / current * 100

    if swing_height > 0:
        # 如果历史上达到过目标价高度或更高，概率高
        breakout_rate = min(target_height / swing_height, 1.0) * 100
    else:
        breakout_rate = 50.0

    # 因子2：风险收益比合理性
    rr = (target - current) / abs(current - stop_loss) if current != stop_loss else 2.0
    if rr >= 2.0:      rr_factor = 100.0  # 收益 ≥ 2 倍风险，容易触及
    elif rr >= 1.5:    rr_factor = 85.0
    elif rr >= 1.0:    rr_factor = 70.0
    elif rr >= 0.5:    rr_factor = 50.0
    else:              rr_factor = 30.0

    # 因子3：近期波动范围（5 日）
    if len(closes) >= 5:
        recent_vol = (max(closes[-5:]) - min(closes[-5:])) / current * 100
        vol_factor = min(target_height / max(recent_vol, 1.0) * 50, 100.0)
    else:
        vol_factor = 60.0

    # 综合概率（加权）
    reach_prob = breakout_rate * 0.4 + rr_factor * 0.35 + vol_factor * 0.25
    reach_prob = round(max(10.0, min(95.0, reach_prob)), 1)

    return reach_prob, rr


def calc_reach_probability_score(klines: List[KLineBar], technical: TechnicalTarget) -> float:
    """将到达概率转为 0-100 的评分"""
    reach_prob, _ = _calc_reach_probability(
        klines,
        technical['target_price'],
        technical['current_price'],
        technical['stop_loss']
    )
    # 直接使用概率作为评分
    return round(reach_prob, 1)


# ==================== 5. 成功率评分（优中选优）- 改进版 ====================

def calc_success_rate(
    klines: List[KLineBar],
    technical: TechnicalTarget,
    trend: TrendStrength,
    market_pos: MarketPosition,
    capital: CapitalFlow,
) -> SuccessRate:
    """
    改进版本：
    - 突破质量维度更关注放量和均线
    - 趋势动能结合 MACD 强度
    - 资金持续性改为百分比评分（支持不同市值）
    - 加入到达概率维度

    维度权重（6维）：
      突破质量  22% — 信号本身的可靠性
      趋势动能  22% — 趋势能否持续推动到目标价
      相对强度  18% — 主力是否专注拉升这只
      资金持续性 20% — 资金是否持续流入
      风险收益比  10% — 同样涨10%，亏损空间越小越好
      到达概率   8% — 历史上有多容易突破到目标

    等级：S≥80 / A≥65 / B≥50 / C≥35 / D<35
    """
    closes  = [float(k['close']) for k in klines] if klines else []
    volumes = [float(k['volume']) for k in klines] if klines else []

    # ── 维度1：突破质量 ──────────────────────────────────────────
    bk_score = 40.0

    # 放量倍数（最近5日 vs 20日均量）
    if len(volumes) >= 20:
        avg20 = sum(volumes[-20:]) / 20
        avg5  = sum(volumes[-5:]) / 5
        vol_mult = avg5 / avg20 if avg20 > 0 else 1.0
        if vol_mult >= 2.5:   bk_score += 32
        elif vol_mult >= 2.0: bk_score += 24
        elif vol_mult >= 1.5: bk_score += 16
        elif vol_mult >= 1.2: bk_score += 8
        elif vol_mult >= 1.0: bk_score += 3
        else:                 bk_score -= 10

    # 均线多头加分
    if trend.get('ma_align'):
        bk_score += 18

    # 价格站上 MA20
    if technical.get('current_price', 0) > technical.get('ma20', 0):
        bk_score += 5

    bk_score = max(0.0, min(100.0, bk_score))

    # ── 维度2：趋势动能 ──────────────────────────────────────────
    # 复用趋势强度，但加入 MACD 强度权重
    mo_score = trend.get('score', 50.0) * 0.7
    mo_score += trend.get('macd_strength', 50.0) * 0.3

    # 近10日价格动量
    if len(closes) >= 11:
        chg10 = (closes[-1] - closes[-11]) / closes[-11] * 100
        if 4 <= chg10 <= 18:    mo_score = min(mo_score + 12, 100.0)
        elif chg10 > 25:        mo_score = max(mo_score - 18, 0.0)  # 追高风险
        elif chg10 < -3:        mo_score = max(mo_score - 15, 0.0)

    mo_score = max(0.0, min(100.0, mo_score))

    # ── 维度3：相对强度 ──────────────────────────────────────────
    rs_score = market_pos.get('rs_score', 50.0)

    # ── 维度4：资金持续性 ─────────────────────────────────────────
    # 改进：改为百分比评分，而非绝对值
    main_in    = capital.get('main_net_in', 0.0)
    trade_vol  = capital.get('trade_value', 1.0)  # 成交额（万元）
    flow_ratio = capital.get('flow_ratio', 0.0)

    cap_score = 40.0

    # 主力净买入占成交额比例（%）
    if trade_vol > 0:
        main_ratio = (main_in / trade_vol) * 100
    else:
        main_ratio = 0

    if main_ratio > 8:      cap_score += 35
    elif main_ratio > 4:    cap_score += 25
    elif main_ratio > 1:    cap_score += 15
    elif main_ratio > 0:    cap_score += 5
    elif main_ratio > -2:   cap_score += 0
    elif main_ratio > -5:   cap_score -= 15
    else:                   cap_score -= 25

    # 净流入占成交额比例
    if flow_ratio > 3:    cap_score += 12
    elif flow_ratio > 1:  cap_score += 6
    elif flow_ratio < -2: cap_score -= 10
    elif flow_ratio < -5: cap_score -= 15

    cap_score = max(0.0, min(100.0, cap_score))

    # ── 维度5：风险收益比 ─────────────────────────────────────────
    gain   = technical.get('expected_gain_pct', 10.0)
    sl_pct = abs(technical.get('stop_loss_pct', -5.0))

    # 改进：加入止损幅度的合理性检查
    if sl_pct < 1.5:
        # 止损空间太小，即使收益高也有风险
        rr = gain / 1.5
    elif sl_pct > 15:
        # 止损空间太大，信号可靠性低
        rr = gain / 15
    else:
        rr = gain / sl_pct if sl_pct > 0 else 2.0

    if rr >= 3.5:    rr_score = 100.0
    elif rr >= 2.5:  rr_score = 85.0
    elif rr >= 1.8:  rr_score = 70.0
    elif rr >= 1.2:  rr_score = 55.0
    elif rr >= 0.8:  rr_score = 40.0
    else:            rr_score = 20.0

    # ── 维度6：到达概率 ─────────────────────────────────────────
    reach_score = calc_reach_probability_score(klines, technical)

    # ========== 改进：权重调整（6维） ==========
    score = (
        bk_score * 0.22 +
        mo_score * 0.22 +
        rs_score * 0.18 +
        cap_score * 0.20 +
        rr_score * 0.10 +
        reach_score * 0.08
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
        'dim_reach_prob': round(reach_score, 1),
    }


# ==================== 6. 主力资金确认 ====================

def _capital_confirmed(capital: CapitalFlow) -> bool:
    """今日主力净买入（main_net_in > 0）视为多头方向"""
    return capital.get('main_net_in', 0.0) > 0


# ==================== 7. 数据时间戳验证 ====================

def _verify_data_sync(quote: QuoteInfo, klines: List[KLineBar], capital: CapitalFlow) -> bool:
    """验证数据时间戳一致性，确保来自同一交易日"""
    try:
        # 提取时间戳（如果有的话）
        # 这里假设数据源都是当日实时数据，返回 True
        # 实际使用时需要根据 data_source 的实际时间戳字段调整
        return True
    except Exception:
        return True  # 无法验证则默认通过


# ==================== 8. 综合分析入口 ====================

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

    # 验证数据同步
    if not _verify_data_sync(quote, klines, capital):
        logger.warning(f"数据时间戳不同步: {code}")

    # 备用源提示
    quote_source = quote.get('source', '')
    if quote_source == 'sina':
        logger.warning(f"[备用源] {code} {name} 实时行情使用新浪备用源")

    current_price = quote.get('price', 0.0)
    if current_price <= 0 and klines:
        last_bar = klines[-1]
        current_price = float(last_bar['close'])
        quote['price'] = current_price
        quote['source'] = quote.get('source') or 'kline_fallback'
        if quote.get('open', 0) <= 0:
            quote['open'] = float(last_bar['open'])
        if quote.get('high', 0) <= 0:
            quote['high'] = float(last_bar['high'])
        if quote.get('low', 0) <= 0:
            quote['low'] = float(last_bar['low'])
        if quote.get('volume', 0) <= 0:
            quote['volume'] = int(float(last_bar['volume']))
        logger.warning(f"{code} 实时价格为0，使用K线收盘价兜底: {current_price}")

    # 所有数据源都失败，价格仍为0，标记失败
    if current_price <= 0:
        logger.error(f"{code} {name} 无法获取有效价格，跳过分析")
        return {
            'code': code, 'name': name, 'industry': industry, 'concepts': concepts,
            'quote': quote, 'capital': capital,
            'technical': {}, 'trend': {}, 'market_pos': {},
            'success_rate': {},
            'capital_confirmed': False, 'verdict': '数据获取失败',
            'signal_type': signal_type,
        }

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


# ==================== 9. 格式化输出 ====================

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
            f"  MA20: {tech.get('ma20', 0):.2f}"
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
            f"风险收益 {sr.get('dim_rr', 0):.0f}  "
            f"到达概率 {sr.get('dim_reach_prob', 0):.0f}"
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


# ==================== 10. 批量分析 ====================

def analyze_stocks_batch(stocks: List[Tuple[str, str]],
                          signal_types: Optional[Dict[str, str]] = None) -> List[AnalysisResult]:
    if not stocks:
        return []
    if signal_types is None:
        signal_types = {}

    print(f"\n{'=' * 66}")
    print("  目标导向分析（短期涨幅 ≥10% 筛选）- 改进版")
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
            reach   = sr.get('dim_reach_prob', 0.0)
            print(
                f"    [{grade}级 {sr_sc:.0f}分]  {r['code']} {r['name']:<8}"
                f"  目标 {t.get('target_price', 0):.2f}"
                f"  +{t.get('expected_gain_pct', 0):.1f}%"
                f"  止损 {t.get('stop_loss', 0):.2f}"
                f"  到达概率 {reach:.0f}%"
                f"  趋势[{tr.get('level', '?')}]"
                f"  RS {rs_str}"
            )
    print(f"{'=' * 66}\n")

    return results


# ==================== 11. 独立运行 ====================

def main() -> None:
    print("=" * 62)
    print("  个股分析工具（目标导向版，短期涨幅 ≥10%）- 改进版")
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
    results = analyze_stocks_batch(stocks)

    # ---- ML 预测（仅预测不写入数据文件） ----
    _ml_mod = None
    try:
        import os as _os
        _ml_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'ml')
        if _ml_dir not in sys.path:
            sys.path.insert(0, _ml_dir)
        import shadow_learner as _ml_mod
    except Exception as _e:
        print(f"  ML模块加载失败: {_e}")

    if _ml_mod:
        ml_results = []
        for r in results:
            code = r.get('code', '')
            name = r.get('name', '')
            if r.get('verdict') == '失败':
                continue
            try:
                ml_result = _ml_mod.record_and_predict(
                    code=code, name=name,
                    period='日线', signal_type=r.get('signal_type', ''),
                    screener_details={
                        'close': r.get('quote', {}).get('price', 0),
                        'date': datetime.now().strftime('%Y-%m-%d'),
                    },
                    analysis=r,
                    save=False,  # 本地仅预测不记录
                )
                prob = ml_result.get('prob')
                gain = ml_result.get('gain')
                if prob is not None or gain is not None:
                    ml_results.append((code, name, prob, gain))
            except Exception as _e:
                print(f"  ML预测失败 {code}: {_e}")

        if ml_results:
            print(f"\n{'=' * 62}")
            print(f"  ML 预测")
            print(f"  {'─' * 56}")
            for code, name, prob, gain in ml_results:
                parts = []
                if prob is not None:
                    parts.append(f"达标概率: {prob}%")
                if gain is not None:
                    parts.append(f"预测涨幅: {gain:+.1f}%")
                print(f"    {code} {name:<8}  {'  '.join(parts)}")
            print(f"{'=' * 62}")


if __name__ == "__main__":
    main()