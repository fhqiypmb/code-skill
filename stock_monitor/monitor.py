"""
股票信号监控 - GitHub Actions 版
单线程循环：三个周期顺序扫描 → 等5分钟 → 再扫 → 收盘自动退出

用法:
    python monitor.py              # 正常运行（等待交易时间）
    python monitor.py --now        # 立即扫描一次（不等交易时间，用于测试）

环境变量:
    DINGTALK_WEBHOOK  - 钉钉机器人Webhook URL
    DINGTALK_SECRET   - 钉钉机器人加签密钥
"""

import os
import sys
import time
import json
import logging
import argparse
import importlib.util
from datetime import datetime, timedelta
from typing import List, Tuple

# 将上级目录加入路径
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)

# 动态导入中文文件名模块
_screener_path = os.path.join(PARENT_DIR, '严格选股_多周期.py')
spec = importlib.util.spec_from_file_location("screener", _screener_path)
screener = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screener)

from notifier import send_dingtalk, format_signal_message

# 导入板块趋势分析模块
try:
    from stock_analyzer import analyze_stock, format_analysis_report
    _HAS_ANALYZER = True
except ImportError:
    _HAS_ANALYZER = False

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== 配置 ====================
# 扫描周期（顺序执行）
PERIODS = [
    {"name": "5分钟", "code": "5min", "max_workers": 4},
    {"name": "30分钟", "code": "30min", "max_workers": 4},
    {"name": "日线", "code": "240min", "max_workers": 6},
]

# 每轮扫描完成后等待时间（秒）
SCAN_INTERVAL = 300  # 5分钟

# 交易时间
TRADING_START_MORNING = "09:25"
TRADING_END_MORNING = "11:35"
TRADING_START_AFTERNOON = "12:55"
TRADING_END_AFTERNOON = "15:05"

# 去重窗口（小时）
DEDUP_HOURS = 24

# 信号结果文件（会被 Actions commit 到仓库）
SIGNALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'signals')


# ==================== 交易时间判断 ====================
def is_trading_time() -> bool:
    """判断当前是否在交易时间内（北京时间）"""
    now = get_beijing_now()
    if now.weekday() >= 5:
        return False
    t = now.strftime('%H:%M')
    morning = TRADING_START_MORNING <= t <= TRADING_END_MORNING
    afternoon = TRADING_START_AFTERNOON <= t <= TRADING_END_AFTERNOON
    return morning or afternoon


def is_before_trading() -> bool:
    """判断是否在当天开盘前"""
    now = get_beijing_now()
    if now.weekday() >= 5:
        return False
    return now.strftime('%H:%M') < TRADING_START_MORNING


def is_after_trading() -> bool:
    """判断是否在当天收盘后"""
    now = get_beijing_now()
    return now.strftime('%H:%M') > TRADING_END_AFTERNOON or now.weekday() >= 5


def is_lunch_break() -> bool:
    """判断是否在午休"""
    now = get_beijing_now()
    t = now.strftime('%H:%M')
    return TRADING_END_MORNING < t < TRADING_START_AFTERNOON


def get_beijing_now() -> datetime:
    """获取北京时间（GitHub Actions 服务器是UTC）"""
    utc_now = datetime.utcnow()
    return utc_now + timedelta(hours=8)


def seconds_to_next_session() -> int:
    """计算到下一个交易时段的秒数"""
    now = get_beijing_now()
    t = now.strftime('%H:%M')

    def to_dt(time_str):
        h, m = map(int, time_str.split(':'))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)

    if t < TRADING_START_MORNING:
        return int((to_dt(TRADING_START_MORNING) - now).total_seconds())
    elif TRADING_END_MORNING < t < TRADING_START_AFTERNOON:
        return int((to_dt(TRADING_START_AFTERNOON) - now).total_seconds())

    return 0


