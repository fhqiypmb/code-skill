"""
统一数据源模块 - 东方财富API为主，新浪为备

解决限流问题的核心策略：
  1. 东方财富API为主源（稳定、无需登录、不依赖第三方库）
  2. 新浪作为备用源
  3. 令牌桶限流 + 指数退避 + 自动重试
  4. 请求头伪装 + 随机延迟

数据能力：
  - 股票列表（全A股）
  - K线数据（分钟/日/周/月）
  - 行业板块分类
  - 概念板块分类
  - 个股新闻
  - 实时行情
  - 指数K线
  - 主力资金流向（用于主力意图因子）

字段说明（东方财富 push2 API）：
  f137 = 今日主力净流入（元，已是净值）
  f138 = 超大单流入（元）
  f139 = 超大单流出（元）
  f140 = 超大单净流入（元，已是净值）
  f141 = 大单流入（元）
  f142 = 大单流出（元）
  f143 = 大单净流入（元，已是净值）
  f62  = 主力净流入强度（‱，除以10000再×100得百分比）

所有接口均为东方财富/新浪公开HTTP API，无需API Key。
"""

import os
import json
import time
import re
import ssl
import random
import threading
import urllib.request
import urllib.parse
import logging
from typing import Dict, List, Optional, Tuple, TypedDict

# 禁用代理
for _key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if _key in os.environ:
        del os.environ[_key]

ssl._create_default_https_context = ssl._create_unverified_context

_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

logger = logging.getLogger(__name__)


# ==================== TypedDict 类型定义 ====================

class KLineBar(TypedDict):
    day: str
    open: str
    high: str
    low: str
    close: str
    volume: str


class IndexBar(TypedDict):
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


class NewsItem(TypedDict):
    title: str
    date: str
    source: str
    url: str


class QuoteInfo(TypedDict):
    name: str
    price: float
    change_pct: float
    high: float
    low: float
    open: float
    pre_close: float
    volume: int
    amount: int
    turnover_rate: float   # 换手率（%），f168 / 100


class CapitalFlow(TypedDict):
    main_net_in: float    # 主力净流入（万元）
    super_net_in: float   # 超大单净流入（万元）
    big_net_in: float     # 大单净流入（万元）
    flow_ratio: float     # 主力净流入强度（%）


class StockIndustry(TypedDict):
    name: str
    industry: str
    board_code: str


class BoardInfo(TypedDict):
    board_code: str
    board_name: str
    stocks: List[str]


# ==================== 通用请求工具 ====================

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 15, retry: int = 2) -> bytes:
    """通用HTTP GET，带重试和随机UA"""
    h: Dict[str, str] = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)

    last_err: Exception = RuntimeError("未知错误")
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with _opener.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last_err = e
            err_str = str(e)
            if any(code in err_str for code in ('456', '403', '429', '503')):
                wait = (attempt + 1) * 2 + random.uniform(0.5, 1.5)
                time.sleep(wait)
            elif attempt < retry:
                time.sleep(0.3 + random.uniform(0, 0.5))
    raise last_err


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None,
                   timeout: int = 15, retry: int = 2) -> dict:
    """HTTP GET 返回JSON"""
    raw = _http_get(url, headers, timeout, retry)
    return json.loads(raw.decode("utf-8"))


# ==================== 速率限制器 ====================

class RateLimiter:
    """令牌桶限流器，支持自适应退避"""

    def __init__(self, max_per_sec: float = 8.0) -> None:
        self._interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._last_time = 0.0
        self._backoff = 0.0

    def wait(self) -> None:
        """请求前调用，阻塞到满足速率"""
        with self._lock:
            now = time.time()
            wait_time = self._interval + self._backoff
            elapsed = now - self._last_time
            if elapsed < wait_time:
                time.sleep(wait_time - elapsed)
            time.sleep(random.uniform(0.01, 0.05))
            self._last_time = time.time()

    def report_throttled(self) -> None:
        """被限流时调用，增加退避"""
        with self._lock:
            self._backoff = min(max(self._backoff * 2, 1.0), 8.0)

    def report_success(self) -> None:
        """成功时调用，减少退避"""
        with self._lock:
            if self._backoff > 0:
                self._backoff = max(self._backoff * 0.5, 0)
                if self._backoff < 0.05:
                    self._backoff = 0.0


