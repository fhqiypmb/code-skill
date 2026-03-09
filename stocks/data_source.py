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
from typing import Dict, List, Optional, Tuple

# 禁用代理
for _key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if _key in os.environ:
        del os.environ[_key]

ssl._create_default_https_context = ssl._create_unverified_context

_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

logger = logging.getLogger(__name__)

# ==================== 通用请求工具 ====================

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _http_get(url: str, headers: dict = None, timeout: int = 15, retry: int = 2) -> bytes:
    """通用HTTP GET，带重试和随机UA"""
    h = {
        "User-Agent": _random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)

    last_err = None
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with _opener.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 限流/拒绝 → 等更久再重试
            if any(code in err_str for code in ('456', '403', '429', '503')):
                wait = (attempt + 1) * 2 + random.uniform(0.5, 1.5)
                time.sleep(wait)
            elif attempt < retry:
                time.sleep(0.3 + random.uniform(0, 0.5))
    raise last_err


def _http_get_json(url: str, headers: dict = None, timeout: int = 15, retry: int = 2) -> dict:
    """HTTP GET 返回JSON"""
    raw = _http_get(url, headers, timeout, retry)
    return json.loads(raw.decode("utf-8"))


# ==================== 速率限制器 ====================

class RateLimiter:
    """令牌桶限流器，支持自适应退避"""

    def __init__(self, max_per_sec: float = 8.0):
        self._interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._last_time = 0.0
        self._backoff = 0.0

    def wait(self):
        """请求前调用，阻塞到满足速率"""
        with self._lock:
            now = time.time()
            wait_time = self._interval + self._backoff
            elapsed = now - self._last_time
            if elapsed < wait_time:
                time.sleep(wait_time - elapsed)
            # 加一点随机抖动，避免多线程同步请求
            time.sleep(random.uniform(0.01, 0.05))
            self._last_time = time.time()

    def report_throttled(self):
        """被限流时调用，增加退避"""
        with self._lock:
            self._backoff = min(max(self._backoff * 2, 1.0), 8.0)

    def report_success(self):
        """成功时调用，减少退避"""
        with self._lock:
            if self._backoff > 0:
                self._backoff = max(self._backoff * 0.5, 0)
                if self._backoff < 0.05:
                    self._backoff = 0


# 全局限流器（分数据源独立限流）
_eastmoney_limiter = RateLimiter(max_per_sec=10.0)
_sina_limiter = RateLimiter(max_per_sec=8.0)

# 限流统计
_throttle_counts = {}
_throttle_lock = threading.Lock()


def _record_throttle(src: str):
    with _throttle_lock:
        _throttle_counts[src] = _throttle_counts.get(src, 0) + 1


def get_throttle_summary() -> str:
    with _throttle_lock:
        if not _throttle_counts:
            return ""
        return "限流: " + ", ".join(f"{k} {v}次" for k, v in _throttle_counts.items())


def reset_throttle_counts():
    with _throttle_lock:
        _throttle_counts.clear()


# ==================== 1. 股票列表 ====================

def fetch_stock_list() -> Dict[str, str]:
    """
    获取全部A股股票列表 {code: name}
    数据源：东方财富实时行情API（一次请求全部返回，无需分页）
    """
    stocks = {}

    # 东方财富 - 沪深A股列表
    try:
        stocks = _fetch_stock_list_eastmoney()
        if len(stocks) > 3000:
            logger.info(f"东方财富获取股票列表成功: {len(stocks)} 只")
            return stocks
    except Exception as e:
        logger.warning(f"东方财富获取股票列表失败: {e}")

    # 备用：新浪
    try:
        sina_stocks = _fetch_stock_list_sina()
        stocks.update(sina_stocks)
        logger.info(f"新浪获取股票列表: {len(sina_stocks)} 只")
    except Exception as e:
        logger.warning(f"新浪获取股票列表失败: {e}")

    return stocks


