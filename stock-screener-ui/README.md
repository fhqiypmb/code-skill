# 🚀 股票智能选股系统

科技感股票分析前端 - Vue3 + Vite + TypeScript

## 🎯 一条命令启动

```bash
cd stock-screener-ui
npm run serve:all
```

**访问地址：**
- 前端：http://localhost:5173
- 后端：http://localhost:5000

## 📁 其他命令

```bash
# 仅前端（模拟数据）
npm run dev

# 仅后端
npm run server

# 构建
npm run build
```

## 🎨 页面

| 路径 | 说明 |
|------|------|
| `/` | 仪表盘 - 统计 + 最新信号 |
| `/screener` | 智能选股 - 控制 + 进度 |
| `/stock/:code` | 个股详情 - 分析 + 雷达图 |
| `/ml-signals` | ML 信号 - 数据列表 |

## 🔌 技术栈

- Vue 3 + TypeScript + Vite
- ECharts 6（图表）
- Pinia（状态管理）
- Vue Router 5（路由）
- Flask（后端 API）
- Axios（HTTP 客户端）

## ⚠️ 首次运行

```bash
# 安装前端依赖
npm install

# 安装 Python 依赖
pip install flask flask-cors
```

## 📊 数据源

自动读取 `stocks/ml/shadow_data.json`，与 Python 选股系统共享数据。