# 全局限流器（分数据源独立限流）
_eastmoney_limiter = RateLimiter(max_per_sec=10.0)
_sina_limiter = RateLimiter(max_per_sec=8.0)

# 限流统计
_throttle_counts: Dict[str, int] = {}
_throttle_lock = threading.Lock()


def _record_throttle(src: str) -> None:
    with _throttle_lock:
        _throttle_counts[src] = _throttle_counts.get(src, 0) + 1


def get_throttle_summary() -> str:
    with _throttle_lock:
        if not _throttle_counts:
            return ""
        return "限流: " + ", ".join(f"{k} {v}次" for k, v in _throttle_counts.items())


def reset_throttle_counts() -> None:
    with _throttle_lock:
        _throttle_counts.clear()


# ==================== 1. 股票列表 ====================

def fetch_stock_list() -> Dict[str, str]:
    """
    获取全部A股股票列表 {code: name}
    数据源：东方财富实时行情API（一次请求全部返回，无需分页）
    """
    stocks: Dict[str, str] = {}

    try:
        stocks = _fetch_stock_list_eastmoney()
        if len(stocks) > 3000:
            logger.info(f"东方财富获取股票列表成功: {len(stocks)} 只")
            return stocks
    except Exception as e:
        logger.warning(f"东方财富获取股票列表失败: {e}")

    try:
        sina_stocks = _fetch_stock_list_sina()
        stocks.update(sina_stocks)
        logger.info(f"新浪获取股票列表: {len(sina_stocks)} 只")
    except Exception as e:
        logger.warning(f"新浪获取股票列表失败: {e}")

    return stocks


def _fetch_stock_list_eastmoney() -> Dict[str, str]:
    """东方财富沪深A股列表（分页获取，约5000只）"""
    stocks: Dict[str, str] = {}
    page_size = 100

    for fs_type in ["m:0+t:6,m:0+t:13,m:0+t:80", "m:1+t:2,m:1+t:23"]:
        page = 1
        while True:
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn={page}&pz={page_size}&po=1&np=2&fltt=2"
                f"&invt=2&fid=f3&fs={fs_type}"
                f"&fields=f12,f14"
                f"&_={int(time.time() * 1000)}"
            )
            _eastmoney_limiter.wait()
            data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
            diff = data.get("data", {}).get("diff") or {}
            items = list(diff.values()) if isinstance(diff, dict) else diff
            if not items:
                break
            for item in items:
                code = str(item.get("f12", ""))
                name = str(item.get("f14", ""))
                if code and name and len(code) == 6:
                    stocks[code] = name
            if len(items) < page_size:
                break
            page += 1
    return stocks


def _fetch_stock_list_sina() -> Dict[str, str]:
    """
    新浪A股列表（分批请求）
    注意：新浪返回的是非标准JSON，使用容错解析而非暴力正则替换
    """
    stocks: Dict[str, str] = {}

    def _parse_sina_json(text: str) -> List[dict]:
        """容错解析新浪非标准JSON，避免暴力正则破坏URL等内容"""
        text = text.strip()
        if not text or text in ('null', '[]'):
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 仅对裸key（开头或逗号/大括号后跟着的单词:）加引号，避免破坏已有引号和URL
        fixed = re.sub(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:', r'"\1":', text)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return []

    for node in ('sh_a', 'sz_a'):
        max_page = 30 if node == 'sh_a' else 50
        for page in range(1, max_page):
            try:
                url = (
                    f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                    f"Market_Center.getHQNodeData?page={page}&num=100&sort=symbol"
                    f"&asc=1&node={node}&symbol=&_s_r_a=sez"
                )
                raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, retry=1)
                text = raw.decode("utf-8")
                items = _parse_sina_json(text)
                if not items:
                    break
                for item in items:
                    code = str(item.get("code", item.get("symbol", "")))[-6:]
                    name = item.get("name", "")
                    if code and name and len(code) == 6 and code.isdigit():
                        stocks[code] = name
                _sina_limiter.wait()
            except Exception:
                break
    return stocks


