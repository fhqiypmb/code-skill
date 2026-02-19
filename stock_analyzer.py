"""
股票板块趋势分析模块
选股后，分析该股所属行业和概念板块的K线走势 + 新闻，判断赛道是否有机会

逻辑：
  1. 获取个股所属的行业板块 + 所有概念板块
  2. 对每个板块拉日K线，分析MA趋势（上升/筑底/下跌/横盘）
  3. 获取个股近期新闻
  4. 综合输出结论：板块处于什么阶段、有几个概念在上升、新闻面如何、上涨概率

数据源：东方财富（免费API）
"""

import os
import json
import time
import ssl
import urllib.request
import urllib.parse
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

ssl._create_default_https_context = ssl._create_unverified_context

_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)


def _request_json(url: str, timeout: int = 15) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com",
    }
    req = urllib.request.Request(url, headers=headers)
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _safe_float(val, default=0.0) -> float:
    if val is None or val == '' or val == '-':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _fmt_money(val: float) -> str:
    if abs(val) >= 100_000_000:
        return f"{val / 100_000_000:.2f}亿"
    elif abs(val) >= 10_000:
        return f"{val / 10_000:.0f}万"
    else:
        return f"{val:.0f}"


# ==================== 1. 获取个股所属板块 ====================

def fetch_stock_sectors(code: str) -> Dict:
    """
    获取个股的行业板块 + 概念板块名称列表
    返回: {industry: str, region: str, concepts: [str, ...]}
    """
    market = 1 if code.startswith('6') else 0
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get?"
        f"secid={market}.{code}"
        f"&fields=f57,f58,f127,f128,f129"
        f"&_={int(time.time()*1000)}"
    )
    try:
        resp = _request_json(url)
        d = resp.get('data', {})
        if not d:
            return {'industry': '', 'region': '', 'concepts': [], 'name': ''}
        concepts_str = d.get('f129', '') or ''
        concepts = [c.strip() for c in concepts_str.split(',') if c.strip()]
        return {
            'name': d.get('f58', ''),
            'industry': d.get('f127', ''),
            'region': d.get('f128', ''),
            'concepts': concepts,
        }
    except Exception:
        return {'industry': '', 'region': '', 'concepts': [], 'name': ''}


# ==================== 2. 板块名称 -> BK代码映射 ====================

# 缓存板块列表（避免重复请求）
_sector_cache = {}
_sector_cache_lock = threading.Lock()


def _load_sector_map():
    """加载行业板块和概念板块的 名称->BK代码 映射"""
    with _sector_cache_lock:
        if _sector_cache:
            return _sector_cache

    result = {}
    # 行业板块 m:90+t:2, 概念板块 m:90+t:3
    # 每页最多返回100条，需分页加载全部
    for t in [2, 3]:
        for pn in range(1, 20):
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
                f"&fs=m:90+t:{t}"
                f"&fields=f12,f14"
                f"&_={int(time.time()*1000)}"
            )
            try:
                resp = _request_json(url)
                items = resp.get('data', {}).get('diff', []) if resp.get('data') else []
                if not items:
                    break
                for it in items:
                    name = it.get('f14', '')
                    bk_code = it.get('f12', '')
                    if name and bk_code:
                        result[name] = bk_code
            except Exception:
                break

    with _sector_cache_lock:
        _sector_cache.update(result)
    return result


def find_bk_code(sector_name: str) -> str:
    """根据板块名称找BK代码"""
    mapping = _load_sector_map()

    # 精确匹配
    if sector_name in mapping:
        return mapping[sector_name]

    # 模糊匹配（包含关系）
    for name, bk in mapping.items():
        if sector_name in name or name in sector_name:
            return bk
    return ''


# ==================== 3. 板块K线获取 + 趋势分析 ====================

def fetch_sector_kline(bk_code: str, days: int = 60) -> List[Dict]:
    """获取板块日K线"""
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid=90.{bk_code}"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56"
        f"&klt=101&fqt=1&end=20500101&lmt={days}"
        f"&_={int(time.time()*1000)}"
    )
    try:
        resp = _request_json(url)
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
        return result
    except Exception:
        return []


