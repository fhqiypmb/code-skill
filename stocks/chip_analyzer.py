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
    """获取K线数据，支持盘中实时拼接当日行情"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"
    df = pd.DataFrame()

    # 1. 尝试腾讯API
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

    # 2. 备用：东方财富API
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

    # 3. 实盘模式：尝试追加当日实时数据（使用 data_source.py 的成熟实现）
    if include_today and not df.empty and DATA_SOURCE_AVAILABLE:
        try:
            today_df = _fetch_today_realtime_data(code, df)
            if not today_df.empty:
                today_str = datetime.now().strftime("%Y-%m-%d")
                df = df[df["date"].dt.strftime("%Y-%m-%d") != today_str]
                df = pd.concat([df, today_df], ignore_index=True)
                df = df.sort_values("date").reset_index(drop=True)
        except:
            pass  # 实时数据获取失败不影响整体

    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)

    return df


def _fetch_today_realtime_data(code: str, hist_df: pd.DataFrame) -> pd.DataFrame:
    """
    获取当日实时数据，用于实盘模式补充/替换当天K线
    返回包含当日实时数据的单行DataFrame

    注意：不复用K线API中的当天行，因为K线API返回的当天close可能是
    开盘价或缓存旧价，并非真实实时最新价。始终用实时行情API覆盖。
    """
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    # 使用 data_source.py 的成熟实现获取实时行情
    quote = fetch_realtime_quote(code)
    if not quote or quote.get("price", 0) == 0:
        return pd.DataFrame()

    # 使用实时行情构建当日K线
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
# 散户心态模拟（新增维度）
# ─────────────────────────────────────────────────────────

def calc_holder_sentiment(avg_cost: float, current_price: float) -> dict:
    """
    散户群体心态模拟分析（市场视角）

    使用市场平均成本（筹码分布50%分位）作为"全市场散户平均买入价"，
    模拟整体散户群体在不同亏损阶段的心理状态。
    核心逻辑：当散户群体心态到达"极度恐慌无脑割肉"程度时，往往是主力拉升的最佳时机。
    """
    if avg_cost <= 0 or current_price <= 0:
        return {"error": "价格数据无效"}

    # 计算市场整体亏损比例（负数为亏损）
    loss_ratio = (current_price - avg_cost) / avg_cost * 100

    result = {
        "avg_cost": avg_cost,
        "current_price": current_price,
        "loss_ratio": round(loss_ratio, 2),
        "loss_amount": round(avg_cost - current_price, 2),
    }

    # 心态阶段判定（亏损比例越低，心态越崩）
    if loss_ratio >= 0:
        # 盈利状态
        result["stage"] = "盈利期"
        result["stage_emoji"] = "[^_^]"
        result["mindset"] = "赚钱了，心情不错"
        result["behavior"] = "可能获利了结或继续持有"
        result["panic_score"] = 0
        result["sentiment_desc"] = "散户情绪稳定，无恐慌压力"
        result["signal"] = "无信号"
        result["signal_desc"] = "盈利状态下，散户不会割肉，主力难以低成本吸筹"

    elif loss_ratio >= -5:
        # 小亏，不服气
        result["stage"] = "微亏期"
        result["stage_emoji"] = "[:-)]"
        result["mindset"] = "小亏而已，很正常"
        result["behavior"] = "观望、补仓，不认为是错误"
        result["panic_score"] = 10
        result["sentiment_desc"] = "散户心态平稳，认为只是正常波动"
        result["signal"] = "无信号"
        result["signal_desc"] = "散户还在幻想反弹，不会割肉"

    elif loss_ratio >= -10:
        # 焦虑期
        result["stage"] = "焦虑期"
        result["stage_emoji"] = "[:-|]"
        result["mindset"] = "有点慌了，要不要止损？"
        result["behavior"] = "频繁看盘，犹豫不决，开始怀疑"
        result["panic_score"] = 25
        result["sentiment_desc"] = "散户开始焦虑，但还抱有幻想"
        result["signal"] = "观望"
        result["signal_desc"] = "部分散户开始动摇，但大多数人还在扛"

    elif loss_ratio >= -20:
        # 痛苦期
        result["stage"] = "痛苦期"
        result["stage_emoji"] = "[;_( ]"
        result["mindset"] = "亏太多了，怎么办..."
        result["behavior"] = "睡不着觉，反复纠结，不敢告诉家人"
        result["panic_score"] = 45
        result["sentiment_desc"] = "散户内心煎熬，在割与不割间挣扎"
        result["signal"] = "关注"
        result["signal_desc"] = "散户心态开始崩溃，主力可逐步建仓"

    elif loss_ratio >= -30:
        # 崩溃期（关键节点）
        result["stage"] = "崩溃期"
        result["stage_emoji"] = "[>_<]"
        result["mindset"] = "受不了了！割肉算了！"
        result["behavior"] = "情绪崩溃，频繁查看账户，考虑清仓"
        result["panic_score"] = 65
        result["sentiment_desc"] = "散户心理防线崩溃，极易无脑割肉"
        result["signal"] = "[!!] 买入信号"
        result["signal_desc"] = "绝大多数散户撑不住了，正是主力吸筹黄金期"

    elif loss_ratio >= -40:
        # 绝望期（关键节点）
        result["stage"] = "绝望期"
        result["stage_emoji"] = "[X_X]"
        result["mindset"] = "彻底没希望了，清仓走人"
        result["behavior"] = "心如死灰，不再看盘，决定割肉离场"
        result["panic_score"] = 80
        result["sentiment_desc"] = "散户彻底绝望，割肉意愿极强"
        result["signal"] = "[***] 强买入信号"
        result["signal_desc"] = "散户大规模割肉，主力吸筹接近完成，拉升在即！"

    elif loss_ratio >= -50:
        # 躺平期
        result["stage"] = "躺平期"
        result["stage_emoji"] = "[-_-]"
        result["mindset"] = "懒得看了，随它去吧"
        result["behavior"] = "死猪不怕开水烫，删软件不看盘"
        result["panic_score"] = 70
        result["sentiment_desc"] = "散户已经麻木，割肉动力反而下降"
        result["signal"] = "[!] 买入信号"
        result["signal_desc"] = "散户筹码已锁定，主力拉升阻力较小"

    elif loss_ratio >= -60:
        # 深度套牢期
        result["stage"] = "深套期"
        result["stage_emoji"] = "[=_=]"
        result["mindset"] = "已经无所谓了..."
        result["behavior"] = "彻底放弃，等解套再说"
        result["panic_score"] = 60
        result["sentiment_desc"] = "散户已躺平，筹码基本不动"
        result["signal"] = "关注"
        result["signal_desc"] = "散户筹码锁定，但拉升需要较强动力"

    else:
        # 极端深度套牢
        result["stage"] = "死筹期"
        result["stage_emoji"] = "[@_@]"
        result["mindset"] = "忘记还有这只股票了"
        result["behavior"] = "账户都不想打开"
        result["panic_score"] = 50
        result["sentiment_desc"] = "散户彻底放弃，筹码变成死筹"
        result["signal"] = "中性"
        result["signal_desc"] = "筹码锁定极强，但需要大利好才能激活"

    # 计算距离关键心理关口（正确逻辑）
    stages = [
        (-5, "焦虑期临界点"),
        (-10, "痛苦期临界点"),
        (-20, "崩溃期临界点"),
        (-30, "绝望期临界点"),
        (-40, "躺平期临界点"),
    ]

    distances = []
    for threshold, name in stages:
        # threshold是临界点（如-5表示亏损5%的关口）
        # loss_ratio是当前亏损（如-30表示亏损30%）
        # 如果当前亏损更深（更负），说明已经过了这个关口
        dist = abs(loss_ratio - threshold)  # 距离该关口的幅度
        if loss_ratio <= threshold:  # 当前亏损比临界点更深 = 已跌破
            distances.append((name, round(dist, 1), f"已跌破{dist:.1f}%"))
        else:  # 当前亏损比临界点更浅 = 还需跌
            distances.append((name, round(dist, 1), f"还需跌{dist:.1f}%"))

    result["stage_distances"] = distances

    # 拉升时机评分（综合评分，越高说明越适合拉升）
    # 最优区间是 -30% 到 -40%（绝望期）
    # 同时根据股票自身 avg_cost 动态计算"理想买点"价位，给出散户视角建议
    ideal_despair = avg_cost * 0.70   # 进入绝望期(-30%)对应价位
    ideal_givrup = avg_cost * 0.60    # 进入躺平期(-40%)对应价位
    rebound_top  = avg_cost * 0.85    # 微亏期上沿(-15%)
    deep_numb    = avg_cost * 0.50    # 麻木区(-50%)

    if -40 <= loss_ratio <= -25:
        result["timing_score"] = 100
        result["timing_desc"] = "★★★ 最佳拉升时机 ★★★"
        result["action_advice"] = (
            f"散户已进入恐慌割肉区，主力收割性价比最高。\n"
            f"     -> 散户视角：可跟随主力建仓，当前价 {current_price:.2f} 元即在黄金区\n"
            f"        若跌破 {deep_numb:.2f} 元（亏-50%）反而进入麻木区，拉升动力下降"
        )
    elif -50 <= loss_ratio <= -20:
        result["timing_score"] = 85
        result["timing_desc"] = "★★☆ 次佳拉升时机"
        if loss_ratio < -25:
            note = f"已接近最佳建仓区，可分批介入"
        else:
            note = f"时机较好，建议分批建仓"
        result["action_advice"] = (
            f"{note}。\n"
            f"     -> 散户视角：理想加仓区 {ideal_givrup:.2f}~{ideal_despair:.2f} 元（亏-30%~-40%绝望期）"
        )
    elif -60 <= loss_ratio <= -10:
        result["timing_score"] = 60
        result["timing_desc"] = "★☆☆ 时机一般"
        if loss_ratio > -25:
            # 偏浅 → 等再跌
            need_drop = abs(loss_ratio - (-30))
            result["action_advice"] = (
                f"散户尚未充分割肉，主力可能还想再砸一砸。\n"
                f"     -> 散户视角：暂不建议追入，等散户进一步割肉\n"
                f"        理想买点：股价再跌至约 {ideal_despair:.2f} 元"
                f"（再跌 {need_drop:.1f}% 进入绝望期）"
            )
        else:
            # 偏深 → 已躺平
            result["action_advice"] = (
                f"散户已接近躺平区，但拉升动力较弱。\n"
                f"     -> 散户视角：可小仓位试探，需观察成交量是否放大\n"
                f"        若放量突破 {avg_cost:.2f} 元（市场平均成本）才是真启动"
            )
    elif loss_ratio >= 0:
        result["timing_score"] = 20
        result["timing_desc"] = "不建议拉升（散户盈利）"
        result["action_advice"] = (
            f"市场整体盈利 +{loss_ratio:.1f}%，散户不会割肉，主力无收割空间。\n"
            f"     -> 散户视角：不建议追高，等回调至 {avg_cost:.2f} 元附近再观察"
        )
    else:
        result["timing_score"] = 40
        result["timing_desc"] = "拉升阻力较大"
        result["action_advice"] = (
            f"散户已深度套牢但已麻木，需大利好催化。\n"
            f"     -> 散户视角：观望为主，等待放量异动信号（量能放大3倍以上）"
        )

    return result


def render_sentiment_bar(panic_score: float, width: int = 20) -> str:
    """渲染恐慌程度条"""
    filled = int(panic_score / 100 * width)
    return "█" * filled + "░" * (width - filled)


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

    # === 散户群体心态模拟分析（自动基于市场平均成本） ===
    result["holder_sentiment"] = None
    avg_cost = result.get("avg_cost", 0)
    if avg_cost and avg_cost > 0:
        result["holder_sentiment"] = calc_holder_sentiment(avg_cost, current_price)

        # 用八维总分对操作建议做仲裁，避免心态视角与综合评分打架
        hs = result["holder_sentiment"]
        sentiment_mature = hs.get("timing_score", 0) >= 85  # 心态85+表示散户已成熟
        sentiment_brewing = hs.get("timing_score", 0) >= 60  # 60-85表示酝酿中
        ideal_low = avg_cost * 0.60
        ideal_high = avg_cost * 0.70

        if total_score >= 70:
            if sentiment_mature:
                hs["action_advice"] = (
                    f"八维 {total_score:.0f}分 + 散户进入恐慌区 -> 主力收割条件全面成熟。\n"
                    f"     -> 综合判断：可建仓，当前价 {current_price:.2f} 元处于黄金区"
                )
            else:
                hs["action_advice"] = (
                    f"八维 {total_score:.0f}分（条件优秀），但散户心态尚未崩溃。\n"
                    f"     -> 综合判断：可逐步建仓，主力可能已先于散户行动"
                )
        elif total_score >= 50:
            # 中间区——这是最容易打架的区间
            if sentiment_mature:
                hs["action_advice"] = (
                    f"心态视角：散户割肉意愿强（局部利好）\n"
                    f"     但八维仅 {total_score:.0f}分（观望区），筹码形态/穿透力等不足\n"
                    f"     -> 综合判断：等待更明确信号，不建议立即建仓\n"
                    f"        若坚持介入，分批价位：{ideal_low:.2f} ~ {ideal_high:.2f} 元（绝望期区间）\n"
                    f"        重点观察：维度6筹码形态是否收敛为单峰、维度8穿透力是否改善"
                )
            elif sentiment_brewing:
                need_drop = abs(loss_ratio - (-30)) if loss_ratio > -30 else 0
                hs["action_advice"] = (
                    f"八维 {total_score:.0f}分 + 散户尚未充分割肉 -> 主力可能还想再砸一砸。\n"
                    f"     -> 综合判断：暂不建议追入\n"
                    f"        理想买点：{ideal_high:.2f} 元附近（再跌 {need_drop:.1f}% 进入绝望期）"
                )
            else:
                hs["action_advice"] = (
                    f"八维 {total_score:.0f}分 + 散户仍盈利或深度麻木 -> 收割条件不齐备。\n"
                    f"     -> 综合判断：观望，等待散户心态进一步演化"
                )
        else:
            # 八维总分<50，无论心态如何都不建议
            hs["action_advice"] = (
                f"八维仅 {total_score:.0f}分，主力收割条件不足。\n"
                f"     -> 综合判断：不建议买入，等待筹码结构改善\n"
                f"        参考关键价位：{ideal_high:.2f} 元（市场绝望期）"
            )

    return result


def render_bar(score: float, width: int = 25) -> str:
    filled = int(score / 100 * width)
    return "#" * filled + "-" * (width - filled)


def print_report(code: str, name: str, r: dict) -> None:
    W = 62
    print("\n" + "=" * W)
    print("  主力视角筹码收割分析（八维综合）")
    print("=" * W)
    print(f"  股票：{name}({code})")
    print(f"  当前价格：{r['current_price']:.2f} 元  ({r['price_date']})")
    print(f"  筹码数据：{r['chip_date']}")

    print("-" * W)

    # === 八维分析统一展示 ===
    print("\n" + "-" * W)
    print("  八维综合分析")
    print("-" * W)

    # 维度1：套牢深度
    print(f"\n  * 维度 1：套牢深度（权重 12%）")
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

    # 维度2：带血程度
    print(f"\n  * 维度 2：带血程度（权重 12%）")
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

    # 维度3：出货空间
    print(f"\n  * 维度 3：出货空间（权重 10%）")
    print(f"     [{render_bar(r['exit_score'])}] {r['exit_score']:.0f}分")
    print(f"     主要套牢盘价位：{r['resistance_price']:.2f} 元  |  拉升空间：+{r['upside_to_resistance']:.1f}%")
    if r["upside_to_resistance"] >= 40:
        print(f"     -> 出货窗口极大")
    elif r["upside_to_resistance"] >= 20:
        print(f"     -> 有合理的出货空间")
    else:
        print(f"     -> 拉升出货空间有限")

    # 维度4：筹码集中度
    print(f"\n  * 维度 4：筹码集中度（权重 8%）")
    print(f"     [{render_bar(r['concentration_score'])}] {r['concentration_score']:.0f}分")
    print(f"     70%区间：{r['conc_70_low']:.2f}~{r['conc_70_high']:.2f}元")
    tightening = r["concentration_tightening"]
    if tightening > 0:
        print(f"     近30日收窄{tightening:.2f}元 -> 筹码集中，大资金换手")
    elif tightening < 0:
        print(f"     近30日扩散{abs(tightening):.2f}元 -> 筹码分散")
    else:
        print(f"     近30日变化不明显")

    # 维度5：死筹比例
    print(f"\n  * 维度 5：死筹比例（权重 15%）")
    print(f"     [{render_bar(r['dead_score'])}] {r['dead_score']:.0f}分")
    print(f"     {r['dead_desc_num']}  -> {r['dead_desc']}")

    # 维度6：筹码形态
    print(f"\n  * 维度 6：筹码形态（权重 15%）")
    print(f"     [{render_bar(r['pattern_score'])}] {r['pattern_score']:.0f}分")
    print(f"     {r['pattern_desc_num']}  -> {r['pattern_desc']}")

    # 维度7：主力成本护盘
    print(f"\n  * 维度 7：主力成本护盘（权重 15%）")
    print(f"     [{render_bar(r['cost_score'])}] {r['cost_score']:.0f}分")
    print(f"     {r['cost_desc_num']}  -> {r['cost_desc']}")

    # 维度8：拉升穿透力
    print(f"\n  * 维度 8：拉升穿透力（权重 13%）")
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

    # === 散户群体心态模拟分析（市场视角） ===
    if r.get("holder_sentiment"):
        hs = r["holder_sentiment"]
        print("\n" + "=" * W)
        print("  散户群体心态模拟（基于市场平均成本）")
        print("=" * W)

        # 基本信息行
        profit_symbol = "[+]" if hs['loss_ratio'] >= 0 else "[-]"
        print(f"\n  {profit_symbol} 市场平均成本：{hs['avg_cost']:.2f} 元")
        print(f"      当前价格：    {hs['current_price']:.2f} 元")

        if hs['loss_ratio'] >= 0:
            print(f"      市场整体盈利：+{abs(hs['loss_ratio']):.1f}%")
        else:
            print(f"      市场整体亏损：{hs['loss_ratio']:.1f}%")

        # 心态阶段
        print(f"\n  {'-' * 28}")
        print(f"   群体心态阶段：{hs['stage_emoji']} {hs['stage']}")
        print(f"  {'-' * 28}")
        print(f"     散户心理：\"{hs['mindset']}\"")
        print(f"     典型行为：{hs['behavior']}")
        print(f"     恐慌程度：[{render_sentiment_bar(hs['panic_score'])}] {hs['panic_score']}分")

        # 心理关口距离
        print(f"\n  [关键心理关口]")
        for stage_name, dist, desc in hs['stage_distances'][:5]:
            print(f"     - {stage_name}：{desc}")

        # 操作建议（散户视角，含具体价位）
        if hs.get("action_advice"):
            print(f"\n  {'=' * 28}")
            print(f"   操作建议")
            print(f"  {'=' * 28}")
            print(f"     {hs['action_advice']}")

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
    print("  原四维 + 增强四维 + 散户群体心态模拟 = 九维综合分析")
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