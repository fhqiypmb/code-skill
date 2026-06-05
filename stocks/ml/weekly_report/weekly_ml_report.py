"""
周度 ML 预测报告生成器
=====================
从 shadow_data.json 中筛选本周已过交易日及前N周交易日 ml_predict_prob >= 阈值的股票，
同时生成 Markdown 报告 和 交互式 HTML 看板。

输出字段：股票名称、股票代码、信号类型、周期、信号价格、资金净流入(流入/流出)、大单净流入、动能、规则匹配、最高价、ML达标概率、ML潜力概率

用法:
    python weekly_ml_report.py                  # 默认阈值 40，覆盖已有报告，默认近1周
    python weekly_ml_report.py --threshold 50   # 自定义阈值
    python weekly_ml_report.py --mode new       # 删除旧报告再写新报告（默认覆盖）

注意：本文件处理动态 JSON 数据，类型标注用宽泛的 dict/list，
      以下 pyright 规则关闭是预期行为。
"""

# pyright: reportMissingTypeArgument=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportImplicitRelativeImport=false, reportUnusedImport=false, reportDeprecated=false

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

# ─────────────────── 路径配置 ───────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ML_DIR = os.path.dirname(_SCRIPT_DIR)          # stocks/ml/
_STOCKS_DIR = os.path.dirname(_ML_DIR)           # stocks/
if _STOCKS_DIR not in sys.path:
    sys.path.insert(0, _STOCKS_DIR)

import stock_analyzer

DATA_FILE = os.path.join(_ML_DIR, "shadow_data.json")
HOLIDAYS_FILE = os.path.join(_STOCKS_DIR, "stock_monitor", "holidays.json")
OUTPUT_FILE = os.path.join(_SCRIPT_DIR, "weekly_ml_report.md")
OUTPUT_HTML_FILE = os.path.join(_SCRIPT_DIR, "weekly_ml_report.html")

# ─────────────────── Emoji 定义 ───────────────────
E_FIRE      = "\U0001f525"   # 🔥
E_GREEN     = "\U0001f7e2"   # 🟢
E_YELLOW    = "\U0001f7e1"   # 🟡
E_RED       = "\U0001f534"   # 🔴
E_WHITE     = "\u26aa"       # ⚪
E_CHART     = "\U0001f4ca"   # 📊
E_CALENDAR  = "\U0001f4c5"   # 📅
E_TARGET    = "\U0001f3af"   # 🎯
E_UP        = "\U0001f4c8"   # 📈
E_EMPTY     = "\U0001f4ed"   # 📭
E_CLIPBOARD = "\U0001f4cb"   # 📋
E_BOOK      = "\U0001f4d6"   # 📖
E_REPEAT    = "\U0001f501"   # 🔁
E_MONEY     = "\U0001f4b0"   # 💰
E_ROCKET    = "\U0001f680"   # 🚀
E_PIN       = "\U0001f4cc"   # 📌


# ─────────────────── 终端兼容（GBK / UTF-8） ───────────────────

