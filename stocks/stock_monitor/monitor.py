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

# ==================== ML 模块（可选，失败不影响主流程） ====================
try:
    _ML_DIR = os.path.join(PARENT_DIR, 'ml')
    import sys as _sys
    if _ML_DIR not in _sys.path:
        _sys.path.insert(0, _ML_DIR)
    import shadow_learner as _shadow_learner
    _ML_AVAILABLE = True
except Exception as _ml_import_err:
    _ML_AVAILABLE = False
    logging.getLogger(__name__).warning(f"ML模块未加载: {_ml_import_err}")


def _ml_record_signal(code, name, period, signal_type, details, analysis):
    """将信号写入ML数据集并返回达标概率，失败自动重试最多3次"""
    if not _ML_AVAILABLE:
        return None
    for attempt in range(1, 4):
        try:
            return _shadow_learner.record_and_predict(
                code=code, name=name,
                period=period, signal_type=signal_type,
                screener_details=details,
                analysis=analysis,
            )
        except Exception as e:
            import traceback
            logging.getLogger(__name__).error(
                f"ML写入失败 {code} {name} (第{attempt}次): {e}\n{traceback.format_exc()}"
            )
            if attempt < 3:
                time.sleep(1)
    return None

def _get_probability_color(probability: float) -> str:
    """根据概率返回 HTML 颜色代码"""
    if probability >= 80:
        return "#FF0000"  # 红色 - 很高
    elif probability >= 65:
        return "#FF6600"  # 橙色 - 较高
    elif probability >= 50:
        return "#FFAA00"  # 黄色 - 中等
    elif probability >= 35:
        return "#0066FF"  # 蓝色 - 较低
    else:
        return "#00AA00"  # 绿色 - 低

def _format_colored_probability(probability: float) -> str:
    """格式化带颜色的上涨概率"""
    color = _get_probability_color(probability)
    level_map = {
        80: "很高",
        65: "较高",
        50: "中等",
        35: "较低",
    }
    level = "低"
    for threshold, level_name in level_map.items():
        if probability >= threshold:
            level = level_name
            break
    return f'<font color="{color}">{probability}% ({level})</font>'

import stock_analyzer

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

    # 构建已有信号的去重集合：(周期, 代码, 信号日期, 类型)
    existing_keys = {
        (r.get('period', ''), r.get('code', ''), r.get('signal_date', ''), r.get('type', ''))
        for r in existing
    }

    timestamp = get_beijing_now().strftime('%Y-%m-%d %H:%M:%S')
    added = 0

    for code, name, details in strict_results:
        key = (period_name, code, details.get('date', ''), '严格买入')
        if key in existing_keys:
            continue
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
        existing_keys.add(key)
        added += 1

    for code, name, details in normal_results:
        key = (period_name, code, details.get('date', ''), '普通买入')
        if key in existing_keys:
            continue
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
        existing_keys.add(key)
        added += 1

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info(f"信号已保存到 {filename}（新增{added}条，已有{len(existing)-added}条去重跳过）")


# ==================== 信号类型映射 ====================
_SIGNAL_TYPE_ICONS = {
    '严格': '🔴',
    '筑底': '🟢',
    '突破': '🔵',
    '普通': '🟡',
}


# ==================== 基本面分析辅助 ====================
def _run_stock_analysis(code: str, name: str, signal_type: str) -> dict:
    """对单只股票运行基本面分析，失败返回空dict"""
    try:
        result = stock_analyzer.analyze_stock(code, name, signal_type=signal_type)
        logger.info(f"基本面分析完成: {code} {name}")
        return result
    except Exception as e:
        logger.warning(f"基本面分析失败 {code} {name}: {e}")
        return {}


