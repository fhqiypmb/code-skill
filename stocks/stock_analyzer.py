"""
个股基本面分析模块
用于选股后的辅助判断，分析：
  1. 所属行业板块
  2. 所属概念板块
  3. 个股近期新闻 + 情绪分析
  4. 实时行情
  5. 上涨概率（多因子打分法）

多因子打分法（5因子，技术面由选股程序覆盖）：
  因子1 - 新闻情绪（25%）：正面/负面新闻比例 + 热点关键词
  因子2 - 新闻关注度（15%）：近期新闻数量，关注度高则合力强
  因子3 - 板块热度（20%）：概念板块数量 + 是否含热门概念
  因子4 - 行业景气度（20%）：所属行业指数近期涨跌趋势
  因子5 - 大盘环境（20%）：上证指数近期走势，系统性风险因子

数据源：data_source.py（东方财富为主、新浪备用，自动限流+fallback）
"""

import sys
import time
import logging
from typing import Dict, List, Tuple

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import data_source

logger = logging.getLogger(__name__)


# ==================== 1. 新闻情绪分析 ====================

def analyze_news_sentiment(news_list: List[Dict]) -> Dict:
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


# ==================== 2. 上涨概率 - 多因子打分 ====================

# 热门概念关键词（命中加分）
HOT_CONCEPTS = [
    'AI', '人工智能', '芯片', '半导体', '机器人', '无人驾驶', '低空经济',
    '新能源', '储能', '光伏', '充电桩', '国产替代', '华为', '鸿蒙',
    '数据要素', '算力', '大模型', '量子计算', '卫星互联网',
    '固态电池', '钙钛矿', '人形机器人', 'Sora', '具身智能',
]



def _score_news_sentiment(news_info: Dict) -> float:
    """因子2：新闻情绪评分（0~100）
    根据正面/负面新闻条数比例打分。
    """
    pos = news_info.get('positive', 0)
    neg = news_info.get('negative', 0)
    total = pos + neg

    if total == 0:
        return 50.0  # 无新闻，中性

    ratio = pos / total  # 正面占比 0~1
    # 映射到 20~90 区间（全负面20，全正面90）
    score = 20 + ratio * 70

    # 额外：热点关键词加分（每个+2，最多+10）
    hot_count = len(news_info.get('hot_keywords', []))
    score += min(hot_count * 2, 10)

    return min(score, 100.0)


def _score_concept_heat(concepts: List[str]) -> float:
    """因子4：板块热度评分（0~100）
    概念数量 + 是否含热门概念。
    """
    if not concepts:
        return 30.0

    score = 30.0

    # (a) 概念数量：每个+2，最多+20
    score += min(len(concepts) * 2, 20)

    # (b) 热门概念命中：每个+8，最多+40
    hot_hits = 0
    for concept in concepts:
        for hot in HOT_CONCEPTS:
            if hot in concept or concept in hot:
                hot_hits += 1
                break
    score += min(hot_hits * 8, 40)

    # (c) 概念数量>=10说明题材丰富，额外+10
    if len(concepts) >= 10:
        score += 10

    return min(score, 100.0)