# ==================== 2. K线数据 ====================

def fetch_kline(code: str, period: str = '240min', limit: int = 1500,
                source_idx: int = 0) -> List[KLineBar]:
    """
    获取K线数据，东方财富优先，新浪备用
    返回格式: [{"day": "2024-01-01", "open": "10.0", "high": ..., "close": ..., "low": ..., "volume": ...}]

    period: '1min','5min','15min','30min','60min','240min','weekly','monthly'
    """
    sources = [_fetch_kline_eastmoney, _fetch_kline_sina]
    order = [sources[(source_idx + i) % len(sources)] for i in range(len(sources))]

    for fetch_fn in order:
        try:
            data = fetch_fn(code, period, limit)
            if data and len(data) > 30:
                return data
        except Exception as e:
            err_str = str(e)
            if any(c in err_str for c in ('456', '403', '429', 'RemoteDisconnected')):
                src_name = fetch_fn.__name__
                _record_throttle(src_name)
                if 'eastmoney' in src_name:
                    _eastmoney_limiter.report_throttled()
                else:
                    _sina_limiter.report_throttled()
            continue
    return []


def _fetch_kline_eastmoney(code: str, period: str, limit: int) -> List[KLineBar]:
    """东方财富K线API"""
    KLT_MAP: Dict[str, int] = {
        '1min': 1, '5min': 5, '15min': 15, '30min': 30,
        '60min': 60, '240min': 101, 'weekly': 102, 'monthly': 103,
    }
    market = 1 if code.startswith(('6', '9')) else 0
    klt = KLT_MAP.get(period, 101)

    _eastmoney_limiter.wait()
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={market}.{code}"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt={klt}&fqt=1&end=20500101&lmt={limit}"
        f"&_={int(time.time() * 1000)}"
    )
    resp = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
    klines: List[str] = resp.get('data', {}).get('klines', []) if resp.get('data') else []

    result: List[KLineBar] = []
    for line in klines:
        parts = line.split(',')
        if len(parts) >= 6:
            result.append({
                "day": parts[0],
                "open": parts[1],
                "high": parts[3],
                "low": parts[4],
                "close": parts[2],
                "volume": parts[5],
            })

    if result:
        _eastmoney_limiter.report_success()
    return result


def _fetch_kline_sina(code: str, period: str, limit: int) -> List[KLineBar]:
    """新浪K线API"""
    SCALE_MAP: Dict[str, int] = {
        '1min': 1, '5min': 5, '15min': 15, '30min': 30,
        '60min': 60, '240min': 240, 'weekly': 240, 'monthly': 240,
    }
    prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
    scale = SCALE_MAP.get(period, 240)

    _sina_limiter.wait()
    url = (
        f"https://quotes.sina.cn/cn/api/json_v2.php/"
        f"CN_MarketDataService.getKLineData"
        f"?symbol={prefix}{code}&scale={scale}&ma=no&datalen={limit}"
    )
    raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"})
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        return []

    result: List[KLineBar] = []
    for d in data:
        result.append({
            "day": d.get("day", ""),
            "open": d.get("open", "0"),
            "high": d.get("high", "0"),
            "low": d.get("low", "0"),
            "close": d.get("close", "0"),
            "volume": d.get("volume", "0"),
        })

    if result:
        _sina_limiter.report_success()
    return result


# ==================== 3. 行业板块 ====================

def fetch_all_industry_boards() -> List[BoardInfo]:
    """
    获取所有行业板块及其成分股
    返回: [{"board_code": "BK0475", "board_name": "白酒", "stocks": ["600519","000858",...]}]
    """
    boards: List[BoardInfo] = []
    try:
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=500&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs=m:90+t:2"
            f"&fields=f12,f14"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        items = data.get("data", {}).get("diff", []) or []

        for item in items:
            board_code = item.get("f12", "")
            board_name = item.get("f14", "")
            if board_code and board_name:
                boards.append({
                    "board_code": board_code,
                    "board_name": board_name,
                    "stocks": [],
                })

        logger.info(f"获取到 {len(boards)} 个行业板块")
    except Exception as e:
        logger.error(f"获取行业板块列表失败: {e}")

    return boards


