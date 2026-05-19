"""
周度 ML 预测报告生成器
=====================
从 shadow_data.json 中筛选本周已过交易日及前N周交易日 ml_predict_prob >= 阈值的股票，
生成美观的 Markdown 报告。

输出字段：股票名称、股票代码、信号类型、周期、信号价格、资金净流入(流入/流出)、大单净流入、动能、规则匹配、最高价、ML达标概率、ML潜力概率

用法:
    python weekly_ml_report.py                  # 默认阈值 40，覆盖已有报告，默认近1周
    python weekly_ml_report.py --threshold 50   # 自定义阈值
    python weekly_ml_report.py --mode new       # 删除旧报告再写新报告（默认覆盖）
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Set

# ─────────────────── 路径配置 ───────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ML_DIR = os.path.dirname(_SCRIPT_DIR)          # stocks/ml/
_STOCKS_DIR = os.path.dirname(_ML_DIR)           # stocks/

DATA_FILE = os.path.join(_ML_DIR, "shadow_data.json")
HOLIDAYS_FILE = os.path.join(_STOCKS_DIR, "stock_monitor", "holidays.json")
OUTPUT_FILE = os.path.join(_SCRIPT_DIR, "weekly_ml_report.md")

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

def _load_holidays() -> Set[str]:
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


def _get_last_week_trading_days(today: datetime = None, weeks: int = 1) -> List[str]:
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

def _load_data() -> List[Dict]:
    if not os.path.exists(DATA_FILE):
        _safe_print(f"[!] 数据文件不存在: {DATA_FILE}")
        sys.exit(1)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _filter_records(
    data: List[Dict],
    trading_days: List[str],
    threshold: float,
) -> tuple:
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
            # 该日有数据但无 ml_predict_prob
            no_ml_data_dates.add(d)
            continue
        if not day_records:
            # 该日无任何数据（可能尚未采集）
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

def _get_signal_price(r: Dict) -> str:
    """出信号时的价格：优先 sc_close，其次 close"""
    val = r.get("sc_close") or r.get("close") or ""
    if val != "":
        return f"{float(val):.2f}"
    return "-"


def _get_capital_flow(r: Dict) -> str:
    """资金净流入金额 + 占比 + 流入/流出标记（简短版，用于表格）"""
    net_in = r.get("an_capital_main_net_in")
    if net_in is None or net_in == "":
        return "-"

    net_in = float(net_in)
    if net_in > 0:
        icon = E_RED
    elif net_in < 0:
        icon = E_GREEN
    else:
        icon = E_WHITE

    if abs(net_in) >= 10000:
        amount_str = f"{abs(net_in)/10000:.2f}亿"
    else:
        amount_str = f"{abs(net_in):.0f}万"

    sign = "+" if net_in > 0 else ("-" if net_in < 0 else "")

    # 资金占比
    ratio = r.get("an_capital_flow_ratio")
    ratio_str = ""
    if ratio is not None and ratio != "":
        ratio_str = f"({float(ratio):.2f})"

    return f"{icon}{sign}{amount_str}{ratio_str}"


def _get_capital_net_value(r: Dict) -> float:
    """获取资金净流入原始数值，用于排序"""
    net_in = r.get("an_capital_main_net_in")
    if net_in is None or net_in == "":
        return 0
    return float(net_in)


def _get_big_order_flow(r: Dict) -> str:
    """大单净流入，单位万元。"""
    val = r.get("an_capital_big_net_in")
    if val is None or val == "":
        return "-"
    val = float(val)
    icon = E_RED if val > 0 else (E_GREEN if val < 0 else E_WHITE)
    sign = "+" if val > 0 else ""
    if abs(val) >= 10000:
        return f"{icon}{sign}{val/10000:.2f}亿"
    return f"{icon}{sign}{val:.0f}万"


def _get_rule_score(r: Dict) -> str:
    """V2改良规则匹配百分比：7条，与钉钉汇总一致。"""
    close = r.get("sc_close") or r.get("close") or 0
    main_in = r.get("an_capital_main_net_in") or 0
    flow = r.get("an_capital_flow_ratio") or 0
    momentum = r.get("an_success_rate_dim_momentum") or 0
    change_pct = r.get("an_quote_change_pct") or 0
    big_in = r.get("an_capital_big_net_in") or 0
    period = r.get("period", "")

    checks = [
        float(close) >= 10,
        float(main_in) >= 2000,
        1 <= float(flow) <= 12,
        float(momentum) >= 95,
        float(change_pct) >= 3,
        float(big_in) < 4000,
        period == "日线",
    ]
    matched = sum(1 for x in checks if x)
    pct = round(matched / len(checks) * 100) if checks else 0
    if matched == len(checks):
        return f"{E_FIRE} **{pct}%【满分】**"
    if pct >= 86:
        return f"{E_RED} **{pct}%**"
    if pct >= 71:
        return f"{E_YELLOW} {pct}%"
    return f"{E_WHITE} {pct}%"


def _get_momentum(r: Dict) -> str:
    """动能评分"""
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


def _get_high(r: Dict) -> str:
    """回填期间最高价（信号发出后5日内最高价），未回填则显示 -
    如果有回填最高价和信号价格，则附带上涨百分比"""
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


def _get_predict_potential(r: Dict) -> str:
    """ML潜力概率：当前表示5日内最大涨幅 >= 8%的概率"""
    val = r.get("ml_predict_potential")
    if val is None or val == "":
        return "-"
    val = float(val)
    if val >= 60:
        return f"{E_FIRE} **{val:.1f}%**"
    elif val >= 50:
        return f"{E_RED} **{val:.1f}%**"
    elif val >= 40:
        return f"{E_YELLOW} {val:.1f}%"
    else:
        return f"{E_WHITE} {val:.1f}%"


def _get_prob_str(prob: float) -> str:
    """概率等级标记"""
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


def _weekday_cn(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return WEEKDAY_CN[d.weekday()]


def _render_table(records: List[Dict]) -> str:
    """渲染单日详情表格"""
    lines = []
    lines.append("| # | 代码 | 名称 | 信号类型 | 周期 | 信号价格 | 资金净流入 | 大单 | 动能 | 回填最高价 | ML达标概率 | ML潜力概率 | 规则 |")
    lines.append("|:---:|:----:|:----:|:-------:|:---:|:-------:|:---------:|:----:|:---:|:---------:|:---------:|:---------:|:---:|")
    for i, r in enumerate(records, 1):
        code = r.get("code", "")
        name = r.get("name", "")
        signal_type = r.get("signal_type", "")
        period = r.get("period", "")
        signal_price = _get_signal_price(r)
        capital_flow = _get_capital_flow(r)
        big_flow = _get_big_order_flow(r)
        momentum = _get_momentum(r)
        rule_score = _get_rule_score(r)
        high = _get_high(r)
        prob = r.get("ml_predict_prob", 0)
        prob_str = _get_prob_str(prob)
        potential_str = _get_predict_potential(r)
        lines.append(
            f"| {i} | {code} | {name} | {signal_type} | {period} "
            f"| {signal_price} | {capital_flow} | {big_flow} | {momentum} | {high} | {prob_str} | {potential_str} | {rule_score} |"
        )
    return "\n".join(lines)


def generate_report(
    filtered: Dict[str, List[Dict]],
    trading_days: List[str],
    threshold: float,
    weeks: int = 1,
    no_ml_data_dates: Set[str] = None,
) -> str:
    """生成完整 Markdown 报告"""
    if no_ml_data_dates is None:
        no_ml_data_dates = set()
    today = datetime.now()
    total = sum(len(v) for v in filtered.values())

    weeks_label = f"本周 + 近 {weeks} 周" if weeks > 1 else "本周 + 上周"

    lines = []

    # ── 标题区 ──
    lines.append(f"# {E_CHART} ML 预测概率周报")
    lines.append("")
    lines.append(f"> {E_CALENDAR} 生成时间：{today.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> {E_CALENDAR} 报告周期：{weeks_label}（{trading_days[0]} ~ {trading_days[-1]}）")
    lines.append(f"> {E_TARGET} 筛选阈值：`ml_predict_prob >= {threshold}%`")
    lines.append(f"> {E_UP} 符合条件：**{total}** 只股票")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not filtered:
        lines.append(f"## {E_EMPTY} 无符合条件的数据")
        lines.append("")
        lines.append(f"{weeks_label}交易日中没有 `ml_predict_prob >= {threshold}%` 的记录。")
        lines.append("")
        return "\n".join(lines)

    # ── 每日概览 ──
    lines.append(f"## {E_CLIPBOARD} 每日概览")
    lines.append("")

    # 只显示有符合条件数据的日期
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

    # 注释：无 ML 数据的日期 & 有 ML 数据但 0 条符合条件的日期
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

    # ── 每日详情 ──
    lines.append(f"## {E_UP} 每日详情")
    lines.append("")

    # 标记说明
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
    lines.append(f"| {E_FIRE} | 7/7，规则100%【满分】 |")
    lines.append(f"| {E_RED} | >= 86% |")
    lines.append(f"| {E_YELLOW} | >= 71% |")
    lines.append(f"| {E_WHITE} | < 71% |")
    lines.append("")
    lines.append("规则包含：股价>=10、主力>=2000万、占比1~12%、动能>=95、涨幅>=3%、大单<4000万、日线。")
    lines.append("")
    lines.append("**ML预测涨幅**")
    lines.append("")
    lines.append("| 标记 | 含义 |")
    lines.append("|:----:|:----:|")
    lines.append(f"| {E_FIRE} | >= 10% |")
    lines.append(f"| {E_RED} | >= 5% |")
    lines.append(f"| {E_YELLOW} | >= 0% |")
    lines.append(f"| {E_GREEN} | < 0% |")
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
    code_count = {}
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
            cap = _get_capital_net_value(r)
            code_count[key]["capitals"].append(cap)
            mom = float(r.get("an_success_rate_dim_momentum") or 0)
            code_count[key]["momentums"].append(mom)

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

    # ── 资金流入 TOP ──
    all_records = []
    for d, records in filtered.items():
        all_records.extend(records)

    inflow_records = [r for r in all_records if _get_capital_net_value(r) > 0]
    if inflow_records:
        inflow_records.sort(key=_get_capital_net_value, reverse=True)
        lines.append(f"## {E_MONEY} 资金净流入 TOP")
        lines.append("")
        lines.append("| # | 代码 | 名称 | 日期 | 资金净流入 | 信号价格 | 动能 | ML预测概率 |")
        lines.append("|:---:|:----:|:----:|:----:|:---------:|:-------:|:---:|:---------:|")
        for i, r in enumerate(inflow_records[:10], 1):
            cap_net = _get_capital_net_value(r)
            if cap_net >= 10000:
                cap_str = f"+{cap_net/10000:.2f}亿"
            else:
                cap_str = f"+{cap_net:.2f}万"
            lines.append(
                f"| {i} | {r.get('code','')} | {r.get('name','')} | {r.get('date','')} "
                f"| {cap_str} | {_get_signal_price(r)} | {_get_momentum(r)} | {_get_prob_str(r['ml_predict_prob'])} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 动能 TOP ──
    if all_records:
        momentum_sorted = sorted(
            all_records,
            key=lambda x: float(x.get("an_success_rate_dim_momentum") or 0),
            reverse=True,
        )
        lines.append(f"## {E_ROCKET} 动能 TOP")
        lines.append("")
        lines.append("| # | 代码 | 名称 | 日期 | 动能 | 信号价格 | 资金净流入 | ML预测概率 |")
        lines.append("|:---:|:----:|:----:|:----:|:---:|:-------:|:---------:|:---------:|")
        for i, r in enumerate(momentum_sorted[:10], 1):
            mom_val = float(r.get("an_success_rate_dim_momentum") or 0)
            lines.append(
                f"| {i} | {r.get('code','')} | {r.get('name','')} | {r.get('date','')} "
                f"| {mom_val:.1f} | {_get_signal_price(r)} | {_get_capital_flow(r)} | {_get_prob_str(r['ml_predict_prob'])} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 页脚 ──
    lines.append(f"<sup>{E_PIN} 数据来源：`stocks/ml/shadow_data.json` | 由 `weekly_ml_report.py` 自动生成</sup>")
    lines.append("")

    return "\n".join(lines)


# ─────────────────── 文件写入 ───────────────────

def _write_report(content: str, mode: str = "overwrite") -> None:
    out_path = OUTPUT_FILE
    if mode == "new" and os.path.exists(out_path):
        os.remove(out_path)
        _safe_print(f"[x] 已删除旧报告: {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    _safe_print(f"[OK] 报告已生成: {out_path}")


# ─────────────────── 主流程 ───────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ML预测概率周报生成器")
    parser.add_argument("--threshold", "-t", type=float, default=None,
                        help="ml_predict_prob 筛选阈值（默认交互输入，回车默认40）")

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
        raw = _safe_input("[?] 请输入 ml_predict_prob 筛选阈值（直接回车默认 40）: ").strip()
        if raw == "":
            threshold = 40.0
        else:
            try:
                threshold = float(raw)
            except ValueError:
                _safe_print("[!] 输入无效，使用默认值 40")
                threshold = 40.0

    _safe_print(f"[*] 筛选阈值: ml_predict_prob >= {threshold}%")

    # ── 写入模式 ──
    if args.mode is not None:
        write_mode = args.mode
    else:
        if os.path.exists(OUTPUT_FILE):
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

    # ── 生成报告 ──
    report = generate_report(filtered, trading_days, threshold, weeks=weeks, no_ml_data_dates=no_ml_data_dates)

    # ── 写入文件 ──
    _write_report(report, write_mode)


if __name__ == "__main__":
    main()