def _fetch_stock_list_eastmoney() -> Dict[str, str]:
    """东方财富沪深A股列表（分页获取，约5000只）"""
    stocks = {}
    page_size = 100  # 东方财富每页最多100条

    # 分两批：深市(m:0+t:6,m:0+t:13,m:0+t:80) 和 沪市(m:1+t:2,m:1+t:23)
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
            if isinstance(diff, dict):
                items = list(diff.values())
            else:
                items = diff
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
    """新浪A股列表（分批请求）"""
    stocks = {}
    # 沪市
    for page in range(1, 30):
        try:
            url = f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=sh_a&symbol=&_s_r_a=sez"
            raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, retry=1)
            text = raw.decode("utf-8")
            if not text or text.strip() in ('null', '[]'):
                break
            # 新浪返回的是JS风格的JSON
            text = re.sub(r'(\w+):', r'"\1":', text)
            items = json.loads(text)
            if not items:
                break
            for item in items:
                code = str(item.get("code", item.get("symbol", "")))[-6:]
                name = item.get("name", "")
                if code and name and len(code) == 6:
                    stocks[code] = name
            _sina_limiter.wait()
        except Exception:
            break
    # 深市
    for page in range(1, 50):
        try:
            url = f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=sz_a&symbol=&_s_r_a=sez"
            raw = _http_get(url, headers={"Referer": "https://finance.sina.com.cn"}, retry=1)
            text = raw.decode("utf-8")
            if not text or text.strip() in ('null', '[]'):
                break
            text = re.sub(r'(\w+):', r'"\1":', text)
            items = json.loads(text)
            if not items:
                break
            for item in items:
                code = str(item.get("code", item.get("symbol", "")))[-6:]
                name = item.get("name", "")
                if code and name and len(code) == 6:
                    stocks[code] = name
            _sina_limiter.wait()
        except Exception:
            break
    return stocks


# ==================== 2. K线数据 ====================

def fetch_kline(code: str, period: str = '240min', limit: int = 1500,
                source_idx: int = 0) -> List[Dict]:
    """
    获取K线数据，东方财富优先，新浪备用
    返回格式: [{"day": "2024-01-01", "open": "10.0", "high": ..., "close": ..., "low": ..., "volume": ...}, ...]

    period: '1min','5min','15min','30min','60min','240min','weekly','monthly'
    """
    sources = [_fetch_kline_eastmoney, _fetch_kline_sina]
    # 通过 source_idx 分散请求到不同数据源
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