def fetch_board_stocks(board_code: str) -> List[str]:
    """获取某个板块的成分股代码列表"""
    try:
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=3000&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs=b:{board_code}+f:!50"
            f"&fields=f12"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        items = data.get("data", {}).get("diff", []) or []
        return [str(item.get("f12", "")) for item in items if item.get("f12")]
    except Exception as e:
        logger.debug(f"获取板块 {board_code} 成分股失败: {e}")
        return []


def fetch_stock_industry(code: str) -> StockIndustry:
    """
    获取个股所属行业板块
    返回: {"name": "贵州茅台", "industry": "白酒", "board_code": "BK0475"}
    """
    result: StockIndustry = {"name": "", "industry": "", "board_code": ""}

    try:
        market = 1 if code.startswith(('6', '9')) else 0
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={market}.{code}"
            f"&fields=f57,f58,f127"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        info = data.get("data", {}) or {}
        result["name"] = info.get("f58", "")
        result["industry"] = info.get("f127", "")
    except Exception as e:
        logger.debug(f"东方财富获取个股行业失败: {e}")

    if not result["name"]:
        try:
            prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
            raw = _http_get(
                f"https://qt.gtimg.cn/q={prefix}{code}",
                headers={"Referer": "https://gu.qq.com"}, retry=1
            )
            text = raw.decode("gbk", errors="replace")
            parts = text.split("~")
            if len(parts) > 1:
                result["name"] = parts[1]
        except Exception:
            pass

    return result


# ==================== 4. 概念板块 ====================

def fetch_all_concept_boards() -> List[BoardInfo]:
    """
    获取所有概念板块
    返回: [{"board_code": "BK1050", "board_name": "人工智能", "stocks": []}]
    """
    boards: List[BoardInfo] = []
    try:
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=1000&po=1&np=1&fltt=2&invt=2"
            f"&fid=f3&fs=m:90+t:3"
            f"&fields=f12,f14"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        items = data.get("data", {}).get("diff", []) or []

        for item in items:
            board_code = item.get("f12", "")
            board_name = item.get("f14", "")
            if board_code and board_name:
                boards.append({
                    "board_code": board_code,
                    "board_name": board_name,
                    "stocks": [],
                })

        logger.info(f"获取到 {len(boards)} 个概念板块")
    except Exception as e:
        logger.error(f"获取概念板块列表失败: {e}")

    return boards


