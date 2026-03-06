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
import signal
import logging
import argparse
import importlib.util
from datetime import datetime, timedelta
from typing import List, Tuple

# ==================== 优雅退出 ====================
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    logger = logging.getLogger(__name__)
    logger.info(f"收到终止信号 ({signum})，正在退出...")
    # 通知 screener 线程池立即停止
    try:
        screener.set_control_state('stopped')
    except Exception:
        pass


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# 将上级目录加入路径
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)

# 动态导入中文文件名模块
_screener_path = os.path.join(PARENT_DIR, '严格选股_多周期.py')
spec = importlib.util.spec_from_file_location("screener", _screener_path)
screener = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screener)

from notifier import send_dingtalk, format_signal_message

# 导入概念/板块分析模块
try:
    from stock_concept_analyzer import analyze_stock_concept
    _HAS_CONCEPT_ANALYZER = True
except ImportError:
    _HAS_CONCEPT_ANALYZER = False

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== 配置 ====================
# 环境自适应：CI/GitHub Actions 跨境延迟高，需更多线程填充I/O等待
_is_ci = os.environ.get('GITHUB_ACTIONS') == 'true' or os.environ.get('CI') == 'true'

# 扫描周期（顺序执行）
if _is_ci:
    PERIODS = [
        {"name": "5分钟", "code": "5min", "max_workers": 10},
        {"name": "30分钟", "code": "30min", "max_workers": 10},
        {"name": "日线", "code": "240min", "max_workers": 14},
    ]
else:
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
DEDUP_HOURS = 2

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


# ==================== 信号类型映射 ====================
_SIGNAL_TYPE_ICONS = {
    '严格': '🔴',
    '筑底': '🟢',
    '突破': '🔵',
    '普通': '🟡',
}


# ==================== 单信号即时推送 ====================
def _format_single_signal(period_name: str, code: str, name: str,
                          signal_type: str, details: dict) -> str:
    """格式化单只股票的信号消息（包含概念分析）"""
    icon = _SIGNAL_TYPE_ICONS.get(signal_type, '⚪')
    tag = f"{icon}{signal_type}买入"

    close = details.get('close', 0)
    gold_cross = details.get('gold_cross_date', '')
    confirm = details.get('date', '')

    lines = [
        f"### {tag} | {period_name}",
        f"**{code} {name}** ¥{close:.2f}",
        f"金叉:{gold_cross} 确认:{confirm}",
    ]

    # 获取概念/板块信息
    if _HAS_CONCEPT_ANALYZER:
        try:
            concept_analysis = analyze_stock_concept(code, name, details)

            # 行业
            if concept_analysis['industry']:
                ind_chg = concept_analysis['industry_info'].get('change', 0)
                lines.append(f"**行业**: {concept_analysis['industry']} ({ind_chg:+.2f}%)")

            # 热概念
            if concept_analysis['hot_concepts']:
                concepts_str = " / ".join([
                    f"{c['name']}({c['change']:+.2f}%)"
                    for c in concept_analysis['hot_concepts'][:3]
                ])
                lines.append(f"**热概念**: {concepts_str}")

            # 建议
            lines.append(f"**评价**: {concept_analysis['recommendation']}")

        except Exception as e:
            logger.warning(f"概念分析失败 {code}: {e}")

    return "\n\n".join(lines)


# ==================== 单周期扫描（边扫边推） ====================
def run_scan(period_cfg: dict, stock_list: list, webhook: str, secret: str, dedup: SignalDedup):
    """执行一个周期的选股扫描，扫到信号立即推送，并返回本轮推送的信号列表"""
    if _shutdown:
        return []

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
        """回调：扫到信号立即去重+推送+保存（普通信号只汇总不单推）"""
        signal_date = details.get('date', '')
        is_normal = signal_type in ('普通', 'normal')

        # 去重
        if not dedup.is_new(period_code, code, signal_date, signal_type):
            logger.info(f"[{period_name}] {code} {name} 已推送过，跳过")
            return

        dedup.mark_sent(period_code, code, signal_date, signal_type)

        # 保存到文件
        if is_normal:
            save_signals_to_file(period_name, [(code, name, details)], [])
        else:
            save_signals_to_file(period_name, [], [(code, name, details)])

        # 普通信号不单推，只收集到汇总
        if not is_normal:
            title = f"{signal_type}买入 | {period_name} | {code} {name}"
            content = _format_single_signal(period_name, code, name, signal_type, details)
            send_dingtalk(webhook, secret, title, content)
            pushed_count[0] += 1

        # 所有信号都收集用于汇总
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

    # 检查限流情况并通知
    throttle_info = screener.get_throttle_summary()
    if throttle_info:
        logger.warning(f"[{period_name}] {throttle_info}")
        beijing_now = get_beijing_now().strftime('%H:%M')
        title = f"⚠️ 数据源限流告警 | {period_name}"
        content = "\n".join([
            f"## ⚠️ 数据源限流告警",
            "",
            f"**周期**: {period_name}",
            f"**时间**: {beijing_now}",
            f"**详情**: {throttle_info}",
            "",
            f"扫描耗时 {elapsed:.0f}s，限流可能导致部分股票数据获取失败。",
        ])
        send_dingtalk(webhook, secret, title, content)

    return pushed_signals


