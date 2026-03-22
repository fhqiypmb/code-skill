#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主力视角筹码收割分析器
========================
核心逻辑：站在主力角度，分析当前收割条件是否成熟。

主力收割的最优时机：
    1. 大多数筹码被套在高位（散户痛苦到极致，不得不割）
    2. 当前价格远低于平均成本（带血筹码足够廉价）
    3. 上方套牢盘密集（拉升后有足够的出货空间）
    4. 筹码充分分散（说明吸筹过程已让筹码从弱手转移完成）

用法:
    cd stocks
    python chip_analyzer.py
    → 按提示输入股票代码即可
"""

import sys
import os

# 设置 UTF-8 编码
if sys.platform == "win32":
    os.system("chcp 65001 > nul")

import warnings
import json
import time
import re
import ssl
import random
import urllib.request
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 禁用代理
for _key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if _key in locals() or _key in globals():
        del _key
import os

for _key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if _key in os.environ:
        del os.environ[_key]

ssl._create_default_https_context = ssl._create_unverified_context
_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class RateLimiter:
    """令牌桶限流器"""

    def __init__(self, max_per_sec: float = 5.0):
        self._interval = 1.0 / max_per_sec
        self._last_time = 0.0
        self._backoff = 0.0

    def wait(self) -> None:
        now = time.time()
        wait_time = self._interval + self._backoff
        elapsed = now - self._last_time
        if elapsed < wait_time:
            time.sleep(wait_time - elapsed)
        time.sleep(random.uniform(0.02, 0.05))
        self._last_time = time.time()

    def report_throttled(self) -> None:
        self._backoff = min(max(self._backoff * 2, 1.0), 8.0)

    def report_success(self) -> None:
        if self._backoff > 0:
            self._backoff = max(self._backoff * 0.5, 0)
            if self._backoff < 0.05:
                self._backoff = 0.0


_limiter = RateLimiter(max_per_sec=5.0)


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _http_get(url: str, timeout: int = 20, retry: int = 3) -> bytes:
    """通用 HTTP GET，带重试和随机 UA"""
    headers = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": "https://quote.eastmoney.com",
    }
    last_err = RuntimeError("未知错误")
    for attempt in range(retry + 1):
        try:
            _limiter.wait()
            req = urllib.request.Request(url, headers=headers)
            with _opener.open(req, timeout=timeout) as r:
                _limiter.report_success()
                return r.read()
        except Exception as e:
            last_err = e
            err_str = str(e)
            _limiter.report_throttled()
            if any(code in err_str for code in ("456", "403", "429", "503")):
                wait = (attempt + 1) * 2 + random.uniform(0.5, 1.5)
                time.sleep(wait)
            elif attempt < retry:
                time.sleep(0.5 + random.uniform(0, 0.5))
    raise last_err


def _http_get_json(url: str, timeout: int = 15, retry: int = 2) -> dict:
    """HTTP GET 返回 JSON"""
    raw = _http_get(url, timeout, retry)
    return json.loads(raw.decode("utf-8"))


# ─────────────────────────────────────────────────────────
# 数据获取（使用 data_source.py 的成熟实现）
# ─────────────────────────────────────────────────────────

import sys
import os

# data_source.py 与当前文件在同一目录
sys.path.insert(0, os.path.dirname(__file__))

try:
    from data_source import (
        fetch_kline,
        fetch_realtime_quote,
        fetch_stock_industry,
    )

    DATA_SOURCE_AVAILABLE = True
except ImportError:
    DATA_SOURCE_AVAILABLE = False


def _fetch_kline_with_turnover(code: str, limit: int = 210) -> pd.DataFrame:
    """
    获取日线 K 线 + 真实换手率（hsl，单位%）
    优先腾讯 newfqkline 接口（稳定，f7=换手率）；失败则降级东方财富。
    返回 DataFrame: date, open, close, high, low, volume, hsl(换手率%)
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"

    # ── 腾讯接口（主）──
    try:
        # 开始日期：往前推足够多天（limit*2 个自然日保证覆盖交易日）
        from datetime import datetime, timedelta
        start_date = (datetime.today() - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
        end_date = datetime.today().strftime("%Y-%m-%d")
        url = (
            f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
            f"?_var=kline_dayqfq&param={symbol},day,{start_date},{end_date},{limit},qfq"
        )
        raw = _http_get(url)
        import re as _re
        text = raw.decode("utf-8", errors="replace")
        text = _re.sub(r"^kline_dayqfq=", "", text.strip())
        data = json.loads(text)
        days = data["data"][symbol].get("qfqday") or data["data"][symbol].get("day")
        rows = []
        for d in days:
            # [日期, 开, 收, 高, 低, 量, {}, 换手率, 成交额, ...]
            if len(d) >= 8:
                hsl = float(d[7]) if d[7] else 0.0
                rows.append({
                    "date": pd.to_datetime(d[0]),
                    "open": float(d[1]),
                    "close": float(d[2]),
                    "high": float(d[3]),
                    "low": float(d[4]),
                    "volume": float(d[5]),
                    "hsl": hsl,
                })
        if rows:
            return pd.DataFrame(rows)
    except Exception:
        pass

    # ── 东方财富接口（备用）──
    try:
        market = 1 if code.startswith(("6", "9")) else 0
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={market}.{code}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt=1&end=20500101&lmt={limit}"
            f"&_={int(time.time() * 1000)}"
        )
        resp = _http_get_json(url)
        klines: list = resp.get("data", {}).get("klines", []) if resp.get("data") else []
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 11:
                rows.append({
                    "date": pd.to_datetime(parts[0]),
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "hsl": float(parts[10]) if parts[10] else 0.0,
                })
        if rows:
            return pd.DataFrame(rows)
    except Exception:
        pass

    return pd.DataFrame()


