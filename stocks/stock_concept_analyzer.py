"""
股票概念/板块分析模块（52etf.site风格）
结合严格选股结果，获取股票所属的概念和板块，分析板块强度和概念热度

数据源：
  1. 东方财富API - 获取股票基本信息、所属概念、所属板块
  2. 新浪财经 - 获取板块/概念行情数据和K线
  3. 腾讯财经 - 备用数据源

核心逻辑：
  1. 通过stock code获取所属概念（通常5-10个）
  2. 通过stock code获取所属板块（行业分类）
  3. 获取这些概念/板块的实时行情和涨跌幅
  4. 计算板块强度评分（基于涨幅、流通市值、个股数量等）
  5. 展示该股在板块中的位置和板块前景
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
    """通用HTTP请求"""
    h = dict(_HEADERS)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        logger.warning(f"请求失败 {url}: {e}")
        return b''


def _fetch_json(url: str, referer: str = None, timeout: int = 15) -> dict:
    raw = _fetch_raw(url, referer, timeout)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"JSON解析失败: {e}")
        return {}


# ==================== 1. 东方财富 API ====================

def fetch_eastmoney_concept_and_industry(code: str) -> Dict:
    """
    通过东方财富获取股票所属的概念和板块
    返回: {
        'concepts': [{'name': '概念名', 'code': '代码', 'change': 涨幅}, ...],
        'industry': '行业名',
        'industry_code': '行业代码',
    }
    """
    result = {
        'concepts': [],
        'industry': '',
        'industry_code': '',
        'stock_name': '',
    }

    market = 1 if code.startswith('6') else 0

    # ---- 获取概念 ----
    try:
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={market}.{code}"
            f"&fields=f57,f58,f59,f60,f61,f62,f63,f64,f65,f66,f67,f68,f69,f84,f85,f86"
            f"&_={int(time.time()*1000)}"
        )
        data = _fetch_json(url, "https://quote.eastmoney.com")

        if data and data.get('data'):
            stock_data = data['data']
            result['stock_name'] = stock_data.get('f58', code)  # 股票名称

            # f73: 概念代码列表(逗号分隔)
            # f75: 概念名称列表(逗号分隔)
            concept_codes = stock_data.get('f73', '').split(',') if stock_data.get('f73') else []
            concept_names = stock_data.get('f75', '').split(',') if stock_data.get('f75') else []

            # 获取行业信息（f70=行业名，f74=行业代码）
            result['industry'] = stock_data.get('f70', '')
            result['industry_code'] = stock_data.get('f74', '')

            # 组合概念信息
            for i, name in enumerate(concept_names):
                if i < len(concept_codes) and name.strip():
                    result['concepts'].append({
                        'name': name.strip(),
                        'code': concept_codes[i].strip() if i < len(concept_codes) else '',
                    })
    except Exception as e:
        logger.warning(f"东方财富概念获取失败 {code}: {e}")

    return result


# ==================== 2. 获取概念/板块实时行情 ====================

def fetch_concept_realtime(concept_code: str, concept_name: str = '') -> Dict:
    """
    获取概念的实时行情（涨跌幅、流通市值等）
    concept_code: 东方财富概念代码或板块代码
    """
    result = {
        'name': concept_name or concept_code,
        'code': concept_code,
        'change': 0.0,          # 涨幅 %
        'change_amount': 0.0,   # 涨跌 点
        'volume': 0,            # 流通市值
        'stock_count': 0,       # 成分股数量
        'avg_change': 0.0,      # 平均涨幅
    }

    try:
        # 东财概念行情接口
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid=90.{concept_code}"
            f"&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62"
            f"&_={int(time.time()*1000)}"
        )
        data = _fetch_json(url, "https://quote.eastmoney.com")

        if data and data.get('data'):
            d = data['data']
            result['name'] = d.get('f14', concept_name)  # 名称
            result['change'] = d.get('f3', 0.0) / 100 if d.get('f3') else 0.0  # 涨幅（百分比）
            result['change_amount'] = d.get('f4', 0.0)  # 涨跌幅度
            result['stock_count'] = d.get('f20', 0)  # 成分股数量

    except Exception as e:
        logger.warning(f"概念行情获取失败 {concept_code}: {e}")

    return result


def fetch_industry_realtime(code: str, name: str = '') -> Dict:
    """获取板块/行业的实时行情"""
    result = {
        'name': name or code,
        'code': code,
        'change': 0.0,
        'change_amount': 0.0,
        'stock_count': 0,
    }

    try:
        # 市场编码：1=沪深300行业，90=概念，其他=指数
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid=1.{code}"
            f"&fields=f2,f3,f4,f14,f20"
            f"&_={int(time.time()*1000)}"
        )
        data = _fetch_json(url, "https://quote.eastmoney.com")

        if data and data.get('data'):
            d = data['data']
            result['change'] = d.get('f3', 0.0) / 100 if d.get('f3') else 0.0
            result['stock_count'] = d.get('f20', 0)

    except Exception as e:
        logger.warning(f"行业行情获取失败: {e}")

    return result


# ==================== 3. 板块强度评分 ====================

def calculate_concept_strength(concept_info: Dict) -> float:
    """
    计算概念的强度评分 (0-100)
    基于：涨幅、成分股数量、成交量等
    """
    change = concept_info.get('change', 0.0)
    stock_count = concept_info.get('stock_count', 0)

    # 涨幅占60%
    change_score = max(0, min(100, 50 + change * 10))  # -5% -> 0, +5% -> 100

    # 成分股数量占40% (30只满分)
    count_score = min(100, (stock_count / 30) * 100) if stock_count > 0 else 0

    return change_score * 0.6 + count_score * 0.4


# ==================== 4. 综合分析 ====================

def analyze_stock_concept(code: str, name: str = '', signal_details: Dict = None) -> Dict:
    """
    分析单只股票的概念和板块信息

    Args:
        code: 股票代码
        name: 股票名称
        signal_details: 选股信号详情（来自严格选股_多周期.py）

    Returns:
        {
            'code': '股票代码',
            'name': '股票名称',
            'signal_info': {...},  # 选股信号信息
            'industry': '所属行业',
            'industry_info': {...},  # 行业实时数据
            'concepts': [
                {'name': '概念名', 'strength': 评分, 'change': 涨幅, ...},
                ...
            ],
            'hot_concepts': [...],  # 最热概念（涨幅最高的前3个）
            'concept_strength_avg': 平均强度,
            'recommendation': '建议',
        }
    """
    result = {
        'code': code,
        'name': name,
        'signal_info': signal_details or {},
        'industry': '',
        'industry_info': {},
        'concepts': [],
        'hot_concepts': [],
        'concept_strength_avg': 0.0,
        'recommendation': '',
    }

    # 1. 获取概念和行业
    concept_data = fetch_eastmoney_concept_and_industry(code)
    if concept_data['stock_name']:
        result['name'] = concept_data['stock_name']

    result['industry'] = concept_data['industry']

    # 2. 获取行业实时数据
    if concept_data['industry']:
        result['industry_info'] = fetch_industry_realtime(
            concept_data['industry_code'],
            concept_data['industry']
        )

    # 3. 获取各概念的实时行情和强度
    for concept in concept_data['concepts']:
        concept_info = fetch_concept_realtime(concept['code'], concept['name'])
        strength = calculate_concept_strength(concept_info)
        concept_info['strength'] = strength
        result['concepts'].append(concept_info)

    # 4. 排序找出热概念
    if result['concepts']:
        sorted_concepts = sorted(result['concepts'], key=lambda x: x['change'], reverse=True)
        result['hot_concepts'] = sorted_concepts[:3]
        result['concept_strength_avg'] = sum(c['strength'] for c in result['concepts']) / len(result['concepts'])

    # 5. 生成建议
    result['recommendation'] = _generate_recommendation(result, signal_details)

    return result


def _generate_recommendation(analysis: Dict, signal_details: Dict = None) -> str:
    """基于概念板块强度和选股信号生成建议"""
    parts = []

    industry_change = analysis['industry_info'].get('change', 0.0)
    avg_strength = analysis['concept_strength_avg']
    hot_count = len([c for c in analysis['concepts'] if c['change'] > 0])
    total_count = len(analysis['concepts'])

    # 行业趋势
    if industry_change > 2:
        parts.append("行业强势")
    elif industry_change > 0:
        parts.append("行业上升")
    elif industry_change > -2:
        parts.append("行业弱势")
    else:
        parts.append("行业下跌")

    # 概念热度
    positive_ratio = hot_count / total_count if total_count > 0 else 0
    if positive_ratio > 0.7:
        parts.append("概念热")
    elif positive_ratio > 0.5:
        parts.append("概念平")
    else:
        parts.append("概念冷")

    # 综合强度
    if avg_strength > 60:
        parts.append("强度高")
    elif avg_strength > 40:
        parts.append("强度中")
    else:
        parts.append("强度低")

    # 结合选股信号
    if signal_details:
        signal_type = signal_details.get('signal_type', '')
        if signal_type in ('严格', '筑底', '突破'):
            parts.append(f"有{signal_type}买入信号")

    return " | ".join(parts) if parts else "待观察"


# ==================== 5. 格式化输出 ====================

def format_analysis_report(result: Dict) -> str:
    """格式化分析报告"""
    lines = []

    lines.append(f"")
    lines.append(f"  股票: {result['code']} {result['name']}")

    # 行业
    if result['industry']:
        ind_info = result['industry_info']
        ind_chg = ind_info.get('change', 0)
        lines.append(f"  行业: {result['industry']} ({ind_chg:+.2f}%)")

    # 热概念
    if result['hot_concepts']:
        concepts_str = " | ".join([
            f"{c['name']}({c['change']:+.2f}%)"
            for c in result['hot_concepts']
        ])
        lines.append(f"  热概念: {concepts_str}")

    # 建议
    lines.append(f"  建议: {result['recommendation']}")
    lines.append(f"")

    return "\n".join(lines)


def analyze_stocks_batch(stocks: List[Tuple[str, str]], signal_details_map: Dict[str, Dict] = None) -> List[Dict]:
    """
    批量分析多只股票的概念和板块

    Args:
        stocks: [(code, name), ...]
        signal_details_map: {code: signal_details, ...} 可选
    """
    if not stocks:
        return []

    if signal_details_map is None:
        signal_details_map = {}

    print(f"\n{'=' * 60}")
    print(f"  概念/板块强度分析")
    print(f"  待分析: {len(stocks)} 只")
    print(f"{'=' * 60}\n")

    results = []
    for i, (code, name) in enumerate(stocks):
        print(f"  [{i+1}/{len(stocks)}] 分析 {code} {name} ...", end='', flush=True)
        try:
            signal_detail = signal_details_map.get(code)
            r = analyze_stock_concept(code, name, signal_detail)
            results.append(r)
            print(format_analysis_report(r))
        except Exception as e:
            print(f"\n    失败: {e}")
            results.append({
                'code': code,
                'name': name,
                'concepts': [],
                'hot_concepts': [],
                'recommendation': '获取失败'
            })

        if i < len(stocks) - 1:
            time.sleep(0.3)  # 降低请求频率

    # 汇总排名
    if len(results) > 1:
        ranked = sorted(results, key=lambda x: x.get('concept_strength_avg', 0), reverse=True)
        print(f"\n  {'=' * 40}")
        print(f"  排名 (按概念强度):")
        for i, r in enumerate(ranked):
            strength = r.get('concept_strength_avg', 0)
            print(f"  {i+1}. {r['code']} {r.get('name', ''):<8} 强度 {strength:.1f}  {r.get('recommendation', '')}")
        print()

    return results


# ==================== 独立运行 ====================

def main():
    print()
    print("=" * 50)
    print("  股票概念/板块分析工具")
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