def _score_news_attention(news_list: List[Dict]) -> float:
    """新闻关注度评分（0~100）
    
    核心逻辑：不只看条数，而是考虑
    1. 媒体权威性 - 权威媒体权重更高
    2. 时效性 - 24h 内新闻权重 > 3 天内 > 7 天内
    3. 去重 - 同一新闻多渠道转载只算一次
    
    加权得分 = Σ(单条新闻权重 × 基础分)
    """
    if not news_list:
        return 20.0
    
    # 权威媒体列表（财联社、证券时报等）
    AUTH_MEDIA = [
        '财联社', '证券时报', '上海证券报', '中国证券报',
        '证券日报', '央视财经', '第一财经', '界面新闻',
        '澎湃新闻', '每经', '经济观察', ' Wind', '同花顺',
        '东方财富', '新浪财经', '腾讯证券', '网易财经',
    ]
    
    # 自媒体/不确定来源
    AUTO_MEDIA = ['搜狐', '今日头条', '百度', '微信', '微博', '自媒体']
    
    now = time.time()
    weighted_count = 0.0
    seen_titles = set()  # 去重
    
    for news in news_list:
        title = news.get('title', '')
        source = news.get('source', '')
        date_str = news.get('date', '')
        
        # (1) 标题去重 - 相同标题只算一次
        title_hash = title[:30]  # 取前 30 字作为标识
        if title_hash in seen_titles:
            continue
        seen_titles.add(title_hash)
        
        # (2) 媒体权威性权重
        if any(m in source for m in AUTH_MEDIA):
            media_weight = 1.0  # 权威媒体
        elif any(m in source for m in AUTO_MEDIA):
            media_weight = 0.3  # 自媒体/转载
        else:
            media_weight = 0.6  # 未知来源
        
        # (3) 时效性权重
        try:
            # 解析日期格式 "2024-01-15 10:30" 或 "2024-01-15"
            if ' ' in date_str:
                dt = time.strptime(date_str, '%Y-%m-%d %H:%M')
            else:
                dt = time.strptime(date_str, '%Y-%m-%d')
            hours_ago = (now - time.mktime(dt)) / 3600
        except:
            hours_ago = 48  # 无法解析按 48 小时前算
        
        if hours_ago <= 24:
            time_weight = 1.0  # 24 小时内
        elif hours_ago <= 72:
            time_weight = 0.6  # 3 天内
        elif hours_ago <= 168:
            time_weight = 0.3  # 7 天内
        else:
            time_weight = 0.1  # 超过 7 天
        
        # 单条新闻贡献 = 基础分 × 媒体权重 × 时效权重
        weighted_count += media_weight * time_weight
    
    # 映射到分数区间
    # 加权后 0 条=20 分，10+=90 分
    if weighted_count <= 0:
        return 20.0
    elif weighted_count <= 1:
        return 30.0
    elif weighted_count <= 3:
        return 50.0
    elif weighted_count <= 5:
        return 65.0
    elif weighted_count <= 8:
        return 80.0
    else:
        return min(90.0, 80 + weighted_count * 2)


def _score_industry_trend(industry: str) -> float:
    """行业景气度评分（0~100）
    通过所属行业指数近期走势判断板块景气度。
    行业整体上涨时个股跟涨概率更大（板块效应）。
    """
    try:
        index_code, _ = data_source.get_industry_index(industry)
        if not index_code:
            return 50.0  # 无对应行业指数，中性

        klines = data_source.fetch_index_kline(index_code, 20)
        if not klines or len(klines) < 10:
            return 50.0

        score = 50.0

        # (a) 近5日涨幅
        closes = [k['close'] for k in klines]
        chg_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
        # -3%~+5% 映射到 -15~+25
        score += max(-15, min(25, chg_5 * 5))

        # (b) 近10日涨幅趋势（正=上行趋势）
        if len(closes) >= 10:
            chg_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
            score += max(-10, min(15, chg_10 * 3))

        # (c) 近5日是否连续上涨（3天以上收阳）
        if len(klines) >= 5:
            up_days = sum(1 for k in klines[-5:] if k['close'] > k['open'])
            if up_days >= 4:
                score += 10
            elif up_days >= 3:
                score += 5
            elif up_days <= 1:
                score -= 5

        return max(0, min(100, score))

    except Exception as e:
        logger.debug(f"行业景气度评分失败 {industry}: {e}")
        return 50.0