def fetch_chip_data(code: str) -> pd.DataFrame:
    """
    获取筹码分布数据（基于通达信 CYQ 算法 - 筹码衰减模型）

    算法原理：
    1. 将价格区间分为 150 个档位
    2. 每日筹码根据换手率衰减（卖出）
    3. 新筹码在当日价格区间叠加（买入）
    4. 累积历史筹码形成完整分布

    换手率来源：东方财富 f61(hsl) 字段（真实流通股换手率），与通达信/akshare 一致
    算法来源：akshare stock_cyq_em 内嵌 JS（CYQCalculator），完整移植为 Python
    """
    # 优先用带真实换手率的接口（东方财富 f61=hsl）
    temp_df = _fetch_kline_with_turnover(code, limit=210)

    # 降级：如果获取失败则用旧接口 + 估算换手率
    if temp_df.empty:
        if not DATA_SOURCE_AVAILABLE:
            raise ImportError("无法获取 K 线数据")
        klines = fetch_kline(code, period="daily", limit=210)
        if not klines:
            return pd.DataFrame()
        temp_df = pd.DataFrame(klines)
        temp_df["date"] = pd.to_datetime(temp_df["day"])
        for col in ("open", "close", "high", "low", "volume"):
            temp_df[col] = temp_df[col].astype(float)
        # 降级时估算换手率，单位保持 %
        avg_vol = temp_df["volume"].rolling(window=20, min_periods=5).mean()
        temp_df["hsl"] = (temp_df["volume"] / avg_vol * 5.0).clip(1, 30).fillna(5.0)

    temp_df = temp_df.sort_values("date").reset_index(drop=True)
    records = temp_df.to_dict(orient="records")

    results = []

    # 对每个交易日计算筹码分布（完整对齐 akshare CYQCalculator JS 逻辑）
    for i in range(len(records)):
        # 使用最近 120 日数据（range=120，同通达信默认）
        start = max(0, i - 119)
        kdata = records[start: i + 1]

        if len(kdata) < 5:
            continue

        factor = 150
        maxprice = max(r["high"] for r in kdata)
        minprice = min(r["low"] for r in kdata)
        if maxprice == minprice:
            continue

        # accuracy 对齐 JS：(maxprice - minprice) / (factor - 1)
        accuracy = max(0.01, (maxprice - minprice) / (factor - 1))
        yrange = [round(minprice + accuracy * j, 2) for j in range(factor)]
        xdata = [0.0] * factor

        # 逐日叠加筹码（完整对齐 JS CYQCalculator）
        for row in kdata:
            open_p = row["open"]
            close_p = row["close"]
            high_p = row["high"]
            low_p = row["low"]
            avg = (open_p + close_p + high_p + low_p) / 4
            # hsl 单位是 %，除以 100 得到比例，再 min(1, ...)
            turnover = min(1.0, row["hsl"] / 100 if row["hsl"] else 0.0)

            # 筹码衰减（卖出）
            xdata = [x * (1 - turnover) for x in xdata]

            H = int((high_p - minprice) / accuracy)
            L = int((low_p - minprice) / accuracy)  # JS 用 ceil，但 int 更接近实际
            g_point = factor - 1 if high_p == low_p else 2 / (high_p - low_p)

            if high_p == low_p:
                # 一字板：全部筹码叠加在 avg 对应档位
                idx = int((high_p - minprice) / accuracy)
                if 0 <= idx < factor:
                    xdata[idx] += g_point * turnover / 2
            else:
                for j in range(max(0, L), min(factor, H + 1)):
                    curprice = minprice + accuracy * j
                    if curprice <= avg:
                        if abs(avg - low_p) < 1e-8:
                            xdata[j] += g_point * turnover
                        else:
                            xdata[j] += (curprice - low_p) / (avg - low_p) * g_point * turnover
                    else:
                        if abs(high_p - avg) < 1e-8:
                            xdata[j] += g_point * turnover
                        else:
                            xdata[j] += (high_p - curprice) / (high_p - avg) * g_point * turnover

        # 当前价格（kdata 是 list of dict）
        current_price = kdata[-1]["close"]
        total_chips = sum(xdata)

        if total_chips == 0:
            continue

        # 获利比例（对齐 JS getBenefitPart：price >= minprice + i*accuracy）
        benefit_part = (
            sum(x for j, x in enumerate(xdata) if current_price >= minprice + j * accuracy)
            / total_chips
        )

        # 平均成本（对齐 JS getCostByChip(totalChips * 0.5)）
        def get_cost_by_chip(chip_target):
            cumsum = 0.0
            for j, x in enumerate(xdata):
                if cumsum + x > chip_target:
                    return round(minprice + j * accuracy, 2)
                cumsum += x
            return yrange[-1]

        avg_cost = get_cost_by_chip(total_chips * 0.5)

        # 计算 90% 和 70% 成本区间（对齐 JS computePercentChips）
        def get_percent_range(percent):
            ps = [(1 - percent) / 2, (1 + percent) / 2]
            return [
                get_cost_by_chip(total_chips * ps[0]),
                get_cost_by_chip(total_chips * ps[1]),
            ]

        range_90 = get_percent_range(0.9)
        range_70 = get_percent_range(0.7)

        results.append(
            {
                "date": kdata[-1]["date"],
                "profit_ratio": benefit_part * 100,
                "avg_cost": avg_cost,
                "conc_90_low": range_90[0],
                "conc_90_high": range_90[1],
                "conc_70_low": range_70[0],
                "conc_70_high": range_70[1],
            }
        )

    return pd.DataFrame(results)


