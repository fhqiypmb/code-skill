# 股票信号监控

基于 `严格选股_多周期.py` 的全自动选股监控，部署在 GitHub Actions 上，出现信号通过微信推送。

## 架构

```
GitHub Actions (免费)
  ↓ 每个交易日 09:20 自动启动
  ↓ 循环: 扫描(5分钟+30分钟+日线) → 等5分钟 → 再扫...
  ↓ 有信号 → PushPlus推送微信
  ↓ 15:05 收盘自动退出
  ↓ 信号结果 commit 到仓库
```

## 部署步骤

### 1. 创建钉钉机器人

1. 钉钉建一个群
2. 群设置 → 智能群助手 → 添加机器人 → 自定义(Webhook)
3. 安全设置选 **加签**，复制 Secret
4. 完成后复制 Webhook 地址

### 2. 设置 GitHub Secrets

进入你的 GitHub 仓库：
`Settings → Secrets and variables → Actions → New repository secret`

添加两个：
- **Name**: `DINGTALK_WEBHOOK`  **Value**: Webhook URL
- **Name**: `DINGTALK_SECRET`   **Value**: SEC开头的签名密钥

### 3. 推送代码到 GitHub

```bash
git add .
git commit -m "添加股票监控"
git push
```

### 4. 确认 workflow 启动

进入仓库 `Actions` 页面，可以看到 `股票信号监控` workflow。

- **自动触发**: 每个交易日 09:20（北京时间）自动启动
- **手动触发**: 点 `Run workflow` 按钮，可选择 `loop`(循环) 或 `now`(跑一次)

## 文件说明

```
stock_monitor/
├── monitor.py           # 主程序（循环扫描）
├── notifier.py          # PushPlus 推送模块
├── signals/             # 每日信号记录（自动生成，JSON）
├── sent_signals.json    # 去重记录（自动生成）
└── README.md            # 本文件

.github/workflows/
└── stock-monitor.yml    # GitHub Actions 工作流
```

## 运行模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 循环模式 | `python monitor.py` | 等待开盘 → 循环扫描到收盘 |
| 立即模式 | `python monitor.py --now` | 立即扫描一次，不等交易时间 |

## 费用

- GitHub Actions: 免费 2000 分钟/月（公开仓库无限制）
- 钉钉机器人: 免费，不限次数

每个交易日运行约 4-5 小时，一个月约 22 天 × 5 小时 = 110 小时 = 6600 分钟。
**建议使用公开仓库**（免费无限制），或 Pro 账户（3000分钟/月）。

## 注意事项

- 确保 `stock_list.md` 在仓库根目录
- GitHub Actions 服务器在海外，访问国内数据源可能有延迟
- 如遇限流严重，可在 monitor.py 中调小 `max_workers`