def _format_analysis_for_dingtalk(analysis: dict, details: dict = None) -> str:
    """将个股分析结果格式化为钉钉Markdown片段（结构化分行版）"""
    if not analysis:
        return ""

    lines = []

    tech     = analysis.get('technical', {})
    sr       = analysis.get('success_rate', {})
    mp       = analysis.get('market_pos', {})
    capital  = analysis.get('capital', {})
    industry = analysis.get('industry', '')
    concepts = analysis.get('concepts', [])

    # 目标价 & 止损
    if tech:
        gain   = tech.get('expected_gain_pct', 0)
        sl_pct = tech.get('stop_loss_pct', 0)
        lines.append(
            f"📈 **目标** {tech.get('target_price', 0):.2f}(**+{gain:.1f}%**)  "
            f"🛡 **止损** {tech.get('stop_loss', 0):.2f}({sl_pct:.1f}%)"
        )

    # 成功率 + 行业 + RS
    if sr:
        score         = sr.get('score', 0)
        grade         = sr.get('grade', '?')
        colored_score = _format_colored_probability(score)
        rs_str = ''
        if mp:
            rs = mp.get('relative_strength', 0)
            rs_str = f"  RS{rs:+.1f}%"
        industry_str = f"  {industry}" if industry else ""
        lines.append(f"⭐ **成功率** {colored_score} [{grade}级]{industry_str}{rs_str}")

    # 6维度（紧凑一行）
    if sr:
        dim_parts = [
            f"突破{sr.get('dim_breakout', 0):.0f}",
            f"动能{sr.get('dim_momentum', 0):.0f}",
            f"强度{sr.get('dim_rs', 0):.0f}",
            f"资金{sr.get('dim_capital', 0):.0f}",
            f"收益{sr.get('dim_rr', 0):.0f}",
            f"到达{sr.get('dim_reach_prob', 0):.0f}",
        ]
        lines.append("📊 " + " | ".join(dim_parts))

    # 主力资金 + 量比
    cap_parts = []
    if capital:
        main_in = capital.get('main_net_in', 0)
        flow    = capital.get('flow_ratio', 0)
        cap_parts.append(
            f"净买入 +{main_in:.0f}万({flow:+.1f}%)" if main_in > 0
            else f"净卖出 {main_in:.0f}万({flow:+.1f}%)"
        )
    if mp:
        cap_parts.append(f"量比 {mp.get('vol_ratio', 1):.2f}x")
    if cap_parts:
        lines.append("💰 " + "  ".join(cap_parts))

    # 金叉 & 确认日期
    if details:
        gold    = details.get('gold_cross_date', '')
        confirm = details.get('date', '')
        if gold or confirm:
            lines.append(f"🕐 金叉 {gold}  确认 {confirm}")

    # 概念（小字，折叠感，取前6个）
    if concepts:
        shown = concepts[:6]
        extra = f" 等{len(concepts)}个" if len(concepts) > 6 else ""
        lines.append(f"🏷 {' / '.join(shown)}{extra}")

    return "\n\n".join(lines)


# ==================== 单信号即时推送 ====================
def _format_single_signal(period_name: str, code: str, name: str,
                          signal_type: str, details: dict,
                          verdict: str = '', round_num: int = 0) -> str:
    """格式化单只股票的信号消息（标题区）"""
    icon      = _SIGNAL_TYPE_ICONS.get(signal_type, '⚪')
    round_tag = f" · 第{round_num}轮" if round_num else ""
    close     = details.get('close', 0)

    if verdict == '达标':
        verdict_html = f'<font color="#00AA00">**✅ {verdict}**</font>'
    elif verdict:
        verdict_html = f'<font color="#FF0000">**❌ {verdict}**</font>'
    else:
        verdict_html = ''

    lines = [
        f"### {icon} {signal_type}买入 · {period_name}{round_tag}",
        f"**{code} {name}  ¥{close:.2f}**  {verdict_html}",
    ]
    return "\n\n".join(lines)


