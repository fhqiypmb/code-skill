"""
个股基本面分析模块
用于选股后的辅助判断，分析：
  1. 所属行业板块
  2. 所属概念板块
  3. 个股近期新闻 + 情绪分析
  4. 实时行情

不含技术面分析（技术面由 严格选股_多周期.py 负责）

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


# ==================== 2. 综合分析入口 ====================

def analyze_stock(code: str, name: str = '') -> Dict:
    """
    对一只股票做基本面分析：
      - 实时行情
      - 所属行业
      - 所属概念板块
      - 个股新闻 + 情绪
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

    # 4. 个股新闻
    news_list = data_source.fetch_stock_news(code, 10)
    news_info = analyze_news_sentiment(news_list)

    return {
        'code': code,
        'name': name,
        'industry': industry,
        'concepts': concepts,
        'quote': quote,
        'news': news_list,
        'news_info': news_info,
    }


# ==================== 3. 格式化输出 ====================

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
    print(f"  基本面分析（行业 / 概念 / 新闻）")
    print(f"  待分析: {len(stocks)} 只")
    print(f"{'=' * 60}")

    results = []
    for i, (code, name) in enumerate(stocks):
        print(f"\n  [{i+1}/{len(stocks)}] 正在分析 {code} {name} ...")
        try:
            r = analyze_stock(code, name)
            r['signal_type'] = signal_types.get(code, '')
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
    print("  （行业 / 概念 / 新闻）")
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