def _fetch_kline_eastmoney(code: str, period: str, limit: int) -> List[Dict]:
    """东方财富K线API"""
    KLT_MAP = {
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
    klines = resp.get('data', {}).get('klines', []) if resp.get('data') else []

    result = []
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


def _fetch_kline_sina(code: str, period: str, limit: int) -> List[Dict]:
    """新浪K线API"""
    SCALE_MAP = {
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

    result = []
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

def fetch_all_industry_boards() -> List[Dict]:
    """
    获取所有行业板块及其成分股
    返回: [{"board_code": "BK0475", "board_name": "白酒", "stocks": ["600519","000858",...]}]
    """
    boards = []

    # 第一步：获取行业板块列表
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


def fetch_stock_industry(code: str) -> Dict:
    """
    获取个股所属行业板块
    返回: {"name": "贵州茅台", "industry": "白酒", "board_code": "BK0475"}
    """
    result = {"name": "", "industry": "", "board_code": ""}

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

    # 备用：腾讯行情获取名称
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

def fetch_all_concept_boards() -> List[Dict]:
    """
    获取所有概念板块
    返回: [{"board_code": "BK1050", "board_name": "人工智能", "stocks": []}]
    """
    boards = []
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
    concepts = []

    # 东方财富 datacenter 个股板块信息
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

def fetch_stock_news(code: str, limit: int = 10) -> List[Dict]:
    """
    获取个股新闻
    返回: [{"title": "...", "date": "2024-01-01 10:00", "source": "东方财富", "url": "..."}]
    """
    news_list = []

    # 东方财富个股新闻API
    try:
        news_list = _fetch_news_eastmoney(code, limit)
        if len(news_list) >= 3:
            return news_list[:limit]
    except Exception as e:
        logger.debug(f"东方财富新闻获取失败: {e}")

    # 备用：新浪搜索
    try:
        sina_news = _fetch_news_sina(code, limit)
        news_list.extend(sina_news)
    except Exception as e:
        logger.debug(f"新浪新闻获取失败: {e}")

    return news_list[:limit]


def _fetch_news_eastmoney(code: str, limit: int) -> List[Dict]:
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

    # 去掉 JSONP 包装
    m = re.search(r'jQuery\((.*)\)', text, re.S)
    if not m:
        return []

    data = json.loads(m.group(1))
    articles = (data.get("result", {})
                .get("cmsArticleWebOld", {})
                .get("list", []))

    news_list = []
    for art in articles:
        title = art.get("title", "").strip()
        title = re.sub(r'<[^>]+>', '', title)  # 去HTML标签
        title = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff\xa0]', '', title)  # 去零宽/特殊字符
        if not title or len(title) < 6:
            continue

        date_str = art.get("date", "")
        source = art.get("mediaName", "") or "东方财富"
        art_url = art.get("url", "")

        news_list.append({
            "title": title,
            "date": date_str,
            "source": source,
            "url": art_url,
        })

    _eastmoney_limiter.report_success()
    return news_list


def _fetch_news_sina(code: str, limit: int) -> List[Dict]:
    """新浪新闻搜索"""
    news_list = []
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
                parts = [p.strip() for p in raw_meta.split('\n') if p.strip()]
                date_str = parts[-1] if parts else raw_meta

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

def fetch_index_kline(index_code: str, days: int = 60) -> List[Dict]:
    """
    获取指数日K线
    index_code: 如 '000001'(上证), '399001'(深证), '399006'(创业板)
    """
    # 东方财富指数K线
    try:
        # 判断市场：上证指数1开头，深证指数0/3开头
        if index_code.startswith('39') or index_code.startswith('00'):
            market = 0  # 深市
        else:
            market = 1  # 沪市

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

        result = []
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
        # 转换代码格式
        if index_code.startswith('39') or index_code.startswith('00'):
            symbol = f"sz{index_code}"
        else:
            symbol = f"sh{index_code}"

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
        result = []
        for bar in data:
            result.append({
                'date': bar.get('day', ''),
                'open': float(bar.get('open', 0)),
                'close': float(bar.get('close', 0)),
                'high': float(bar.get('high', 0)),
                'low': float(bar.get('low', 0)),
                'volume': float(bar.get('volume', 0)),
            })
        return result
    except Exception as e:
        logger.debug(f"新浪指数K线失败 {index_code}: {e}")

    return []


# ==================== 7. 实时行情 ====================

def fetch_realtime_quote(code: str) -> Dict:
    """
    获取个股实时行情
    返回: {"name", "price", "change_pct", "volume", "amount", "high", "low", "open", "pre_close"}
    """
    try:
        market = 1 if code.startswith(('6', '9')) else 0
        _eastmoney_limiter.wait()
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={market}.{code}"
            f"&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f170"
            f"&_={int(time.time() * 1000)}"
        )
        data = _http_get_json(url, headers={"Referer": "https://quote.eastmoney.com"})
        info = data.get("data", {}) or {}

        return {
            "name": info.get("f58", ""),
            "price": info.get("f43", 0) / 100 if info.get("f43") else 0,
            "change_pct": info.get("f170", 0) / 100 if info.get("f170") else 0,
            "high": info.get("f44", 0) / 100 if info.get("f44") else 0,
            "low": info.get("f45", 0) / 100 if info.get("f45") else 0,
            "open": info.get("f46", 0) / 100 if info.get("f46") else 0,
            "pre_close": info.get("f60", 0) / 100 if info.get("f60") else 0,
            "volume": info.get("f47", 0),
            "amount": info.get("f48", 0),
        }
    except Exception as e:
        logger.debug(f"实时行情获取失败 {code}: {e}")
        return {}