def fetch_stock_concepts(code: str) -> List[str]:
    """
    获取个股所属的所有概念板块名称
    返回: ["人工智能", "芯片", "华为概念", ...]
    """
    concepts: List[str] = []
    try:
        _eastmoney_limiter.wait()
        url = (
            f"https://datacenter.eastmoney.com/securities/api/data/v1/get?"
            f"reportName=RPT_F10_CORETHEME_BOARDTYPE"
            f"&columns=BOARD_NAME,BOARD_CODE,BOARD_TYPE"
            f"&filter=(SECURITY_CODE%3D%22{code}%22)"
            f"&pageNumber=1&pageSize=50"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://emweb.securities.eastmoney.com"})
        items = data.get("result", {}).get("data", []) or []
        for item in items:
            name = item.get("BOARD_NAME", "")
            if name:
                concepts.append(name)
    except Exception as e:
        logger.debug(f"东方财富datacenter概念获取失败 {code}: {e}")

    return concepts


# ==================== 5. 新闻 ====================

def fetch_stock_news(code: str, limit: int = 10) -> List[NewsItem]:
    """
    获取个股新闻
    返回: [{"title": "...", "date": "2024-01-01 10:00", "source": "东方财富", "url": "..."}]
    """
    news_list: List[NewsItem] = []

    try:
        news_list = _fetch_news_eastmoney(code, limit)
        if len(news_list) >= 3:
            return news_list[:limit]
    except Exception as e:
        logger.debug(f"东方财富新闻获取失败: {e}")

    try:
        sina_news = _fetch_news_sina(code, limit)
        news_list.extend(sina_news)
    except Exception as e:
        logger.debug(f"新浪新闻获取失败: {e}")

    return news_list[:limit]


def _fetch_news_eastmoney(code: str, limit: int) -> List[NewsItem]:
    """东方财富个股新闻"""
    _eastmoney_limiter.wait()
    url = (
        f"https://search-api-web.eastmoney.com/search/jsonp?"
        f"cb=jQuery&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{code}%22%2C"
        f"%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C"
        f"%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22%2C"
        f"%22clientVersion%22%3A%22curr%22%2C"
        f"%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C"
        f"%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{limit}%2C"
        f"%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )

    raw = _http_get(url, headers={"Referer": "https://so.eastmoney.com"})
    text = raw.decode("utf-8")

    m = re.search(r'jQuery\((.*)\)', text, re.S)
    if not m:
        return []

    data = json.loads(m.group(1))
    articles = (data.get("result", {})
                .get("cmsArticleWebOld", {})
                .get("list", []))

    news_list: List[NewsItem] = []
    for art in articles:
        title = art.get("title", "").strip()
        title = re.sub(r'<[^>]+>', '', title)
        title = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff\xa0]', '', title)
        if not title or len(title) < 6:
            continue

        news_list.append({
            "title": title,
            "date": art.get("date", ""),
            "source": art.get("mediaName", "") or "东方财富",
            "url": art.get("url", ""),
        })

    _eastmoney_limiter.report_success()
    return news_list


def _fetch_news_sina(code: str, limit: int) -> List[NewsItem]:
    """新浪新闻搜索"""
    news_list: List[NewsItem] = []
    _sina_limiter.wait()

    kw = urllib.parse.quote(code)
    url = (
        f"https://search.sina.com.cn/news?q={kw}"
        f"&range=all&c=news&sort=time&num={limit}"
    )
    raw = _http_get(url, headers={"Referer": "https://sina.com.cn"}, retry=1)
    text = raw.decode("utf-8", errors="replace")

    all_links = re.findall(
        r'<h2><a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        text, re.S
    )

    for url_str, raw_title in all_links:
        title = re.sub(r'<[^>]+>', '', raw_title).strip()
        if not title or len(title) < 8:
            continue
        if re.search(r'ETF|开盘[涨跌]|净值|份额|申购|赎回', title):
            continue

        date_str = ""
        idx = text.find(url_str)
        if idx >= 0:
            after = text[idx:idx + 2000]
            m_time = re.search(r'fgray_time[^>]*>\s*(.*?)</span>', after, re.S)
            if m_time:
                raw_meta = re.sub(r'<[^>]+>', '', m_time.group(1)).strip()
                parts_meta = [p.strip() for p in raw_meta.split('\n') if p.strip()]
                date_str = parts_meta[-1] if parts_meta else raw_meta

        news_list.append({
            "title": title,
            "date": date_str,
            "source": "新浪",
            "url": url_str,
        })

        if len(news_list) >= limit:
            break

    return news_list


# ==================== 6. 指数K线（行业趋势分析用） ====================

def fetch_index_kline(index_code: str, days: int = 60) -> List[IndexBar]:
    """
    获取指数日K线
    index_code: 如 '000001'(上证), '399001'(深证), '399006'(创业板)
    """
    try:
        market = 0 if index_code.startswith('39') or index_code.startswith('00') else 1

        _eastmoney_limiter.wait()
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={market}.{index_code}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt=0&end=20500101&lmt={days}"
            f"&_={int(time.time() * 1000)}"
        )
        resp = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        klines = resp.get('data', {}).get('klines', []) if resp.get('data') else []

        result: List[IndexBar] = []
        for line in klines:
            parts = line.split(',')
            if len(parts) >= 6:
                result.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                })

        if result:
            _eastmoney_limiter.report_success()
            return result
    except Exception as e:
        logger.debug(f"东方财富指数K线失败 {index_code}: {e}")

    # 备用：新浪
    try:
        symbol = f"sz{index_code}" if (index_code.startswith('39') or index_code.startswith('00')) else f"sh{index_code}"

        _sina_limiter.wait()
        url = (
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
        )
        raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"})
        text = raw.decode("utf-8")
        if text.strip() in ('null', '[]', ''):
            return []
        data = json.loads(text)
        return [{
            'date': bar.get('day', ''),
            'open': float(bar.get('open', 0)),
            'close': float(bar.get('close', 0)),
            'high': float(bar.get('high', 0)),
            'low': float(bar.get('low', 0)),
            'volume': float(bar.get('volume', 0)),
        } for bar in data]
    except Exception as e:
        logger.debug(f"新浪指数K线失败 {index_code}: {e}")

    return []


