# -*- coding: utf-8 -*-
"""
浏览器资金流向备用源 —— 东方财富 API 限流时的降级方案。

设计：
  - 当 push2 API 被限流时，用 Playwright 无头浏览器打开
    https://data.eastmoney.com/zjlx/{code}.html 抓取资金流向。
  - 浏览器实例全局单例、懒加载、进程退出自动回收（避免每股开关）。
  - 反爬：webdriver 伪装、随机 UA/视口、随机延迟、限流检测。
  - 单股按需查询，适合低频（一天 100~200 次）使用。

数据准确性（已实测对比 push2 API，三只样本股票）：
  主力净流入 / 超大单净流入 / 大单净流入 三字段与 API 完全一致。
  这正是 data_source.fetch_capital_flow 所使用的字段。

返回结构与 data_source.CapitalFlow 同构：
  main_net_in   主力净流入（万元）
  super_net_in  超大单净流入（万元）
  big_net_in    大单净流入（万元）
  flow_ratio    主力净比（%）—— 取页面"主力净比"，与 API 口径对齐
"""

import re
import time
import atexit
import random
import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_PAGE_URL = "https://data.eastmoney.com/zjlx/{code}.html"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]

_ANTI_DETECT = """
(function(){
    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
    Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
    Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});
    window.chrome = window.chrome || { runtime: {} };
})();
"""

# 限流/异常关键词
_BLOCK_WORDS = ("验证码", "频繁访问", "访问过于", "拒绝访问", "请先登录", "Access Denied")

_TIMEOUT_MS = 20000
_MAX_RENDER_WAIT = 12.0   # 轮询等待数据渲染的最长秒数（CI 跨境网络慢，需足够余量）
_MAX_ATTEMPTS = 3         # 抓取重试轮数（尽量保证拿到数据）


class _BrowserSession:
    """全局单例无头浏览器会话，懒加载、线程安全、进程退出自动回收。"""

    _instance: Optional["_BrowserSession"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._available: Optional[bool] = None  # None=未知, True/False=已探测

    @classmethod
    def get(cls) -> "_BrowserSession":
        with cls._lock:
            if cls._instance is None:
                cls._instance = _BrowserSession()
                atexit.register(cls._instance.close)
            return cls._instance

    def _ensure_browser(self):
        """懒加载浏览器进程（复用），返回 browser 或 None（不可用）。
        注意：只保证 Chromium 进程存在，context/page 由 fetch 每股新建并关闭。"""
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("未安装 playwright，浏览器备用源不可用。"
                           "安装: pip install playwright && playwright install chromium")
            self._available = False
            return None
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._available = True
            logger.info("浏览器备用源已启动")
            return self._browser
        except Exception as e:
            logger.warning(f"浏览器备用源启动失败: {e}")
            self._available = False
            self.close()
            return None

    def fetch(self, code: str) -> Optional[Dict[str, float]]:
        """抓取单只股票资金流向，返回 dict 或 None。
        每只股票/每轮重试都开全新 context+page，用完即关，避免反爬会话累积。"""
        with self._lock:
            browser = self._ensure_browser()
            if browser is None:
                return None

            # 最多尝试 3 轮（CI 跨境网络慢，首轮可能渲染不全；尽量保证拿到）
            for attempt in range(_MAX_ATTEMPTS):
                context = None
                page = None
                body = ""
                try:
                    # 每次全新上下文：独立 cookie/会话/指纹，规避反爬累积
                    context = browser.new_context(
                        user_agent=random.choice(_USER_AGENTS),
                        viewport=random.choice(_VIEWPORTS),
                        locale="zh-CN",
                    )
                    context.add_init_script(_ANTI_DETECT)
                    page = context.new_page()
                    page.goto(
                        _PAGE_URL.format(code=code),
                        wait_until="domcontentloaded",
                        timeout=_TIMEOUT_MS,
                    )
                    # 轮询等待：直到页面出现"主力净流入"文字（数据已渲染）或超时。
                    deadline = time.time() + _MAX_RENDER_WAIT
                    while time.time() < deadline:
                        time.sleep(0.6)
                        try:
                            body = page.locator("body").inner_text(timeout=5000)
                        except Exception:
                            body = ""
                            continue
                        if "主力净流入" in body:
                            break
                except Exception as e:
                    logger.debug(f"浏览器抓取 {code} 失败 (第{attempt+1}次): {e}")
                    body = ""
                finally:
                    # 无论成败，关闭本轮 context+page（释放会话）
                    for obj in (page, context):
                        if obj is not None:
                            try:
                                obj.close()
                            except Exception:
                                pass

                if any(w in body for w in _BLOCK_WORDS):
                    logger.warning(f"浏览器备用源疑似被限流/拦截 ({code})")
                    return None

                result = _parse_fund_page(body)
                if result is not None:
                    return result
                # 解析失败（数据没渲染出来），稍等后下一轮重试
                logger.debug(f"浏览器 {code} 第{attempt+1}/{_MAX_ATTEMPTS}次未解析到数据，重试")
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(1.0 + attempt)

            logger.warning(f"浏览器备用源 {code} 经 {_MAX_ATTEMPTS} 次重试仍未获取到资金数据")
            return None

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


def _to_wan(num_str: str, unit: str) -> float:
    """统一换算为万元：'亿'×10000，'万'×1。"""
    v = float(num_str)
    return round(v * 10000, 2) if unit == "亿" else round(v, 2)


def _parse_fund_page(html: str) -> Optional[Dict[str, float]]:
    """
    从资金流向页面文本提取主力/超大单/大单净流入及主力净比。
    真实文本格式: '今日主力净流入：\\t-2086.3514万\\t主力净比：\\t-8.47%'
    "大单"用负向后顾断言 (?<!超) 排除"超大单"，避免字段污染。
    所有净流入统一为【万元】。
    """
    result: Dict[str, float] = {}

    flow_specs = [
        ("main_net_in",  r"主力净流入"),
        ("super_net_in", r"超大单净流入"),
        ("big_net_in",   r"(?<!超)大单净流入"),
    ]
    for key, pat in flow_specs:
        m = re.search(pat + r"[：:]\s*(-?[\d.]+)\s*([万亿])", html)
        if m:
            result[key] = _to_wan(m.group(1), m.group(2))

    # 主力净比（%）
    m = re.search(r"主力净比[：:]\s*(-?[\d.]+)\s*%", html)
    if m:
        result["flow_ratio"] = float(m.group(1))

    # 至少要拿到主力净流入才算有效
    if "main_net_in" not in result:
        return None
    return result


def fetch_capital_flow_browser(code: str) -> Optional[Dict[str, float]]:
    """
    浏览器备用源：获取单只股票资金流向。
    返回 dict（与 data_source.CapitalFlow 同构）或 None（失败/限流/未安装）。
      {main_net_in, super_net_in, big_net_in, flow_ratio}  单位：万元 / %
    """
    data = _BrowserSession.get().fetch(code)
    if not data:
        return None
    return {
        "main_net_in":  data.get("main_net_in", 0.0),
        "super_net_in": data.get("super_net_in", 0.0),
        "big_net_in":   data.get("big_net_in", 0.0),
        "flow_ratio":   data.get("flow_ratio", 0.0),
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    test_code = sys.argv[1] if len(sys.argv) > 1 else "300290"
    print(f"测试浏览器备用源: {test_code}")
    print(fetch_capital_flow_browser(test_code))