# ==================== 信号去重 ====================
class SignalDedup:
    """信号去重：同一信号在窗口期内不重复推送"""

    def __init__(self):
        self._sent = {}
        self._file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'sent_signals.json'
        )
        self._load()

    def _load(self):
        if os.path.exists(self._file):
            try:
                with open(self._file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                now = time.time()
                self._sent = {k: v for k, v in data.items()
                              if now - v < DEDUP_HOURS * 3600}
            except Exception:
                self._sent = {}

    def _save(self):
        try:
            with open(self._file, 'w', encoding='utf-8') as f:
                json.dump(self._sent, f, ensure_ascii=False)
        except Exception:
            pass

    def is_new(self, period: str, code: str, signal_date: str, signal_type: str) -> bool:
        key = f"{period}|{code}|{signal_date}|{signal_type}"
        ts = self._sent.get(key)
        if ts and time.time() - ts < DEDUP_HOURS * 3600:
            return False
        return True

    def mark_sent(self, period: str, code: str, signal_date: str, signal_type: str):
        key = f"{period}|{code}|{signal_date}|{signal_type}"
        self._sent[key] = time.time()
        self._save()


# ==================== 信号结果保存 ====================
def save_signals_to_file(period_name: str, normal_results: list, strict_results: list):
    """保存信号结果到文件，供前端读取或 Actions commit"""
    os.makedirs(SIGNALS_DIR, exist_ok=True)

    today = get_beijing_now().strftime('%Y-%m-%d')
    filename = os.path.join(SIGNALS_DIR, f'{today}.json')

    # 读取已有记录
    existing = []
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    timestamp = get_beijing_now().strftime('%Y-%m-%d %H:%M:%S')

    for code, name, details in strict_results:
        existing.append({
            'time': timestamp,
            'period': period_name,
            'type': '严格买入',
            'code': code,
            'name': name,
            'close': details.get('close', 0),
            'signal_date': details.get('date', ''),
            'gold_cross_date': details.get('gold_cross_date', ''),
        })

    for code, name, details in normal_results:
        existing.append({
            'time': timestamp,
            'period': period_name,
            'type': '普通买入',
            'code': code,
            'name': name,
            'close': details.get('close', 0),
            'signal_date': details.get('date', ''),
            'gold_cross_date': details.get('gold_cross_date', ''),
        })

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info(f"信号已保存到 {filename}")


# ==================== 单信号即时推送 ====================
def _format_single_signal(period_name: str, code: str, name: str,
                          signal_type: str, details: dict) -> str:
    """格式化单只股票的信号消息 + 板块趋势分析"""
    tag = "🔴 严格买入" if signal_type == 'strict' else "🟡 普通买入"
    lines = [
        f"## {tag} | {period_name}",
        "",
        f"**{code} {name}**",
        "",
        f"| 项目 | 值 |",
        f"|------|------|",
        f"| 收盘价 | {details.get('close', 0):.2f} |",
        f"| 金叉日期 | {details.get('gold_cross_date', '')} |",
        f"| 放量阳日期 | {details.get('first_double_date', '')} |",
        f"| 确认阳日期 | {details.get('date', '')} |",
    ]

    # 板块趋势分析
    if _HAS_ANALYZER:
        try:
            result = analyze_stock(code, name)
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("### 📈 板块趋势分析")
            lines.append("")

            # 行业趋势
            for sr in result.get('sector_results', []):
                if sr['type'] == '行业':
                    t = sr['trend']
                    lines.append(f"- 行业 **{sr['name']}**: {t['trend']}  "
                                 f"近5日{t.get('recent_5d_chg', 0):+.1f}%  "
                                 f"近20日{t.get('recent_20d_chg', 0):+.1f}%")
                    break

            # 上升概念
            concept_list = [sr for sr in result.get('sector_results', []) if sr['type'] == '概念']
            rising = [sr for sr in concept_list if sr['trend'].get('score', 0) >= 55]
            falling = [sr for sr in concept_list if sr['trend'].get('score', 0) < 30]
            total_c = len(concept_list)

            if rising:
                names_str = ', '.join(f"{sr['name']}({sr['trend']['trend']})" for sr in rising[:5])
                lines.append(f"- 上升概念({len(rising)}个): {names_str}")
            if falling:
                names_str = ', '.join(f"{sr['name']}({sr['trend']['trend']})" for sr in falling[:3])
                lines.append(f"- 弱势概念({len(falling)}个): {names_str}")
            if total_c > 0:
                lines.append(f"- 概念总览: {total_c}个, {len(rising)}个上升, {len(falling)}个弱势")

            # 新闻
            news_info = result.get('news_info', {})
            sentiment = news_info.get('sentiment', '中性')
            hot = news_info.get('hot_keywords', [])
            news_str = f"消息面{sentiment}"
            if hot:
                news_str += f"(热点: {','.join(hot)})"
            lines.append(f"- {news_str}")

            # 结论
            prob = result.get('probability', 0)
            lines.append(f"")
            lines.append(f"**近期上涨概率: {prob}%**")
        except Exception as e:
            logger.warning(f"板块分析失败 {code}: {e}")

    return "\n".join(lines)


# ==================== 单周期扫描（边扫边推） ====================
def run_scan(period_cfg: dict, stock_list: list, webhook: str, secret: str, dedup: SignalDedup):
    """执行一个周期的选股扫描，扫到信号立即推送，并返回本轮推送的信号列表"""
    period_name = period_cfg['name']
    period_code = period_cfg['code']
    max_workers = period_cfg['max_workers']

    logger.info(f"[{period_name}] 开始扫描 {len(stock_list)} 只股票...")
    start = time.time()

    screener.reset_throttle_counts()

    s = screener.StrictStockScreener(
        period=period_code,
        period_name=period_name,
        max_workers=max_workers
    )

    # 记录本轮推送的信号
    pushed_count = [0]  # 用list以便在闭包中修改
    pushed_signals = []  # 收集本轮推送的信号

    def on_signal(code, name, signal_type, details):
        """回调：扫到信号立即去重+推送+保存"""
        signal_date = details.get('date', '')

        # 去重
        if not dedup.is_new(period_code, code, signal_date, signal_type):
            logger.info(f"[{period_name}] {code} {name} 已推送过，跳过")
            return

        dedup.mark_sent(period_code, code, signal_date, signal_type)

        # 保存到文件
        if signal_type == 'strict':
            save_signals_to_file(period_name, [], [(code, name, details)])
        else:
            save_signals_to_file(period_name, [(code, name, details)], [])

        # 立即推送钉钉
        tag = "严格" if signal_type == 'strict' else "普通"
        title = f"{tag}买入 | {period_name} | {code} {name}"
        content = _format_single_signal(period_name, code, name, signal_type, details)
        send_dingtalk(webhook, secret, title, content)
        pushed_count[0] += 1

        # 收集信号用于汇总
        pushed_signals.append({
            'period': period_name,
            'code': code,
            'name': name,
            'signal_type': signal_type,
            'details': details,
        })

    normal_results, strict_results = s.screen_all_stocks(stock_list, on_signal=on_signal)

    elapsed = time.time() - start
    logger.info(f"[{period_name}] 扫描完成，耗时 {elapsed:.0f}s，"
                f"严格 {len(strict_results)} + 普通 {len(normal_results)}，"
                f"本轮推送 {pushed_count[0]} 条")

    return pushed_signals


# ==================== 一轮完整扫描 ====================
def _format_round_summary(all_signals: list, round_num: int) -> str:
    """格式化一轮扫描的汇总消息"""
    beijing_now = get_beijing_now().strftime('%H:%M')
    lines = [f"## 📋 第{round_num}轮扫描汇总 ({beijing_now})", ""]

    if not all_signals:
        lines.append("本轮未发现新信号")
        return "\n".join(lines)

    # 按周期分组
    from collections import OrderedDict
    grouped = OrderedDict()
    for sig in all_signals:
        period = sig['period']
        if period not in grouped:
            grouped[period] = {'strict': [], 'normal': []}
        grouped[period][sig['signal_type']].append(sig)

    for period, sigs in grouped.items():
        lines.append(f"### {period}")
        lines.append("")
        lines.append("| 类型 | 代码 | 名称 | 收盘价 | 信号日期 |")
        lines.append("|------|------|------|--------|----------|")
        for s in sigs['strict']:
            d = s['details']
            lines.append(f"| 🔴严格 | {s['code']} | {s['name']} | {d.get('close', 0):.2f} | {d.get('date', '')} |")
        for s in sigs['normal']:
            d = s['details']
            lines.append(f"| 🟡普通 | {s['code']} | {s['name']} | {d.get('close', 0):.2f} | {d.get('date', '')} |")
        lines.append("")

    strict_total = sum(1 for s in all_signals if s['signal_type'] == 'strict')
    normal_total = sum(1 for s in all_signals if s['signal_type'] == 'normal')
    lines.append(f"**合计 {len(all_signals)} 只** (严格 {strict_total} + 普通 {normal_total})")

    return "\n".join(lines)


def run_full_round(stock_list: list, webhook: str, secret: str, dedup: SignalDedup,
                   round_num: int = 0):
    """依次扫描三个周期，最后推送整合汇总"""
    beijing_now = get_beijing_now().strftime('%H:%M:%S')
    logger.info(f"========== 开始新一轮扫描 (北京时间 {beijing_now}) ==========")

    all_signals = []
    for period_cfg in PERIODS:
        signals = run_scan(period_cfg, stock_list, webhook, secret, dedup)
        all_signals.extend(signals)

    logger.info(f"========== 本轮扫描完成，共 {len(all_signals)} 条新信号 ==========")

    # 推送整合汇总消息
    title = f"第{round_num}轮汇总 | 共{len(all_signals)}条信号"
    content = _format_round_summary(all_signals, round_num)
    send_dingtalk(webhook, secret, title, content)


# ==================== 主循环 ====================
def main():
    parser = argparse.ArgumentParser(description='股票信号监控')
    parser.add_argument('--now', action='store_true', help='立即扫描一次（不等交易时间）')
    args = parser.parse_args()

    # 从环境变量读取Token
    webhook = os.environ.get('DINGTALK_WEBHOOK', '')
    secret = os.environ.get('DINGTALK_SECRET', '')
    if not webhook or not secret:
        logger.warning("DINGTALK_WEBHOOK 或 DINGTALK_SECRET 未设置，仅控制台输出，不推送钉钉")

    # 加载股票列表
    s = screener.StrictStockScreener()
    stock_list = s.load_stock_list()
    if not stock_list:
        logger.error("股票列表为空，请确保 stock_list.md 存在")
        sys.exit(1)

    dedup = SignalDedup()

    logger.info("=" * 60)
    logger.info("  股票信号监控启动 (GitHub Actions 版)")
    logger.info(f"  监控周期: {', '.join(p['name'] for p in PERIODS)}")
    logger.info(f"  股票数量: {len(stock_list)}")
    logger.info(f"  扫描间隔: {SCAN_INTERVAL}s (跑完等5分钟)")
    logger.info(f"  钉钉推送: {'已配置' if webhook and secret else '未配置'}")
    logger.info(f"  北京时间: {get_beijing_now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # --now 模式：立即跑一次就退出
    if args.now:
        logger.info("立即扫描模式")
        run_full_round(stock_list, webhook, secret, dedup, round_num=1)
        return

    # 正常模式：循环到收盘
    round_count = 0
    while True:
        if is_after_trading():
            logger.info("已收盘，退出")
            break

        if is_trading_time():
            round_count += 1
            logger.info(f"--- 第 {round_count} 轮 ---")
            run_full_round(stock_list, webhook, secret, dedup, round_num=round_count)

            # 跑完等5分钟
            if not is_after_trading():
                logger.info(f"等待 {SCAN_INTERVAL}s 后开始下一轮...")
                time.sleep(SCAN_INTERVAL)

        elif is_before_trading():
            wait = seconds_to_next_session()
            next_time = (get_beijing_now() + timedelta(seconds=wait)).strftime('%H:%M')
            logger.info(f"未开盘，等待到 {next_time} ({wait}s)")
            time.sleep(wait)

        elif is_lunch_break():
            wait = seconds_to_next_session()
            next_time = (get_beijing_now() + timedelta(seconds=wait)).strftime('%H:%M')
            logger.info(f"午休中，等待到 {next_time} ({wait}s)")
            time.sleep(wait)

        else:
            time.sleep(30)

    logger.info(f"今日共完成 {round_count} 轮扫描")


if __name__ == "__main__":
    main()
