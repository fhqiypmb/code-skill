#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地全市场筹码扫描

只供本地手动执行；GitHub Action/monitor.py 不会调用本文件。
基于 chip_analyzer.py 对 stock_list.md 中股票逐只做筹码分析，筛出高分结果。

用法：
    cd stocks
    python local_chip_scan.py
    python local_chip_scan.py --workers 4 --min-score 70 --min-timing 85 --min-price 10
"""

import argparse
import csv
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Optional, Tuple

from chip_analyzer import fetch_chip_data, _fetch_kline_with_turnover, analyze_chip


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_LIST_FILE = os.path.join(ROOT_DIR, "stock_list.md")
OUTPUT_DIR = os.path.join(ROOT_DIR, "stock_monitor", "local_scan_results")


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


def analyze_one(code: str, name: str, min_score: float, min_timing: float,
                min_price: float, realtime: bool) -> Optional[dict]:
    """单股筹码分析；达到阈值则返回结果。"""
    try:
        chip_df = fetch_chip_data(code, realtime=realtime)
        price_df = _fetch_kline_with_turnover(code, limit=5, include_today=realtime)
        if chip_df is None or chip_df.empty or price_df is None or price_df.empty:
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


def save_csv(rows: List[dict]) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"chip_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    fields = [
        "code", "name", "price", "score", "timing_score", "stage",
        "loss_ratio", "avg_cost", "profit_ratio", "verdict", "desc",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="本地全市场筹码扫描")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数，默认4")
    parser.add_argument("--min-score", type=float, default=70, help="八维总分阈值，默认70")
    parser.add_argument("--min-timing", type=float, default=85, help="散户心态 timing 阈值，默认85")
    parser.add_argument("--min-price", type=float, default=10, help="最低价格，默认10元")
    parser.add_argument("--realtime", action="store_true", help="盘中使用实时数据补当日K线")
    parser.add_argument("--limit", type=int, default=0, help="只扫描前N只，0表示全量")
    args = parser.parse_args()

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
    out = save_csv(results)

    elapsed = time.time() - start
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
