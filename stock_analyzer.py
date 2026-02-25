"""
股票板块趋势分析模块
选股后，分析该股所属行业的指数K线走势 + 新闻，判断赛道是否有机会

逻辑：
  1. 获取个股所属的行业板块（新浪）
  2. 通过行业→指数代码映射，拉指数日K线分析MA趋势
  3. 获取个股近期新闻（新浪搜索）
  4. 综合输出结论：行业趋势、新闻面、上涨概率

数据源：新浪财经（板块信息/K线/新闻） + 腾讯（K线备用）
"""

import os
import json
import time
import re
import ssl
import urllib.request
import urllib.parse
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

ssl._create_default_https_context = ssl._create_unverified_context

_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _fetch_raw(url: str, referer: str = None, timeout: int = 15) -> bytes:
    """通用HTTP请求，返回原始bytes"""
    h = dict(_HEADERS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with _opener.open(req, timeout=timeout) as r:
        return r.read()


def _fetch_json(url: str, referer: str = None, timeout: int = 15) -> dict:
    raw = _fetch_raw(url, referer, timeout)
    return json.loads(raw.decode("utf-8"))


# ==================== 1. 行业指数映射 ====================

# 常见行业 → 中证/国证/申万指数代码 的映射
# 新浪K线API支持 sh/sz 开头的指数代码
_INDUSTRY_INDEX_MAP = {
    # 消费
    '白酒': 'sz399998',        # 中证白酒
    '酿酒行业': 'sz399998',
    '食品行业': 'sz399996',    # 中证食品(如不可用则用主食品)
    '食品饮料': 'sz399996',
    '饮料制造': 'sz399998',
    '家电行业': 'sz399996',
    '医药制造': 'sz399913',    # 中证医药
    '医疗器械': 'sz399913',
    '生物制药': 'sz399913',
    '化学制药': 'sz399913',
    '中药': 'sz399913',
    '医药': 'sz399913',
    '汽车制造': 'sz399976',    # 中证新能源汽车
    '汽车配件': 'sz399976',
    # 科技
    '电子器件': 'sz399986',    # 中证电子
    '电子信息': 'sz399986',
    '半导体': 'sz399986',
    '芯片': 'sz399986',
    '通讯行业': 'sz399806',    # 中证通信
    '软件服务': 'sz399998',    # 暂用白酒代替... 改用中证信息
    '计算机': 'sz399998',
    '互联网': 'sz399998',
    # 制造/工业
    '钢铁行业': 'sh000801',    # 申万钢铁
    '有色金属': 'sh000819',    # 申万有色
    '煤炭采选': 'sh000820',    # 申万煤炭
    '化工行业': 'sh000813',    # 申万化工
    '化纤行业': 'sh000813',
    '建筑建材': 'sh000812',    # 申万建材
    '机械行业': 'sz399969',    # 中证制造
    '电力行业': 'sh000807',    # 申万电力
    '电器行业': 'sz399969',
    '水泥': 'sh000812',
    # 金融
    '银行': 'sz399986',
    '券商': 'sz399975',        # 中证证券
    '保险': 'sh000952',
    '金融行业': 'sz399975',
    # 地产/基建
    '房地产': 'sh000806',      # 申万地产
    '建筑工程': 'sh000812',
    # 能源
    '石油行业': 'sh000824',
    '新能源': 'sz399808',      # 中证新能
    '光伏': 'sz399808',
    '储能': 'sz399808',
    # 其他
    '农牧饲渔': 'sz399966',    # 中证农业
    '纺织服装': 'sz399969',
    '造纸行业': 'sz399969',
    '交通运输': 'sh000804',    # 申万交运
    '船舶制造': 'sz399969',
    '飞机制造': 'sz399965',    # 中证军工
    '酒店旅游': 'sz399996',
    '传媒娱乐': 'sz399971',    # 中证传媒
    '环保行业': 'sz399808',
}

# 通用指数（当行业无精确映射时）
_FALLBACK_INDICES = [
    ('sh000001', '上证指数'),
    ('sh000300', '沪深300'),
    ('sz399006', '创业板指'),
]


def _get_industry_index(industry_name: str) -> Tuple[str, str]:
    """根据行业名称返回 (指数代码, 指数名称)"""
    # 精确匹配
    if industry_name in _INDUSTRY_INDEX_MAP:
        return _INDUSTRY_INDEX_MAP[industry_name], industry_name

    # 模糊匹配
    for name, code in _INDUSTRY_INDEX_MAP.items():
        if name in industry_name or industry_name in name:
            return code, name

    return '', ''


# ==================== 2. 获取个股所属行业（新浪） ====================

def fetch_stock_industry(code: str) -> Dict:
    """
    通过新浪获取个股行业信息
    返回: {name: 股票名, industry: 行业名}
    """
    result = {'name': '', 'industry': ''}

    # 腾讯行情获取名称（快速可靠）
    try:
        prefix = 'sh' if code.startswith('6') else 'sz'
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        raw = _fetch_raw(url, "https://gu.qq.com")
        text = raw.decode("gbk", errors="replace")
        parts = text.split("~")
        if len(parts) > 1:
            result['name'] = parts[1]
    except Exception:
        pass

    # 新浪"所属行业"页面获取行业分类
    try:
        url = (
            f"http://vip.stock.finance.sina.com.cn/corp/go.php/"
            f"vCI_CorpOtherInfo/stockid/{code}/menu_num/2.phtml"
        )
        raw = _fetch_raw(url, "https://finance.sina.com.cn")
        text = raw.decode("gbk", errors="replace")

        # 去掉HTML标签后提取"同行业个股 XXX"中的行业名
        clean = re.sub(r'<[^>]+>', ' ', text)
        m = re.search(r'同行业个股\s+(\S+)', clean)
        if m:
            result['industry'] = m.group(1).strip()
    except Exception as e:
        logger.debug(f"新浪行业获取失败: {e}")

    return result


# ==================== 3. K线获取（新浪 + 腾讯双源） ====================

def _fetch_kline_sina(symbol: str, days: int = 60) -> List[Dict]:
    """新浪K线API获取指数日K线"""
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    )
    raw = _fetch_raw(url, "https://finance.sina.com.cn")
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


