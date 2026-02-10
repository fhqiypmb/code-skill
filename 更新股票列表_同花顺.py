"""
A股股票列表更新工具 - 从同花顺获取
生成/更新 stock_list.md 文件

用法：
  python 更新股票列表_同花顺.py
"""

import os
import json
import re
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Set
import urllib.request
import urllib.parse
import ssl

# 禁用代理
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if key in os.environ:
        del os.environ[key]

# 忽略SSL证书验证
ssl._create_default_https_context = ssl._create_unverified_context


class StockListUpdater:
    """股票列表更新器 - 同花顺数据源"""

    def __init__(self):
        self.md_file = os.path.join(os.path.dirname(__file__), 'stock_list.md')
        self.stocks: Dict[str, str] = {}

    def load_existing(self) -> Dict[str, str]:
        """加载现有的股票列表"""
        stocks = {}
        if not os.path.exists(self.md_file):
            return stocks

        try:
            with open(self.md_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('|---'):
                        continue
                    # 格式: | 000001 | 平安银行 |
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
                f.write(f"# A股股票列表\n\n")
                f.write(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"共 {len(stocks)} 只股票\n\n")
                f.write("| 股票代码 | 股票名称 |\n")
                f.write("|---------|---------|\n")

                # 按代码排序
                for code in sorted(stocks.keys()):
                    name = stocks[code]
                    f.write(f"| {code} | {name} |\n")

            print(f"\n成功保存到: {self.md_file}")
            print(f"共 {len(stocks)} 只股票")
        except Exception as e:
            print(f"保存文件失败: {e}")

    def fetch_from_ths(self) -> Dict[str, str]:
        """从同花顺获取股票列表"""
        print("\n从同花顺获取股票列表...")
        stocks = {}

        # 同花顺行情中心接口 - 获取所有A股
        # 尝试不同的接口
        urls = [
            # 同花顺行情中心 - 沪深A股
            "http://q.10jqka.com.cn/api.php?t=indexflash&",
            # 同花顺自选股接口
            "http://d.10jqka.com.cn/v6/line/hs_1a/01/last.js",
        ]

        # 尝试获取沪深A股列表
        try:
            # 使用同花顺的筛选接口
            print("  尝试获取沪深A股...")

            # 沪市A股
            sh_stocks = self._fetch_ths_market('shanghai')
            print(f"    沪市: {len(sh_stocks)} 只")
            stocks.update(sh_stocks)

            # 深市A股
            sz_stocks = self._fetch_ths_market('shenzhen')
            print(f"    深市: {len(sz_stocks)} 只")
            stocks.update(sz_stocks)

        except Exception as e:
            print(f"  同花顺获取失败: {e}")

        return stocks

    def _fetch_ths_market(self, market: str) -> Dict[str, str]:
        """从同花顺获取指定市场的股票"""
        stocks = {}

        # 同花顺行情接口
        # 沪市: http://q.10jqka.com.cn/index/index/board/hs/field/zdf/order/desc/page/1/ajax/1/
        # 深市: http://q.10jqka.com.cn/index/index/board/sz/field/zdf/order/desc/page/1/ajax/1/

        board_map = {
            'shanghai': 'hs',  # 沪深
            'shenzhen': 'sz',  # 深圳
        }

        board = board_map.get(market, 'hs')

        try:
            # 获取多页数据
            for page in range(1, 200):  # 最多200页
                url = f"http://q.10jqka.com.cn/index/index/board/{board}/field/zdf/order/desc/page/{page}/ajax/1/"

                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "http://q.10jqka.com.cn/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                })

                with urllib.request.urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors='ignore')

                    # 解析HTML中的股票数据
                    # 格式: <tr>...</tr> 中包含股票代码和名称
                    import re

                    # 查找股票代码和名称
                    # 格式: <td class="">000001</td> 或 <a href="...">平安银行</a>
                    pattern = r'<tr[^>]*>.*?<td[^>]*>(\d{6})</td>.*?<a[^>]*>([^<]+)</a>.*?</tr>'
                    matches = re.findall(pattern, html, re.DOTALL)

                    if not matches:
                        # 尝试其他格式
                        pattern2 = r'<td[^>]*>(\d{6})</td>.*?<td[^>]*>([^<]*?)</td>'
                        matches = re.findall(pattern2, html, re.DOTALL)

                    page_stocks = 0
                    for code, name in matches:
                        code = code.strip()
                        name = name.strip()

                        # 过滤科创板和北交所
                        if code.startswith(('688', '689', '8', '4')):
                            continue

                        # 过滤ST
                        if name.startswith(('ST', '*ST')):
                            continue

                        if code and name and len(code) == 6:
                            stocks[code] = name
                            page_stocks += 1

                    if page_stocks == 0:
                        break  # 没有数据了

                    if page % 10 == 0:
                        print(f"    已获取 {len(stocks)} 只...")

        except Exception as e:
            print(f"    获取失败: {e}")

        return stocks

    def fetch_from_hexin(self) -> Dict[str, str]:
        """从同花顺核心数据接口获取"""
        print("\n从同花顺核心数据获取...")
        stocks = {}

        try:
            # 同花顺核心数据接口 - 获取所有股票代码
            url = "http://d.10jqka.com.cn/v4/line/hs_1a/01/last.js"

            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "http://d.10jqka.com.cn/",
            })

            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", errors='ignore')

                # 解析返回的数据
                # 格式: quote_1a({"1A0001":"上证指数,...
                if 'quote_1a' in text:
                    # 提取JSON数据
                    match = re.search(r'quote_1a\((.*?)\);', text, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        for code, info in data.items():
                            if len(code) == 6 and code[0] in '036':
                                parts = info.split(',')
                                if len(parts) >= 1:
                                    name = parts[0]
                                    # 过滤
                                    if not code.startswith(('688', '689', '8', '4')):
                                        if not name.startswith(('ST', '*ST')):
                                            stocks[code] = name
        except Exception as e:
            print(f"  核心数据获取失败: {e}")

        return stocks

    def fetch_from_sina_simple(self) -> Dict[str, str]:
        """从新浪财经简化接口获取 - 作为备用"""
        print("\n从新浪财经获取...")
        stocks = {}

        # 使用新浪财经的列表接口
        markets = [
            ('sh_a', 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=1'),
            ('sz_a', 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz399001&scale=240&ma=no&datalen=1'),
        ]

        # 沪市主板
        print("  获取沪市主板...")
        for start in range(600000, 604000, 100):
            codes = [f"sh{i}" for i in range(start, min(start+100, 604000))]
            batch = self._fetch_sina_batch(codes)
            stocks.update(batch)

        # 深市主板 + 中小板
        print("  获取深市主板...")
        for start in range(1, 5000, 100):
            codes = [f"sz{i:06d}" for i in range(start, min(start+100, 5000))]
            batch = self._fetch_sina_batch(codes)
            stocks.update(batch)

        # 创业板
        print("  获取创业板...")
        for start in range(300001, 302000, 100):
            codes = [f"sz{i}" for i in range(start, min(start+100, 302000))]
            batch = self._fetch_sina_batch(codes)
            stocks.update(batch)

        return stocks

    def _fetch_sina_batch(self, codes: List[str]) -> Dict[str, str]:
        """批量获取新浪股票信息"""
        stocks = {}
        if not codes:
            return stocks

        try:
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("gbk", errors='ignore')

                for line in text.split('\n'):
                    if not line.strip():
                        continue

                    match = re.search(r'var hq_str_(\w+)="([^"]*)"', line)
                    if match:
                        full_code = match.group(1)
                        data = match.group(2)

                        if not data or data == '':
                            continue

                        parts = data.split(',')
                        if len(parts) >= 1:
                            name = parts[0].strip()
                            code = full_code[2:]

                            # 过滤科创板、北交所、ST
                            if code.startswith(('688', '689', '8', '4')):
                                continue
                            if name.startswith(('ST', '*ST', '退市', 'N', 'C')):
                                continue

                            if name and '="' not in name:
                                stocks[code] = name
        except:
            pass

        return stocks

    def update(self):
        """更新股票列表"""
        print("="*60)
        print("A股股票列表更新工具 - 同花顺数据源")
        print("="*60)

        # 加载现有列表
        existing = self.load_existing()
        print(f"\n现有股票列表: {len(existing)} 只")

        # 从各数据源获取
        all_stocks: Dict[str, str] = {}

        # 1. 尝试同花顺
        ths_stocks = self.fetch_from_ths()
        if ths_stocks:
            all_stocks.update(ths_stocks)
            print(f"同花顺: {len(ths_stocks)} 只")

        # 2. 尝试同花顺核心数据
        if len(all_stocks) < 3000:
            hexin_stocks = self.fetch_from_hexin()
            for code, name in hexin_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name
            print(f"同花顺核心: {len(hexin_stocks)} 只")

        # 3. 使用新浪财经作为备用
        if len(all_stocks) < 3000:
            print("\n同花顺获取不足，使用新浪财经备用...")
            sina_stocks = self.fetch_from_sina_simple()
            for code, name in sina_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name
            print(f"新浪财经: {len(sina_stocks)} 只")

        if not all_stocks:
            print("\n所有数据源都失败，无法更新股票列表")
            return

        # 统计变化
        new_codes = set(all_stocks.keys()) - set(existing.keys())
        removed_codes = set(existing.keys()) - set(all_stocks.keys())

        print(f"\n更新统计:")
        print(f"  原有: {len(existing)} 只")
        print(f"  现有: {len(all_stocks)} 只")
        print(f"  新增: {len(new_codes)} 只")
        print(f"  删除: {len(removed_codes)} 只")

        if new_codes:
            print(f"\n新增股票示例:")
            for code in list(new_codes)[:10]:
                print(f"  {code} {all_stocks[code]}")

        # 保存
        self.save_to_md(all_stocks)


def main():
    updater = StockListUpdater()
    updater.update()


if __name__ == "__main__":
    main()