def _score_market_env() -> float:
    """大盘环境评分（0~100）
    上证指数近期走势，反映系统性风险。
    牛市环境中个股上涨概率普遍更高，熊市中打折。
    """
    try:
        klines = data_source.fetch_index_kline('000001', 20)
        if not klines or len(klines) < 10:
            return 50.0

        score = 50.0
        closes = [k['close'] for k in klines]

        # (a) 近5日涨幅
        if len(closes) >= 6:
            chg_5 = (closes[-1] - closes[-6]) / closes[-6] * 100
            score += max(-15, min(20, chg_5 * 5))

        # (b) 近10日涨幅
        if len(closes) >= 11:
            chg_10 = (closes[-1] - closes[-11]) / closes[-11] * 100
            score += max(-10, min(15, chg_10 * 3))

        # (c) 近5日阳线占比
        if len(klines) >= 5:
            up_days = sum(1 for k in klines[-5:] if k['close'] > k['open'])
            if up_days >= 4:
                score += 10
            elif up_days >= 3:
                score += 5
            elif up_days <= 1:
                score -= 10

        return max(0, min(100, score))

    except Exception as e:
        logger.debug(f"大盘环境评分失败: {e}")
        return 50.0



def _score_zhuli_intent(code: str, klines, news_list, concepts, industry, quote) -> float:
    """因子 6：主力意图评分（0~100）- 主力思维模拟分析"""
    if not klines or len(klines) < 30:
        return 50.0
    closes = [float(k['close']) for k in klines[-30:]]
    volumes = [float(k['volume']) for k in klines[-30:]]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else 0
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0
    avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1
    vol_ratio = volumes[-1] / avg_vol_5 if avg_vol_5 > 0 else 1
    chg_10 = (closes[-1] - closes[-11]) / closes[-11] * 100 if len(closes) >= 11 else 0
    min_p, max_p = min(closes), max(closes)
    price_pos = (closes[-1] - min_p) / (max_p - min_p) * 100 if max_p > min_p else 50
    pos_news = sum(1 for n in news_list if any(kw in n.get('title', '') for kw in ['增长', '突破', '利好', '中标', '签约', '新高']))
    neg_news = sum(1 for n in news_list if any(kw in n.get('title', '') for kw in ['下跌', '利空', '减持', '违规', '亏损']))
    score = 50
    if price_pos < 20: score += 25
    elif price_pos < 40: score += 10
    elif price_pos > 80: score -= 35
    elif price_pos > 70: score -= 15
    if vol_ratio > 3 and price_pos > 70: score -= 25
    elif vol_ratio > 2.5: score -= 15
    elif vol_ratio > 1.5: score += 15 if price_pos < 50 else 5
    elif vol_ratio < 0.5: score -= 10
    if chg_10 > 30: score -= 25
    elif chg_10 > 20: score -= 15
    elif chg_10 > 10: score -= 5
    elif chg_10 < -20: score += 10
    elif chg_10 < -5: score += 5
    if pos_news > neg_news + 3: score += 15
    elif neg_news > pos_news + 1: score -= 10
    hot = ['AI', '芯片', '机器人', '新能源', '华为', '半导体', '低空经济', '储能']
    hot_hits = [c for c in concepts if any(h in c for h in hot)]
    if len(hot_hits) >= 2: score += 15
    elif len(concepts) >= 6: score += 5
    return float(max(0, min(100, score)))


