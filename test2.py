import urllib.request
import json
import sys

def get_market_prefix(code):
    """根据股票代码判断市场前缀"""
    if code.startswith(('6', '9')):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    else:
        raise ValueError("无法识别的股票代码（应为6位数字，如600835或000831）")

def fetch_kline(code, days=1500):
    """从新浪获取K线数据（旧到新）"""
    prefix = get_market_prefix(code)
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        "CN_MarketDataService.getKLineData"
        f"?symbol={prefix}{code}&scale=240&ma=no&datalen={days}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("返回数据格式异常")
            return data
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        sys.exit(1)

def safe_ma(arr, n, i):
    """安全计算移动平均，若不足n日则返回None"""
    if i < n - 1:  # 注意：i 是索引，从0开始；要算MA20，至少需要20个元素（i >= 19）
        return None
    return sum(arr[i - n + 1:i + 1]) / n

def main():
    STOCK_CODE = input("请输入股票代码（如 600835 或 000831）：").strip()
    if len(STOCK_CODE) != 6 or not STOCK_CODE.isdigit():
        print("❌ 股票代码必须是6位数字")
        sys.exit(1)

    HOLD_DAYS = 30
    TARGET = 1.2  # 目标涨幅 20%
    MAX_SIGNALS = 5

    # ===== 获取并清洗数据 =====
    raw = fetch_kline(STOCK_CODE)

    if not raw:
        print("❌ 未获取到任何K线数据，请检查股票代码是否正确或是否已退市")
        sys.exit(1)

    data = []
    for d in raw:
        try:
            data.append({
                "date": d["day"],
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": float(d["volume"])
            })
        except (KeyError, ValueError, TypeError):
            continue

    if len(data) < 60:
        print("❌ 历史数据不足，无法分析")
        sys.exit(1)

    # 确保按时间正序（最早 → 最新）
    data.sort(key=lambda x: x["date"])
    dates = [d["date"] for d in data]
    closes = [d["close"] for d in data]
    opens = [d["open"] for d in data]
    vols = [d["volume"] for d in data]

    print(f"\n📊 股票代码: {STOCK_CODE} | 数据范围: {dates[0]} 至 {dates[-1]}")

    # ===== 第一步：找出所有金叉日 =====
    golden_crosses = []
    for i in range(30, len(data)):  # 从第30天开始（MA30需要30根K线）
        ma20 = safe_ma(closes, 20, i)
        ma30 = safe_ma(closes, 30, i)
        ma20_pre = safe_ma(closes, 20, i - 1)
        ma30_pre = safe_ma(closes, 30, i - 1)

        if None in (ma20, ma30, ma20_pre, ma30_pre):
            continue

        # 金叉条件：前一天 MA20 < MA30，当天 MA20 > MA30
        if ma20_pre < ma30_pre and ma20 > ma30:
            golden_crosses.append({
                "cross_index": i,
                "cross_date": dates[i],
                "ma20": ma20,
                "ma30": ma30,
                "close": closes[i],
                "volume": vols[i]
            })

    if not golden_crosses:
        print("⚠️  近期未发现任何金叉。")
        print("💡 操盘建议：无技术金叉信号，谨慎操作。")
        return

    # ===== 第二步：对每个金叉，寻找确认阳线 =====
    signals = []
    for gc in reversed(golden_crosses):  # 从最近的金叉开始
        i = gc["cross_index"]
        cross_date = gc["cross_date"]
        cross_close = gc["close"]  # 金叉日的收盘价

        # 在金叉日后1～10天内寻找符合条件的确认阳线
        buy_found = False
        buy_price = None
        buy_date = None
        buy_index = None

        # 检查在整个搜索过程中是否出现死叉（MA20下穿MA30）
        # 搜索范围：从金叉后一天到最多15天（覆盖所有可能的确认阳线搜索范围）
        has_death_cross = False
        search_end = min(i + 16, len(data))
        for j in range(i + 1, search_end):
            ma20_current = safe_ma(closes, 20, j)
            ma30_current = safe_ma(closes, 30, j)
            ma20_prev = safe_ma(closes, 20, j - 1)
            ma30_prev = safe_ma(closes, 30, j - 1)

            if None not in (ma20_current, ma30_current, ma20_prev, ma30_prev):
                # 死叉条件：前一天 MA20 > MA30，当天 MA20 < MA30
                if ma20_prev > ma30_prev and ma20_current < ma30_current:
                    has_death_cross = True
                    break

        if has_death_cross:
            continue  # 如果出现死叉，这个金叉作废

        # 第二个条件：寻找金叉后是否有阴线
        has_yin_after_cross = False
        for j in range(i + 1, min(i + 11, len(data))):
            if closes[j] < opens[j]:  # 找到阴线
                has_yin_after_cross = True
                break

        if not has_yin_after_cross:
            continue  # 如果金叉后没有阴线，跳过这个金叉

        # 第三个条件：找最后一根阴线，然后找它后面的倍量阳线
        double_vol_yang_index = None
        double_vol_yang_close = None

        for j in range(i + 1, min(i + 11, len(data))):
            # 找到金叉后从i+1到j-1的最后一根阴线
            last_yin_index = None
            last_yin_vol = 0
            for k in range(i + 1, j):
                if closes[k] < opens[k]:  # 阴线
                    last_yin_index = k
                    last_yin_vol = vols[k]

            # 如果j是阳线
            if closes[j] > opens[j]:
                # 检查第三个条件：j的量能是否是最后一根阴线的2倍以上
                if last_yin_index is not None and vols[j] >= last_yin_vol * 2:
                    double_vol_yang_index = j
                    double_vol_yang_close = closes[j]
                    break

        if double_vol_yang_index is None:
            continue  # 没找到倍量阳线，跳过这个金叉

        # 记录倍量阳线的高点、收盘价、低点，用于上引线判断
        double_vol_yang_high = data[double_vol_yang_index]["high"]
        double_vol_yang_low = data[double_vol_yang_index]["low"]
        k_length = double_vol_yang_high - double_vol_yang_low
        upper_shadow = double_vol_yang_high - double_vol_yang_close
        # 上引线过长：上引线占K线长度60%以上
        has_long_upper_shadow = k_length > 0 and (upper_shadow / k_length) >= 0.6

        # 第四个条件：倍量阳线之后再出现阳线，收盘价要高于或接近倍量阳线收盘价（容差0.07%）
        for j in range(double_vol_yang_index + 1, min(double_vol_yang_index + 6, len(data))):
            # 如果j是阳线
            if closes[j] > opens[j]:
                price_threshold = double_vol_yang_close * 0.9993  # 允许低0.07%（与通达信一致）
                if closes[j] >= price_threshold:
                    # 上引线判断：无长上引线 或 确认阳线突破倍量阳线最高价
                    break_upper = closes[j] >= double_vol_yang_high
                    if not has_long_upper_shadow or break_upper:
                        buy_price = opens[j]  # 这根确认阳线当天开盘买入
                        buy_date = dates[j]
                        buy_index = j
                        buy_found = True
                        break

        if not buy_found:
            continue

        # ===== 回测持有期表现 =====
        max_price = buy_price
        hit_day = None
        for d in range(1, HOLD_DAYS + 1):
            idx = buy_index + d
            if idx >= len(data):
                break
            high = data[idx]["high"]
            if high > max_price:
                max_price = high
            if high >= buy_price * TARGET:
                hit_day = d
                break

        max_gain = (max_price / buy_price - 1) * 100

        # 打分
        if hit_day and hit_day <= 10:
            level = "强"
        elif hit_day:
            level = "中"
        elif max_gain >= 10:
            level = "中"
        else:
            level = "弱"

        signals.append({
            "cross_date": cross_date,      # 金叉发生日
            "buy_date": buy_date,          # 实际买入日（确认阳线日）
            "buy_price": buy_price,
            "max_gain": max_gain,
            "hit_day": hit_day,
            "level": level
        })

        if len(signals) >= MAX_SIGNALS:
            break

    # ===== 输出结果 =====
    if not signals:
        print("⚠️  发现金叉，但未找到符合条件的确认阳线（无有效交易信号）。")
        print("💡 操盘建议：金叉缺乏量能或价格确认，谨慎追高。")
        return

    print(f"\n✅ 找到 {len(signals)} 个有效交易信号（含确认阳线）：\n")

    for idx, s in enumerate(signals, 1):
        print(f"第 {idx} 次信号")
        print(f"  金叉日期: {s['cross_date']}")      # ← 关键：这里显示真正的金叉日
        print(f"  买入日期: {s['buy_date']}")
        print(f"  买入价: {s['buy_price']:.2f}")
        print(f"  最大涨幅: {s['max_gain']:.2f}%")
        if s["hit_day"]:
            print(f"  达到{int((TARGET-1)*100)}%用时: {s['hit_day']} 天")
        else:
            print(f"  {HOLD_DAYS} 天内未达 {int((TARGET-1)*100)}%")
        print(f"  强弱评级: {s['level']}\n")

    # ===== 操盘建议 =====
    levels = [s["level"] for s in signals]
    strong_count = levels.count("强")
    weak_count = levels.count("弱")

    if strong_count >= 2:
        advice = "✅ 历史上该股在此模型下爆发性较强，属于高质量形态"
    elif "强" in levels and "中" in levels:
        advice = "🟡 历史表现尚可，但稳定性一般，需结合大盘环境与基本面"
    elif weak_count >= 2:
        advice = "⚠️  历史上该模型在此股成功率偏低，谨慎对待"
    else:
        advice = "⚪ 历史表现中性，建议配合其他技术指标或基本面确认"

    print("💡 操盘建议：")
    print(advice)

if __name__ == "__main__":
    main()