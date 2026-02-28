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

def fetch_stock_name(code):
    """从新浪获取股票名称"""
    prefix = get_market_prefix(code)
    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode("gbk")
            # 格式: var hq_str_sh600835="上海机电,..."
            parts = text.split('"')
            if len(parts) >= 2 and parts[1]:
                return parts[1].split(',')[0]
    except Exception:
        pass
    return None

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

    # 获取股票名称
    stock_name = fetch_stock_name(STOCK_CODE) or "未知"

    print(f"\n📊 {stock_name}（{STOCK_CODE}） | 数据范围: {dates[0]} 至 {dates[-1]}")

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
        # 搜索范围：从金叉后一天到最多26天（覆盖20天倍量阳窗口+确认阳线5天）
        has_death_cross = False
        search_end = min(i + 26, len(data))
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

        # 第二个条件：寻找金叉后是否有阴线（窗口20天）
        has_yin_after_cross = False
        for j in range(i + 1, min(i + 21, len(data))):
            if closes[j] < opens[j]:  # 找到阴线
                has_yin_after_cross = True
                break

        if not has_yin_after_cross:
            continue  # 如果金叉后没有阴线，跳过这个金叉

        # 第三个条件：找最后一根阴线，然后找它后面的倍量阳线（窗口20天）
        double_vol_yang_index = None
        double_vol_yang_close = None

        for j in range(i + 1, min(i + 21, len(data))):
            # 找到金叉后从i+1到j-1的最后一根阴线
            last_yin_index = None
            last_yin_vol = 0
            for k in range(i + 1, j):
                if closes[k] < opens[k]:  # 阴线
                    last_yin_index = k
                    last_yin_vol = vols[k]

            # 如果j是阳线
            if closes[j] > opens[j]:
                # 检查第三个条件：j的量能是否是最后一根阴线的2倍以上，且大于金叉日量
                cross_day_vol = vols[i]  # 金叉日量能
                if last_yin_index is not None and vols[j] >= last_yin_vol * 2 and vols[j] > cross_day_vol:
                    double_vol_yang_index = j
                    double_vol_yang_close = closes[j]
                    break

        if double_vol_yang_index is None:
            continue  # 没找到倍量阳线，跳过这个金叉

        # 记录倍量阳线的量能
        double_vol_yang_vol = vols[double_vol_yang_index]
        cross_vol = vols[i]  # 金叉日量能

        # ===== 阴线缩量判断（洗盘vs出货）=====
        # 取金叉到倍量阳之间量最大的阴线来判断
        max_yin_vol_between = 0
        for k in range(i + 1, double_vol_yang_index):
            if closes[k] < opens[k]:  # 阴线
                if vols[k] > max_yin_vol_between:
                    max_yin_vol_between = vols[k]

        # 阴线缩量：最大阴线量 < 金叉日量的2倍
        yin_shrink = max_yin_vol_between > 0 and max_yin_vol_between < cross_vol * 2
        if not yin_shrink:
            continue  # 阴线没缩量，跳过

        # ===== 放量适度判断 =====
        # 放量适度：倍量阳线量能 < 最后阴线量的6倍
        vol_moderate = double_vol_yang_vol < last_yin_vol * 6
        # 放量过大不跳过，标记为爆量信号
        is_explode_vol = not vol_moderate

        # 统计金叉到确认阳线之间所有阳线的最大量能（排除倍量阳线）
        def get_max_yang_vol_between(start_idx, end_idx, exclude_idx):
            """获取start_idx到end_idx之间所有阳线的最大量能（排除exclude_idx）"""
            max_vol = 0
            for k in range(start_idx + 1, end_idx):
                if k == exclude_idx:
                    continue
                if closes[k] > opens[k]:  # 是阳线
                    if vols[k] > max_vol:
                        max_vol = vols[k]
            return max_vol

        # 第四个条件：倍量阳线之后再出现阳线，收盘价要高于或接近倍量阳线收盘价（容差0.07%）
        # 增加条件：确认阳线量能 > 金叉到确认阳线之间所有阳线量能（排除倍量阳线）
        for j in range(double_vol_yang_index + 1, min(double_vol_yang_index + 6, len(data))):
            # 如果j是阳线
            if closes[j] > opens[j]:
                price_threshold = double_vol_yang_close * 0.9993  # 允许低0.07%（与通达信一致）
                if closes[j] >= price_threshold:
                    # 确认阳线量能达标：量能 > 金叉到确认阳之间所有阳线（排除倍量阳）
                    max_yang_vol = get_max_yang_vol_between(i, j, double_vol_yang_index)
                    if vols[j] <= max_yang_vol:
                        continue  # 量能不达标，跳过
                    buy_price = closes[j]  # 确认阳线收盘价买入
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
            "level": level,
            "is_explode_vol": is_explode_vol  # 是否爆量信号
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
        signal_type = "爆量" if s["is_explode_vol"] else "正常"
        print(f"第 {idx} 次信号 [{signal_type}]")
        print(f"  金叉日期: {s['cross_date']}")
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