def fetch_price_data(code: str, days: int = 210) -> pd.DataFrame:
    """获取日线价格数据"""
    df = _fetch_kline_with_turnover(code, limit=days)
    if not df.empty:
        return df.sort_values("date").reset_index(drop=True)
    # 降级
    if not DATA_SOURCE_AVAILABLE:
        return pd.DataFrame()
    klines = fetch_kline(code, period="daily", limit=days)
    if not klines:
        return pd.DataFrame()
    df = pd.DataFrame(klines)
    df["date"] = pd.to_datetime(df["day"])
    for col in ("close", "high", "low", "open"):
        df[col] = df[col].astype(float)
    return df.sort_values("date").reset_index(drop=True)


def fetch_stock_name(code: str) -> str:
    """获取股票名称（使用 data_source.py）"""
    if not DATA_SOURCE_AVAILABLE:
        return code

    try:
        industry = fetch_stock_industry(code)
        return industry.get("name", code)
    except Exception:
        return code


# ─────────────────────────────────────────────────────────
# 核心分析（主力视角）
# ─────────────────────────────────────────────────────────


def analyze_chip(code: str, chip_df: pd.DataFrame, price_df: pd.DataFrame) -> dict:
    """
    主力视角四维分析：

    维度1：套牢程度（恐慌深度）
        获利比例越低 = 越多人被套 = 散户越绝望 = 带血筹码越多
        主力最爱的状态：获利比例 < 20%，也就是80%的人都在亏钱

    维度2：带血程度（吸筹性价比）
        当前价格 vs 平均成本的距离
        越便宜于平均成本 = 筹码越"带血" = 吸筹越划算

    维度3：上方压力（出货空间）
        套牢盘集中在哪个价位
        主力拉升到那里，套牢者解套急着卖 = 主力出货窗口
        压力越集中越好，说明出货目标明确

    维度4：筹码集中度（吸筹完成度）
        筹码越分散 = 还没有人完成大规模吸筹 = 主力还有机会
        筹码突然集中 = 有大资金在某价位完成了换手
    """
    result = {}

    # ── 最新筹码数据 ──
    latest_chip = chip_df.iloc[-1]
    result["chip_date"] = latest_chip["date"].strftime("%Y-%m-%d")
    result["profit_ratio"] = float(latest_chip["profit_ratio"])  # 获利比例 %
    result["avg_cost"] = float(latest_chip["avg_cost"])  # 平均成本
    result["concentration_90_low"] = float(latest_chip["conc_90_low"])
    result["concentration_90_high"] = float(latest_chip["conc_90_high"])
    result["concentration_70_low"] = float(latest_chip["conc_70_low"])
    result["concentration_70_high"] = float(latest_chip["conc_70_high"])

    # ── 当前价格 ──
    current_price = float(price_df["close"].iloc[-1])
    result["current_price"] = current_price
    result["price_date"] = price_df["date"].iloc[-1].strftime("%Y-%m-%d")

    # ── 维度1：套牢程度 ──
    # 获利比例越低 = 被套的人越多 = 恐慌越深
    profit_ratio = result["profit_ratio"]
    loss_ratio = 100 - profit_ratio  # 被套比例
    result["loss_ratio"] = loss_ratio

    # 套牢程度评分（0-100）
    # 获利比例 0% → 满分100，获利比例 50% → 0分
    trap_score = max(0.0, min(100.0, (50 - profit_ratio) / 50 * 100))
    result["trap_score"] = round(trap_score, 1)

    # ── 维度2：带血程度（吸筹性价比）──
    avg_cost = result["avg_cost"]
    if avg_cost > 0:
        # 当前价格低于平均成本多少
        discount = (avg_cost - current_price) / avg_cost * 100
    else:
        discount = 0.0
    result["discount_to_avg"] = round(discount, 2)

    # 折扣评分（0-100）
    # 折扣 0% → 0分，折扣 ≥ 40% → 满分
    blood_score = max(0.0, min(100.0, discount / 40 * 100))
    result["blood_score"] = round(blood_score, 1)

    # ── 维度3：上方压力（出货空间）──
    # 套牢盘主要集中价位 = 70%筹码集中区间的上沿
    resistance_price = result["concentration_70_high"]
    if resistance_price > 0 and current_price > 0:
        # 从当前价格到主要套牢盘的距离（拉升空间）
        upside = (resistance_price - current_price) / current_price * 100
    else:
        upside = 0.0
    result["upside_to_resistance"] = round(upside, 2)
    result["resistance_price"] = resistance_price

    # 出货空间评分（0-100）
    # 拉升空间 ≥ 50% → 满分，说明主力出货窗口很大
    exit_score = max(0.0, min(100.0, upside / 50 * 100))
    result["exit_score"] = round(exit_score, 1)

    # ── 维度4：筹码集中度变化（吸筹完成度）──
    # 90%集中度区间越窄 = 筹码越集中 = 有大资金完成换手
    chip_range_90 = result["concentration_90_high"] - result["concentration_90_low"]
    chip_range_70 = result["concentration_70_high"] - result["concentration_70_low"]

    result["chip_range_90"] = round(chip_range_90, 2)
    result["chip_range_70"] = round(chip_range_70, 2)

    # 近30日筹码集中度变化趋势
    if len(chip_df) >= 30:
        past_chip = chip_df.iloc[-30]
        past_range = float(past_chip.get("90集中度高", 0)) - float(
            past_chip.get("90集中度低", 0)
        )
        # 集中度收窄 = 筹码在向某个价位集中
        concentration_change = past_range - chip_range_90  # 正值 = 收窄
        result["concentration_tightening"] = round(concentration_change, 2)
    else:
        result["concentration_tightening"] = 0.0

    # 集中度评分（筹码越集中越好，说明换手越完整）
    # 相对于股价的区间宽度，越窄越集中
    relative_range = chip_range_70 / current_price * 100 if current_price > 0 else 100
    concentration_score = max(0.0, min(100.0, (1 - relative_range / 80) * 100))
    result["concentration_score"] = round(concentration_score, 1)

    # ── 综合收割成熟度评分 ──
    # 套牢程度（0.35）+ 带血程度（0.30）+ 出货空间（0.25）+ 集中度（0.10）
    harvest_score = (
        trap_score * 0.35
        + blood_score * 0.30
        + exit_score * 0.25
        + concentration_score * 0.10
    )
    result["harvest_score"] = round(harvest_score, 1)

    # 结论判断
    if harvest_score >= 70:
        result["verdict"] = "[OK] 收割条件成熟"
        result["verdict_desc"] = (
            "大多数筹码深度被套，带血筹码充裕，拉升出货空间大。主力此刻吸筹性价比极高。"
        )
    elif harvest_score >= 50:
        result["verdict"] = "[WARN] 条件初步具备"
        result["verdict_desc"] = (
            "恐慌程度较高但尚未到极致，仍有散户未割肉离场。可能处于吸筹过程中。"
        )
    elif harvest_score >= 30:
        result["verdict"] = "[WAIT] 尚未成熟"
        result["verdict_desc"] = (
            "套牢程度不足，散户仍有较多获利盘，主力尚无充分收割条件。"
        )
    else:
        result["verdict"] = "[NO] 不具备收割条件"
        result["verdict_desc"] = (
            "大多数筹码仍处于获利状态，散户情绪稳定，不存在恐慌性抛售。"
        )

    return result