# ==================== 单周期扫描（边扫边推） ====================
def run_scan(period_cfg: dict, stock_list: list, webhook: str, secret: str, dedup: SignalDedup, round_num: int = 0):
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
        """回调：扫到信号立即推送+保存（每轮都推，普通信号只汇总不单推）"""
        signal_date = details.get('date', '')
        is_normal = signal_type in ('普通', 'normal')

        # 保存到文件（save_signals_to_file 内部去重）
        if is_normal:
            save_signals_to_file(period_name, [(code, name, details)], [])
        else:
            save_signals_to_file(period_name, [], [(code, name, details)])

        # 所有信号都跑分析（普通信号也跑，汇总时用）
        analysis = _run_stock_analysis(code, name, signal_type)
        verdict  = analysis.get('verdict', '')   # 达标 / 空间不足 / 趋势偏弱
        sr       = analysis.get('success_rate', {})
        grade    = sr.get('grade', '?')          # S/A/B/C/D
        sr_score = sr.get('score', 0.0)

        # ML自动记录 + 预测（复用已有analysis，不重复请求）
        ml_prob = _ml_record_signal(code, name, period_name, signal_type, details, analysis)

        # 非普通信号：立即单推
        if not is_normal:
            icon = '🔴' if signal_type == '严格' else '🟢'
            round_tag = f" | 第{round_num}轮" if round_num else ""
            title = (
                f"{icon}{signal_type}买入"
                f" | {period_name} | {code} {name} | {verdict}{round_tag}"
            )
            content = _format_single_signal(
                period_name, code, name, signal_type, details,
                verdict=verdict, round_num=round_num
            )
            analysis_text = _format_analysis_for_dingtalk(analysis, details=details)
            if analysis_text:
                content += "\n\n" + analysis_text
            if ml_prob is not None:
                content += f"\n\n🤖 **ML达标概率** {ml_prob}%"
            send_dingtalk(webhook, secret, title, content)
            pushed_count[0] += 1

        # 所有信号都收集用于汇总
        sig_entry = {
            'period':      period_name,
            'code':        code,
            'name':        name,
            'signal_type': signal_type,
            'details':     details,
            'verdict':     verdict,
            'analysis':    analysis,
            'ml_prob':     ml_prob,
        }

        pushed_signals.append(sig_entry)

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
        lines.append(f"**── {period} ──**")
        for s in sigs:
            d        = s['details']
            verdict  = s.get('verdict', '')
            icon     = _SIGNAL_TYPE_ICONS.get(s['signal_type'], '⚪')
            analysis = s.get('analysis', {})
            sr       = analysis.get('success_rate', {}) if analysis else {}
            tech     = analysis.get('technical', {}) if analysis else {}
            mp       = analysis.get('market_pos', {}) if analysis else {}
            industry = analysis.get('industry', '') if analysis else ''

            # verdict 彩色
            if verdict == '达标':
                verdict_html = f'<font color="#00AA00">✅{verdict}</font>'
            elif verdict:
                verdict_html = f'<font color="#FF0000">❌{verdict}</font>'
            else:
                verdict_html = ''

            # 第1行：信号类型 + 股票 + 价格 + 达标
            lines.append(
                f"{icon}**{s['signal_type']}** {s['code']} {s['name']}"
                f" ¥{d.get('close', 0):.2f}  {verdict_html}"
            )

            # 第2行：成功率 + 目标/止损 + 行业 + RS + ML概率（紧凑一行）
            row2_parts = []
            if sr:
                score = sr.get('score', 0)
                grade = sr.get('grade', '?')
                row2_parts.append(f"{_format_colored_probability(score)}[{grade}]")
            if tech:
                gain = tech.get('expected_gain_pct', 0)
                sl   = tech.get('stop_loss_pct', 0)
                row2_parts.append(f"+{gain:.1f}%/-{abs(sl):.1f}%")
            if industry:
                row2_parts.append(industry)
            if mp:
                rs = mp.get('relative_strength', 0)
                row2_parts.append(f"RS{rs:+.1f}%")
            ml_prob = s.get('ml_prob')
            if ml_prob is not None:
                row2_parts.append(f"🤖{ml_prob}%")
            if row2_parts:
                lines.append("  ↳ " + "  ".join(row2_parts))

    lines.append(f"\n共 {len(all_signals)} 条信号")
    return "\n\n".join(lines)


def run_full_round(stock_list: list, webhook: str, secret: str, dedup: SignalDedup,
                   round_num: int = 0):
    """依次扫描三个周期，最后推送整合汇总"""
    beijing_now = get_beijing_now().strftime('%H:%M:%S')
    logger.info(f"========== 开始新一轮扫描 (北京时间 {beijing_now}) ==========")

    all_signals = []
    for idx, period_cfg in enumerate(PERIODS, 1):
        if _shutdown:
            logger.info("收到终止信号，跳过剩余周期")
            break
        logger.info(f">>> 开始扫描周期 {idx}/{len(PERIODS)}: {period_cfg['name']}")
        signals = run_scan(period_cfg, stock_list, webhook, secret, dedup, round_num=round_num)
        logger.info(f"<<< 周期 {idx}/{len(PERIODS)} 完成，获得 {len(signals)} 条信号")
        all_signals.extend(signals)

    if _shutdown:
        logger.info(f"========== 扫描被终止，已收集 {len(all_signals)} 条信号 ==========")
    else:
        logger.info(f"========== 本轮扫描完成，共 {len(all_signals)} 条新信号 ==========")

    # 普通信号已在 on_signal 里完成分析，此处无需补做

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