def analyze_trend(klines: List[Dict]) -> Dict:
    """
    分析板块K线趋势
    返回: {trend: 上升/筑底/下跌/横盘, ma5, ma10, ma20, ma5_dir, ma20_dir, recent_chg, ...}
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

    # 近5日涨幅
    recent_chg = (closes[-1] - closes[-6]) / closes[-6] * 100 if n >= 6 else 0
    # 近20日涨幅
    recent_20d_chg = (closes[-1] - closes[-21]) / closes[-21] * 100 if n >= 21 else 0

    # 近20日最低价和当前价的关系
    low_20 = min(k['low'] for k in klines[-20:])
    high_20 = max(k['high'] for k in klines[-20:])
    price_pos = (closes[-1] - low_20) / (high_20 - low_20) * 100 if high_20 > low_20 else 50

    # 多头排列: ma5 > ma10 > ma20
    bull_align = ma5 > ma10 > ma20
    # 空头排列: ma5 < ma10 < ma20
    bear_align = ma5 < ma10 < ma20

    # 判断趋势
    score = 0
    if bull_align and ma5_rising and ma20_rising:
        trend = '上升趋势'
        score = 80
    elif bull_align and ma5_rising:
        trend = '偏多上攻'
        score = 65
    elif ma5_rising and ma10_rising and not bear_align:
        trend = '企稳回升'
        score = 55
    elif ma20_rising and price_pos > 60:
        trend = '中期偏多'
        score = 50
    elif not ma20_rising and not ma5_rising and price_pos < 30:
        trend = '下跌趋势'
        score = 10
    elif bear_align:
        trend = '空头排列'
        score = 5
    elif not ma20_rising and ma5_rising and price_pos > 40:
        trend = '筑底反弹'
        score = 45
    elif not ma20_rising and price_pos > 30 and price_pos < 70:
        trend = '横盘整理'
        score = 30
    elif ma20_rising and not ma5_rising:
        trend = '短期回调'
        score = 40
    else:
        trend = '震荡'
        score = 35

    return {
        'trend': trend,
        'score': score,
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
        'ma5_rising': ma5_rising,
        'ma10_rising': ma10_rising,
        'ma20_rising': ma20_rising,
        'bull_align': bull_align,
        'bear_align': bear_align,
        'recent_5d_chg': round(recent_chg, 2),
        'recent_20d_chg': round(recent_20d_chg, 2),
        'price_position': round(price_pos, 1),
    }


# ==================== 4. 新闻获取 ====================

def fetch_news(code: str, limit: int = 10) -> List[Dict]:
    """获取个股近期新闻+公告"""
    news_list = []

    # 搜索接口
    param_obj = {
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": limit,
            "preTag": "", "postTag": ""
        }}
    }
    param_str = urllib.parse.quote(json.dumps(param_obj, ensure_ascii=False))
    url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=x&param={param_str}"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://so.eastmoney.com",
        }
        req = urllib.request.Request(url, headers=headers)
        with _opener.open(req, timeout=15) as r:
            text = r.read().decode('utf-8')
        json_str = text[text.index('(') + 1: text.rindex(')')]
        resp = json.loads(json_str)
        cms = (resp.get('result') or {}).get('cmsArticleWebOld') or {}
        articles = cms if isinstance(cms, list) else cms.get('list', [])
        for art in articles[:limit]:
            title = art.get('title', '').replace('<em>', '').replace('</em>', '')
            news_list.append({
                'title': title,
                'date': art.get('date', ''),
                'source': art.get('mediaName', '') or '资讯',
            })
    except Exception:
        pass

    # 补充公告
    if len(news_list) < limit:
        try:
            url2 = (
                f"https://np-anotice-stock.eastmoney.com/api/security/ann?"
                f"sr=-1&page_size={limit - len(news_list)}&page_index=1"
                f"&ann_type=A&client_source=web&stock_list={code}"
            )
            resp = _request_json(url2)
            for item in (resp.get('data', {}).get('list') or []):
                title = item.get('title', '') or item.get('title_ch', '')
                date = (item.get('notice_date', '') or item.get('display_time', ''))[:10]
                news_list.append({'title': title, 'date': date, 'source': '公告'})
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

    pos = 0
    neg = 0
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


# ==================== 5. 综合分析入口 ====================

def analyze_stock(code: str, name: str = '') -> Dict:
    """
    对一只股票进行板块趋势 + 新闻分析
    返回结构化结果
    """
    # 1. 获取所属板块
    sectors = fetch_stock_sectors(code)
    if not name:
        name = sectors.get('name', code)

    industry = sectors.get('industry', '')
    concepts = sectors.get('concepts', [])
    all_sectors = []
    if industry:
        all_sectors.append(('行业', industry))
    for c in concepts:
        all_sectors.append(('概念', c))

    # 2. 并行获取所有板块K线 + 新闻
    sector_results = []
    bk_tasks = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        # 提交板块K线任务
        for stype, sname in all_sectors:
            bk_code = find_bk_code(sname)
            if bk_code:
                future = executor.submit(fetch_sector_kline, bk_code, 60)
                bk_tasks[future] = (stype, sname, bk_code)

        # 提交新闻任务
        news_future = executor.submit(fetch_news, code, 10)

        # 收集板块结果
        for future in as_completed(bk_tasks):
            stype, sname, bk_code = bk_tasks[future]
            try:
                klines = future.result()
                trend_info = analyze_trend(klines)
                sector_results.append({
                    'type': stype,
                    'name': sname,
                    'bk_code': bk_code,
                    'trend': trend_info,
                })
            except Exception:
                sector_results.append({
                    'type': stype, 'name': sname,
                    'trend': {'trend': '获取失败', 'score': 0},
                })

        news_list = news_future.result()

    # 3. 新闻情绪
    news_info = analyze_news_sentiment(news_list)

    # 4. 计算综合上涨概率
    # 行业板块权重高，概念板块取均值
    industry_score = 0
    concept_scores = []
    for sr in sector_results:
        sc = sr['trend'].get('score', 0)
        if sr['type'] == '行业':
            industry_score = sc
        else:
            concept_scores.append(sc)

    concept_avg = sum(concept_scores) / len(concept_scores) if concept_scores else 0
    # 上升趋势的概念数量
    rising_concepts = [sr for sr in sector_results
                       if sr['type'] == '概念' and sr['trend'].get('score', 0) >= 55]

    # 综合分 = 行业40% + 概念均分30% + 新闻15% + 上升概念数量加成15%
    news_score = 0
    if news_info['sentiment'] == '偏正面':
        news_score = 70
    elif news_info['sentiment'] == '中性':
        news_score = 40
    else:
        news_score = 10

    rising_bonus = min(len(rising_concepts) * 15, 100)

    total = (industry_score * 0.4 + concept_avg * 0.3 +
             news_score * 0.15 + rising_bonus * 0.15)

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
        'concepts': concepts,
        'sector_results': sector_results,
        'news': news_list,
        'news_info': news_info,
        'industry_score': industry_score,
        'concept_avg': concept_avg,
        'rising_concepts': len(rising_concepts),
        'total_concepts': len(concept_scores),
        'total_score': round(total, 1),
        'probability': probability,
    }


# ==================== 6. 格式化输出 ====================

def format_analysis_report(result: Dict) -> str:
    """精简输出：板块趋势 + 新闻 + 结论"""
    code = result['code']
    name = result['name']
    industry = result.get('industry', '')
    sector_results = result.get('sector_results', [])
    news_info = result.get('news_info', {})
    prob = result['probability']

    lines = []
    lines.append(f"")
    lines.append(f"  {code} {name}")

    # 行业趋势
    for sr in sector_results:
        if sr['type'] == '行业':
            t = sr['trend']
            lines.append(f"  行业 [{sr['name']}]: {t['trend']}  "
                          f"近5日{t.get('recent_5d_chg', 0):+.1f}%  "
                          f"近20日{t.get('recent_20d_chg', 0):+.1f}%")
            break

    # 概念趋势（按分数排序，只显示关键信息）
    concept_list = [sr for sr in sector_results if sr['type'] == '概念']
    concept_list.sort(key=lambda x: x['trend'].get('score', 0), reverse=True)

    rising = [sr for sr in concept_list if sr['trend'].get('score', 0) >= 55]
    falling = [sr for sr in concept_list if sr['trend'].get('score', 0) < 30]

    if rising:
        names = [f"{sr['name']}({sr['trend']['trend']})" for sr in rising[:5]]
        lines.append(f"  上升概念({len(rising)}个): {', '.join(names)}")
    if falling:
        names = [f"{sr['name']}({sr['trend']['trend']})" for sr in falling[:3]]
        lines.append(f"  弱势概念({len(falling)}个): {', '.join(names)}")

    total_c = len(concept_list)
    if total_c > 0:
        lines.append(f"  概念总览: {total_c}个概念, {len(rising)}个上升, {len(falling)}个弱势")

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


# ==================== 7. 批量分析 ====================

def analyze_stocks_batch(stocks: List[Tuple[str, str]]) -> List[Dict]:
    """批量分析，选股后自动调用"""
    if not stocks:
        return []

    print(f"\n{'=' * 60}")
    print(f"  板块趋势 + 消息面分析")
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
            time.sleep(0.3)

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
    print("  板块趋势分析工具")
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
