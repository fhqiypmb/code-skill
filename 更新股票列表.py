"""
A股股票列表更新工具
支持数据源：东方财富（akshare）、新浪财经、同花顺
生成/更新 stock_list.md 文件

用法：
  python 更新股票列表.py

数据清洗规则：
  - 只保留沪深主板(60/00)、中小板(002)、创业板(300/301)
  - 过滤科创板(688/689)、北交所(8/4开头)
  - 过滤ST/*ST/S*ST/PT股票
  - 过滤退市股（名称含"退"）
  - 过滤已退市/被吸收合并的历史代码
"""

import os
import json
import re
import sys
import ssl
import time
from datetime import datetime
from typing import Dict, List, Set
import urllib.request
import urllib.parse

# 禁用代理（避免代理软件干扰国内API请求）
for _key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if _key in os.environ:
        del os.environ[_key]

# 忽略SSL证书验证
ssl._create_default_https_context = ssl._create_unverified_context

# 创建无代理的opener
_proxy_handler = urllib.request.ProxyHandler({})
_opener = urllib.request.build_opener(_proxy_handler)

# ===== 已退市/被吸收合并的历史代码（新浪等数据源可能仍返回这些） =====
DEAD_CODES: Set[str] = {
    '000003',  # PT金田A
    '000013',  # *ST石化A -> 退市
    '000015',  # PT中浩A
    '000022',  # 深赤湾Ａ -> 被招商港口吸收合并
    '000024',  # 招商地产 -> 更名招商蛇口(001979)
    '000043',  # 中航善达 -> 被招商积余(001914)吸收合并
    '000406',  # 石油大明 -> 退市
    '000418',  # 小天鹅Ａ -> 被美的集团(000333)吸收合并
    '000508',  # 琼民源A -> 退市
    '000515',  # 攀渝钛业 -> 更名后退市
    '000522',  # 白云山A -> 更名迁移至600332
    '000527',  # 美的电器 -> 被美的集团(000333)吸收合并
    '000549',  # S湘火炬 -> 未完成股改
    '000556',  # PT南洋
    '000562',  # 宏源证券 -> 被申万宏源(000166)吸收合并
    '000569',  # 长城股份 -> 退市
    '000578',  # 盐湖集团 -> 更名盐湖股份(000792)
    '000583',  # S*ST托普
    '000588',  # PT粤金曼
    '000618',  # 吉林化工 -> 退市
    '000699',  # S*ST佳纸
    '000748',  # 长城信息 -> 被中国长城(000066)吸收合并
    '000763',  # 锦州石化 -> 退市
    '000866',  # 扬子石化 -> 退市（被中国石化吸收）
    '000916',  # 华北高速 -> 更名招商公路(001965)
    '002013',  # 中航机电 -> 被中航沈飞等整合
    '002022',  # 科华生物 -> 退市
    '002710',  # 慈铭体检 -> 被美年健康(002044)吸收合并
    '300186',  # 大华农 -> 被温氏股份(300498)吸收合并
    '600625',  # PT水仙
    '600092',  # S*ST圣纸
    '600181',  # S*ST云大
    '600286',  # S*ST国瓷
    '600762',  # S*ST金荔
    '600772',  # S*ST龙昌
}


def is_valid_stock(code: str, name: str) -> bool:
    """
    判断股票是否有效（应被纳入列表）

    过滤规则：
    1. 代码必须6位数字
    2. 只保留 60/00/001/002/003/300/301 开头
    3. 排除科创板 688/689
    4. 排除北交所 8/4 开头
    5. 排除 ST/*ST/S*ST/PT
    6. 排除退市股（名称含"退"）
    7. 排除已知的历史无效代码
    8. 排除空名称或无效名称
    """
    # 基本格式检查
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if not name or name.strip() == '':
        return False

    # 排除已知无效代码
    if code in DEAD_CODES:
        return False

    # 只保留主板/中小板/创业板
    valid_prefixes = ('60', '00', '001', '002', '003', '300', '301')
    if not code.startswith(valid_prefixes):
        return False

    # 排除科创板
    if code.startswith(('688', '689')):
        return False

    # 排除北交所
    if code.startswith(('8', '4')):
        return False

    # 排除名称中的问题股
    name_upper = name.upper().strip()

    # PT股
    if 'PT' in name_upper:
        return False

    # ST股（包括ST、*ST、S*ST）
    if name_upper.startswith(('ST', '*ST', 'S*ST')):
        return False

    # S前缀未股改股票（如S湘火炬）
    if re.match(r'^S[^a-zA-Z]', name):
        return False

    # 退市股（名称中含"退"）
    if '退' in name:
        return False

    # 无效数据（新浪返回的异常数据）
    if '="' in name:
        return False

    return True