# ==================== 8. 行业指数映射（stock_analyzer用） ====================

# 东方财富行业→板块指数代码映射
INDUSTRY_INDEX_MAP = {
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

FALLBACK_INDICES = [
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


# ==================== 测试入口 ====================

def test_all_sources():
    """测试所有数据源"""
    print("=" * 60)
    print("  数据源测试")
    print("=" * 60)

    # 1. 股票列表
    print("\n[1] 股票列表...")
    try:
        stocks = fetch_stock_list()
        print(f"    成功: {len(stocks)} 只")
        sample = list(stocks.items())[:3]
        for c, n in sample:
            print(f"    {c} {n}")
    except Exception as e:
        print(f"    失败: {e}")

    # 2. K线
    print("\n[2] K线数据 (000001 日线)...")
    try:
        klines = fetch_kline("000001", "240min", 60)
        print(f"    成功: {len(klines)} 根")
        if klines:
            print(f"    最新: {klines[-1]['day']} 收盘:{klines[-1]['close']}")
    except Exception as e:
        print(f"    失败: {e}")

    # 3. K线（分钟线）
    print("\n[3] K线数据 (000001 5分钟线)...")
    try:
        klines = fetch_kline("000001", "5min", 100)
        print(f"    成功: {len(klines)} 根")
    except Exception as e:
        print(f"    失败: {e}")

    # 4. 行业板块
    print("\n[4] 行业板块...")
    try:
        boards = fetch_all_industry_boards()
        print(f"    成功: {len(boards)} 个板块")
        for b in boards[:3]:
            print(f"    {b['board_code']} {b['board_name']}")
    except Exception as e:
        print(f"    失败: {e}")

    # 5. 个股行业
    print("\n[5] 个股行业 (600519)...")
    try:
        info = fetch_stock_industry("600519")
        print(f"    {info}")
    except Exception as e:
        print(f"    失败: {e}")

    # 6. 概念板块
    print("\n[6] 概念板块...")
    try:
        concepts = fetch_all_concept_boards()
        print(f"    成功: {len(concepts)} 个概念")
        for c in concepts[:3]:
            print(f"    {c['board_code']} {c['board_name']}")
    except Exception as e:
        print(f"    失败: {e}")

    # 7. 个股概念
    print("\n[7] 个股概念 (600519)...")
    try:
        cs = fetch_stock_concepts("600519")
        print(f"    {cs}")
    except Exception as e:
        print(f"    失败: {e}")

    # 8. 新闻
    print("\n[8] 个股新闻 (600519)...")
    try:
        news = fetch_stock_news("600519", 5)
        print(f"    成功: {len(news)} 条")
        for n in news[:3]:
            print(f"    [{n['date']}] {n['title'][:40]}")
    except Exception as e:
        print(f"    失败: {e}")

    # 9. 指数K线
    print("\n[9] 指数K线 (上证指数)...")
    try:
        klines = fetch_index_kline("000001", 30)
        print(f"    成功: {len(klines)} 根")
    except Exception as e:
        print(f"    失败: {e}")

    # 10. 实时行情
    print("\n[10] 实时行情 (600519)...")
    try:
        q = fetch_realtime_quote("600519")
        print(f"    {q.get('name','')} {q.get('price',0)} {q.get('change_pct',0)}%")
    except Exception as e:
        print(f"    失败: {e}")

    print(f"\n{'=' * 60}")
    throttle = get_throttle_summary()
    if throttle:
        print(f"  {throttle}")
    else:
        print("  无限流")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    test_all_sources()