def _fetch_kline_tencent(symbol: str, days: int = 60) -> List[Dict]:
    """腾讯K线API获取指数日K线"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days},"
    raw = _fetch_raw(url, "https://gu.qq.com")
    data = json.loads(raw.decode("utf-8"))

    # 解析腾讯格式: data -> {symbol: {day: [[date,open,close,high,low,vol], ...]}}
    if not isinstance(data, dict):
        return []
    inner = data.get('data', {})
    if not isinstance(inner, dict):
        return []
    sym_data = inner.get(symbol, {})
    if not isinstance(sym_data, dict):
        return []

    bars = sym_data.get('day', []) or sym_data.get('qfqday', [])
    if not isinstance(bars, list):
        return []

    result = []
    for bar in bars:
        if isinstance(bar, list) and len(bar) >= 6:
            result.append({
                'date': str(bar[0]),
                'open': float(bar[1]),
                'close': float(bar[2]),
                'high': float(bar[3]),
                'low': float(bar[4]),
                'volume': float(bar[5]),
            })
    return result


def fetch_index_kline(symbol: str, days: int = 60) -> List[Dict]:
    """获取指数日K线，新浪优先，失败用腾讯"""
    try:
        klines = _fetch_kline_sina(symbol, days)
        if klines and len(klines) >= 20:
            return klines
    except Exception as e:
        logger.debug(f"新浪K线失败 {symbol}: {e}")

    try:
        klines = _fetch_kline_tencent(symbol, days)
        if klines:
            return klines
    except Exception as e:
        logger.debug(f"腾讯K线失败 {symbol}: {e}")

    return []


# ==================== 4. MA趋势分析 ====================

def analyze_trend(klines: List[Dict]) -> Dict:
    """
    分析指数K线趋势
    返回: {trend: 上升/筑底/下跌/横盘, score, ma5, ma10, ma20, ...}
    """
    if len(klines) < 20:
        return {'trend': '数据不足', 'score': 0}

    closes = [k['close'] for k in klines]
    n = len(closes)

    # 计算MA
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20

    # 5天前的MA
    ma5_prev = sum(closes[-10:-5]) / 5 if n >= 10 else ma5
    ma10_prev = sum(closes[-20:-10]) / 10 if n >= 20 else ma10
    ma20_prev = sum(closes[-40:-20]) / 20 if n >= 40 else ma20

    # MA方向
    ma5_rising = ma5 > ma5_prev
    ma10_rising = ma10 > ma10_prev
    ma20_rising = ma20 > ma20_prev

    # 近期涨幅
    recent_chg = (closes[-1] - closes[-6]) / closes[-6] * 100 if n >= 6 else 0
    recent_20d_chg = (closes[-1] - closes[-21]) / closes[-21] * 100 if n >= 21 else 0

    # 价格位置
    low_20 = min(k['low'] for k in klines[-20:])
    high_20 = max(k['high'] for k in klines[-20:])
    price_pos = (closes[-1] - low_20) / (high_20 - low_20) * 100 if high_20 > low_20 else 50

    # 多头排列 / 空头排列
    bull_align = ma5 > ma10 > ma20
    bear_align = ma5 < ma10 < ma20

    # 判断趋势
    if bull_align and ma5_rising and ma20_rising:
        trend, score = '上升趋势', 80
    elif bull_align and ma5_rising:
        trend, score = '偏多上攻', 65
    elif ma5_rising and ma10_rising and not bear_align:
        trend, score = '企稳回升', 55
    elif ma20_rising and price_pos > 60:
        trend, score = '中期偏多', 50
    elif not ma20_rising and not ma5_rising and price_pos < 30:
        trend, score = '下跌趋势', 10
    elif bear_align:
        trend, score = '空头排列', 5
    elif not ma20_rising and ma5_rising and price_pos > 40:
        trend, score = '筑底反弹', 45
    elif not ma20_rising and 30 < price_pos < 70:
        trend, score = '横盘整理', 30
    elif ma20_rising and not ma5_rising:
        trend, score = '短期回调', 40
    else:
        trend, score = '震荡', 35

    return {
        'trend': trend,
        'score': score,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
        'ma5_rising': ma5_rising,
        'ma10_rising': ma10_rising,
        'ma20_rising': ma20_rising,
        'bull_align': bull_align,
        'bear_align': bear_align,
        'recent_5d_chg': round(recent_chg, 2),
        'recent_20d_chg': round(recent_20d_chg, 2),
        'price_position': round(price_pos, 1),
    }


# ==================== 5. 新闻获取（新浪搜索） ====================

def fetch_news(code: str, name: str = '', limit: int = 10) -> List[Dict]:
    """通过新浪搜索获取个股相关新闻"""
    news_list = []
    keyword = name or code

    # 新浪新闻搜索页
    try:
        kw_encoded = urllib.parse.quote(keyword)
        url = (
            f"https://search.sina.com.cn/news?q={kw_encoded}"
            f"&range=all&c=news&sort=time&num=20"
        )
        raw = _fetch_raw(url, "https://sina.com.cn")
        text = raw.decode("utf-8", errors="replace")

        # 提取所有 h2>a 标题链接
        all_links = re.findall(
            r'<h2><a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            text, re.S
        )

        for url_str, raw_title in all_links:
            # 清理HTML标签
            title = re.sub(r'<[^>]+>', '', raw_title).strip()
            if not title or len(title) < 8:
                continue

            # 过滤ETF/基金水文
            if re.search(r'ETF|开盘[涨跌]|净值|份额|申购|赎回', title):
                continue

            # 从标题后面找时间（"XX分钟前" 或 "YYYY-MM-DD"）
            date_str = ''
            idx = text.find(url_str)
            if idx >= 0:
                after = text[idx:idx+2000]
                m_time = re.search(r'fgray_time[^>]*>\s*(.*?)</span>', after, re.S)
                if m_time:
                    raw_meta = re.sub(r'<[^>]+>', '', m_time.group(1)).strip()
                    # 取最后一段有意义的文本作为时间
                    parts = [p.strip() for p in raw_meta.split('\n') if p.strip()]
                    date_str = parts[-1] if parts else raw_meta

            news_list.append({
                'title': title,
                'date': date_str,
                'source': '新浪',
            })

            if len(news_list) >= limit:
                break
    except Exception as e:
        logger.debug(f"新浪新闻搜索失败: {e}")

    # 补充: 新浪财经 roll API
    if len(news_list) < 3:
        try:
            kw_encoded = urllib.parse.quote(keyword)
            url = (
                f"https://feed.mix.sina.com.cn/api/roll/get?"
                f"pageid=153&lid=2516&k={kw_encoded}&num={limit}&page=1"
            )
            raw = _fetch_raw(url, "https://finance.sina.com.cn")
            data = json.loads(raw.decode("utf-8"))
            articles = data.get('result', {}).get('data', [])
            for art in articles:
                title = art.get('title', '').strip()
                if not title or re.search(r'ETF|开盘[涨跌]|净值', title):
                    continue
                ctime = art.get('ctime', '')
                try:
                    date_str = datetime.fromtimestamp(int(ctime)).strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError, OSError):
                    date_str = str(ctime)
                news_list.append({
                    'title': title,
                    'date': date_str,
                    'source': art.get('media_name', '') or '新浪财经',
                })
                if len(news_list) >= limit:
                    break
        except Exception:
            pass

    return news_list


def analyze_news_sentiment(news_list: List[Dict]) -> Dict:
    """简单的新闻情绪分析"""
    positive_kw = [
        '增长', '突破', '新高', '大涨', '利好', '中标', '签约', '合作',
        '创新', '扩产', '订单', '超预期', '回购', '增持', '分红', '涨停',
        '景气', '龙头', '国产替代', 'AI', '人工智能', '芯片', '新能源',
        '储能', '充电桩', '机器人', '无人驾驶', '低空经济',
    ]
    negative_kw = [
        '下跌', '暴跌', '利空', '减持', '违规', '处罚', '亏损',
        '下滑', '风险', '退市', '问询', '质押', '诉讼', '立案',
    ]

    pos, neg = 0, 0
    hot = []
    for n in news_list:
        title = n.get('title', '')
        for kw in positive_kw:
            if kw in title:
                pos += 1
                if kw not in hot and len(hot) < 5:
                    hot.append(kw)
                break
        for kw in negative_kw:
            if kw in title:
                neg += 1
                break

    if pos > neg + 2:
        sentiment = '偏正面'
    elif neg > pos + 1:
        sentiment = '偏负面'
    else:
        sentiment = '中性'

    return {'sentiment': sentiment, 'positive': pos, 'negative': neg, 'hot_keywords': hot}


# ==================== 6. 综合分析入口 ====================

def analyze_stock(code: str, name: str = '') -> Dict:
    """
    对一只股票进行行业趋势 + 大盘趋势 + 新闻分析
    返回结构化结果
    """
    # 1. 获取行业信息
    stock_info = fetch_stock_industry(code)
    if not name:
        name = stock_info.get('name', code)
    industry = stock_info.get('industry', '')

    # 2. 获取行业指数K线 + 大盘K线
    sector_results = []

    # 行业指数
    if industry:
        index_code, index_name = _get_industry_index(industry)
        if index_code:
            klines = fetch_index_kline(index_code, 60)
            trend = analyze_trend(klines)
            display_name = industry if industry == index_name else f"{industry}"
            sector_results.append({
                'type': '行业',
                'name': display_name,
                'index_code': index_code,
                'trend': trend,
            })

    # 大盘参考指数
    for idx_code, idx_name in _FALLBACK_INDICES:
        klines = fetch_index_kline(idx_code, 60)
        trend = analyze_trend(klines)
        sector_results.append({
            'type': '大盘',
            'name': idx_name,
            'index_code': idx_code,
            'trend': trend,
        })

    # 3. 新闻
    news_list = fetch_news(code, name, 10)
    news_info = analyze_news_sentiment(news_list)

    # 4. 计算综合上涨概率
    industry_score = 0
    market_scores = []
    for sr in sector_results:
        sc = sr['trend'].get('score', 0)
        if sr['type'] == '行业':
            industry_score = sc
        else:
            market_scores.append(sc)

    market_avg = sum(market_scores) / len(market_scores) if market_scores else 0

    news_score = {'偏正面': 70, '中性': 40, '偏负面': 10}.get(
        news_info['sentiment'], 40)

    # 综合分 = 行业50% + 大盘30% + 新闻20%
    if industry_score > 0:
        total = industry_score * 0.5 + market_avg * 0.3 + news_score * 0.2
    else:
        total = market_avg * 0.6 + news_score * 0.4

    # 映射到概率
    if total >= 70:
        probability = min(65 + int((total - 70) * 0.7), 85)
    elif total >= 50:
        probability = 40 + int((total - 50) * 1.2)
    elif total >= 30:
        probability = 20 + int((total - 30))
    else:
        probability = max(5, int(total * 0.6))

    return {
        'code': code,
        'name': name,
        'industry': industry,
        'sector_results': sector_results,
        'news': news_list,
        'news_info': news_info,
        'industry_score': industry_score,
        'market_avg': round(market_avg, 1),
        'total_score': round(total, 1),
        'probability': probability,
    }


# ==================== 7. 格式化输出 ====================

def format_analysis_report(result: Dict) -> str:
    """精简输出：行业趋势 + 大盘 + 新闻 + 结论"""
    code = result['code']
    name = result['name']
    sector_results = result.get('sector_results', [])
    news_info = result.get('news_info', {})
    prob = result['probability']

    lines = [f"", f"  {code} {name}"]

    # 行业趋势
    for sr in sector_results:
        if sr['type'] == '行业':
            t = sr['trend']
            lines.append(
                f"  行业 [{sr['name']}]: {t['trend']}  "
                f"近5日{t.get('recent_5d_chg', 0):+.1f}%  "
                f"近20日{t.get('recent_20d_chg', 0):+.1f}%"
            )
            break

    # 大盘趋势
    for sr in sector_results:
        if sr['type'] == '大盘':
            t = sr['trend']
            lines.append(
                f"  大盘 [{sr['name']}]: {t['trend']}  "
                f"近5日{t.get('recent_5d_chg', 0):+.1f}%"
            )

    # 新闻
    sentiment = news_info.get('sentiment', '中性')
    hot = news_info.get('hot_keywords', [])
    news_str = f"消息面{sentiment}"
    if hot:
        news_str += f"(热点: {','.join(hot)})"
    lines.append(f"  {news_str}")

    # 结论
    lines.append(f"  --> 近期上涨概率: {prob}%")
    lines.append(f"")

    return "\n".join(lines)


# ==================== 8. 批量分析 ====================

def analyze_stocks_batch(stocks: List[Tuple[str, str]]) -> List[Dict]:
    """批量分析，选股后自动调用"""
    if not stocks:
        return []

    print(f"\n{'=' * 60}")
    print(f"  行业趋势 + 消息面分析")
    print(f"  待分析: {len(stocks)} 只")
    print(f"{'=' * 60}")

    results = []
    for i, (code, name) in enumerate(stocks):
        print(f"\n  [{i+1}/{len(stocks)}] 分析 {code} {name} ...")
        try:
            r = analyze_stock(code, name)
            results.append(r)
            print(format_analysis_report(r))
        except Exception as e:
            print(f"    失败: {e}")
            results.append({'code': code, 'name': name, 'probability': 0})

        if i < len(stocks) - 1:
            time.sleep(0.5)

    if len(results) > 1:
        ranked = sorted(results, key=lambda x: x.get('probability', 0), reverse=True)
        print(f"  {'=' * 40}")
        print(f"  排名:")
        for i, r in enumerate(ranked):
            print(f"  {i+1}. {r['code']} {r.get('name', ''):<8} 上涨概率 {r.get('probability', 0)}%")
        print()

    return results


# ==================== 独立运行 ====================

def main():
    print()
    print("=" * 50)
    print("  行业趋势分析工具")
    print("=" * 50)
    print("  输入股票代码（6位），多只用逗号分隔")
    print()

    codes_input = input("  请输入股票代码: ").strip()
    if not codes_input:
        return

    codes = [c.strip() for c in codes_input.replace('\uff0c', ',').split(',') if c.strip()]
    stocks = [(c, '') for c in codes if len(c) == 6 and c.isdigit()]

    if not stocks:
        print("  无有效代码")
        return

    analyze_stocks_batch(stocks)


if __name__ == "__main__":
    main()
