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


def _fetch_kline_with_turnover(code: str, limit: int = 210, include_today: bool = True) -> pd.DataFrame:
    """
    获取日线 K 线 + 真实换手率（hsl，单位%）
    优先腾讯 newfqkline 接口（稳定，f7=换手率）；失败则降级东方财富。

    参数:
        code: 股票代码
        limit: 获取天数
        include_today: 是否尝试获取当日实时数据（实盘模式）

    返回 DataFrame: date, open, close, high, low, volume, hsl(换手率%)
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"
    df = pd.DataFrame()

    # ── 腾讯接口（主）──
    try:
        # 开始日期：往前推足够多天（limit*2 个自然日保证覆盖交易日）
        start_date = (datetime.today() - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
        end_date = datetime.today().strftime("%Y-%m-%d")
        url = (
            f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
            f"?_var=kline_dayqfq&param={symbol},day,{start_date},{end_date},{limit},qfq"
        )
        raw = _http_get(url)
        text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"^kline_dayqfq=", "", text.strip())
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
            df = pd.DataFrame(rows)
    except Exception:
        pass

    # ── 东方财富接口（备用）──
    if df.empty:
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
                df = pd.DataFrame(rows)
        except Exception:
            pass

    # ── 实盘模式：尝试追加当日实时数据 ──
    if include_today and not df.empty and DATA_SOURCE_AVAILABLE:
        try:
            today_df = _fetch_today_realtime_data(code, df)
            if not today_df.empty:
                # 移除已有的当天数据（如果有），追加实时数据
                today_str = datetime.now().strftime("%Y-%m-%d")
                df = df[df["date"].dt.strftime("%Y-%m-%d") != today_str]
                df = pd.concat([df, today_df], ignore_index=True)
                df = df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            # 实时数据获取失败不影响整体，继续使用历史数据
            pass

    return df


def _fetch_today_realtime_data(code: str, hist_df: pd.DataFrame) -> pd.DataFrame:
    """
    获取当日实时数据，用于实盘模式补充当天K线

    返回包含当日实时数据的单行DataFrame
    """
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    # 检查历史数据最后一天是否已经是今天
    if not hist_df.empty:
        last_date = hist_df["date"].iloc[-1]
        if isinstance(last_date, str):
            last_date = pd.to_datetime(last_date)
        if last_date.strftime("%Y-%m-%d") == today_str:
            # 今天的数据已经存在，无需补充
            return pd.DataFrame()

    # 获取实时行情
    quote = fetch_realtime_quote(code)
    if not quote or quote.get("price", 0) == 0:
        return pd.DataFrame()

    # 使用实时行情构建当日K线数据
    # 注意：实时行情中的换手率是当日累计的
    today_data = {
        "date": pd.to_datetime(today_str),
        "open": quote.get("open", 0),
        "close": quote.get("price", 0),
        "high": quote.get("high", 0),
        "low": quote.get("low", 0),
        "volume": float(quote.get("volume", 0)),
        "hsl": float(quote.get("turnover_rate", 0)),  # 换手率%
    }

    # 验证数据有效性
    if today_data["open"] <= 0 or today_data["close"] <= 0:
        return pd.DataFrame()

    return pd.DataFrame([today_data])


def fetch_chip_data(code: str, realtime: bool = False) -> pd.DataFrame:
    """
    获取筹码分布数据（自实现 CYQ 算法 - 基于换手率衰减模型）

    参数:
        code: 股票代码
        realtime: 是否启用实盘模式（尝试获取当日实时数据）

    返回:
        pd.DataFrame: 筹码数据
    """
    # 获取带换手率的K线数据
    temp_df = _fetch_kline_with_turnover(code, limit=210, include_today=realtime)

    # 二级降级：如果获取失败则用旧接口 + 估算换手率
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

    # ── 通达信 CYQ 算法核心实现 ──
    # 关键改进点：
    # 1. 使用更精细的价格分辨率 (factor=200)
    # 2. 正确的三角形分布权重计算
    # 3. 筹码衰减考虑历史换手率累积

    for i in range(len(records)):
        # 使用最近 120 日数据（range=120，同通达信默认）
        start = max(0, i - 119)
        kdata = records[start: i + 1]

        if len(kdata) < 5:
            continue

        # 提高价格分辨率，使结果更精确
        factor = 200  # 通达信默认约200档位
        maxprice = max(r["high"] for r in kdata)
        minprice = min(r["low"] for r in kdata)

        if maxprice == minprice:
            # 价格区间为0时无法计算，使用最小分辨率
            maxprice = minprice + 0.01

        # accuracy: 价格步长，对齐通达信
        accuracy = max(0.01, (maxprice - minprice) / (factor - 1))

        # 初始化筹码数组
        xdata = [0.0] * factor

        # ── 核心改进：正确的三角形分布算法 ──
        # 通达信假设当日成交的筹码均匀分布在最低价到最高价之间
        # 但权重按照成交均价位置呈三角形分布

        for day_idx, row in enumerate(kdata):
            open_p = float(row["open"])
            close_p = float(row["close"])
            high_p = float(row["high"])
            low_p = float(row["low"])
            hsl = float(row.get("hsl", 0) or 0)  # 换手率 %

            if hsl <= 0:
                continue

            # 当日成交均价（通达信使用 (open+close+high+low)/4）
            avg_price = (open_p + close_p + high_p + low_p) / 4.0

            # 换手率转为衰减比例
            turnover_rate = min(1.0, hsl / 100.0)

            # ── 筹码衰减 ──
            # 当日换手卖出，旧筹码按换手率衰减
            # 这是通达信的核心逻辑：历史筹码会被新成交稀释
            xdata = [x * (1 - turnover_rate) for x in xdata]

            # ── 新筹码分布（三角形分布）──
            price_range = high_p - low_p

            if price_range <= 0:
                # 一字板：筹码集中在一个价格点
                idx = int((high_p - minprice) / accuracy)
                if 0 <= idx < factor:
                    # 一字板筹码量 = 换手率 * 成交量因子
                    xdata[idx] += turnover_rate
            else:
                # 正常交易日：三角形分布
                # 通达信假设筹码在最低价到最高价之间均匀分布
                # 但成交均价位置筹码最多，呈三角形

                L = int((low_p - minprice) / accuracy)
                H = int((high_p - minprice) / accuracy)
                L = max(0, L)
                H = min(factor - 1, H)

                # 三角形分布的峰值在均价位置
                # 从最低价到均价：筹码量线性增加
                # 从均价到最高价：筹码量线性减少

                for j in range(L, H + 1):
                    cur_price = minprice + accuracy * j

                    # 计算当前价格位置的筹码权重
                    if cur_price <= avg_price:
                        # 价格低于均价：权重从最低价线性增加到均价
                        if avg_price > low_p:
                            weight = (cur_price - low_p) / (avg_price - low_p)
                        else:
                            weight = 1.0
                    else:
                        # 价格高于均价：权重从均价线性减少到最高价
                        if high_p > avg_price:
                            weight = (high_p - cur_price) / (high_p - avg_price)
                        else:
                            weight = 1.0

                    # 筹码分布量 = 换手率 * 权重
                    # 注意：需要归一化，使总筹码量等于换手率
                    chip_amount = turnover_rate * weight

                    # 归一化因子（三角形面积 = 底 * 高 / 2）
                    # 底 = H - L + 1, 高 = 1 (峰值权重)
                    triangle_area = (H - L + 1) / 2.0
                    if triangle_area > 0:
                        chip_amount = turnover_rate * weight / triangle_area

                    xdata[j] += chip_amount

        # ── 计算筹码指标 ──
        current_price = float(kdata[-1]["close"])
        total_chips = sum(xdata)

        if total_chips <= 0:
            continue

        # 获利比例：当前价格以下的筹码占比
        profit_chips = 0.0
        for j, x in enumerate(xdata):
            price_at_j = minprice + j * accuracy
            if price_at_j <= current_price:
                profit_chips += x
        benefit_part = profit_chips / total_chips

        # 平均成本：筹码累积中位数位置的价格
        def get_cost_by_chip(chip_target):
            cumsum = 0.0
            for j, x in enumerate(xdata):
                if cumsum + x >= chip_target:
                    # 线性插值获取更精确的成本价格
                    if cumsum < chip_target and x > 0:
                        ratio = (chip_target - cumsum) / x
                        return minprice + accuracy * (j + ratio)
                    return minprice + accuracy * j
                cumsum += x
            return minprice + accuracy * (factor - 1)

        avg_cost = get_cost_by_chip(total_chips * 0.5)

        # 90% 和 70% 成本区间
        def get_percent_range(percent):
            lower_target = total_chips * (1 - percent) / 2
            upper_target = total_chips * (1 + percent) / 2
            return [
                get_cost_by_chip(lower_target),
                get_cost_by_chip(upper_target),
            ]

        range_90 = get_percent_range(0.9)
        range_70 = get_percent_range(0.7)

        results.append(
            {
                "date": kdata[-1]["date"],
                "profit_ratio": benefit_part * 100,
                "avg_cost": round(avg_cost, 2),
                "conc_90_low": round(range_90[0], 2),
                "conc_90_high": round(range_90[1], 2),
                "conc_70_low": round(range_70[0], 2),
                "conc_70_high": round(range_70[1], 2),
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
    主力视角四维分析

    参数:
        code: 股票代码
        chip_df: 筹码数据DataFrame
        price_df: 价格数据DataFrame
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
    profit_ratio = result["profit_ratio"]
    loss_ratio = 100 - profit_ratio  # 被套比例
    result["loss_ratio"] = loss_ratio

    # 套牢程度评分（0-100）
    trap_score = max(0.0, min(100.0, (50 - profit_ratio) / 50 * 100))
    result["trap_score"] = round(trap_score, 1)

    # ── 维度2：带血程度（吸筹性价比）──
    avg_cost = result["avg_cost"]
    if avg_cost > 0:
        discount = (avg_cost - current_price) / avg_cost * 100
    else:
        discount = 0.0
    result["discount_to_avg"] = round(discount, 2)

    blood_score = max(0.0, min(100.0, discount / 40 * 100))
    result["blood_score"] = round(blood_score, 1)

    # ── 维度3：上方压力（出货空间）──
    resistance_price = result["concentration_70_high"]
    if resistance_price > 0 and current_price > 0:
        upside = (resistance_price - current_price) / current_price * 100
    else:
        upside = 0.0
    result["upside_to_resistance"] = round(upside, 2)
    result["resistance_price"] = resistance_price

    exit_score = max(0.0, min(100.0, upside / 50 * 100))
    result["exit_score"] = round(exit_score, 1)

    # ── 维度4：筹码集中度变化（吸筹完成度）──
    chip_range_90 = result["concentration_90_high"] - result["concentration_90_low"]
    chip_range_70 = result["concentration_70_high"] - result["concentration_70_low"]

    result["chip_range_90"] = round(chip_range_90, 2)
    result["chip_range_70"] = round(chip_range_70, 2)

    # 近30日筹码集中度变化趋势  ← BUG 1 已修复
    if len(chip_df) >= 30:
        past_chip = chip_df.iloc[-30]
        past_range = float(past_chip.get("conc_90_high", 0)) - float(
            past_chip.get("conc_90_low", 0)
        )
        concentration_change = past_range - chip_range_90  # 正值 = 收窄
        result["concentration_tightening"] = round(concentration_change, 2)
    else:
        result["concentration_tightening"] = 0.0

    # 集中度评分
    relative_range = chip_range_70 / current_price * 100 if current_price > 0 else 100
    concentration_score = max(0.0, min(100.0, (1 - relative_range / 80) * 100))
    result["concentration_score"] = round(concentration_score, 1)

    # ── 综合收割成熟度评分 ──
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


def is_trading_time() -> bool:
    """
    判断当前是否在A股交易时段内

    A股交易时间：
    - 上午：09:30 - 11:30
    - 下午：13:00 - 15:00

    返回：True 表示在交易时段内
    """
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=周一, 6=周日

    # 周末不交易
    if weekday >= 5:  # 周六、周日
        return False

    # 上午交易时段：09:30 - 11:30
    if hour == 9 and minute >= 30:
        return True
    if hour == 10:
        return True
    if hour == 11 and minute <= 30:
        return True

    # 下午交易时段：13:00 - 15:00
    if hour == 13:
        return True
    if hour == 14:
        return True
    if hour == 15 and minute == 0:  # 15:00 整点算交易时间内（收盘时刻）
        return True

    return False


def main() -> None:
    print("=" * 62)
    print("  主力视角筹码收割分析器")
    print("  站在主力角度，判断当前收割条件是否成熟")
    print("=" * 62)

    # 自动判断是否在交易时段
    realtime = is_trading_time()
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_str = weekday_names[now.weekday()]

    if realtime:
        print(f"\n  当前时间：{time_str} ({weekday_str})")
        print("  >>> 正在交易时段，自动启用实盘模式 <<<")
        print("  将获取当日实时数据进行筹码分析")
    else:
        print(f"\n  当前时间：{time_str} ({weekday_str})")
        print("  >>> 非交易时段，使用历史模式 <<<")
        print("  使用最近收盘数据进行筹码分析")

    while True:
        code = input("\n请输入股票代码（输入 q 退出）：").strip()
        if code.lower() == "q":
            print("\n已退出。\n")
            break
        if not code:
            continue

        mode_hint = "实盘" if realtime else "历史"
        print(f"\n正在获取 [{code}] 数据（{mode_hint}模式），请稍候...\n")
        try:
            name = fetch_stock_name(code)
            chip_df = fetch_chip_data(code, realtime=realtime)

            # 获取价格数据（保持与chip_df一致的模式）
            price_df = _fetch_kline_with_turnover(code, limit=5, include_today=realtime)
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