# ─────────────────────────────────────────────────────────
# 报告输出
# ─────────────────────────────────────────────────────────


def render_bar(score: float, width: int = 25) -> str:
    filled = int(score / 100 * width)
    return "#" * filled + "-" * (width - filled)


def print_report(code: str, name: str, r: dict) -> None:
    W = 62
    print("\n" + "=" * W)
    print("  主力视角筹码收割分析")
    print("=" * W)
    print(f"  股票：{name}({code})")
    print(f"  当前价格：{r['current_price']:.2f} 元  ({r['price_date']})")
    print(f"  筹码数据：{r['chip_date']}")
    print("-" * W)

    # 核心结论
    print(f"\n  +-- 收割成熟度评分 {'-' * 30}")
    print(f"  |  {r['harvest_score']:5.1f} / 100")
    print(f"  |  [{render_bar(r['harvest_score'])}]")
    print(f"  |")
    print(f"  |  {r['verdict']}")
    print(f"  +{'-' * 45}")
    print(f"\n  {r['verdict_desc']}")

    print("\n" + "-" * W)
    print("  筹码全景（主力视角四维分析）")
    print("-" * W)

    # 维度 1：套牢程度
    print(f"\n  * 维度 1：套牢深度（权重 35%）")
    print(f"     [{render_bar(r['trap_score'])}] {r['trap_score']:.0f}分")
    print(
        f"     当前获利比例：{r['profit_ratio']:.1f}%  |  被套比例：{r['loss_ratio']:.1f}%"
    )
    if r["loss_ratio"] >= 80:
        print(f"     -> 超过八成持仓者亏损，恐慌割肉压力极大，带血筹码充裕")
    elif r["loss_ratio"] >= 60:
        print(f"     -> 六成以上持仓者亏损，情绪偏向恐慌，有一定吸筹价值")
    elif r["loss_ratio"] >= 40:
        print(f"     -> 套牢程度一般，仍有大量获利盘，散户尚未崩溃")
    else:
        print(f"     -> 大多数人仍在盈利，不存在恐慌性抛售环境")

    # 维度 2：带血程度
    print(f"\n  * 维度 2：带血程度（权重 30%）")
    print(f"     [{render_bar(r['blood_score'])}] {r['blood_score']:.0f}分")
    print(
        f"     平均成本：{r['avg_cost']:.2f} 元  |  当前折价：{r['discount_to_avg']:.1f}%"
    )
    if r["discount_to_avg"] >= 30:
        print(f"     -> 当前价格大幅低于平均成本，筹码极度带血，主力吸筹性价比极高")
    elif r["discount_to_avg"] >= 15:
        print(f"     -> 价格明显低于成本区，有一定吸筹吸引力")
    elif r["discount_to_avg"] >= 0:
        print(f"     -> 价格略低于平均成本，折价不足，筹码血腥程度有限")
    else:
        print(f"     -> 当前价格高于平均成本，筹码处于普遍获利状态，无需恐慌")

    # 维度 3：出货空间
    print(f"\n  * 维度 3：出货空间（权重 25%）")
    print(f"     [{render_bar(r['exit_score'])}] {r['exit_score']:.0f}分")
    print(f"     主要套牢盘集中价位：{r['resistance_price']:.2f} 元")
    print(f"     从当前价位拉升空间：+{r['upside_to_resistance']:.1f}%")
    if r["upside_to_resistance"] >= 40:
        print(f"     -> 套牢盘在高位密集，主力拉升后出货窗口极大，解套盘会蜂拥而出")
    elif r["upside_to_resistance"] >= 20:
        print(f"     -> 上方有一定套牢盘，主力有合理的出货空间")
    else:
        print(f"     -> 上方套牢盘距当前价格较近，拉升出货空间有限")

    # 维度 4：筹码集中度
    print(f"\n  * 维度 4：筹码集中度（权重 10%）")
    print(
        f"     [{render_bar(r['concentration_score'])}] {r['concentration_score']:.0f}分"
    )
    print(
        f"     70% 筹码分布区间：{r['concentration_70_low']:.2f} ~ {r['concentration_70_high']:.2f} 元"
    )
    print(
        f"     90% 筹码分布区间：{r['concentration_90_low']:.2f} ~ {r['concentration_90_high']:.2f} 元"
    )
    tightening = r["concentration_tightening"]
    if tightening > 0:
        print(
            f"     近 30 日集中度收窄 {tightening:.2f} 元 -> 筹码在向某价位集中，有大资金在换手"
        )
    elif tightening < 0:
        print(
            f"     近 30 日集中度扩散 {abs(tightening):.2f} 元 -> 筹码趋于分散，市场分歧加大"
        )
    else:
        print(f"     近 30 日集中度变化不明显")

    print("\n" + "-" * W)
    print("  本工具仅供研究人性与市场规律，不构成投资建议")
    print("=" * W + "\n")


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 62)
    print("  主力视角筹码收割分析器")
    print("  站在主力角度，判断当前收割条件是否成熟")
    print("=" * 62)

    while True:
        code = input("\n请输入股票代码（输入 q 退出）：").strip()
        if code.lower() == "q":
            print("\n已退出。\n")
            break
        if not code:
            continue

        print(f"\n正在获取 [{code}] 数据，请稍候...\n")
        try:
            name = fetch_stock_name(code)
            # fetch_chip_data 内部已获取 K 线，price_df 复用同一份数据
            chip_df = fetch_chip_data(code)
            price_df = chip_df[["date", "close"] if "close" in chip_df.columns else ["date"]].copy() if not chip_df.empty else pd.DataFrame()
            # chip_df 不含 close，用独立接口取最新价
            price_df = _fetch_kline_with_turnover(code, limit=5)
            if price_df.empty:
                price_df = fetch_price_data(code)

            if chip_df.empty or price_df.empty:
                print("数据为空，请检查股票代码是否正确")
                continue

            result = analyze_chip(code, chip_df, price_df)
            print_report(code, name, result)

        except Exception as e:
            print(f"分析失败：{e}")
            print("请检查股票代码是否正确，或稍后重试\n")


if __name__ == "__main__":
    main()
