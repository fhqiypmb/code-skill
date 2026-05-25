from moomoo import *

# 连接本地 OpenD（默认端口 11111）
quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

# 1. 订阅股票（以贵州茅台为例）
ret_sub, err_msg = quote_ctx.subscribe(['SH.600519'], [SubType.QUOTE])
if ret_sub == RET_OK:
    print("✅ 订阅成功")
else:
    print(f"❌ 订阅失败: {err_msg}")

# 2. 获取实时行情快照
ret, data = quote_ctx.get_market_snapshot(['SH.600519'])
if ret == RET_OK:
    print("\n📊 贵州茅台实时行情:")
    print(data[['code', 'last_price', 'open_price', 'high_price', 'low_price', 'volume']])
else:
    print(f"获取行情失败: {data}")

# 3. 获取实时资金流向
ret, data = quote_ctx.get_capital_flow('SH.600519')
if ret == RET_OK:
    print("\n💰 资金流向:")
    print(data)
else:
    print(f"获取资金流向失败: {data}")

# 4. 获取日K线
ret, data, _ = quote_ctx.request_history_kline(
    'SH.600519',
    ktype=KLType.K_DAY,
    max_count=500
)
if ret == RET_OK:
    print("\n📈 最近5日日K线:")
    print(data[['code', 'time_key', 'open', 'close', 'high', 'low', 'volume']])
else:
    print(f"获取K线失败: {data}")

# 关闭连接
quote_ctx.close()
print("\n✅ 完成")