# ==================== 7. 实时行情 ====================

def fetch_realtime_quote(code: str) -> QuoteInfo:
    """
    获取个股实时行情
    返回: {"name", "price", "change_pct", "volume", "amount", "high", "low", "open", "pre_close"}
    """
    empty: QuoteInfo = {
        "name": "", "price": 0.0, "change_pct": 0.0,
        "high": 0.0, "low": 0.0, "open": 0.0,
        "pre_close": 0.0, "volume": 0, "amount": 0,
        "turnover_rate": 0.0,
    }
    try:
        market = 1 if code.startswith(('6', '9')) else 0
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={market}.{code}"
            f"&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f170"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        info = data.get("data", {}) or {}

        def _div(val: object, div: int = 100) -> float:
            return val / div if val else 0.0  # type: ignore[operator]

        # f168 换手率，单位 ‱（万分之一），除以 100 得百分比
        raw_turnover = info.get("f168")
        turnover_rate = round(float(raw_turnover) / 100, 2) if raw_turnover else 0.0  # type: ignore[arg-type]

        return {
            "name": info.get("f58", ""),
            "price": _div(info.get("f43")),
            "change_pct": _div(info.get("f170")),
            "high": _div(info.get("f44")),
            "low": _div(info.get("f45")),
            "open": _div(info.get("f46")),
            "pre_close": _div(info.get("f60")),
            "volume": info.get("f47", 0),
            "amount": info.get("f48", 0),
            "turnover_rate": turnover_rate,
        }
    except Exception as e:
        logger.debug(f"实时行情获取失败 {code}: {e}")
        return empty


# ==================== 8. 行业指数映射 ====================

INDUSTRY_INDEX_MAP: Dict[str, str] = {
    '白酒': '399998', '酿酒行业': '399998', '饮料制造': '399998',
    '食品饮料': '399996', '食品': '399996',
    '医药制造': '399913', '医疗器械': '399913', '生物制药': '399913',
    '化学制药': '399913', '中药': '399913', '医药': '399913',
    '汽车整车': '399976', '汽车零部件': '399976',
    '电子': '399986', '半导体': '399986', '芯片': '399986',
    '通信设备': '399806', '通讯': '399806',
    '计算机应用': '930851', '软件开发': '930851',
    '钢铁': '399481',
    '有色金属': '399395',
    '煤炭': '399998',
    '化工': '399481',
    '建筑材料': '399481',
    '机械设备': '399969',
    '电力设备': '399808', '新能源': '399808', '光伏': '399808',
    '银行': '399986',
    '证券': '399975', '券商': '399975',
    '保险': '399975',
    '房地产': '399393',
    '农林牧渔': '399966',
    '传媒': '399971',
    '国防军工': '399965',
    '交通运输': '399481',
}

FALLBACK_INDICES: List[Tuple[str, str]] = [
    ('000001', '上证指数'),
    ('000300', '沪深300'),
    ('399006', '创业板指'),
]


