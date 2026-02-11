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


# ==================== 单周期扫描 ====================
def run_scan(period_cfg: dict, stock_list: list, webhook: str, secret: str, dedup: SignalDedup):
    """执行一个周期的选股扫描"""
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

    normal_results, strict_results = s.screen_all_stocks(stock_list)

    elapsed = time.time() - start
    logger.info(f"[{period_name}] 扫描完成，耗时 {elapsed:.0f}s，"
                f"严格 {len(strict_results)} + 普通 {len(normal_results)}")

    if not normal_results and not strict_results:
        return

    # 保存到文件
    save_signals_to_file(period_name, normal_results, strict_results)

    # 去重过滤
    new_normal = []
    for code, name, details in normal_results:
        signal_date = details.get('date', '')
        if dedup.is_new(period_code, code, signal_date, 'normal'):
            new_normal.append((code, name, details))
            dedup.mark_sent(period_code, code, signal_date, 'normal')

    new_strict = []
    for code, name, details in strict_results:
        signal_date = details.get('date', '')
        if dedup.is_new(period_code, code, signal_date, 'strict'):
            new_strict.append((code, name, details))
            dedup.mark_sent(period_code, code, signal_date, 'strict')

    if not new_normal and not new_strict:
        logger.info(f"[{period_name}] 信号均已推送过，跳过")
        return

    # 推送微信
    total_new = len(new_normal) + len(new_strict)
    title = f"选股信号 | {period_name} | {total_new}只"
    content = format_signal_message(period_name, new_normal, new_strict)
    send_dingtalk(webhook, secret, title, content)


# ==================== 一轮完整扫描 ====================
def run_full_round(stock_list: list, webhook: str, secret: str, dedup: SignalDedup):
    """依次扫描三个周期"""
    beijing_now = get_beijing_now().strftime('%H:%M:%S')
    logger.info(f"========== 开始新一轮扫描 (北京时间 {beijing_now}) ==========")

    for period_cfg in PERIODS:
        run_scan(period_cfg, stock_list, webhook, secret, dedup)

    logger.info(f"========== 本轮扫描完成 ==========")


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
        run_full_round(stock_list, webhook, secret, dedup)
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
            run_full_round(stock_list, webhook, secret, dedup)

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
