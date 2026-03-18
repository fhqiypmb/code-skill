"""优化后的自动选股 - 使用原脚本的并行扫描"""


def _scan_stocks_for_period_optimized(period: str):
    """扫描指定周期的股票（优化版 - 使用原脚本并行扫描）"""
    global _screener_results, _selected_stocks

    try:
        import importlib.util

        # 1. 加载选股模块
        screener_path = stocks_dir / "严格选股_多周期.py"
        spec = importlib.util.spec_from_file_location("screener", screener_path)
        screener_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(screener_module)

        # 2. 创建选股器实例（使用多线程）
        is_minute = period in ("5 分钟", "15 分钟", "30 分钟", "60 分钟")
        max_workers = 8  # 使用 8 个并行线程

        screener_instance = screener_module.StrictStockScreener(
            period=period, period_name=period, max_workers=max_workers
        )

        stock_list = screener_instance.load_stock_list()
        if not stock_list:
            print(f"[AUTO] {period}: 股票列表为空")
            return

        print(
            f"[AUTO] {period}: 开始并行扫描 {len(stock_list)} 只股票 ({max_workers}线程)..."
        )

        # 3. 加载分析模块（只加载一次）
        analyzer_path = stocks_dir / "stock_analyzer.py"
        spec = importlib.util.spec_from_file_location("analyzer", analyzer_path)
        analyzer_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(analyzer_module)

        found_count = 0

        # 4. 定义信号回调函数
        def on_signal_callback(code, name, signal_type, details):
            nonlocal found_count
            try:
                # 5. 深度分析
                analysis = analyzer_module.analyze_stock(
                    code, name, signal_type=signal_type
                )

                result = {
                    "code": code,
                    "name": name,
                    "period": period,
                    "signal_type": signal_type,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "timestamp": datetime.now().isoformat(),
                    "analysis": analysis,
                }

                # 6. 去重添加
                if _add_result_if_new(result):
                    found_count += 1

            except Exception as e:
                print(f"[AUTO] {code} 分析失败：{e}")

        # 5. 执行并行扫描（带回调）
        screener_instance.screen_all_stocks(stock_list, on_signal=on_signal_callback)

        print(f"[AUTO] {period}: 扫描完成，找到 {found_count} 只新股票")

    except Exception as e:
        print(f"[AUTO] {period}: 错误 - {e}")
        import traceback

        traceback.print_exc()
