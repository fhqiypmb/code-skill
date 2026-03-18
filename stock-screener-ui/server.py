"""
股票选股系统 API 服务
直接调用选股脚本和分析模块，不读取 JSON 文件
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import sys
import os
import threading
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import subprocess

# 添加 stocks 目录到路径
stocks_dir = Path(__file__).parent.parent / "stocks"
sys.path.insert(0, str(stocks_dir))

app = Flask(__name__)
CORS(app)

# 全局状态
_screener_results: List[Dict] = []
_screener_running = False
_screener_progress = 0
_screener_status = "idle"
_current_code = ""  # 当前正在处理的股票代码
_screener_params = {}  # 选股参数
_auto_screener_running = False  # 自动选股是否运行
_auto_screener_thread = None  # 自动选股线程

# 数据文件路径（不再使用）
# ML_DATA_FILE = stocks_dir / "ml" / "shadow_data.json"

# 已选股票记录（去重用）
_selected_stocks = set()


def _add_result_if_new(result: Dict):
    """添加结果（去重）"""
    global _screener_results, _selected_stocks

    # 去重 key：代码 + 周期 + 信号类型 + 日期
    # 同一只股票在同一天、同一周期、同一信号类型只保留一次
    key = f"{result['code']}_{result.get('period', '')}_{result.get('signal_type', '')}_{result.get('date', '')}"

    if key not in _selected_stocks:
        _selected_stocks.add(key)
        _screener_results.insert(0, result)  # 新结果插入到开头
        print(
            f"[NEW] 添加新股票：{result['code']} {result['name']} ({result.get('period', '')} {result.get('signal_type', '')})"
        )
        return True
    else:
        print(
            f"[SKIP] 重复股票：{result['code']} {result.get('period', '')} {result.get('signal_type', '')}"
        )
        return False


def _auto_screener_loop():
    """自动选股循环（持续运行）"""
    global _auto_screener_running, _screener_results, _selected_stocks

    periods = ["5 分钟", "30 分钟", "日线"]
    period_index = 0

    print("=" * 60)
    print("[AUTO] 自动选股启动")
    print(f"[AUTO] 选股周期：{periods}")
    print(f"[AUTO] 并行线程：8 线程")
    print(f"[AUTO] 去重规则：code+period+signal_type+date")
    print("=" * 60)

    while _auto_screener_running:
        try:
            period = periods[period_index % len(periods)]
            prev_period = period

            # 执行选股
            print(
                f"\n[AUTO] 开始选股周期：{period} (第{period_index // len(periods) + 1}轮)"
            )
            _scan_stocks_for_period_optimized(period)

            # 周期索引 +1
            period_index += 1

            # 显示下一个周期
            next_period = periods[period_index % len(periods)]
            print(f"[AUTO] {period} 完成，等待 60 秒后切换到 {next_period}...")

            # 等待 60 秒后切换到下一个周期
            for i in range(60):
                if not _auto_screener_running:
                    print("[AUTO] 停止自动选股")
                    return
                if (i + 1) % 15 == 0:
                    print(f"[AUTO] 等待中... {60 - i - 1}秒后切换到 {next_period}")
                time.sleep(1)

        except Exception as e:
            print(f"[AUTO] 错误：{e}")
            import traceback

            traceback.print_exc()
            time.sleep(10)


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
        # 原脚本默认 8 个线程，大幅提升速度
        max_workers = 8

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
        # 使用原脚本的 screen_all_stocks 方法，支持并行和多数据源
        screener_instance.screen_all_stocks(stock_list, on_signal=on_signal_callback)

        print(f"[AUTO] {period}: 扫描完成，找到 {found_count} 只新股票")

    except Exception as e:
        print(f"[AUTO] {period}: 错误 - {e}")
        import traceback

        traceback.print_exc()


@app.route("/api/auto-screener/start", methods=["POST"])
def start_auto_screener():
    """启动自动选股"""
    global _auto_screener_running, _auto_screener_thread

    if _auto_screener_running:
        return jsonify({"error": "自动选股已在运行中"}), 400

    _auto_screener_running = True
    _auto_screener_thread = threading.Thread(target=_auto_screener_loop, daemon=True)
    _auto_screener_thread.start()

    return jsonify({"status": "started", "message": "自动选股已启动"})


@app.route("/api/auto-screener/stop", methods=["POST"])
def stop_auto_screener():
    """停止自动选股"""
    global _auto_screener_running

    _auto_screener_running = False
    return jsonify({"status": "stopped", "message": "自动选股已停止"})


@app.route("/api/auto-screener/status", methods=["GET"])
def get_auto_screener_status():
    """获取自动选股状态"""
    return jsonify(
        {
            "running": _auto_screener_running,
            "results_count": len(_screener_results),
            "selected_count": len(_selected_stocks),
        }
    )


@app.route("/api/health", methods=["GET"])
def health_check():
    """健康检查"""
    return jsonify(
        {"status": "ok", "timestamp": datetime.now().isoformat(), "backend": "running"}
    )


@app.route("/api/screener/start", methods=["POST"])
def start_screener():
    """开始选股任务"""
    global \
        _screener_running, \
        _screener_results, \
        _screener_progress, \
        _screener_status, \
        _screener_params

    if _screener_running:
        return jsonify({"error": "选股任务正在运行中"}), 400

    # 获取前端传来的参数
    data = request.get_json() or {}
    _screener_params = {
        "period": data.get("period", "日线"),
        "signalTypes": data.get(
            "signalTypes",
            {
                "goldCross": True,
                "doubleVolume": True,
                "breakout": True,
            },
        ),
    }
    print(
        f"[INFO] 选股参数：period={_screener_params['period']}, signalTypes={_screener_params['signalTypes']}"
    )

    # 重置状态
    _screener_results = []
    _screener_progress = 0
    _screener_status = "starting"

    # 启动后台线程
    thread = threading.Thread(target=_run_screener_thread, daemon=True)
    thread.start()

    return jsonify({"task_id": "screener_task", "status": "started"})


@app.route("/api/screener/progress", methods=["GET"])
def get_screener_progress():
    """获取选股进度"""
    return jsonify(
        {
            "running": _screener_running,
            "progress": _screener_progress,
            "status": _screener_status,
            "results_count": len(_screener_results),
            "current_code": _current_code,
        }
    )


@app.route("/api/screener/results", methods=["GET"])
def get_screener_results():
    """获取选股结果"""
    return jsonify(_screener_results)


@app.route("/api/ml/signals", methods=["GET"])
def get_ml_signals():
    """获取 ML 信号数据 - 只返回自动选股的新数据"""
    try:
        # 分页支持
        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("pageSize", 20, type=int)

        print(f"[DEBUG] _screener_results: {len(_screener_results)} items")

        # 只返回自动选股的结果（内存中的数据）
        if _screener_results:
            print("[DEBUG] Returning screener results")
            start = (page - 1) * page_size
            end = start + page_size
            page_results = _screener_results[start:end]

            signals = []
            for item in page_results:
                analysis = item.get("analysis", {})
                signals.append(
                    {
                        "date": item.get("date", datetime.now().strftime("%Y-%m-%d")),
                        "code": item.get("code", ""),
                        "name": item.get("name", ""),
                        "period": item.get("period", ""),
                        "signal_type": item.get("signal_type", ""),
                        "close": analysis.get("quote", {}).get("price", 0),
                        "verdict": analysis.get("verdict", ""),
                        "industry": analysis.get("industry", ""),
                        "sr_score": analysis.get("success_rate", {}).get("score", 0),
                        "sr_grade": analysis.get("success_rate", {}).get("grade", ""),
                        "target_price": analysis.get("technical", {}).get(
                            "target_price", 0
                        ),
                        "stop_loss": analysis.get("technical", {}).get("stop_loss", 0),
                        "reached_target": None,
                        "actual_return": None,
                    }
                )

            return jsonify(signals)
        else:
            # 没有数据时返回空数组
            return jsonify([])
    except Exception as e:
        print(f"[DEBUG] Exception: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/analysis/<code>", methods=["GET"])
def get_analysis(code: str):
    """获取个股分析详情"""
    try:
        # 先从当前选股结果中查找
        for item in _screener_results:
            if item.get("code") == code and "analysis" in item:
                return jsonify(item["analysis"])

        # 如果没有，实时分析
        import importlib.util

        analyzer_path = stocks_dir / "stock_analyzer.py"
        spec = importlib.util.spec_from_file_location("analyzer", analyzer_path)
        analyzer_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(analyzer_module)

        analysis = analyzer_module.analyze_stock(code, code, signal_type="普通")
        return jsonify(analysis)

    except Exception as e:
        print(f"[FAIL] 分析失败 {code}: {e}")
        return jsonify({"error": f"分析失败：{str(e)}"}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("  股票选股系统 API 服务")
    print("  访问：http://localhost:5000")
    print("=" * 60)
    app.run(debug=False, port=5000, host="0.0.0.0", threaded=True)