def calc_rise_probability(code: str, signal_type: str, news_info: Dict,
                          concepts: List[str], quote: Dict,
                          news_list: List[Dict] = None,
                          industry: str = '',
                          klines = None) -> Dict:
    """
    多因子打分法计算上涨概率（5因子，不含技术面）

    参数:
      code: 股票代码
      signal_type: 信号类型（保留参数，不参与评分）
      news_info: 新闻情绪分析结果
      concepts: 概念板块列表
      quote: 实时行情
      news_list: 原始新闻列表（用于关注度评分）
      industry: 所属行业（用于行业景气度评分）

    返回:
      {
        'probability': 72.5,           # 综合上涨概率 (0~100)
        'level': '较高',               # 概率等级
        'factors': {                   # 各因子得分明细
          'news_sentiment': (70, 0.25),
          'news_attention': (65, 0.15),
          'concept_heat': (60, 0.20),
          'industry_trend': (72, 0.20),
          'market_env': (68, 0.20),
        }
      }
    """
    if news_list is None:
        news_list = []

    # 各因子权重
    weights = {
        'news_sentiment': 0.20,
        'news_attention': 0.12,
        'concept_heat': 0.16,
        'industry_trend': 0.16,
        'market_env': 0.16,
        'zhuli_intent': 0.20,
    }

    # 计算各因子得分
    scores = {
        'news_sentiment': _score_news_sentiment(news_info),
        'news_attention': _score_news_attention(news_list),
        'concept_heat': _score_concept_heat(concepts),
        'industry_trend': _score_industry_trend(industry),
        'market_env': _score_market_env(),
        'zhuli_intent': _score_zhuli_intent(code, klines, news_list, concepts, industry, quote),
    }

    # 加权求和
    probability = sum(scores[k] * weights[k] for k in weights)
    probability = round(max(0, min(100, probability)), 1)

    # 概率等级
    if probability >= 80:
        level = '很高'
    elif probability >= 65:
        level = '较高'
    elif probability >= 50:
        level = '中等'
    elif probability >= 35:
        level = '较低'
    else:
        level = '低'

    factors = {k: (round(scores[k], 1), weights[k]) for k in weights}

    return {
        'probability': probability,
        'level': level,
        'factors': factors,
    }


# ==================== 3. 综合分析入口 ====================

def analyze_stock(code: str, name: str = '', signal_type: str = '') -> Dict:
    """
    对一只股票做基本面分析 + 上涨概率：
      - 实时行情
      - 所属行业
      - 所属概念板块
      - 个股新闻 + 情绪
      - 上涨概率（多因子打分）
    """
    # 1. 基本信息：行业
    stock_info = data_source.fetch_stock_industry(code)
    if not name:
        name = stock_info.get('name', code)
    industry = stock_info.get('industry', '')

    # 2. 实时行情
    quote = data_source.fetch_realtime_quote(code)

    # 3. 概念板块
    concepts = data_source.fetch_stock_concepts(code)

    # 4. 个股新闻（现在看质量不是数量，10 条足够）
    news_list = data_source.fetch_stock_news(code, 10)
    # 4. 个股新闻（增加数量以便更好评估关注度）
    news_list = data_source.fetch_stock_news(code, 30)
    news_info = analyze_news_sentiment(news_list)

    # 5. K 线数据（主力分析需要）
    klines = data_source.fetch_kline(code, '240min', 60)
    
    # 6. 上涨概率（多因子打分，含主力意图）
    rise_prob = calc_rise_probability(
        code, signal_type, news_info, concepts, quote,
        news_list=news_list, industry=industry, klines=klines,
    )

    return {
        'code': code,
        'name': name,
        'industry': industry,
        'concepts': concepts,
        'quote': quote,
        'news': news_list,
        'news_info': news_info,
        'rise_probability': rise_prob,
    }


# ==================== 3. 格式化输出 ====================


def _get_probability_indicator(probability: float) -> str:
    """根据概率返回带 emoji 的标识"""
    if probability >= 80:
        return f"🔴 {probability}% (很高)"  # 红色圆
    elif probability >= 65:
        return f"🟠 {probability}% (较高)"  # 橙色圆
    elif probability >= 50:
        return f"🟡 {probability}% (中等)"  # 黄色圆
    elif probability >= 35:
        return f"🔵 {probability}% (较低)"  # 蓝色圆
    else:
        return f"🟢 {probability}% (低)"  # 绿色圆

