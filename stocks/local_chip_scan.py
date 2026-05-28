#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地全市场筹码扫描

只供本地手动执行；GitHub Action/monitor.py 不会调用本文件。
基于 chip_analyzer.py 对 stock_list.md 中股票逐只做筹码分析，筛出高分结果。

用法：
    cd stocks
    python local_chip_scan.py
    python local_chip_scan.py --no-input --workers 16 --min-score 70 --min-timing 85 --min-price 10
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional, Tuple

from chip_analyzer import _fetch_kline_with_turnover, analyze_chip


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_LIST_FILE = os.path.join(ROOT_DIR, "stock_list.md")
OUTPUT_DIR = os.path.join(ROOT_DIR, "chip_scan_results")


def load_stock_list() -> List[Tuple[str, str]]:
    """从 stock_list.md 加载股票列表，沿用监控侧的基础过滤。"""
    if not os.path.exists(STOCK_LIST_FILE):
        raise FileNotFoundError(f"找不到股票列表: {STOCK_LIST_FILE}")

    stocks: List[Tuple[str, str]] = []
    with open(STOCK_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            match = re.match(r"\|\s*(\d{6})\s*\|\s*([^|]+)\s*\|", line)
            if not match:
                continue
            code = match.group(1)
            name = match.group(2).strip()
            if not code.startswith(("60", "00", "30")):
                continue
            if "ST" in name or "退" in name:
                continue
            stocks.append((code, name))
    return stocks


def build_chip_distribution(temp_df, tail_rows: int = 30):
    """从已获取的K线计算筹码分布，只计算最近若干行以提升全市场扫描速度。"""
    try:
        import pandas as pd

        if temp_df is None or temp_df.empty:
            return None

        records = temp_df.to_dict(orient="records")
        results = []
        factor = 200

        start_i = max(0, len(records) - tail_rows)
        for i in range(start_i, len(records)):
            start = max(0, i - 119)
            kdata = records[start:i + 1]
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

                open_p, close_p = row["open"], row["close"]
                high_p, low_p = row["high"], row["low"]
                avg_price = (open_p + close_p + high_p + low_p) / 4.0

                if high_p == low_p:
                    idx = int((high_p - minprice) / accuracy)
                    if 0 <= idx < factor:
                        xdata[idx] += turnover_rate
                else:
                    L = max(0, int((low_p - minprice) / accuracy))
                    H = min(factor - 1, int((high_p - minprice) / accuracy))
                    triangle_area = (H - L + 1) / 2.0
                    for j in range(L, H + 1):
                        cur_price = minprice + accuracy * j
                        if cur_price <= avg_price and avg_price > low_p:
                            weight = (cur_price - low_p) / (avg_price - low_p)
                        elif cur_price > avg_price and high_p > avg_price:
                            weight = (high_p - cur_price) / (high_p - avg_price)
                        else:
                            weight = 1.0
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

            profit_chips = sum(
                x for j, x in enumerate(xdata)
                if minprice + j * accuracy <= current_price
            )
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

        return pd.DataFrame(results) if results else None
    except Exception:
        return None


def analyze_one(code: str, name: str, min_score: float, min_timing: float,
                min_price: float, realtime: bool) -> Optional[dict]:
    """单股筹码分析；达到阈值则返回结果。"""
    try:
        kline_df = _fetch_kline_with_turnover(code, limit=210, include_today=realtime)
        if kline_df is None or kline_df.empty:
            return None

        try:
            if float(kline_df.iloc[-1]["close"] or 0) < min_price:
                return None
        except Exception:
            return None

        chip_df = build_chip_distribution(kline_df)
        price_df = kline_df.tail(5).copy()
        if chip_df is None or chip_df.empty or price_df.empty:
            return None

        result = analyze_chip(code, chip_df, price_df)
        current_price = float(result.get("current_price", 0) or 0)
        if current_price < min_price:
            return None

        score = float(result.get("total_score", 0) or 0)
        sentiment = result.get("holder_sentiment") or {}
        timing = float(sentiment.get("timing_score", 0) or 0)
        if score < min_score or timing < min_timing:
            return None

        return {
            "code": code,
            "name": name,
            "price": current_price,
            "score": score,
            "timing_score": timing,
            "stage": sentiment.get("stage", ""),
            "loss_ratio": sentiment.get("loss_ratio", 0),
            "avg_cost": result.get("avg_cost", 0),
            "profit_ratio": result.get("profit_ratio", 0),
            "verdict": result.get("final_verdict", ""),
            "desc": result.get("final_desc", ""),
        }
    except Exception:
        return None


def save_markdown(rows: List[dict], scanned_total: int, elapsed_sec: float, args: argparse.Namespace) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = os.path.join(OUTPUT_DIR, f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.md")

    lines = [
        f"# 本地全市场筹码扫描结果",
        "",
        f"- 扫描时间: {scan_time}",
        f"- 扫描数量: {scanned_total}",
        f"- 命中数量: {len(rows)}",
        f"- 耗时: {elapsed_sec / 60:.1f} 分钟",
        f"- 阈值: score >= {args.min_score}, timing >= {args.min_timing}, price >= {args.min_price}",
        f"- 实时模式: {'是' if args.realtime else '否'}",
        "",
    ]

    if not rows:
        lines.append("本次没有筛出符合条件的股票。")
    else:
        lines.extend([
            "| 排名 | 代码 | 名称 | 当前价 | 八维分 | timing | 阶段 | 被套比例 | 平均成本 | 获利比例 | 结论 |",
            "|---:|---|---|---:|---:|---:|---|---:|---:|---:|---|",
        ])
        for idx, row in enumerate(rows, 1):
            lines.append(
                f"| {idx} "
                f"| {row['code']} "
                f"| {row['name']} "
                f"| {row['price']:.2f} "
                f"| {row['score']:.1f} "
                f"| {row['timing_score']:.1f} "
                f"| {row['stage']} "
                f"| {float(row.get('loss_ratio', 0) or 0):.1f}% "
                f"| {float(row.get('avg_cost', 0) or 0):.2f} "
                f"| {float(row.get('profit_ratio', 0) or 0):.1f}% "
                f"| {row['verdict']} |"
            )

        lines.extend([
            "",
            "## 说明",
            "",
            "- 八维分越高，筹码条件越成熟。",
            "- timing 越高，散户恐慌/绝望程度越高。",
            "- 本结果仅供研究，不构成投资建议。",
        ])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _ask_int(prompt: str, default: int, min_value: int = 1) -> int:
    raw = input(f"{prompt}（默认 {default}）：").strip()
    if not raw:
        return default
    try:
        return max(min_value, int(raw))
    except ValueError:
        print(f"输入无效，使用默认值 {default}")
        return default


def _ask_float(prompt: str, default: float, min_value: float = 0.0) -> float:
    raw = input(f"{prompt}（默认 {default:g}）：").strip()
    if not raw:
        return default
    try:
        return max(min_value, float(raw))
    except ValueError:
        print(f"输入无效，使用默认值 {default:g}")
        return default


def _ask_bool(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{prompt}（{suffix}）：").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true", "是")


def apply_interactive_args(args: argparse.Namespace) -> argparse.Namespace:
    print("\n本地全市场筹码扫描参数")
    args.workers = _ask_int("并发线程数", args.workers, min_value=1)
    args.min_score = _ask_float("八维总分阈值", args.min_score)
    args.min_timing = _ask_float("散户心态 timing 阈值", args.min_timing)
    args.min_price = _ask_float("最低价格", args.min_price)
    args.limit = _ask_int("扫描前N只，0表示全量", args.limit, min_value=0)
    args.realtime = _ask_bool("是否盘中补实时数据", args.realtime)
    print()
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description="本地全市场筹码扫描")
    parser.add_argument("--workers", type=int, default=16, help="并发线程数，默认16")
    parser.add_argument("--min-score", type=float, default=70, help="八维总分阈值，默认70")
    parser.add_argument("--min-timing", type=float, default=85, help="散户心态 timing 阈值，默认85")
    parser.add_argument("--min-price", type=float, default=10, help="最低价格，默认10元")
    parser.add_argument("--realtime", action="store_true", help="盘中使用实时数据补当日K线")
    parser.add_argument("--limit", type=int, default=0, help="只扫描前N只，0表示全量")
    parser.add_argument("--no-input", action="store_true", help="不交互，直接使用命令行参数")
    args = parser.parse_args()

    if not args.no_input:
        args = apply_interactive_args(args)

    stocks = load_stock_list()
    if args.limit and args.limit > 0:
        stocks = stocks[:args.limit]

    print(f"加载股票: {len(stocks)} 只")
    print(
        f"阈值: score>={args.min_score}, timing>={args.min_timing}, "
        f"price>={args.min_price}, workers={args.workers}, realtime={args.realtime}"
    )

    start = time.time()
    done = 0
    results: List[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                analyze_one, code, name, args.min_score,
                args.min_timing, args.min_price, args.realtime,
            ): (code, name)
            for code, name in stocks
        }

        for fut in as_completed(futures):
            done += 1
            row = fut.result()
            if row:
                results.append(row)
                print(
                    f"命中 {row['code']} {row['name']} "
                    f"¥{row['price']:.2f} score={row['score']:.1f} "
                    f"timing={row['timing_score']:.1f} {row['stage']}"
                )
            if done % 100 == 0:
                elapsed = time.time() - start
                speed = done / elapsed if elapsed > 0 else 0
                print(f"进度 {done}/{len(stocks)}，速度 {speed:.2f} 只/秒，命中 {len(results)}")

    results.sort(key=lambda x: (x["score"], x["timing_score"]), reverse=True)
    elapsed = time.time() - start
    out = save_markdown(results, done, elapsed, args)

    print("=" * 60)
    print(f"扫描完成: {done}/{len(stocks)}，命中 {len(results)}，耗时 {elapsed / 60:.1f} 分钟")
    print(f"结果文件: {out}")
    print("=" * 60)

    for row in results[:30]:
        print(
            f"{row['code']} {row['name']} ¥{row['price']:.2f} "
            f"score={row['score']:.1f} timing={row['timing_score']:.1f} "
            f"{row['stage']} {row['verdict']}"
        )


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    main()
