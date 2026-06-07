"""
持仓盯盘 - 盘中每30分钟检查持仓健康度，到目标/止损/转弱时推钉钉

设计要点：
  1. 只盯 positions.json 里登记的几只票，请求量极小，不触发限流
  2. 复用 data_source 的东财接口 + 令牌桶限流器 + 新浪备用源
  3. 历史K线开盘缓存（已收盘的K线不变），当天K线用实时行情现拼 → MA/MACD 盘中实时准确
  4. 止损线硬：跌破直接提醒砍，不给"洗盘"找借口
  5. 止盈+走弱：结合资金/趋势/RS/量价的健康度判断，分"洗盘"和"真变质"

用法：
    python position_monitor.py            # 正常：等交易时间，每30分钟一轮
    python position_monitor.py --now      # 立即跑一轮（测试用）

环境变量：DINGTALK_WEBHOOK / DINGTALK_SECRET
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# 上级目录入 path，复用 data_source
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)
import data_source

# 复用钉钉推送
sys.path.insert(0, os.path.join(PARENT_DIR, 'stock_monitor'))
from notifier import send_dingtalk

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ==================== 配置 ====================
_DIR = os.path.dirname(os.path.abspath(__file__))
POSITIONS_FILE = os.path.join(_DIR, 'positions.json')

SCAN_INTERVAL = 1800            # 30分钟一轮
DEFAULT_TARGET_PCT = 10.0       # 默认目标涨幅
DEFAULT_STOP_PCT = 8.0          # 默认止损幅度

TRADING_START_MORNING = "09:30"
TRADING_END_MORNING = "11:30"
TRADING_START_AFTERNOON = "13:00"
TRADING_END_AFTERNOON = "15:00"

_HOLIDAYS_FILE = os.path.join(PARENT_DIR, 'stock_monitor', 'holidays.json')

# 当天日K历史部分的缓存：{code: (cache_date, klines_without_today)}
_kline_cache: Dict[str, tuple] = {}


# ==================== 时间工具（复用 monitor 逻辑） ====================

def get_beijing_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def _load_holidays() -> set:
    try:
        with open(_HOLIDAYS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        out = set()
        for _, dates in data.items():
            if isinstance(dates, list):
                out.update(dates)
        return out
    except Exception:
        return set()


def is_trading_day() -> bool:
    now = get_beijing_now()
    if now.weekday() >= 5:
        return False
    return now.strftime('%Y-%m-%d') not in _load_holidays()


def is_trading_time() -> bool:
    if not is_trading_day():
        return False
    t = get_beijing_now().strftime('%H:%M')
    return (TRADING_START_MORNING <= t <= TRADING_END_MORNING) or \
           (TRADING_START_AFTERNOON <= t <= TRADING_END_AFTERNOON)


def is_after_trading() -> bool:
    if not is_trading_day():
        return True
    return get_beijing_now().strftime('%H:%M') > TRADING_END_AFTERNOON


def is_before_trading() -> bool:
    if not is_trading_day():
        return False
    return get_beijing_now().strftime('%H:%M') < TRADING_START_MORNING


# ==================== 持仓读取 ====================

def load_positions() -> List[Dict]:
    if not os.path.exists(POSITIONS_FILE):
        logger.error(f"持仓文件不存在: {POSITIONS_FILE}")
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        holdings = data.get('holdings', [])
        valid = []
        for h in holdings:
            code = str(h.get('code', '')).strip()
            cost = h.get('cost')
            if len(code) == 6 and code.isdigit() and cost:
                valid.append(h)
            else:
                logger.warning(f"跳过无效持仓条目: {h}")
        return valid
    except Exception as e:
        logger.error(f"持仓文件解析失败: {e}")
        return []


# ==================== K线：历史缓存 + 当天实时拼接 ====================

def get_klines_with_today(code: str, current_price: float,
                          today_high: float, today_low: float,
                          today_open: float, today_volume: float) -> List[dict]:
    """
    返回用于算 MA/MACD 的日K序列：
      - 历史K线（昨天及以前，已定死）：当天首次拉取后缓存，整天复用
      - 今天这根K线：用实时行情现拼（收盘价=当前价），盘中实时变化
    """
    today_str = get_beijing_now().strftime('%Y-%m-%d')
    cached = _kline_cache.get(code)

    if cached and cached[0] == today_str:
        hist = cached[1]
    else:
        raw = data_source.fetch_kline(code, '240min', 120)
        # 去掉最后一根（可能是今天的，盘中会变），只缓存已定死的历史
        hist = [k for k in raw if k.get('day', '')[:10] < today_str]
        _kline_cache[code] = (today_str, hist)

    # 拼上今天这根（实时）
    today_bar = {
        'day': today_str,
        'open': str(today_open if today_open > 0 else current_price),
        'high': str(today_high if today_high > 0 else current_price),
        'low': str(today_low if today_low > 0 else current_price),
        'close': str(current_price),
        'volume': str(int(today_volume) if today_volume > 0 else 0),
    }
    return hist + [today_bar]


# ==================== 健康度计算 ====================

def _ma(closes: List[float], n: int) -> Optional[float]:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _ema(data: List[float], period: int) -> List[float]:
    if len(data) < period:
        return data
    out = [sum(data[:period]) / period]
    k = 2.0 / (period + 1)
    for i in range(period, len(data)):
        out.append(data[i] * k + out[-1] * (1 - k))
    return out


def _macd_dead_cross(closes: List[float]) -> Optional[bool]:
    """返回 MACD 是否处于空头（DIF<DEA 或 DIF<0）。数据不足返回 None"""
    if len(closes) < 35:
        return None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    n = min(len(ema12), len(ema26))
    dif = [a - b for a, b in zip(ema12[-n:], ema26[-n:])]
    dea = _ema(dif, 9) if len(dif) >= 9 else dif
    cur_dif = dif[-1]
    cur_dea = dea[-1] if isinstance(dea, list) else dea
    return cur_dif < cur_dea or cur_dif < 0


def compute_health(code: str, quote: dict, capital: dict,
                   klines: List[dict]) -> Dict:
    """
    四维健康度判断（每维：好/中/坏），汇总成 健康/观察/转弱。
    返回 {level, reasons:[...], detail:{...}}
    """
    reasons_bad = []
    reasons_good = []
    closes = [float(k['close']) for k in klines] if klines else []

    # ── 维度1：主力资金 ──
    main_in = capital.get('main_net_in', 0.0)
    if main_in > 0:
        cap_state = 'good'
        reasons_good.append(f"主力净流入+{main_in:.0f}万")
    elif main_in < 0:
        cap_state = 'bad'
        reasons_bad.append(f"主力净流出{main_in:.0f}万")
    else:
        cap_state = 'mid'

    # ── 维度2：趋势（MA20 + MACD）──
    price = quote.get('price', 0.0)
    ma20 = _ma(closes, 20)
    trend_state = 'mid'
    if ma20:
        if price < ma20:
            trend_state = 'bad'
            reasons_bad.append(f"跌破MA20({ma20:.2f})")
        else:
            reasons_good.append(f"站上MA20")
    dead = _macd_dead_cross(closes)
    if dead is True:
        if trend_state != 'bad':
            trend_state = 'bad'
        reasons_bad.append("MACD空头")
    elif dead is False:
        reasons_good.append("MACD多头")

    # ── 维度3：量价（放量下跌 = 坏）──
    change_pct = quote.get('change_pct', 0.0)
    vol = float(quote.get('volume', 0) or 0)
    vp_state = 'mid'
    if len(klines) >= 6:
        hist_vol = [float(k['volume']) for k in klines[-6:-1]]
        avg_vol = sum(hist_vol) / len(hist_vol) if hist_vol else 0
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0
        if change_pct < -2 and vol_ratio > 1.3:
            vp_state = 'bad'
            reasons_bad.append(f"放量下跌(量比{vol_ratio:.1f})")
        elif change_pct < 0 and vol_ratio < 0.8:
            vp_state = 'good'
            reasons_good.append("缩量回踩")

    # ── 汇总 ──
    bad_count = sum(1 for s in [cap_state, trend_state, vp_state] if s == 'bad')
    if trend_state == 'bad' and cap_state == 'bad':
        level = '转弱'    # 趋势破 + 资金撤 = 真变质
    elif bad_count >= 2:
        level = '转弱'
    elif bad_count == 1:
        level = '观察'
    else:
        level = '健康'

    return {
        'level': level,
        'reasons_bad': reasons_bad,
        'reasons_good': reasons_good,
        'cap_state': cap_state,
        'trend_state': trend_state,
    }


# ==================== 单只持仓评估 ====================

def evaluate_position(h: Dict) -> Optional[Dict]:
    """拉数据 + 算盈亏 + 算健康度 + 定结论。失败返回 None"""
    code = str(h['code']).strip()
    cost = float(h['cost'])
    target_pct = float(h.get('target_pct') or DEFAULT_TARGET_PCT)
    stop_pct = float(h.get('stop_pct') or DEFAULT_STOP_PCT)

    quote = data_source.fetch_realtime_quote(code)
    price = quote.get('price', 0.0)
    if price <= 0:
        logger.warning(f"{code} 行情获取失败，跳过")
        return None

    name = h.get('name') or quote.get('name') or code
    capital = data_source.fetch_capital_flow(code)

    klines = get_klines_with_today(
        code, price,
        quote.get('high', 0.0), quote.get('low', 0.0),
        quote.get('open', 0.0), float(quote.get('volume', 0) or 0),
    )

    health = compute_health(code, quote, capital, klines)

    target_price = round(cost * (1 + target_pct / 100), 2)
    stop_price = round(cost * (1 - stop_pct / 100), 2)
    pnl_pct = round((price - cost) / cost * 100, 2)

    # ── 结论（止损硬，止盈结合健康度）──
    if price <= stop_price:
        action = '止损'
        action_msg = f"触止损线{stop_price}，按纪律该砍"
    elif price >= target_price:
        if health['level'] == '健康':
            action = '到目标·仍强'
            action_msg = f"到目标价{target_price}且趋势仍强，可移动止盈让利润跑"
        else:
            action = '到目标·走弱'
            action_msg = f"到目标价{target_price}且开始走弱，建议落袋为安"
    elif health['level'] == '转弱':
        action = '逻辑走坏'
        action_msg = "未到止损但逻辑走坏（趋势破+资金撤），可提前撤"
    elif health['level'] == '观察':
        action = '观察'
        action_msg = "出现走弱迹象，留意"
    else:
        action = '持有'
        action_msg = "逻辑健康，按计划持有"

    return {
        'code': code, 'name': name, 'price': price, 'cost': cost,
        'pnl_pct': pnl_pct, 'target_price': target_price, 'stop_price': stop_price,
        'health': health, 'action': action, 'action_msg': action_msg,
        'capital': capital, 'quote': quote,
        'shares': h.get('shares'),
    }


# ==================== 推送格式化 ====================

_ACTION_ICON = {
    '止损': '🛑', '到目标·仍强': '🚀', '到目标·走弱': '✅',
    '逻辑走坏': '⚠️', '观察': '👀', '持有': '🟢',
}


def format_round_message(results: List[Dict], round_num: int) -> str:
    now = get_beijing_now().strftime('%m-%d %H:%M')
    lines = [f"### 📊 持仓盯盘 · 第{round_num}轮 ({now})", ""]

    # 需要立即行动的置顶
    urgent = [r for r in results if r['action'] in ('止损', '到目标·仍强', '到目标·走弱', '逻辑走坏')]
    if urgent:
        lines.append("**⚡ 需要决策**")
        for r in urgent:
            icon = _ACTION_ICON.get(r['action'], '•')
            lines.append(f"{icon} **{r['code']} {r['name']}** ¥{r['price']:.2f} "
                         f"({r['pnl_pct']:+.1f}%)")
            lines.append(f"  ↳ {r['action_msg']}")
        lines.append("")

    lines.append("**持仓明细**")
    for r in results:
        icon = _ACTION_ICON.get(r['action'], '•')
        pnl = r['pnl_pct']
        pnl_str = f'<font color="#FF0000">{pnl:+.1f}%</font>' if pnl >= 0 else \
                  f'<font color="#00AA00">{pnl:+.1f}%</font>'
        lines.append(f"{icon} **{r['code']} {r['name']}** ¥{r['price']:.2f}  {pnl_str}  [{r['health']['level']}]")
        cap = r['capital']
        main_in = cap.get('main_net_in', 0)
        detail = f"  ↳ 成本{r['cost']:.2f} 目标{r['target_price']:.2f} 止损{r['stop_price']:.2f} 主力{main_in:+.0f}万"
        lines.append(detail)
        bad = r['health'].get('reasons_bad', [])
        if bad:
            lines.append(f"  ↳ ⚠️ {' / '.join(bad)}")

    return "\n".join(lines)


# ==================== 主循环 ====================

def run_one_round(webhook: str, secret: str, round_num: int) -> None:
    holdings = load_positions()
    if not holdings:
        logger.info("无持仓，跳过")
        return

    logger.info(f"第{round_num}轮：检查 {len(holdings)} 只持仓")
    results = []
    for h in holdings:
        try:
            r = evaluate_position(h)
            if r:
                results.append(r)
                logger.info(f"  {r['code']} {r['name']} {r['pnl_pct']:+.1f}% "
                            f"[{r['health']['level']}] {r['action']}")
        except Exception as e:
            logger.error(f"评估失败 {h.get('code')}: {e}")

    if not results:
        logger.warning("本轮无有效结果")
        return

    content = format_round_message(results, round_num)
    title = f"持仓盯盘 第{round_num}轮 | {len(results)}只"
    if webhook and secret:
        send_dingtalk(webhook, secret, title, content)
    else:
        print(content)


def _interruptible_sleep(seconds: int) -> None:
    for _ in range(int(seconds)):
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description='持仓盯盘')
    parser.add_argument('--now', action='store_true', help='立即跑一轮')
    args = parser.parse_args()

    # 持仓盯盘用独立的钉钉机器人（与选股信号分开推送）；未配置则仅控制台输出
    webhook = os.environ.get('POSITION_DINGTALK_WEBHOOK', '')
    secret = os.environ.get('POSITION_DINGTALK_SECRET', '')
    if not webhook or not secret:
        logger.warning("未配置持仓盯盘钉钉（POSITION_DINGTALK_WEBHOOK/SECRET），仅控制台输出")

    if args.now:
        run_one_round(webhook, secret, 1)
        return

    if not is_trading_day():
        logger.info("非交易日，退出")
        return

    round_count = 0
    while True:
        if is_after_trading():
            logger.info("已收盘，退出")
            break
        if is_trading_time():
            round_count += 1
            run_one_round(webhook, secret, round_count)
            if not is_after_trading():
                logger.info(f"等待 {SCAN_INTERVAL}s 后下一轮")
                _interruptible_sleep(SCAN_INTERVAL)
        else:
            # 未开盘或午休，等10分钟再看
            logger.info("非交易时段，等待中")
            _interruptible_sleep(600)


if __name__ == '__main__':
    main()