def get_industry_index(industry_name: str) -> Tuple[str, str]:
    """根据行业名返回 (指数代码, 指数名称)"""
    if industry_name in INDUSTRY_INDEX_MAP:
        return INDUSTRY_INDEX_MAP[industry_name], industry_name
    for name, code in INDUSTRY_INDEX_MAP.items():
        if name in industry_name or industry_name in name:
            return code, name
    return '', ''


# ==================== 9. 主力资金流向 ====================

def fetch_capital_flow(code: str) -> CapitalFlow:
    """
    获取当日主力/超大单/大单资金流向。

    字段说明（东方财富 push2 API，单位均为元）：
      f137 = 今日主力净流入（已是净值）
      f140 = 超大单净流入（已是净值）
      f143 = 大单净流入（已是净值）
      f47  = 成交量（手）
      f48  = 成交额（元）

    flow_ratio 计算方式：主力净流入 / 当日成交额 * 100（%）
    不使用 f62：该字段在收盘后/非交易时段返回接近 0 的占位值，不可靠。

    返回单位：万元，flow_ratio 单位：%
    """
    empty: CapitalFlow = {
        "main_net_in": 0.0,
        "super_net_in": 0.0,
        "big_net_in": 0.0,
        "flow_ratio": 0.0,
    }
    try:
        market = 1 if code.startswith(('6', '9')) else 0
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={market}.{code}"
            f"&fields=f47,f48,f137,f140,f143"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        info = data.get("data", {}) or {}

        def _to_wan(val: object) -> float:
            """元 → 万元，异常值返回 0"""
            try:
                v = float(val)  # type: ignore[arg-type]
                return round(v / 10000, 2)
            except (TypeError, ValueError):
                return 0.0

        main_net  = _to_wan(info.get("f137"))   # 主力净流入（万元）
        super_net = _to_wan(info.get("f140"))   # 超大单净流入（万元）
        big_net   = _to_wan(info.get("f143"))   # 大单净流入（万元）

        # 成交额（元）→ 万元
        amount_wan = _to_wan(info.get("f48"))

        # 强度 = 主力净流入 / 成交额，成交额为 0 时取 0
        if amount_wan > 0:
            flow_ratio = round(main_net / amount_wan * 100, 2)
        else:
            flow_ratio = 0.0

        return {
            "main_net_in":  main_net,
            "super_net_in": super_net,
            "big_net_in":   big_net,
            "flow_ratio":   flow_ratio,
        }
    except Exception as e:
        logger.debug(f"主力资金流向获取失败 {code}: {e}")
        return empty


# ==================== 10. 测试入口 ====================

def test_all_sources() -> None:
    """测试所有数据源"""
    print("=" * 60)
    print("  数据源测试")
    print("=" * 60)

    test_cases = [
        ("股票列表",        lambda: fetch_stock_list()),
        ("K线-日线",        lambda: fetch_kline("000001", "240min", 60)),
        ("K线-5分钟",       lambda: fetch_kline("000001", "5min", 100)),
        ("行业板块",        lambda: fetch_all_industry_boards()),
        ("个股行业",        lambda: fetch_stock_industry("600519")),
        ("概念板块",        lambda: fetch_all_concept_boards()),
        ("个股概念",        lambda: fetch_stock_concepts("600519")),
        ("个股新闻",        lambda: fetch_stock_news("600519", 5)),
        ("指数K线",         lambda: fetch_index_kline("000001", 30)),
        ("实时行情",        lambda: fetch_realtime_quote("600519")),
        ("主力资金流向",    lambda: fetch_capital_flow("600519")),
    ]

    for idx, (label, fn) in enumerate(test_cases, 1):
        print(f"\n[{idx}] {label}...")
        try:
            result = fn()
            if isinstance(result, list):
                print(f"    成功: {len(result)} 条")
                for item in result[:3]:
                    print(f"    {item}")
            elif isinstance(result, dict):
                print(f"    成功: {result}")
            else:
                print(f"    成功")
        except Exception as e:
            print(f"    失败: {e}")

    print(f"\n{'=' * 60}")
    throttle = get_throttle_summary()
    print(f"  {throttle if throttle else '无限流'}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    test_all_sources()