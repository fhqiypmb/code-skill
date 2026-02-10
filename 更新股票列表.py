"""
A股股票列表更新工具
支持从多个数据源获取：东方财富、同花顺、新浪财经
生成/更新 stock_list.md 文件

用法：
  python 更新股票列表.py
"""

import os
import json
import re
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Set
import urllib.request
import urllib.parse

# 禁用代理
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    if key in os.environ:
        del os.environ[key]


class StockListUpdater:
    """股票列表更新器"""

    def __init__(self):
        self.md_file = os.path.join(os.path.dirname(__file__), 'stock_list.md')
        self.stocks: Dict[str, str] = {}  # code -> name

    def load_existing(self) -> Dict[str, str]:
        """加载现有的股票列表"""
        stocks = {}
        if not os.path.exists(self.md_file):
            return stocks

        try:
            with open(self.md_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # 格式: 000001 平安银行
                    parts = line.split(maxsplit=1)
                    if len(parts) >= 1:
                        code = parts[0]
                        name = parts[1] if len(parts) > 1 else ''
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

    def fetch_from_eastmoney(self) -> Dict[str, str]:
        """从东方财富获取股票列表"""
        print("\n从东方财富获取股票列表...")
        stocks = {}

        try:
            import akshare as ak

            # 上海市场
            print("  获取上海市场...")
            try:
                df = ak.stock_sh_a_spot_em()
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    name = str(row['名称']).strip()
                    if code and not name.startswith(('ST', '*ST')):
                        stocks[code] = name
                print(f"    获取到 {len(df)} 只")
            except Exception as e:
                print(f"    失败: {e}")

            # 深圳市场
            print("  获取深圳市场...")
            try:
                df = ak.stock_sz_a_spot_em()
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    name = str(row['名称']).strip()
                    if code and not name.startswith(('ST', '*ST')):
                        stocks[code] = name
                print(f"    获取到 {len(df)} 只")
            except Exception as e:
                print(f"    失败: {e}")

            # 创业板
            print("  获取创业板...")
            try:
                df = ak.stock_cy_a_spot_em()
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    name = str(row['名称']).strip()
                    if code and not name.startswith(('ST', '*ST')):
                        stocks[code] = name
                print(f"    获取到 {len(df)} 只")
            except Exception as e:
                print(f"    失败: {e}")

            # 科创板
            print("  获取科创板...")
            try:
                df = ak.stock_kc_a_spot_em()
                for _, row in df.iterrows():
                    code = str(row['代码']).strip()
                    name = str(row['名称']).strip()
                    if code and not name.startswith(('ST', '*ST')):
                        stocks[code] = name
                print(f"    获取到 {len(df)} 只")
            except Exception as e:
                print(f"    失败: {e}")

        except ImportError:
            print("  akshare未安装，跳过东方财富数据源")
        except Exception as e:
            print(f"  东方财富获取失败: {e}")

        return stocks

    def fetch_from_sina(self) -> Dict[str, str]:
        """从新浪财经获取股票列表"""
        print("\n从新浪财经获取股票列表...")
        stocks = {}

        # 沪市A股 (600000-609999)
        print("  获取沪市主板...")
        for start in range(600000, 610000, 100):
            codes = [f"sh{i}" for i in range(start, min(start+100, 604000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start}-{start+99}: {len(batch_stocks)} 只")

        # 深市主板 (000001-000999)
        print("  获取深市主板000...")
        for start in range(1, 1000, 100):
            codes = [f"sz{i:06d}" for i in range(start, min(start+100, 1000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start:06d}-{(start+99):06d}: {len(batch_stocks)} 只")

        # 深市主板 (001001-001999)
        print("  获取深市主板001...")
        for start in range(1001, 2000, 100):
            codes = [f"sz{i:06d}" for i in range(start, min(start+100, 2000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start:06d}-{(start+99):06d}: {len(batch_stocks)} 只")

        # 中小板 (002001-002999)
        print("  获取中小板...")
        for start in range(2001, 3000, 100):
            codes = [f"sz{i:06d}" for i in range(start, min(start+100, 3000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start:06d}-{(start+99):06d}: {len(batch_stocks)} 只")

        # 深市主板 (003001-004999)
        print("  获取深市主板003-004...")
        for start in range(3001, 5000, 100):
            codes = [f"sz{i:06d}" for i in range(start, min(start+100, 5000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start:06d}-{(start+99):06d}: {len(batch_stocks)} 只")

        # 创业板 (300001-302000)
        print("  获取创业板...")
        for start in range(300001, 302000, 100):
            codes = [f"sz{i}" for i in range(start, min(start+100, 301000))]
            batch_stocks = self._fetch_sina_batch(codes)
            stocks.update(batch_stocks)
            if batch_stocks:
                print(f"    {start}-{start+99}: {len(batch_stocks)} 只")

        # 注意：科创板(688xxx)和北交所(8xxxxx/4xxxxx)已在 _fetch_sina_batch 中过滤

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

                    # 解析: var hq_str_sh600000="浦发银行,...
                    match = re.search(r'var hq_str_(\w+)="([^"]*)"', line)
                    if match:
                        full_code = match.group(1)
                        data = match.group(2)

                        if not data or data == '':
                            continue

                        parts = data.split(',')
                        if len(parts) >= 1:
                            name = parts[0].strip()
                            # 提取纯数字代码
                            code = full_code[2:]  # 去掉 sh/sz 前缀

                            # 过滤科创板 (688xxx, 689xxx)
                            if code.startswith('688') or code.startswith('689'):
                                continue

                            # 过滤北交所 (8xxxxxx, 4xxxxxx)
                            if code.startswith('8') or code.startswith('4'):
                                continue

                            # 过滤ST、退市、无效数据
                            if name and not name.startswith(('ST', '*ST', '退市', 'N', 'C')) and '="' not in name:
                                stocks[code] = name
        except Exception as e:
            pass

        return stocks

    def fetch_from_ths(self) -> Dict[str, str]:
        """从同花顺获取股票列表"""
        print("\n从同花顺获取股票列表...")
        stocks = {}

        try:
            # 同花顺行业列表接口
            url = "http://basic.10jqka.com.cn/api/stockph/stockph/hsa"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })

            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))

                if 'data' in data and 'hsa' in data['data']:
                    for item in data['data']['hsa']:
                        code = item.get('code', '')
                        name = item.get('name', '')
                        if code and name and not name.startswith(('ST', '*ST')):
                            stocks[code] = name

                    print(f"  获取到 {len(stocks)} 只")
        except Exception as e:
            print(f"  同花顺获取失败: {e}")

        return stocks

    def update(self):
        """更新股票列表"""
        print("="*60)
        print("A股股票列表更新工具")
        print("="*60)

        # 加载现有列表
        existing = self.load_existing()
        print(f"\n现有股票列表: {len(existing)} 只")

        # 从各数据源获取
        all_stocks: Dict[str, str] = {}

        # 1. 尝试东方财富
        em_stocks = self.fetch_from_eastmoney()
        if em_stocks:
            all_stocks.update(em_stocks)
            print(f"东方财富: {len(em_stocks)} 只")

        # 2. 尝试新浪财经（如果东方财富失败或数据太少）
        if len(all_stocks) < 3000:
            sina_stocks = self.fetch_from_sina()
            for code, name in sina_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name
            print(f"新浪财经: {len(sina_stocks)} 只")

        # 3. 尝试同花顺
        if len(all_stocks) < 3000:
            ths_stocks = self.fetch_from_ths()
            for code, name in ths_stocks.items():
                if code not in all_stocks:
                    all_stocks[code] = name
            print(f"同花顺: {len(ths_stocks)} 只")

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

        if removed_codes:
            print(f"\n删除股票示例:")
            for code in list(removed_codes)[:10]:
                print(f"  {code} {existing.get(code, '')}")

        # 保存
        self.save_to_md(all_stocks)


def main():
    updater = StockListUpdater()
    updater.update()


if __name__ == "__main__":
    main()