def _safe_print(msg: str):
    """安全输出，兼容 GBK 终端"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("gbk", "replace").decode("gbk"))


def _safe_input(prompt: str) -> str:
    """安全输入，兼容 GBK 终端"""
    try:
        return input(prompt)
    except UnicodeEncodeError:
        return input(prompt.encode("gbk", "replace").decode("gbk"))


# ─────────────────── 交易日计算 ───────────────────

def _load_holidays() -> set[str]:
    """加载法定假日（落在工作日的休市日）"""
    if not os.path.exists(HOLIDAYS_FILE):
        return set()
    with open(HOLIDAYS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    holidays = set()
    for year_key, dates in data.items():
        if year_key.startswith("_"):
            continue
        holidays.update(dates)
    return holidays


def _get_last_week_trading_days(today: datetime | None = None, weeks: int = 1) -> list[str]:
    """
    根据今天日期，返回「本周已过交易日 + 前 N 个完整周」的交易日列表。
    weeks=1 时取本周已过 + 上周；weeks=3 时取本周已过 + 上周 + 上上周 + 上上上周。
    自动排除周末和 holidays.json 中的法定假日。
    返回格式: ['2026-05-06', '2026-05-07', ...]  按日期降序
    """
    if today is None:
        today = datetime.now()

    holidays = _load_holidays()

    this_monday = today - timedelta(days=today.weekday())

    trading_days = []

    # ── 1. 本周已过交易日（this_monday ~ today，含今天） ──
    d = this_monday
    while d <= today:
        ds = d.strftime("%Y-%m-%d")
        weekday = d.weekday()
        if weekday < 5 and ds not in holidays:
            trading_days.append(ds)
        d += timedelta(days=1)

    # ── 2. 前 N 个完整周 ──
    for w in range(weeks):
        week_monday = this_monday - timedelta(days=7 * (w + 1))
        week_sunday = this_monday - timedelta(days=7 * w + 1)

        d = week_monday
        while d <= week_sunday:
            ds = d.strftime("%Y-%m-%d")
            weekday = d.weekday()
            if weekday >= 5:
                d += timedelta(days=1)
                continue
            if ds in holidays:
                d += timedelta(days=1)
                continue
            trading_days.append(ds)
            d += timedelta(days=1)

    trading_days.sort(reverse=True)
    return trading_days


# ─────────────────── 数据加载与筛选 ───────────────────

def _load_data() -> list[dict]:
    if not os.path.exists(DATA_FILE):
        _safe_print(f"[!] 数据文件不存在: {DATA_FILE}")
        sys.exit(1)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _filter_records(
    data: list[dict],
    trading_days: list[str],
    threshold: float,
) -> tuple[dict[str, list[dict]], set[str]]:
    """按日期分组，筛选 ml_predict_prob >= threshold 的记录，按 prob 降序

    返回: (filtered, no_ml_data_dates)
        filtered: Dict[str, List[Dict]]  日期 -> 符合条件的记录
        no_ml_data_dates: Set[str]  完全没有 ml_predict_prob 数据的日期集合
    """
    result = {}
    no_ml_data_dates = set()

    for d in trading_days:
        day_records = [r for r in data if r.get("date") == d]
        has_prob = any(
            isinstance(r.get("ml_predict_prob"), (int, float))
            for r in day_records
        )
        if not has_prob and day_records:
            no_ml_data_dates.add(d)
            continue
        if not day_records:
            no_ml_data_dates.add(d)
            continue

        items = [
            r for r in day_records
            if isinstance(r.get("ml_predict_prob"), (int, float))
            and r["ml_predict_prob"] >= threshold
        ]
        items.sort(key=lambda x: (
            -x.get("ml_predict_prob", 0),
            -_get_capital_net_value(x),
            -float(x.get("an_success_rate_dim_momentum") or 0),
        ))
        if items:
            result[d] = items
    return result, no_ml_data_dates


# ─────────────────── 字段提取辅助 ───────────────────

def _get_signal_price(r: dict) -> str:
    val = r.get("sc_close") or r.get("close") or ""
    if val != "":
        return f"{float(val):.2f}"
    return "-"


def _get_capital_flow(r: dict) -> str:
    net_in = r.get("an_capital_main_net_in")
    if net_in is None or net_in == "":
        return "-"
    net_in = float(net_in)
    icon = E_RED if net_in > 0 else (E_GREEN if net_in < 0 else E_WHITE)
    if abs(net_in) >= 10000:
        amount_str = f"{abs(net_in)/10000:.2f}亿"
    else:
        amount_str = f"{abs(net_in):.0f}万"
    sign = "+" if net_in > 0 else ("-" if net_in < 0 else "")
    ratio = r.get("an_capital_flow_ratio")
    ratio_str = "" if ratio is None or ratio == "" else f"({float(ratio):.2f})"
    return f"{icon}{sign}{amount_str}{ratio_str}"


def _get_capital_net_value(r: dict) -> float:
    net_in = r.get("an_capital_main_net_in")
    if net_in is None or net_in == "":
        return 0
    return float(net_in)


def _get_big_order_flow(r: dict) -> str:
    val = r.get("an_capital_big_net_in")
    if val is None or val == "":
        return "-"
    val = float(val)
    icon = E_RED if val > 0 else (E_GREEN if val < 0 else E_WHITE)
    sign = "+" if val > 0 else ""
    if abs(val) >= 10000:
        return f"{icon}{sign}{val/10000:.2f}亿"
    return f"{icon}{sign}{val:.0f}万"


def _format_amount_wan(val: float) -> str:
    sign = "+" if val > 0 else ""
    if abs(val) >= 10000:
        return f"{sign}{val/10000:.2f}亿"
    return f"{sign}{val:.0f}万"


def _get_capital_flow_plain(r: dict) -> str:
    net_in = r.get("an_capital_main_net_in")
    if net_in is None or net_in == "":
        return "-"
    net_in = float(net_in)
    ratio = r.get("an_capital_flow_ratio")
    ratio_str = "" if ratio is None or ratio == "" else f"({float(ratio):.1f})"
    return f"{_format_amount_wan(net_in)}{ratio_str}"


def _get_big_order_flow_plain(r: dict) -> str:
    val = r.get("an_capital_big_net_in")
    if val is None or val == "":
        return "-"
    return _format_amount_wan(float(val))


def _get_rule_score(r: dict) -> str:
    rule = stock_analyzer.calc_v2_rule_match(record=r)
    pct = rule['pct']
    if rule.get('is_full'):
        return f"{E_FIRE} **{pct}%【满分】**"
    if pct >= 86:
        return f"{E_RED} **{pct}%**"
    if pct >= 71:
        return f"{E_YELLOW} {pct}%"
    return f"{E_WHITE} {pct}%"


def _get_rule_score_plain(r: dict) -> str:
    rule = stock_analyzer.calc_v2_rule_match(record=r)
    pct = rule['pct']
    return f"**{pct}%**" if rule.get('is_full') or pct >= 90 else f"{pct}%"


def _get_rule_pct(r: dict) -> int:
    """返回规则匹配百分比整数，供 HTML 使用"""
    rule = stock_analyzer.calc_v2_rule_match(record=r)
    return rule['pct']


def _get_momentum(r: dict) -> str:
    val = r.get("an_success_rate_dim_momentum")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 80:
        return f"{E_FIRE} **{val:.1f}**"
    elif val >= 60:
        return f"{E_RED} {val:.1f}"
    elif val >= 40:
        return f"{E_YELLOW} {val:.1f}"
    else:
        return f"{E_WHITE} {val:.1f}"


def _get_high(r: dict) -> str:
    val = r.get("max_high")
    if val is not None and val != "":
        max_h = float(val)
        price_val = r.get("sc_close") or r.get("close") or ""
        if price_val != "":
            entry = float(price_val)
            if entry > 0:
                pct = (max_h - entry) / entry * 100
                sign = "+" if pct >= 0 else ""
                return f"{max_h:.2f}({sign}{pct:.1f}%)"
        return f"{max_h:.2f}"
    return "-"


def _get_predict_potential(r: dict) -> str:
    val = r.get("ml_predict_potential")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 30:
        return f"{E_FIRE} **{val:.1f}%**"
    elif val >= 25:
        return f"{E_RED} **{val:.1f}%**"
    elif val >= 20:
        return f"{E_YELLOW} {val:.1f}%"
    else:
        return f"{E_WHITE} {val:.1f}%"


def _get_vol_ratio(r: dict) -> str:
    val = r.get("an_market_pos_vol_ratio")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 1.5:
        return f"<font color=\"#d32f2f\"><b>{val:.2f}x</b></font>"
    if val >= 1.2:
        return f"<font color=\"#f57c00\">{val:.2f}x</font>"
    return f"{val:.2f}x"


def _get_space(r: dict) -> str:
    val = r.get("an_technical_expected_gain_pct")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 15:
        return f"<font color=\"#d32f2f\"><b>{val:.1f}%</b></font>"
    if val >= 10:
        return f"<font color=\"#f57c00\">{val:.1f}%</font>"
    return f"{val:.1f}%"


def _get_reach_prob(r: dict) -> str:
    val = r.get("an_success_rate_dim_reach_prob")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 70:
        return f"<font color=\"#d32f2f\"><b>{val:.0f}</b></font>"
    if val >= 60:
        return f"<font color=\"#f57c00\">{val:.0f}</font>"
    return f"{val:.0f}"


def _get_factor_summary(r: dict) -> str:
    momentum = r.get("an_success_rate_dim_momentum")
    if momentum is None or momentum == "":
        mom = "-"
    else:
        mom_val = float(momentum)
        mom = f"<font color=\"#d32f2f\"><b>{mom_val:.0f}</b></font>" if mom_val >= 95 else f"{mom_val:.0f}"
    return f"动{mom}｜量{_get_vol_ratio(r)}｜空{_get_space(r)}｜达{_get_reach_prob(r)}"


def _get_ml_summary(r: dict) -> str:
    prob = r.get("ml_predict_prob", 0)
    if prob is None or prob == "":
        prob_str = "-"
    else:
        prob_val = float(prob)
        if prob_val >= 28:
            prob_str = f"<font color=\"#d32f2f\"><b>{prob_val:.1f}%</b></font>"
        elif prob_val >= 25:
            prob_str = f"<font color=\"#f57c00\">{prob_val:.1f}%</font>"
        else:
            prob_str = f"{prob_val:.1f}%"
    potential = r.get("ml_predict_potential")
    if potential is None or potential == "":
        potential_str = "-"
    else:
        potential_val = float(potential)
        if potential_val >= 30:
            potential_str = f"<font color=\"#d32f2f\"><b>{potential_val:.1f}%</b></font>"
        elif potential_val >= 25:
            potential_str = f"<font color=\"#f57c00\">{potential_val:.1f}%</font>"
        else:
            potential_str = f"{potential_val:.1f}%"
    gain = r.get("ml_predict_gain")
    if gain is None or gain == "":
        gain_str = "-"
    else:
        gain_val = float(gain)
        if gain_val >= 67:
            gain_str = f"<font color=\"#d32f2f\"><b>{gain_val:.0f}</b></font>"
        elif gain_val >= 60:
            gain_str = f"<font color=\"#f57c00\">{gain_val:.0f}</font>"
        else:
            gain_str = f"{gain_val:.0f}"
    return f"达{prob_str} / 潜{potential_str} / 涨{gain_str}"


def _get_prob_str(prob: float) -> str:
    if prob >= 90:
        return f"{E_FIRE} **{prob:.1f}%**"
    elif prob >= 80:
        return f"{E_RED} **{prob:.1f}%**"
    elif prob >= 70:
        return f"{E_YELLOW} {prob:.1f}%"
    else:
        return f"{E_WHITE} {prob:.1f}%"


# ─────────────────── Markdown 生成 ───────────────────

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
WEEKDAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _weekday_cn(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return WEEKDAY_CN[d.weekday()]


def _render_table(records: list[dict]) -> str:
    lines = []
    lines.append("| # | 股票 | 信号 | 价/高 | 资金 | 大单 | 因子 | ML(胜/潜/涨) | 规则 |")
    lines.append("|:---:|:----|:---:|:----:|:----:|:----:|:----:|:----:|:---:|")
    for i, r in enumerate(records, 1):
        code = r.get("code", "")
        name = r.get("name", "")
        signal_type = r.get("signal_type", "")
        period = r.get("period", "")
        signal_price = _get_signal_price(r)
        high = _get_high(r)
        price_high = signal_price if high == "-" else f"{signal_price}/{high}"
        capital_flow = _get_capital_flow_plain(r)
        big_flow = _get_big_order_flow_plain(r)
        factors = _get_factor_summary(r)
        rule_score = _get_rule_score_plain(r)
        ml_summary = _get_ml_summary(r)
        stock = f"{code} {name}"
        signal = f"{signal_type}/{period}"
        lines.append(
            f"| {i} | {stock} | {signal} | {price_high} | {capital_flow} "
            f"| {big_flow} | {factors} | {ml_summary} | {rule_score} |"
        )
    return "\n".join(lines)


def generate_report(
    filtered: dict[str, list[dict]],
    trading_days: list[str],
    threshold: float,
    weeks: int = 1,
    no_ml_data_dates: set[str] | None = None,
) -> str:
    if no_ml_data_dates is None:
        no_ml_data_dates = set()
    today = datetime.now()
    total = sum(len(v) for v in filtered.values())

    weeks_label = f"本周 + 近 {weeks} 周" if weeks > 1 else "本周 + 上周"

    lines = []

    lines.append(f"# {E_CHART} ML 预测概率周报")
    lines.append("")
    lines.append(f"> {E_CALENDAR} 生成时间：{today.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> {E_CALENDAR} 报告周期：{weeks_label}（{trading_days[0]} ~ {trading_days[-1]}）")
    lines.append(f"> {E_TARGET} 筛选阈值：`ml_predict_prob >= {threshold}%`")
    lines.append(f"> {E_UP} 符合条件：**{total}** 只股票")
    lines.append("")

    lines.append("<details>")
    lines.append(f"<summary>{E_BOOK} 字段说明：因子与资金占比</summary>")
    lines.append("")
    lines.append("| 表格字段 | 中文解释 | 规则达标线 | 标记等级 | 对应属性名 |")
    lines.append("|:--------|:---------|:----------|:---------|:-----------|")
    lines.append("| 资金 `+740万(3.7)` | 主力净流入金额；括号内为主力净流入强度，占成交额比例，单位% | 主力>=2000万，占比3%~12% | 🔴净流入 🟢净流出 ⚪持平 | `an_capital_main_net_in` / `an_capital_flow_ratio` |")
    lines.append("| 大单 | 大单净流入金额，单位万元 | 10万~4000万 | 🔴净流入 🟢净流出 ⚪持平 | `an_capital_big_net_in` |")
    lines.append("| 动 | 动能评分，越高代表趋势推动力越强 | >=95 | 🔥>=80 🔴>=60 🟡>=40 ⚪<40 | `an_success_rate_dim_momentum` |")
    lines.append("| 量 | 量比，当前成交量相对近20日均量的倍数 | >=1.5 | 粗体>=1.5 橙色>=1.2 | `an_market_pos_vol_ratio` |")
    lines.append("| 空 | 空间/预期涨幅，即当前价到系统目标价的距离 | >=15% | 粗体>=15% 橙色>=10% | `an_technical_expected_gain_pct` |")
    lines.append("| 达 | 到达概率评分，衡量目标价短期可达性 | >=70 | 粗体>=70 橙色>=60 | `an_success_rate_dim_reach_prob` |")
    lines.append("| ML胜 | 短线胜率模型概率，预测持有5日净赚>5%。注意：高分段样本少不单调，28分附近最可信，40+反而不稳 | - | 🔥>=28% 🔴>=25% 🟡>=22% ⚪<22% | `ml_predict_prob` |")
    lines.append("| ML潜 | 大涨潜力模型概率，预测5日内最大涨幅>=15%（分数越高越准） | - | 🔥>=30% 🔴>=25% 🟡>=20% ⚪<20% | `ml_predict_potential` |")
    lines.append("| ML涨 | 涨幅排序模型分数（全特征、不校准），预测5日内涨≥8%概率，≥67为Top20%信号 | - | 🔥>=67 ⭐>=60 💡<60 | `ml_predict_gain` |")
    lines.append("| 规则 | 10条V2规则匹配百分比 | 100%为满分 | 🔥100%满分 🔴>=86% 🟡>=71% ⚪<71% | `calc_v2_rule_match()` |")
    lines.append("")
    lines.append("> 示例：`资金 +740万(3.7)` 表示主力净流入约740万元，主力净流入强度约为3.7%。")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not filtered:
        lines.append(f"## {E_EMPTY} 无符合条件的数据")
        lines.append("")
        lines.append(f"{weeks_label}交易日中没有 `ml_predict_prob >= {threshold}%` 的记录。")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"## {E_CLIPBOARD} 每日概览")
    lines.append("")

    active_days = [d for d in trading_days if d in filtered]
    skipped_zero_days = [d for d in trading_days if d not in filtered and d not in no_ml_data_dates]

    if active_days:
        lines.append("| 日期 | 数量 | 最高概率股 | 资金净流入最优 | 动能最强 |")
        lines.append("|:----:|:----:|:---------:|:-------------:|:-------:|")
        for d in active_days:
            recs = filtered[d]
            count = len(recs)
            top_prob = recs[0]
            top_prob_info = f"{top_prob['name']}({top_prob['ml_predict_prob']:.1f}%)"
            top_capital = max(recs, key=_get_capital_net_value)
            cap_net = _get_capital_net_value(top_capital)
            if cap_net > 0:
                cap_info = f"{top_capital['name']}(+{cap_net:.0f}万)"
            else:
                best = min(recs, key=_get_capital_net_value)
                best_net = _get_capital_net_value(best)
                cap_info = f"{best['name']}({best_net:.0f}万)"
            top_mom = max(recs, key=lambda x: float(x.get("an_success_rate_dim_momentum") or 0))
            mom_val = float(top_mom.get("an_success_rate_dim_momentum") or 0)
            mom_info = f"{top_mom['name']}({mom_val:.1f})"
            lines.append(f"| 周{_weekday_cn(d)} {d[5:]} | {count} 只 | {top_prob_info} | {cap_info} | {mom_info} |")
    else:
        lines.append(f"{E_EMPTY} 所选周期内无符合条件的数据")
    lines.append("")

    if no_ml_data_dates:
        no_ml_str = ", ".join(f"周{_weekday_cn(d)} {d[5:]}" for d in sorted(no_ml_data_dates))
        lines.append(f"> {E_YELLOW} 以下日期无 ML 预测数据：{no_ml_str}")
    if skipped_zero_days:
        skip_str = ", ".join(f"周{_weekday_cn(d)} {d[5:]}" for d in sorted(skipped_zero_days))
        lines.append(f"> {E_WHITE} 以下日期有 ML 数据但无符合 `>={threshold}%` 条件的股票：{skip_str}")
    if no_ml_data_dates or skipped_zero_days:
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"## {E_UP} 每日详情")
    lines.append("")

    lines.append("<details>")
    lines.append(f"<summary>{E_BOOK} 标记说明</summary>")
    lines.append("")
    lines.append("**概率等级**")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_FIRE} | >= 90% |")
    lines.append(f"| {E_RED} | >= 80% |")
    lines.append(f"| {E_YELLOW} | >= 70% |")
    lines.append(f"| {E_WHITE} | < 70% |")
    lines.append("")
    lines.append("**资金流向**")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_RED} | 主力净流入 |")
    lines.append(f"| {E_GREEN} | 主力净流出 |")
    lines.append(f"| {E_WHITE} | 持平 |")
    lines.append("")
    lines.append("**动能**")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_FIRE} | >= 80 |")
    lines.append(f"| {E_RED} | >= 60 |")
    lines.append(f"| {E_YELLOW} | >= 40 |")
    lines.append(f"| {E_WHITE} | < 40 |")
    lines.append("")
    lines.append("**规则匹配**")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_FIRE} | 10/10，规则100%【满分】 |")
    lines.append(f"| {E_RED} | >= 86% |")
    lines.append(f"| {E_YELLOW} | >= 71% |")
    lines.append(f"| {E_WHITE} | < 71% |")
    lines.append("")
    lines.append("**V2规则10条明细（每条占10%，满分100%）：**")
    lines.append("")
    lines.append("| # | 规则 | 达标线 | 检查字段 |")
    lines.append("|:---:|:-----|:-------|:---------|")
    lines.append("| 1 | 股价 | >= 10元 | `close` |")
    lines.append("| 2 | 主力净流入 | >= 2000万 | `an_capital_main_net_in` |")
    lines.append("| 3 | 占比（主力/成交额） | 3% ~ 12% | `an_capital_flow_ratio` |")
    lines.append("| 4 | 动能 | >= 95 | `an_success_rate_dim_momentum` |")
    lines.append("| 5 | 涨幅 | >= 3% | `an_quote_change_pct` |")
    lines.append("| 6 | 大单净流入 | 10万 ~ 4000万 | `an_capital_big_net_in` |")
    lines.append("| 7 | 周期 | = 日线 | `period` |")
    lines.append("| 8 | 量比 | >= 1.5 | `an_market_pos_vol_ratio` |")
    lines.append("| 9 | 空间（预期涨幅） | >= 15% | `an_technical_expected_gain_pct` |")
    lines.append("| 10 | 到达概率 | >= 70 | `an_success_rate_dim_reach_prob` |")
    lines.append("")
    lines.append("**核心因子列**")
    lines.append("")
    lines.append("- `动`：动能评分")
    lines.append("- `量`：量比，粗体表示 >=1.5")
    lines.append("- `空`：空间/预期涨幅，粗体表示 >=15%")
    lines.append("- `达`：到达概率评分，粗体表示 >=70")
    lines.append("")
    lines.append("**ML胜率概率**（持有5日净赚>5%，28分附近最可信，40+样本少不稳）")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_FIRE} | >= 28% |")
    lines.append(f"| {E_RED} | >= 25% |")
    lines.append(f"| {E_YELLOW} | >= 22% |")
    lines.append(f"| {E_WHITE} | < 22% |")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    for d in trading_days:
        if d not in filtered:
            continue
        records = filtered[d]
        wd = _weekday_cn(d)
        lines.append(f"### {E_CALENDAR} {d}（周{wd}） - {len(records)} 只")
        lines.append("")
        lines.append(_render_table(records))
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 多日重复出现统计 ──
    code_count: dict[str, dict] = {}
    for d, records in filtered.items():
        for r in records:
            key = r.get("code", "")
            if key not in code_count:
                code_count[key] = {
                    "name": r.get("name", ""),
                    "dates": [],
                    "probs": [],
                    "capitals": [],
                    "momentums": [],
                }
            code_count[key]["dates"].append(d)
            code_count[key]["probs"].append(r["ml_predict_prob"])
            code_count[key]["capitals"].append(_get_capital_net_value(r))
            code_count[key]["momentums"].append(float(r.get("an_success_rate_dim_momentum") or 0))

    multi_day = {k: v for k, v in code_count.items() if len(v["dates"]) >= 2}
    if multi_day:
        lines.append(f"## {E_REPEAT} 多日重复出现")
        lines.append("")
        lines.append("| 代码 | 名称 | 次数 | 日期 | 各日概率 | 各日资金(万) | 各日动能 |")
        lines.append("|:----:|:----:|:----:|:----:|:-------:|:-----------:|:-------:|")
        sorted_multi = sorted(
            multi_day.items(),
            key=lambda x: (-len(x[1]["dates"]), -sum(x[1]["probs"]) / len(x[1]["probs"]))
        )
        for code, info in sorted_multi:
            dates_str = "<br>".join(info["dates"])
            probs_str = "<br>".join(f"{p:.1f}%" for p in info["probs"])
            caps_str = "<br>".join(
                (f"+{c:.0f}" if c >= 0 else f"{c:.0f}") for c in info["capitals"]
            )
            moms_str = "<br>".join(f"{m:.1f}" for m in info["momentums"])
            lines.append(
                f"| {code} | {info['name']} | {len(info['dates'])} "
                f"| {dates_str} | {probs_str} | {caps_str} | {moms_str} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    all_records: list[dict] = []
    for d, records in filtered.items():
        all_records.extend(records)

    inflow_records = [r for r in all_records if _get_capital_net_value(r) > 0]
    if inflow_records:
        inflow_records.sort(key=_get_capital_net_value, reverse=True)
        lines.append(f"## {E_MONEY} 资金净流入 TOP")
        lines.append("")
        lines.append("| # | 股票 | 日期 | 资金 | 因子 | ML | 规则 |")
        lines.append("|:---:|:----|:----:|:----:|:----|:----:|:---:|")
        for i, r in enumerate(inflow_records[:10], 1):
            lines.append(
                f"| {i} | {r.get('code','')} {r.get('name','')} | {r.get('date','')} "
                f"| {_get_capital_flow_plain(r)} | {_get_factor_summary(r)} | {_get_ml_summary(r)} | {_get_rule_score_plain(r)} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    if all_records:
        momentum_sorted = sorted(
            all_records,
            key=lambda x: float(x.get("an_success_rate_dim_momentum") or 0),
            reverse=True,
        )
        lines.append(f"## {E_ROCKET} 动能 TOP")
        lines.append("")
        lines.append("| # | 股票 | 日期 | 因子 | 资金 | ML | 规则 |")
        lines.append("|:---:|:----|:----:|:----|:----:|:----:|:---:|")
        for i, r in enumerate(momentum_sorted[:10], 1):
            lines.append(
                f"| {i} | {r.get('code','')} {r.get('name','')} | {r.get('date','')} "
                f"| {_get_factor_summary(r)} | {_get_capital_flow_plain(r)} | {_get_ml_summary(r)} | {_get_rule_score_plain(r)} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(f"<sup>{E_PIN} 数据来源：`stocks/ml/shadow_data.json` | 由 `weekly_ml_report.py` 自动生成</sup>")
    lines.append("")

    return "\n".join(lines)


# ─────────────────── HTML 生成 ───────────────────

def _build_html_days_data(
    filtered: dict[str, list[dict]],
    trading_days: list[str],
) -> list[dict]:
    """
    将筛选后的数据转换为 HTML 看板所需的 JSON 结构。
    同时计算哪些股票在多日重复出现，打上 rep 标记。
    """
    # 统计各 code 出现日期数，用于 rep 标记
    code_dates: dict[str, int] = {}
    for d, records in filtered.items():
        for r in records:
            code = r.get("code", "")
            code_dates[code] = code_dates.get(code, 0) + 1

    days_data: list[dict] = []
    for d in trading_days:
        if d not in filtered:
            continue
        d_obj = datetime.strptime(d, "%Y-%m-%d")
        label = f"{WEEKDAY_SHORT[d_obj.weekday()]} {d[5:]}"

        stocks: list[dict] = []
        for r in filtered[d]:
            price_raw = r.get("sc_close") or r.get("close") or 0
            price = round(float(price_raw), 2) if price_raw else 0

            cap_raw = r.get("an_capital_main_net_in")
            cap = round(float(cap_raw), 0) if cap_raw is not None and cap_raw != "" else 0

            cap_r_raw = r.get("an_capital_flow_ratio")
            cap_r = round(float(cap_r_raw), 1) if cap_r_raw is not None and cap_r_raw != "" else 0

            big_raw = r.get("an_capital_big_net_in")
            big = round(float(big_raw), 0) if big_raw is not None and big_raw != "" else 0

            mom_raw = r.get("an_success_rate_dim_momentum")
            mom = round(float(mom_raw), 1) if mom_raw is not None and mom_raw != "" else 0

            vol_raw = r.get("an_market_pos_vol_ratio")
            vol = round(float(vol_raw), 2) if vol_raw is not None and vol_raw != "" else 0

            spc_raw = r.get("an_technical_expected_gain_pct")
            spc = round(float(spc_raw), 1) if spc_raw is not None and spc_raw != "" else 0

            reach_raw = r.get("an_success_rate_dim_reach_prob")
            reach = round(float(reach_raw), 0) if reach_raw is not None and reach_raw != "" else 0

            ml_raw = r.get("ml_predict_prob")
            ml = round(float(ml_raw), 1) if ml_raw is not None and ml_raw != "" else 0

            pot_raw = r.get("ml_predict_potential")
            pot: float | None = round(float(pot_raw), 1) if pot_raw is not None and pot_raw != "" else None

            gain_raw = r.get("ml_predict_gain")
            gain: float | None = round(float(gain_raw), 0) if gain_raw is not None and gain_raw != "" else None

            high_str = _get_high(r)  # 如 "15.22(+3.7%)" 或 "-"

            rule_pct = _get_rule_pct(r)

            stocks.append({
                "c":    r.get("code", ""),
                "n":    r.get("name", ""),
                "sig":  f"{r.get('signal_type', '')}/{r.get('period', '')}",
                "p":    price,
                "high": high_str,
                "cap":  int(cap),
                "capR": cap_r,
                "big":  int(big),
                "mom":  mom,
                "vol":  vol,
                "spc":  spc,
                "reach": int(reach),
                "ml":   ml,
                "pot":  pot,
                "gain": gain,
                "rule": rule_pct,
                "rep":  code_dates.get(r.get("code", ""), 1) >= 2,
            })

        days_data.append({
            "date":   d,
            "label":  label,
            "stocks": stocks,
        })

    return days_data


def generate_html_report(
    filtered: dict[str, list[dict]],
    trading_days: list[str],
    threshold: float,
    weeks: int = 1,
    no_ml_data_dates: set[str] | None = None,
) -> str:
    """生成交互式 HTML 看板报告"""
    if no_ml_data_dates is None:
        no_ml_data_dates = set()

    today = datetime.now()
    total = sum(len(v) for v in filtered.values())
    weeks_label = f"本周 + 近 {weeks} 周" if weeks > 1 else "本周 + 上周"

    date_range = ""
    if trading_days:
        date_range = f"{trading_days[-1]} ~ {trading_days[0]}"

    days_data = _build_html_days_data(filtered, trading_days)
    days_json = json.dumps(days_data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ML 预测概率周报 {date_range}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;background:#f4f4f2;color:#1a1a1a;padding:16px;min-width:360px}}
/* ── 头部 ── */
.header{{background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:12px;border:1px solid #e4e4e0}}
.header h1{{font-size:17px;font-weight:600}}
.header-meta{{font-size:12px;color:#888;margin-top:4px;display:flex;flex-wrap:wrap;gap:8px}}
.legend{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
.leg{{font-size:11px;padding:3px 9px;border-radius:10px;display:flex;align-items:center;gap:5px;border:1px solid transparent}}
.leg-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
/* ── Tabs ── */
.tabs-wrapper{{position:relative;margin-bottom:12px}}
.tabs{{display:flex;gap:5px;overflow-x:auto;padding:6px 24px;background:#fff;border-radius:10px;border:1px solid #e4e4e0;-webkit-overflow-scrolling:touch;scroll-behavior:smooth}}
.tabs::-webkit-scrollbar{{height:3px}}
.tabs::-webkit-scrollbar-track{{background:transparent}}
.tabs::-webkit-scrollbar-thumb{{background:#ccc;border-radius:3px}}
.tab{{padding:6px 13px;font-size:12px;border-radius:7px;border:none;background:transparent;color:#888;cursor:pointer;white-space:nowrap;transition:all .15s;font-weight:500;flex-shrink:0}}
.tab.active{{background:#111;color:#fff}}
.tab-arrow{{position:absolute;top:50%;transform:translateY(-50%);width:20px;height:100%;display:flex;align-items:center;justify-content:center;background:linear-gradient(to right,rgba(255,255,255,.9),rgba(255,255,255,.5));border:none;cursor:pointer;font-size:14px;color:#999;z-index:2;pointer-events:none;opacity:0;transition:opacity .2s}}
.tab-arrow.show{{pointer-events:auto;opacity:1}}
.tab-arrow.left{{left:0;border-radius:10px 0 0 10px;background:linear-gradient(to left,rgba(255,255,255,0),rgba(255,255,255,.95) 60%)}}
.tab-arrow.right{{right:0;border-radius:0 10px 10px 0;background:linear-gradient(to right,rgba(255,255,255,0),rgba(255,255,255,.95) 60%)}}
/* ── 工具栏 ── */
.toolbar{{display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap}}
.ctrl-label{{font-size:12px;color:#888;font-weight:500}}
.sort-btn{{padding:5px 12px;font-size:12px;border-radius:7px;border:1px solid #ddd;background:#fff;color:#666;cursor:pointer;transition:all .15s}}
.sort-btn.active{{border-color:#111;color:#111;font-weight:600;background:#f9f9f7}}
/* ── 日概览 chips ── */
.day-chips{{display:flex;gap:7px;margin-bottom:12px;flex-wrap:wrap}}
.chip{{font-size:11px;padding:4px 10px;border-radius:8px;background:#fff;border:1px solid #e4e4e0;color:#555}}
/* ── 卡片网格 ── */
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:10px}}
/* ── 单张卡片 ── */
.card{{background:#fff;border-radius:12px;padding:13px 15px;border:1px solid #e4e4e0;border-left:4px solid #ccc;transition:box-shadow .15s}}
.card:hover{{box-shadow:0 3px 14px rgba(0,0,0,.07)}}
.card.c-blue{{border-left-color:#2563eb}}
.card.c-teal{{border-left-color:#059669}}
.card.c-amber{{border-left-color:#d97706}}
.card.c-gray{{border-left-color:#bbb}}
/* 卡片顶部 */
.card-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:9px}}
.stock-name{{font-size:15px;font-weight:600;color:#111;display:flex;align-items:center;gap:5px;flex-wrap:wrap}}
.rep-tag{{font-size:9px;padding:1px 5px;border-radius:4px;background:#fef3c7;color:#92400e;border:1px solid #fbbf24;font-weight:500}}
.stock-meta{{font-size:11px;color:#999;margin-top:2px}}
.ml-box{{text-align:right;flex-shrink:0;margin-left:8px}}
.ml-num{{font-size:20px;font-weight:700;line-height:1}}
.ml-num.c-blue{{color:#1d4ed8}}
.ml-num.c-teal{{color:#065f46}}
.ml-num.c-amber{{color:#92400e}}
.ml-num.c-gray{{color:#9ca3af}}
.ml-pot{{font-size:11px;color:#aaa;margin-top:2px}}
.ml-pot.has{{color:#b91c1c;font-weight:500}}
/* 信号行 */
.sig-row{{margin-bottom:9px;display:flex;gap:5px;align-items:center;flex-wrap:wrap}}
.sig-tag{{font-size:10px;padding:2px 7px;border-radius:5px;border:1px solid #e0e0e0;background:#f7f7f5;color:#666}}
.sig-tag.strict{{background:#f0f0ff;color:#3730a3;border-color:#c7d2fe}}
.sig-tag.break{{background:#f0fdf4;color:#065f46;border-color:#86efac}}
.price-tag{{font-size:10px;padding:2px 7px;border-radius:5px;background:#f5f5f3;color:#777;border:1px solid #e8e8e4}}
.high-tag{{font-size:10px;padding:2px 7px;border-radius:5px;background:#fef9ee;color:#92400e;border:1px solid #fde68a}}
/* 指标格 */
.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-bottom:9px}}
.metric{{background:#f8f8f6;border-radius:7px;padding:6px 3px;text-align:center;border:1px solid #efefec}}
.m-label{{font-size:9px;color:#bbb;margin-bottom:2px}}
.m-val{{font-size:12px;font-weight:600;color:#333}}
.m-val.hot{{color:#dc2626}}
.m-val.warm{{color:#d97706}}
.m-val.ok{{color:#059669}}
.m-val.dim{{color:#bbb}}
/* 资金行 */
.cap-row{{display:flex;justify-content:space-between;align-items:center;padding-top:8px;border-top:1px solid #f0f0ec}}
.cap-in{{font-size:11px;font-weight:600;color:#dc2626}}
.cap-out{{font-size:11px;font-weight:600;color:#059669}}
.cap-flat{{font-size:11px;color:#bbb}}
.big-txt{{font-size:11px;color:#bbb}}
/* 规则条 */
.rule-wrap{{display:flex;flex-direction:column;align-items:center;gap:2px}}
.rule-bar{{width:44px;height:3px;background:#eee;border-radius:2px;overflow:hidden}}
.rule-fill{{height:100%;border-radius:2px;background:#bbb}}
.rule-fill.r80{{background:#2563eb}}
.rule-fill.r60{{background:#059669}}
.rule-fill.r40{{background:#d97706}}
.rule-num{{font-size:10px;color:#aaa}}
/* 空状态 */
.empty{{text-align:center;padding:40px;color:#bbb;font-size:14px}}
/* 响应式 */
@media(max-width:768px){{
  .header-meta{{flex-direction:column;gap:2px}}
  .card-top{{flex-direction:column;align-items:stretch}}
  .ml-box{{text-align:left;margin-left:0;margin-top:4px;display:flex;align-items:center;gap:8px}}
  .ml-num{{font-size:18px}}
  .toolbar{{gap:4px}}
  .toolbar .ctrl-label{{display:none}}
  .sort-btn{{padding:5px 10px;font-size:11px}}
}}
@media(max-width:480px){{
  .grid{{grid-template-columns:1fr}}
  .metrics{{grid-template-columns:repeat(5,1fr)}}
  .header{{padding:12px}}
  .header h1{{font-size:15px}}
  .cap-row{{flex-direction:column;align-items:flex-start;gap:4px}}
  .sig-row{{flex-wrap:wrap;gap:3px}}
}}
</style>
</head>
<body>

<div class="header">
  <h1>📊 ML 预测概率周报</h1>
  <div class="header-meta">
    <span>📅 生成：{today.strftime('%Y-%m-%d %H:%M')}</span>
    <span>📅 周期：{weeks_label}（{date_range}）</span>
    <span>🎯 阈值：ML ≥ {threshold}%</span>
    <span>📈 共 <strong>{total}</strong> 只</span>
  </div>
  <div class="legend">
    <span class="leg" style="background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe">
      <span class="leg-dot" style="background:#2563eb"></span>ML ≥ 28%
    </span>
    <span class="leg" style="background:#f0fdf4;color:#065f46;border-color:#bbf7d0">
      <span class="leg-dot" style="background:#059669"></span>ML ≥ 25%
    </span>
    <span class="leg" style="background:#fffbeb;color:#92400e;border-color:#fde68a">
      <span class="leg-dot" style="background:#d97706"></span>ML ≥ 22%
    </span>
    <span class="leg" style="background:#f9fafb;color:#6b7280;border-color:#e5e7eb">
      <span class="leg-dot" style="background:#bbb"></span>&lt; 22%
    </span>
    <span class="leg" style="background:#fef3c7;color:#92400e;border-color:#fbbf24">
      连续 = 多日重复出现
    </span>
  </div>
</div>

<div class="tabs-wrapper">
  <div class="tabs" id="tabs"></div>
  <button class="tab-arrow left" id="tabArrowLeft" onclick="scrollTabs(-200)">◀</button>
  <button class="tab-arrow right" id="tabArrowRight" onclick="scrollTabs(200)">▶</button>
</div>

<div class="toolbar">
  <span class="ctrl-label">排序：</span>
  <button class="sort-btn active" onclick="setSort('ml',this)">ML 概率</button>
  <button class="sort-btn" onclick="setSort('gain',this)">🔥涨幅分</button>
  <button class="sort-btn" onclick="setSort('cap',this)">资金流入</button>
  <button class="sort-btn" onclick="setSort('mom',this)">动能</button>
  <button class="sort-btn" onclick="setSort('rule',this)">规则匹配</button>
</div>

<div class="day-chips" id="day-chips"></div>
<div class="grid" id="grid"></div>

<script>
const DAYS = {days_json};

let curDay = 0, curSort = 'ml';

function fmtCap(v) {{
  const a = Math.abs(v);
  const s = v > 0 ? '+' : '';
  if (a >= 10000) return s + (a / 10000).toFixed(1) + '亿';
  return s + a + '万';
}}

function mlCls(v) {{
  return v >= 28 ? 'c-blue' : v >= 25 ? 'c-teal' : v >= 22 ? 'c-amber' : 'c-gray';
}}
function momCls(v) {{
  return v >= 100 ? 'hot' : v >= 80 ? 'warm' : v >= 60 ? 'ok' : 'dim';
}}
function volCls(v) {{
  return v >= 1.5 ? 'hot' : v >= 1.0 ? 'warm' : 'dim';
}}
function spcCls(v) {{
  return v >= 15 ? 'hot' : v >= 10 ? 'warm' : 'dim';
}}
function reachCls(v) {{
  return v >= 80 ? 'hot' : v >= 70 ? 'warm' : v >= 60 ? 'ok' : 'dim';
}}
function ruleCls(v) {{
  return v >= 80 ? 'r80' : v >= 60 ? 'r60' : v >= 40 ? 'r40' : '';
}}
function sigCls(s) {{
  if (s.startsWith('严格')) return 'strict';
  if (s.startsWith('突破')) return 'break';
  return '';
}}

function renderTabs() {{
  document.getElementById('tabs').innerHTML = DAYS.map((d, i) =>
    `<button class="tab${{i === curDay ? ' active' : ''}}" onclick="switchDay(${{i}})">${{d.label}} (${{d.stocks.length}})</button>`
  ).join('');
}}

function renderChips(day) {{
  const st = day.stocks;
  if (!st.length) {{ document.getElementById('day-chips').innerHTML = ''; return; }}
  const topML  = st.reduce((a, b) => b.ml > a.ml ? b : a);
  const pos    = st.filter(s => s.cap > 0);
  const topCap = pos.length ? pos.reduce((a, b) => b.cap > a.cap ? b : a) : null;
  const topMom = st.reduce((a, b) => b.mom > a.mom ? b : a);
  document.getElementById('day-chips').innerHTML = `
    <span class="chip">共 ${{st.length}} 只</span>
    <span class="chip">🏆 最高ML：${{topML.n}} ${{topML.ml}}%</span>
    <span class="chip">💰 资金最优：${{topCap ? topCap.n + ' ' + fmtCap(topCap.cap) : '无净流入'}}</span>
    <span class="chip">⚡ 动能最强：${{topMom.n}} ${{topMom.mom}}</span>
  `;
}}

function renderGrid(day) {{
  if (!day) {{ document.getElementById('grid').innerHTML = '<div class="empty">暂无数据</div>'; return; }}
  let st = [...day.stocks];
  if (curSort === 'ml')   st.sort((a, b) => b.ml - a.ml);
  else if (curSort === 'gain') st.sort((a, b) => (b.gain||0) - (a.gain||0));
  else if (curSort === 'cap')  st.sort((a, b) => b.cap - a.cap);
  else if (curSort === 'mom')  st.sort((a, b) => b.mom - a.mom);
  else                         st.sort((a, b) => b.rule - a.rule);

  const cls = mlCls;
  document.getElementById('grid').innerHTML = st.map(s => `
    <div class="card ${{cls(s.ml)}}">
      <div class="card-top">
        <div>
          <div class="stock-name">
            ${{s.n}}
            ${{s.rep ? '<span class="rep-tag">连续</span>' : ''}}
          </div>
          <div class="stock-meta">${{s.c}}</div>
        </div>
        <div class="ml-box">
          <div class="ml-num ${{cls(s.ml)}}">${{s.ml}}%</div>
          <div class="ml-pot${{s.pot !== null && s.pot > 30 ? ' has' : ''}}">潜 ${{s.pot !== null ? s.pot + '%' : '—'}}</div>
          <div class="ml-gain${{s.gain !== null && s.gain >= 67 ? ' hot' : (s.gain !== null && s.gain >= 60 ? ' warm' : '')}}" style="font-size:10px;color:${{s.gain !== null && s.gain >= 67 ? '#dc2626' : (s.gain !== null && s.gain >= 60 ? '#d97706' : '#bbb')}};margin-top:2px;font-weight:${{s.gain !== null && s.gain >= 67 ? '700' : '400'}}">涨 ${{s.gain !== null ? Math.round(s.gain) : '—'}}</div>
        </div>
      </div>
      <div class="sig-row">
        <span class="sig-tag ${{sigCls(s.sig)}}">${{s.sig}}</span>
        <span class="price-tag">¥${{s.p}}</span>
        ${{s.high !== '-' ? `<span class="high-tag">高 ${{s.high}}</span>` : ''}}
      </div>
      <div class="metrics">
        <div class="metric">
          <div class="m-label">动能</div>
          <div class="m-val ${{momCls(s.mom)}}">${{s.mom}}</div>
        </div>
        <div class="metric">
          <div class="m-label">量比</div>
          <div class="m-val ${{volCls(s.vol)}}">${{s.vol}}x</div>
        </div>
        <div class="metric">
          <div class="m-label">空间</div>
          <div class="m-val ${{spcCls(s.spc)}}">${{s.spc}}%</div>
        </div>
        <div class="metric">
          <div class="m-label">到达</div>
          <div class="m-val ${{reachCls(s.reach)}}">${{s.reach}}</div>
        </div>
        <div class="metric">
          <div class="m-label">规则</div>
          <div class="rule-wrap">
            <span class="rule-num">${{s.rule}}%</span>
            <div class="rule-bar">
              <div class="rule-fill ${{ruleCls(s.rule)}}" style="width:${{s.rule}}%"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="cap-row">
        <span class="${{s.cap > 0 ? 'cap-in' : s.cap < 0 ? 'cap-out' : 'cap-flat'}}">
          ${{s.cap !== 0 ? fmtCap(s.cap) + ' (' + s.capR + '%)' : '资金持平'}}
        </span>
        <span class="big-txt">大单 ${{s.big > 0 ? '+' : ''}}${{s.big}}万</span>
      </div>
    </div>
  `).join('');
}}

function render() {{
  renderTabs();
  const day = DAYS[curDay];
  renderChips(day);
  renderGrid(day);
  setTimeout(updateArrows, 50);
}}

document.getElementById('tabs').addEventListener('scroll', updateArrows);
window.addEventListener('resize', updateArrows);

function switchDay(i) {{ curDay = i; render(); }}

function scrollTabs(delta) {{
  const el = document.getElementById('tabs');
  el.scrollBy({{ left: delta, behavior: 'smooth' }});
}}

function updateArrows() {{
  const el = document.getElementById('tabs');
  const hasOverflow = el.scrollWidth > el.clientWidth;
  const canLeft  = el.scrollLeft > 1;
  const canRight = el.scrollLeft < el.scrollWidth - el.clientWidth - 1;
  const arrowL = document.getElementById('tabArrowLeft');
  const arrowR = document.getElementById('tabArrowRight');
  if (arrowL) arrowL.classList.toggle('show', hasOverflow && canLeft);
  if (arrowR) arrowR.classList.toggle('show', hasOverflow && canRight);
}}

function setSort(s, btn) {{
  curSort = s;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderGrid(DAYS[curDay]);
}}

render();
</script>
</body>
</html>"""

    return html