def format_analysis_report(result: Dict) -> str:
    code = result['code']
    name = result['name']
    industry = result.get('industry', '')
    concepts = result.get('concepts', [])
    quote = result.get('quote', {})
    news_list = result.get('news', [])
    news_info = result.get('news_info', {})

    lines = []
    lines.append(f"")
    lines.append(f"  {'=' * 56}")
    lines.append(f"  {code} {name}")
    lines.append(f"  {'=' * 56}")

    # 实时行情
    if quote:
        price = quote.get('price', 0)
        chg = quote.get('change_pct', 0)
        chg_str = f"+{chg}%" if chg >= 0 else f"{chg}%"
        lines.append(f"  现价: {price}  涨跌: {chg_str}  "
                      f"今开: {quote.get('open',0)}  "
                      f"最高: {quote.get('high',0)}  最低: {quote.get('low',0)}")

    # 行业
    lines.append(f"")
    lines.append(f"  【行业】{industry if industry else '未知'}")

    # 概念
    if concepts:
        lines.append(f"  【概念】{', '.join(concepts[:15])}")
        if len(concepts) > 15:
            lines.append(f"          ...共{len(concepts)}个")
    else:
        lines.append(f"  【概念】无")

    # 新闻
    lines.append(f"")
    sentiment = news_info.get('sentiment', '中性')
    hot = news_info.get('hot_keywords', [])
    hot_str = f"  热点: {', '.join(hot)}" if hot else ""
    lines.append(f"  【新闻】情绪{sentiment}  "
                  f"(正面{news_info.get('positive',0)}条 / 负面{news_info.get('negative',0)}条)"
                  f"{hot_str}")
    for i, n in enumerate(news_list[:5]):
        title = n.get('title', '')
        if len(title) > 50:
            title = title[:50] + '...'
        src = n.get('source', '')
        date = n.get('date', '')
        prefix = f"[{date}]" if date else f"[{src}]"
        lines.append(f"    {i+1}. {prefix} {title}")

    # 上涨概率
    rise_prob = result.get('rise_probability', {})
    if rise_prob:
        prob = rise_prob.get('probability', 0)
        level = rise_prob.get('level', '未知')
        factors = rise_prob.get('factors', {})

        lines.append(f"")
        lines.append(f"")
        prob_text = _get_probability_indicator(prob)
        lines.append(f"  【上涨概率】 {prob_text}")
        lines.append(f"  {'-' * 50}")

        factor_names = {
            'news_sentiment': '新闻情绪',
            'news_attention': '新闻关注',
            'concept_heat':   '板块热度',
            'industry_trend': '行业景气',
            'market_env':     '大盘环境',
            'zhuli_intent':   '主力意图',
        }
        for key in ('news_sentiment', 'news_attention', 'concept_heat',
                     'industry_trend', 'market_env', 'zhuli_intent'):
            if key in factors:
                score, weight = factors[key]
                bar_len = int(score / 5)  # 0~20格
                bar = '█' * bar_len + '░' * (20 - bar_len)
                lines.append(f"    {factor_names[key]:<8} {bar} {score:>5.1f}  (权重{int(weight*100)}%)")

    lines.append(f"  {'=' * 56}")
    lines.append(f"")

    return "\n".join(lines)


# ==================== 4. 批量分析 ====================

def analyze_stocks_batch(stocks: List[Tuple[str, str]], signal_types: Dict[str, str] = None) -> List[Dict]:
    """批量分析
    stocks: [(code, name), ...]
    signal_types: {code: signal_type} 可选
    """
    if not stocks:
        return []

    if signal_types is None:
        signal_types = {}

    print(f"\n{'=' * 60}")
    print(f"  基本面分析（行业 / 概念 / 新闻 / 上涨概率）")
    print(f"  待分析: {len(stocks)} 只")
    print(f"{'=' * 60}")

    results = []
    for i, (code, name) in enumerate(stocks):
        print(f"\n  [{i+1}/{len(stocks)}] 正在分析 {code} {name} ...")
        try:
            sig = signal_types.get(code, '')
            r = analyze_stock(code, name, signal_type=sig)
            r['signal_type'] = sig
            results.append(r)
            print(format_analysis_report(r))
        except Exception as e:
            print(f"    分析失败: {e}")
            results.append({
                'code': code, 'name': name,
                'signal_type': signal_types.get(code, ''),
            })

        if i < len(stocks) - 1:
            time.sleep(0.3)

    return results


# ==================== 独立运行 ====================

def main():
    print()
    print("=" * 50)
    print("  个股基本面分析工具")
    print("  （行业 / 概念 / 新闻 / 上涨概率）")
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
