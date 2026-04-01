#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主力视角筹码收割分析器（增强版）
========================
核心逻辑：站在主力角度，分析当前收割条件是否成熟。

原四维分析 + 增强四维分析 + 综合总结

用法:
    cd stocks
    python chip_analyzer.py
"""

import sys
import os

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

for _key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if _key in os.environ:
        del os.environ[_key]

ssl._create_default_https_context = ssl._create_unverified_context
_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0",
]


class RateLimiter:
    def __init__(self, max_per_sec: float = 5.0):
        self._interval = 1.0 / max_per_sec
        self._last_time = 0.0
        self._backoff = 0.0

    def wait(self):
        now = time.time()
        if now - self._last_time < self._interval + self._backoff:
            time.sleep(self._interval + self._backoff - (now - self._last_time))
        time.sleep(random.uniform(0.02, 0.05))
        self._last_time = time.time()

    def report_throttled(self):
        self._backoff = min(max(self._backoff * 2, 1.0), 8.0)

    def report_success(self):
        if self._backoff > 0:
            self._backoff = max(self._backoff * 0.5, 0)


_limiter = RateLimiter()


def _http_get(url: str, timeout: int = 20, retry: int = 3) -> bytes:
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://quote.eastmoney.com",
    }
    for attempt in range(retry + 1):
        try:
            _limiter.wait()
            req = urllib.request.Request(url, headers=headers)
            with _opener.open(req, timeout=timeout) as r:
                _limiter.report_success()
                return r.read()
        except:
            _limiter.report_throttled()
            if attempt < retry:
                time.sleep(0.5)
    raise RuntimeError(f"请求失败: {url}")


def _http_get_json(url: str) -> dict:
    return json.loads(_http_get(url).decode("utf-8"))


sys.path.insert(0, os.path.dirname(__file__))

try:
    from data_source import fetch_kline, fetch_realtime_quote, fetch_stock_industry
    DATA_SOURCE_AVAILABLE = True
except ImportError:
    DATA_SOURCE_AVAILABLE = False


def _fetch_kline_with_turnover(code: str, limit: int = 210, include_today: bool = True) -> pd.DataFrame:
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"
    df = pd.DataFrame()

    try:
        start_date = (datetime.today() - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
        end_date = datetime.today().strftime("%Y-%m-%d")
        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?_var=kline_dayqfq&param={symbol},day,{start_date},{end_date},{limit},qfq"
        raw = _http_get(url)
        text = re.sub(r"^kline_dayqfq=", "", raw.decode("utf-8", errors="replace").strip())
        data = json.loads(text)
        days = data["data"][symbol].get("qfqday") or data["data"][symbol].get("day")
        rows = [{"date": pd.to_datetime(d[0]), "open": float(d[1]), "close": float(d[2]),
                 "high": float(d[3]), "low": float(d[4]), "volume": float(d[5]), "hsl": float(d[7]) if d[7] else 0.0}
                for d in days if len(d) >= 8]
        if rows:
            df = pd.DataFrame(rows)
    except:
        pass

    if df.empty:
        try:
            market = 1 if code.startswith(("6", "9")) else 0
            url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&end=20500101&lmt={limit}&_={int(time.time()*1000)}"
            resp = _http_get_json(url)
            klines = resp.get("data", {}).get("klines", []) if resp.get("data") else []
            rows = [{"date": pd.to_datetime(p[0]), "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]), "volume": float(p[5]), "hsl": float(p[10]) if p[10] else 0.0}
                    for p in [line.split(",") for line in klines] if len(p) >= 11]
            if rows:
                df = pd.DataFrame(rows)
        except:
            pass

    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)

    return df


def fetch_chip_data(code: str, realtime: bool = False) -> pd.DataFrame:
    temp_df = _fetch_kline_with_turnover(code, limit=210, include_today=realtime)

    if temp_df.empty and DATA_SOURCE_AVAILABLE:
        klines = fetch_kline(code, period="daily", limit=210)
        if klines:
            temp_df = pd.DataFrame(klines)
            temp_df["date"] = pd.to_datetime(temp_df["day"])
            temp_df["hsl"] = 5.0

    if temp_df.empty:
        return pd.DataFrame()

    records = temp_df.to_dict(orient="records")
    results = []
    factor = 200

    for i in range(len(records)):
        start = max(0, i - 119)
        kdata = records[start:i+1]
        if len(kdata) < 5:
            continue

        maxprice = max(r["high"] for r in kdata)
        minprice = min(r["low"] for r in kdata)
        if maxprice == minprice:
            maxprice = minprice + 0.01
        accuracy = max(0.01, (maxprice - minprice) / (factor - 1))
        xdata = [0.0] * factor

        for row in kdata:
            hsl = float(row.get("hsl", 0) or 0)
            if hsl <= 0:
                continue
            turnover_rate = min(1.0, hsl / 100.0)
            xdata = [x * (1 - turnover_rate) for x in xdata]

            open_p, close_p, high_p, low_p = row["open"], row["close"], row["high"], row["low"]
            avg_price = (open_p + close_p + high_p + low_p) / 4.0

            if high_p == low_p:
                idx = int((high_p - minprice) / accuracy)
                if 0 <= idx < factor:
                    xdata[idx] += turnover_rate
            else:
                L = max(0, int((low_p - minprice) / accuracy))
                H = min(factor - 1, int((high_p - minprice) / accuracy))
                for j in range(L, H + 1):
                    cur_price = minprice + accuracy * j
                    weight = (cur_price - low_p) / (avg_price - low_p) if cur_price <= avg_price and avg_price > low_p else \
                             (high_p - cur_price) / (high_p - avg_price) if cur_price > avg_price and high_p > avg_price else 1.0
                    triangle_area = (H - L + 1) / 2.0
                    if triangle_area > 0:
                        xdata[j] += turnover_rate * weight / triangle_area

        current_price = float(kdata[-1]["close"])
        total_chips = sum(xdata)
        if total_chips <= 0:
            continue

        def get_cost(target):
            cumsum = 0.0
            for j, x in enumerate(xdata):
                if cumsum + x >= target:
                    if x > 0:
                        return minprice + accuracy * (j + (target - cumsum) / x)
                    return minprice + accuracy * j
                cumsum += x
            return minprice + accuracy * (factor - 1)

        profit_chips = sum(x for j, x in enumerate(xdata) if minprice + j * accuracy <= current_price)
        benefit_part = profit_chips / total_chips

        results.append({
            "date": kdata[-1]["date"],
            "profit_ratio": benefit_part * 100,
            "avg_cost": round(get_cost(total_chips * 0.5), 2),
            "conc_90_low": round(get_cost(total_chips * 0.05), 2),
            "conc_90_high": round(get_cost(total_chips * 0.95), 2),
            "conc_70_low": round(get_cost(total_chips * 0.15), 2),
            "conc_70_high": round(get_cost(total_chips * 0.85), 2),
            "chip_distribution": xdata,
            "price_levels": [minprice + accuracy * j for j in range(factor)],
        })

    return pd.DataFrame(results)


def fetch_stock_name(code: str) -> str:
    if not DATA_SOURCE_AVAILABLE:
        return code
    try:
        return fetch_stock_industry(code).get("name", code)
    except:
        return code


# ─────────────────────────────────────────────────────────
# 增强分析函数
# ─────────────────────────────────────────────────────────

def calc_dead_score(chip_distribution, price_levels, current_price):
    """增强维度1：死筹比例（散户躺平程度）"""
    total = sum(chip_distribution)
    if total <= 0:
        return 0, "数据不足", ""

    dead = sum(c for p, c in zip(price_levels, chip_distribution)
               if p < current_price * 0.8 or p > current_price * 1.2)
    dead_ratio = dead / total * 100
    score = min(100, dead_ratio * 1.2)

    if dead_ratio >= 60:
        desc = "大部分筹码已躺平不动"
    elif dead_ratio >= 30:
        desc = "约三成筹码为死筹"
    else:
        desc = "大部分筹码仍活跃"

    return round(score, 1), f"死筹{dead_ratio:.1f}%", desc


def calc_pattern_score(chip_distribution, price_levels, current_price):
    """增强维度2：筹码形态"""
    valid = [(p, c) for p, c in zip(price_levels, chip_distribution) if c > 0]
    if len(valid) < 3:
        return 0, "数据不足", ""

    chips = [c for p, c in valid]
    avg_chip = sum(chips) / len(chips)

    peaks = []
    for i in range(1, len(chips) - 1):
        if chips[i] > chips[i-1] and chips[i] > chips[i+1] and chips[i] > avg_chip * 1.2:
            peaks.append(valid[i])

    if not peaks:
        peaks = [max(valid, key=lambda x: x[1])]

    if len(peaks) == 1:
        peak_price = peaks[0][0]
        if peak_price < current_price * 0.9:
            score, pattern, desc = 100, "低位单峰", "筹码集中在低位，主力吸筹完成"
        elif peak_price > current_price * 1.1:
            score, pattern, desc = 30, "高位单峰", "筹码集中在高位，上方压力大"
        else:
            score, pattern, desc = 70, "中位单峰", "筹码集中在中位，即将变盘"
    elif len(peaks) == 2:
        score, pattern, desc = 50, "双峰形态", "筹码双峰分布，上下都有阻力"
    else:
        score, pattern, desc = 20, "多峰分散", "筹码分散，市场分歧大"

    return round(score, 1), pattern, desc


def calc_cost_protection_score(chip_distribution, price_levels, current_price):
    """增强维度3：主力成本护盘"""
    total = sum(chip_distribution)
    if total <= 0:
        return 0, "数据不足", ""

    sorted_data = sorted(zip(price_levels, chip_distribution), key=lambda x: x[1], reverse=True)
    accumulated = 0.0
    main_prices = []
    for p, c in sorted_data:
        if accumulated >= total * 0.4:
            break
        accumulated += c
        main_prices.append(p)

    if not main_prices:
        return 0, "数据不足", ""

    main_cost = sum(main_prices) / len(main_prices)
    distance = (current_price - main_cost) / main_cost * 100 if main_cost > 0 else 0

    if distance <= -10:
        score, desc = 100, "已跌破主力成本，强力护盘"
    elif distance <= 0:
        score, desc = 90, "接近主力成本，会护盘"
    elif distance <= 10:
        score, desc = 60, "略高于主力成本，护盘意愿减弱"
    else:
        score, desc = 30, "远高于主力成本，无护盘必要"

    return round(score, 1), f"偏离{distance:.1f}%", desc


def calc_penetration_score(chip_distribution, price_levels, current_price):
    """增强维度4：拉升穿透力"""
    total = sum(chip_distribution)
    if total <= 0:
        return 0, "数据不足", ""

    near_chips = sum(c for p, c in zip(price_levels, chip_distribution)
                     if current_price * 1.05 < p <= current_price * 1.15)
    near_ratio = near_chips / total * 100

    if near_ratio <= 5:
        score, desc = 100, "上方阻力极小，极易拉升"
    elif near_ratio <= 15:
        score, desc = 80, "上方阻力较小，容易拉升"
    elif near_ratio <= 30:
        score, desc = 50, "上方阻力中等"
    else:
        score, desc = 20, "上方阻力较大，拉升困难"

    return round(score, 1), f"阻力{near_ratio:.1f}%", desc


# ─────────────────────────────────────────────────────────
# 核心分析
# ─────────────────────────────────────────────────────────

def analyze_chip(code: str, chip_df: pd.DataFrame, price_df: pd.DataFrame) -> dict:
    """主力视角八维分析"""
    result = {}
    latest = chip_df.iloc[-1]

    result["chip_date"] = latest["date"].strftime("%Y-%m-%d")
    result["profit_ratio"] = float(latest["profit_ratio"])
    result["avg_cost"] = float(latest["avg_cost"])
    result["conc_90_low"] = float(latest["conc_90_low"])
    result["conc_90_high"] = float(latest["conc_90_high"])
    result["conc_70_low"] = float(latest["conc_70_low"])
    result["conc_70_high"] = float(latest["conc_70_high"])

    current_price = float(price_df["close"].iloc[-1])
    result["current_price"] = current_price
    result["price_date"] = price_df["date"].iloc[-1].strftime("%Y-%m-%d")

    chip_dist = latest.get("chip_distribution", [])
    price_levels = latest.get("price_levels", [])

    # === 原四维分析 ===
    profit_ratio = result["profit_ratio"]
    loss_ratio = 100 - profit_ratio
    result["loss_ratio"] = loss_ratio
    trap_score = max(0.0, min(100.0, (50 - profit_ratio) / 50 * 100))
    result["trap_score"] = round(trap_score, 1)

    avg_cost = result["avg_cost"]
    discount = (avg_cost - current_price) / avg_cost * 100 if avg_cost > 0 else 0
    result["discount_to_avg"] = round(discount, 2)
    blood_score = max(0.0, min(100.0, discount / 40 * 100))
    result["blood_score"] = round(blood_score, 1)

    resistance_price = result["conc_70_high"]
    upside = (resistance_price - current_price) / current_price * 100 if resistance_price > 0 and current_price > 0 else 0
    result["upside_to_resistance"] = round(upside, 2)
    result["resistance_price"] = resistance_price
    exit_score = max(0.0, min(100.0, upside / 50 * 100))
    result["exit_score"] = round(exit_score, 1)

    chip_range_90 = result["conc_90_high"] - result["conc_90_low"]
    chip_range_70 = result["conc_70_high"] - result["conc_70_low"]
    result["chip_range_90"] = round(chip_range_90, 2)
    result["chip_range_70"] = round(chip_range_70, 2)

    if len(chip_df) >= 30:
        past = chip_df.iloc[-30]
        past_range = float(past.get("conc_90_high", 0)) - float(past.get("conc_90_low", 0))
        result["concentration_tightening"] = round(past_range - chip_range_90, 2)
    else:
        result["concentration_tightening"] = 0.0

    relative_range = chip_range_70 / current_price * 100 if current_price > 0 else 100
    concentration_score = max(0.0, min(100.0, (1 - relative_range / 80) * 100))
    result["concentration_score"] = round(concentration_score, 1)

    # === 增强四维分析 ===
    if chip_dist and price_levels:
        result["dead_score"], result["dead_desc_num"], result["dead_desc"] = calc_dead_score(chip_dist, price_levels, current_price)
        result["pattern_score"], result["pattern_desc_num"], result["pattern_desc"] = calc_pattern_score(chip_dist, price_levels, current_price)
        result["cost_score"], result["cost_desc_num"], result["cost_desc"] = calc_cost_protection_score(chip_dist, price_levels, current_price)
        result["penetration_score"], result["penetration_desc_num"], result["penetration_desc"] = calc_penetration_score(chip_dist, price_levels, current_price)
    else:
        result["dead_score"] = result["pattern_score"] = result["cost_score"] = result["penetration_score"] = 0
        result["dead_desc_num"] = result["pattern_desc_num"] = result["cost_desc_num"] = result["penetration_desc_num"] = "无"
        result["dead_desc"] = result["pattern_desc"] = result["cost_desc"] = result["penetration_desc"] = "数据不足"

    # === 原收割成熟度评分 ===
    harvest_score = trap_score * 0.35 + blood_score * 0.30 + exit_score * 0.25 + concentration_score * 0.10
    result["harvest_score"] = round(harvest_score, 1)

    if harvest_score >= 70:
        result["verdict"] = "[OK] 收割条件成熟"
        result["verdict_desc"] = "大多数筹码深度被套，带血筹码充裕，拉升出货空间大。主力此刻吸筹性价比极高。"
    elif harvest_score >= 50:
        result["verdict"] = "[WARN] 条件初步具备"
        result["verdict_desc"] = "恐慌程度较高但尚未到极致，仍有散户未割肉离场。可能处于吸筹过程中。"
    elif harvest_score >= 30:
        result["verdict"] = "[WAIT] 尚未成熟"
        result["verdict_desc"] = "套牢程度不足，散户仍有较多获利盘，主力尚无充分收割条件。"
    else:
        result["verdict"] = "[NO] 不具备收割条件"
        result["verdict_desc"] = "大多数筹码仍处于获利状态，散户情绪稳定，不存在恐慌性抛售。"

    # === 综合总分（八维） ===
    total_score = (
        trap_score * 0.12 + blood_score * 0.12 + exit_score * 0.10 + concentration_score * 0.08
        + result["dead_score"] * 0.15 + result["pattern_score"] * 0.15
        + result["cost_score"] * 0.15 + result["penetration_score"] * 0.13
    )
    result["total_score"] = round(total_score, 1)

    if total_score >= 70:
        result["final_verdict"] = "[买入信号]"
        result["final_desc"] = "综合条件优秀，主力吸筹完成、护盘意愿强、拉升阻力小"
    elif total_score >= 50:
        result["final_verdict"] = "[观望]"
        result["final_desc"] = "条件初步具备，但仍有风险，建议等待更明确信号"
    elif total_score >= 30:
        result["final_verdict"] = "[不买]"
        result["final_desc"] = "条件不足，主力吸筹未完成或拉升阻力较大"
    else:
        result["final_verdict"] = "[不买]"
        result["final_desc"] = "不具备买入条件，大多数筹码获利，无收割空间"

    return result


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

    # 原收割成熟度评分
    print(f"\n  +-- 收割成熟度评分 {'-' * 30}")
    print(f"  |  {r['harvest_score']:5.1f} / 100")
    print(f"  |  [{render_bar(r['harvest_score'])}]")
    print(f"  |  {r['verdict']}")
    print(f"  +{'-' * 45}")
    print(f"\n  {r['verdict_desc']}")

    print("\n" + "-" * W)
    print("  筹码全景（主力视角四维分析）")
    print("-" * W)

    # 维度1-4（原版）
    print(f"\n  * 维度 1：套牢深度（权重 35%）")
    print(f"     [{render_bar(r['trap_score'])}] {r['trap_score']:.0f}分")
    print(f"     当前获利比例：{r['profit_ratio']:.1f}%  |  被套比例：{r['loss_ratio']:.1f}%")
    if r["loss_ratio"] >= 80:
        print(f"     -> 超过八成持仓者亏损，恐慌割肉压力极大")
    elif r["loss_ratio"] >= 60:
        print(f"     -> 六成以上持仓者亏损，情绪偏向恐慌")
    elif r["loss_ratio"] >= 40:
        print(f"     -> 套牢程度一般，散户尚未崩溃")
    else:
        print(f"     -> 大多数人仍在盈利，不存在恐慌性抛售")

    print(f"\n  * 维度 2：带血程度（权重 30%）")
    print(f"     [{render_bar(r['blood_score'])}] {r['blood_score']:.0f}分")
    print(f"     平均成本：{r['avg_cost']:.2f} 元  |  当前折价：{r['discount_to_avg']:.1f}%")
    if r["discount_to_avg"] >= 30:
        print(f"     -> 筹码极度带血，主力吸筹性价比极高")
    elif r["discount_to_avg"] >= 15:
        print(f"     -> 有一定吸筹吸引力")
    elif r["discount_to_avg"] >= 0:
        print(f"     -> 折价不足，筹码血腥程度有限")
    else:
        print(f"     -> 筹码处于普遍获利状态")

    print(f"\n  * 维度 3：出货空间（权重 25%）")
    print(f"     [{render_bar(r['exit_score'])}] {r['exit_score']:.0f}分")
    print(f"     主要套牢盘价位：{r['resistance_price']:.2f} 元  |  拉升空间：+{r['upside_to_resistance']:.1f}%")
    if r["upside_to_resistance"] >= 40:
        print(f"     -> 出货窗口极大")
    elif r["upside_to_resistance"] >= 20:
        print(f"     -> 有合理的出货空间")
    else:
        print(f"     -> 拉升出货空间有限")

    print(f"\n  * 维度 4：筹码集中度（权重 10%）")
    print(f"     [{render_bar(r['concentration_score'])}] {r['concentration_score']:.0f}分")
    print(f"     70%区间：{r['conc_70_low']:.2f}~{r['conc_70_high']:.2f}元")
    tightening = r["concentration_tightening"]
    if tightening > 0:
        print(f"     近30日收窄{tightening:.2f}元 -> 筹码集中，大资金换手")
    elif tightening < 0:
        print(f"     近30日扩散{abs(tightening):.2f}元 -> 筹码分散")
    else:
        print(f"     近30日变化不明显")

    # === 增强分析 ===
    print("\n" + "=" * W)
    print("  增强分析（主力行为识别）")
    print("=" * W)

    print(f"\n  * 增强维度1：筹码活跃度（权重15%）")
    print(f"     [{render_bar(r['dead_score'])}] {r['dead_score']:.0f}分")
    print(f"     {r['dead_desc_num']}  -> {r['dead_desc']}")

    print(f"\n  * 增强维度2：筹码形态（权重15%）")
    print(f"     [{render_bar(r['pattern_score'])}] {r['pattern_score']:.0f}分")
    print(f"     {r['pattern_desc_num']}  -> {r['pattern_desc']}")

    print(f"\n  * 增强维度3：主力成本护盘（权重15%）")
    print(f"     [{render_bar(r['cost_score'])}] {r['cost_score']:.0f}分")
    print(f"     {r['cost_desc_num']}  -> {r['cost_desc']}")

    print(f"\n  * 增强维度4：拉升穿透力（权重13%）")
    print(f"     [{render_bar(r['penetration_score'])}] {r['penetration_score']:.0f}分")
    print(f"     {r['penetration_desc_num']}  -> {r['penetration_desc']}")

    # === 综合总结 ===
    print("\n" + "=" * W)
    print("  综合总结")
    print("=" * W)

    print(f"\n  八维综合评分：{r['total_score']:.1f}/100")
    print(f"  [{render_bar(r['total_score'])}]")
    print(f"\n  >>> {r['final_verdict']}")
    print(f"  >>> {r['final_desc']}")

    # 八维评分一览表
    print(f"\n  八维评分一览表：")
    dims = ["套牢深度", "带血程度", "出货空间", "集中度", "死筹比例", "筹码形态", "成本护盘", "穿透力"]
    scores = [r['trap_score'], r['blood_score'], r['exit_score'], r['concentration_score'],
              r['dead_score'], r['pattern_score'], r['cost_score'], r['penetration_score']]
    for i, (dim, score) in enumerate(zip(dims, scores)):
        bar = render_bar(score, 15)
        print(f"    {i+1}.{dim}: {score:>5.1f}分 [{bar}]")

    print("\n" + "-" * W)
    print("  本工具仅供研究，不构成投资建议")
    print("=" * W + "\n")


def is_trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h == 9 and m >= 30) or h == 10 or (h == 11 and m <= 30) or h in [13, 14]


def main():
    print("=" * 62)
    print("  主力视角筹码收割分析器")
    print("  原四维 + 增强四维 = 八维综合分析")
    print("=" * 62)

    realtime = is_trading_time()
    print(f"\n  当前: {datetime.now().strftime('%Y-%m-%d %H:%M')} {'[实盘模式]' if realtime else '[历史模式]'}")

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
            chip_df = fetch_chip_data(code, realtime=realtime)
            price_df = _fetch_kline_with_turnover(code, limit=5, include_today=realtime)

            if chip_df.empty or price_df.empty:
                print("数据为空，请检查股票代码")
                continue

            result = analyze_chip(code, chip_df, price_df)
            print_report(code, name, result)
        except Exception as e:
            print(f"分析失败：{e}")


if __name__ == "__main__":
    main()