# ─────────────────── 文件写入 ───────────────────

def _write_report(content: str, mode: str = "overwrite") -> None:
    out_path = OUTPUT_FILE
    if mode == "new" and os.path.exists(out_path):
        os.remove(out_path)
        _safe_print(f"[x] 已删除旧报告: {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    _safe_print(f"[OK] Markdown 报告已生成: {out_path}")


def _write_html_report(content: str, mode: str = "overwrite") -> None:
    out_path = OUTPUT_HTML_FILE
    if mode == "new" and os.path.exists(out_path):
        os.remove(out_path)
        _safe_print(f"[x] 已删除旧 HTML 看板: {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    _safe_print(f"[OK] HTML 看板已生成: {out_path}")


# ─────────────────── 主流程 ───────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ML预测概率周报生成器")
    parser.add_argument("--threshold", "-t", type=float, default=None,
                        help="ml_predict_prob 筛选阈值（默认交互输入，回车默认25）")
    parser.add_argument("--mode", "-m", choices=["overwrite", "new"], default=None,
                        help="写入模式：overwrite=覆盖（默认）, new=删除旧文件再写")
    args = parser.parse_args()

    # ── 周数输入 ──
    raw = _safe_input("[?] 统计近几周数据？（直接回车默认 1，即本周+上周）: ").strip()
    if raw == "":
        weeks = 1
    else:
        try:
            weeks = int(raw)
            if weeks < 1:
                _safe_print("[!] 周数不能小于1，使用默认值 1")
                weeks = 1
        except ValueError:
            _safe_print("[!] 输入无效，使用默认值 1")
            weeks = 1
    weeks_label = f"本周 + 近 {weeks} 周" if weeks > 1 else "本周 + 上周"
    _safe_print(f"[*] 统计范围: {weeks_label}")

    # ── 阈值输入 ──
    if args.threshold is not None:
        threshold = args.threshold
    else:
        raw = _safe_input("[?] 请输入 ml_predict_prob 筛选阈值（直接回车默认 25）: ").strip()
        if raw == "":
            threshold = 25.0
        else:
            try:
                threshold = float(raw)
            except ValueError:
                _safe_print("[!] 输入无效，使用默认值 25")
                threshold = 25.0

    _safe_print(f"[*] 筛选阈值: ml_predict_prob >= {threshold}%")

    # ── 写入模式 ──
    if args.mode is not None:
        write_mode = args.mode
    else:
        if os.path.exists(OUTPUT_FILE) or os.path.exists(OUTPUT_HTML_FILE):
            raw = _safe_input("[?] 报告文件已存在，(O)覆盖 / (N)删除重写？[直接回车默认删除重写]: ").strip().upper()
            write_mode = "overwrite" if raw == "O" else "new"
        else:
            write_mode = "new"

    # ── 计算近N周交易日 ──
    trading_days = _get_last_week_trading_days(weeks=weeks)
    if not trading_days:
        _safe_print("[!] 无法确定上周交易日")
        sys.exit(1)
    _safe_print(f"[*] {weeks_label}交易日: {', '.join(trading_days)}")

    # ── 加载数据 ──
    data = _load_data()
    _safe_print(f"[*] 数据总量: {len(data)} 条")

    has_prob = sum(1 for r in data if isinstance(r.get("ml_predict_prob"), (int, float)))
    _safe_print(f"[*] 含 ml_predict_prob: {has_prob} 条")

    # ── 筛选 ──
    filtered, no_ml_data_dates = _filter_records(data, trading_days, threshold)
    total = sum(len(v) for v in filtered.values())
    _safe_print(f"[*] 符合条件: {total} 条")
    for d in trading_days:
        count = len(filtered.get(d, []))
        if count > 0:
            _safe_print(f"    {d}: {count} 只")
        elif d in no_ml_data_dates:
            _safe_print(f"    {d}: 无 ML 预测数据")

    # ── 生成并写入 Markdown ──
    md_report = generate_report(filtered, trading_days, threshold, weeks=weeks, no_ml_data_dates=no_ml_data_dates)
    _write_report(md_report, write_mode)

    # ── 生成并写入 HTML 看板 ──
    html_report = generate_html_report(filtered, trading_days, threshold, weeks=weeks, no_ml_data_dates=no_ml_data_dates)
    _write_html_report(html_report, write_mode)


if __name__ == "__main__":
    main()