class StockListUpdater:
    """股票列表更新器 - 多数据源支持"""

    def __init__(self):
        self.md_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_list.md')

    def load_existing(self) -> Dict[str, str]:
        """加载现有的股票列表（解析MD表格格式）"""
        stocks = {}
        if not os.path.exists(self.md_file):
            return stocks

        try:
            with open(self.md_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('|---'):
                        continue
                    match = re.match(r'\|\s*(\d{6})\s*\|\s*([^|]+)\s*\|', line)
                    if match:
                        code = match.group(1)
                        name = match.group(2).strip()
                        stocks[code] = name
        except Exception as e:
            print(f"读取现有文件失败: {e}")

        return stocks

    def save_to_md(self, stocks: Dict[str, str]):
        """保存到MD文件"""
        try:
            with open(self.md_file, 'w', encoding='utf-8') as f:
                f.write("# A股股票列表\n\n")
                f.write(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"共 {len(stocks)} 只股票\n\n")
                f.write("| 股票代码 | 股票名称 |\n")
                f.write("|---------|---------|\n")

                for code in sorted(stocks.keys()):
                    name = stocks[code]
                    f.write(f"| {code} | {name} |\n")

            print(f"\n成功保存到: {self.md_file}")
            print(f"共 {len(stocks)} 只股票")
        except Exception as e:
            print(f"保存文件失败: {e}")

    # ==================== 数据源1：东方财富（akshare） ====================

    def fetch_from_eastmoney(self) -> Dict[str, str]:
        """从东方财富获取股票列表（需要安装akshare）"""
        print("\n[数据源1] 东方财富...")
        stocks = {}

        try:
            import akshare as ak

            print("  获取A股实时行情...")
            try:
                df = ak.stock_zh_a_spot_em()
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    name = str(row['名称']).strip()
                    if is_valid_stock(code, name):
                        stocks[code] = name
                print(f"  获取到 {len(stocks)} 只有效股票")
            except Exception as e:
                print(f"  获取失败: {e}")

        except ImportError:
            print("  akshare未安装，跳过")
        except Exception as e:
            print(f"  东方财富获取失败: {e}")

        return stocks

    # ==================== 数据源2：新浪财经 ====================

    def fetch_from_sina(self) -> Dict[str, str]:
        """从新浪财经获取股票列表"""
        print("\n[数据源2] 新浪财经...")
        stocks = {}

        ranges = [
            ("沪市主板 600xxx", [(f"sh{i}", i) for i in range(600000, 606000)]),
            ("沪市主板 601xxx", [(f"sh{i}", i) for i in range(601000, 602000)]),
            ("沪市主板 603xxx", [(f"sh{i}", i) for i in range(603000, 604000)]),
            ("沪市主板 605xxx", [(f"sh{i}", i) for i in range(605000, 606000)]),
            ("深市主板 000xxx", [(f"sz{i:06d}", i) for i in range(1, 1000)]),
            ("深市主板 001xxx", [(f"sz{i:06d}", i) for i in range(1001, 2000)]),
            ("中小板 002xxx", [(f"sz{i:06d}", i) for i in range(2001, 3000)]),
            ("深市 003xxx", [(f"sz{i:06d}", i) for i in range(3001, 4000)]),
            ("创业板 300xxx", [(f"sz{i}", i) for i in range(300001, 302000)]),
        ]

        for label, code_list in ranges:
            print(f"  获取{label}...")
            count = 0
            # 每100个一批
            for batch_start in range(0, len(code_list), 100):
                batch = [c[0] for c in code_list[batch_start:batch_start+100]]
                batch_stocks = self._fetch_sina_batch(batch)
                stocks.update(batch_stocks)
                count += len(batch_stocks)
            if count > 0:
                print(f"    -> {count} 只")

        print(f"  新浪合计: {len(stocks)} 只")
        return stocks

    def _fetch_sina_batch(self, codes: List[str]) -> Dict[str, str]:
        """批量从新浪获取股票信息"""
        stocks = {}
        if not codes:
            return stocks

        try:
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            with _opener.open(req, timeout=15) as r:
                text = r.read().decode("gbk", errors='ignore')

                for line in text.split('\n'):
                    if not line.strip():
                        continue

                    match = re.search(r'var hq_str_(\w+)="([^"]*)"', line)
                    if match:
                        full_code = match.group(1)
                        data = match.group(2)

                        if not data:
                            continue

                        parts = data.split(',')
                        if len(parts) < 2:
                            continue

                        name = parts[0].strip()
                        code = full_code[2:]  # 去掉 sh/sz 前缀

                        if is_valid_stock(code, name):
                            stocks[code] = name
        except Exception:
            pass

        return stocks

    # ==================== 数据源3：同花顺 ====================

    def fetch_from_ths(self) -> Dict[str, str]:
        """从同花顺行情中心获取股票列表"""
        print("\n[数据源3] 同花顺...")
        stocks = {}

        try:
            for board, label in [('hs', '沪深'), ('sz', '深市')]:
                print(f"  获取{label}...")
                board_stocks = self._fetch_ths_pages(board)
                stocks.update(board_stocks)
                print(f"    -> {len(board_stocks)} 只")
        except Exception as e:
            print(f"  同花顺获取失败: {e}")

        print(f"  同花顺合计: {len(stocks)} 只")
        return stocks

    def _fetch_ths_pages(self, board: str) -> Dict[str, str]:
        """从同花顺分页获取股票列表"""
        stocks = {}

        for page in range(1, 200):
            try:
                url = (f"http://q.10jqka.com.cn/index/index/board/{board}/"
                       f"field/zdf/order/desc/page/{page}/ajax/1/")

                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "http://q.10jqka.com.cn/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                })

                with _opener.open(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors='ignore')

                    # 解析HTML表格中的股票代码和名称
                    pattern = r'<td[^>]*>(\d{6})</td>.*?<a[^>]*>([^<]+)</a>'
                    matches = re.findall(pattern, html, re.DOTALL)

                    if not matches:
                        break

                    page_count = 0
                    for code, name in matches:
                        code = code.strip()
                        name = name.strip()
                        if is_valid_stock(code, name):
                            stocks[code] = name
                            page_count += 1

                    if page_count == 0 and not matches:
                        break

                    if page % 20 == 0:
                        print(f"    已获取 {len(stocks)} 只...")

            except Exception:
                break

        return stocks

    # ==================== 主流程 ====================

    def update(self):
        """更新股票列表（多数据源 + 数据清洗）"""
        print("=" * 60)
        print("  A股股票列表更新工具")
        print("  数据源：东方财富 / 新浪财经 / 同花顺")
        print("=" * 60)

        # 加载现有列表
        existing = self.load_existing()
        print(f"\n现有股票列表: {len(existing)} 只")

        all_stocks: Dict[str, str] = {}

        # 1. 尝试东方财富（数据最全最准）
        em_stocks = self.fetch_from_eastmoney()
        if em_stocks:
            all_stocks.update(em_stocks)

        # 2. 新浪财经（无需额外依赖，作为主要/补充数据源）
        if len(all_stocks) < 3000:
            sina_stocks = self.fetch_from_sina()
            for code, name in sina_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name

        # 3. 同花顺（补充）
        if len(all_stocks) < 3000:
            ths_stocks = self.fetch_from_ths()
            for code, name in ths_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name

        if not all_stocks:
            print("\n所有数据源都失败，无法更新股票列表")
            return

        # 二次清洗：对合并后的数据再做一遍过滤
        cleaned = {}
        for code, name in all_stocks.items():
            if is_valid_stock(code, name):
                cleaned[code] = name

        # 统计变化
        new_codes = set(cleaned.keys()) - set(existing.keys())
        removed_codes = set(existing.keys()) - set(cleaned.keys())

        print(f"\n{'=' * 60}")
        print(f"  更新统计:")
        print(f"  原有: {len(existing)} 只")
        print(f"  获取: {len(all_stocks)} 只")
        print(f"  清洗后: {len(cleaned)} 只")
        print(f"  新增: {len(new_codes)} 只")
        print(f"  移除: {len(removed_codes)} 只")
        print(f"{'=' * 60}")

        if new_codes:
            print(f"\n新增股票 (前10只):")
            for code in sorted(new_codes)[:10]:
                print(f"  {code} {cleaned[code]}")
            if len(new_codes) > 10:
                print(f"  ... 共 {len(new_codes)} 只")

        if removed_codes:
            print(f"\n移除股票 (前10只):")
            for code in sorted(removed_codes)[:10]:
                print(f"  {code} {existing.get(code, '')}")
            if len(removed_codes) > 10:
                print(f"  ... 共 {len(removed_codes)} 只")

        # 保存
        self.save_to_md(cleaned)


def main():
    updater = StockListUpdater()
    updater.update()


if __name__ == "__main__":
    main()