# ==================== 一轮完整扫描 ====================
def _format_round_summary(all_signals: list, round_num: int) -> str:
    """格式化一轮扫描的汇总消息（精简版，无普通信号）"""
    beijing_now = get_beijing_now().strftime('%H:%M')
    lines = [f"### 📋 第{round_num}轮汇总 ({beijing_now})"]

    if not all_signals:
        lines.append("本轮无新信号")
        return "\n\n".join(lines)

    # 按周期分组
    from collections import OrderedDict
    grouped = OrderedDict()
    for sig in all_signals:
        period = sig['period']
        if period not in grouped:
            grouped[period] = []
        grouped[period].append(sig)

    for period, sigs in grouped.items():
        lines.append(f"**{period}**")
        for s in sigs:
            d = s['details']
            icon = _SIGNAL_TYPE_ICONS.get(s['signal_type'], '⚪')
            lines.append(f"{icon}{s['signal_type']} {s['code']} {s['name']} ¥{d.get('close', 0):.2f}")

    lines.append(f"共{len(all_signals)}条")
    return "\n\n".join(lines)


def run_full_round(stock_list: list, webhook: str, secret: str, dedup: SignalDedup,
                   round_num: int = 0):
    """依次扫描三个周期，最后推送整合汇总"""
    beijing_now = get_beijing_now().strftime('%H:%M:%S')
    logger.info(f"========== 开始新一轮扫描 (北京时间 {beijing_now}) ==========")

    all_signals = []
    for period_cfg in PERIODS:
        if _shutdown:
            logger.info("收到终止信号，跳过剩余周期")
            break
        signals = run_scan(period_cfg, stock_list, webhook, secret, dedup)
        all_signals.extend(signals)

    if _shutdown:
        logger.info(f"========== 扫描被终止，已收集 {len(all_signals)} 条信号 ==========")
    else:
        logger.info(f"========== 本轮扫描完成，共 {len(all_signals)} 条新信号 ==========")

    # 推送整合汇总消息
    title = f"第{round_num}轮汇总 | 共{len(all_signals)}条信号"
    content = _format_round_summary(all_signals, round_num)
    send_dingtalk(webhook, secret, title, content)


def _interruptible_sleep(seconds: int):
    """可中断的sleep，每秒检查一次退出标志"""
    for _ in range(int(seconds)):
        if _shutdown:
            return
        time.sleep(1)


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
    logger.info(f"  运行环境: {'CI/GitHub Actions' if _is_ci else '本地'}")
    logger.info(f"  监控周期: {', '.join(p['name'] for p in PERIODS)}")
    threads_info = ', '.join(f"{p['name']}={p['max_workers']}线程" for p in PERIODS)
    logger.info(f"  线程配置: {threads_info}")
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
    while not _shutdown:
        if is_after_trading():
            logger.info("已收盘，退出")
            break

        if is_trading_time():
            round_count += 1
            logger.info(f"--- 第 {round_count} 轮 ---")
            run_full_round(stock_list, webhook, secret, dedup, round_num=round_count)

            # 跑完等5分钟（可中断）
            if not is_after_trading() and not _shutdown:
                logger.info(f"等待 {SCAN_INTERVAL}s 后开始下一轮...")
                _interruptible_sleep(SCAN_INTERVAL)

        elif is_before_trading():
            wait = seconds_to_next_session()
            next_time = (get_beijing_now() + timedelta(seconds=wait)).strftime('%H:%M')
            logger.info(f"未开盘，等待到 {next_time} ({wait}s)")
            _interruptible_sleep(wait)

        elif is_lunch_break():
            wait = seconds_to_next_session()
            next_time = (get_beijing_now() + timedelta(seconds=wait)).strftime('%H:%M')
            logger.info(f"午休中，等待到 {next_time} ({wait}s)")
            _interruptible_sleep(wait)

        else:
            _interruptible_sleep(30)

    logger.info(f"今日共完成 {round_count} 轮扫描")


if __name__ == "__main__":
    main()
