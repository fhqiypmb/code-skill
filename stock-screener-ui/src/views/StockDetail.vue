<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import axios from 'axios'
import type { AnalysisResult } from '@/types/stock'

const route = useRoute()
const stockData = ref<AnalysisResult | null>(null)
const chartRef = ref<HTMLElement | null>(null)
const chartInstance = ref<echarts.ECharts | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const dataLoaded = ref(false)

const code = computed(() => route.params.code as string)

watch(dataLoaded, async (newVal) => {
  if (newVal && stockData.value) {
    await nextTick()
    await new Promise(resolve => setTimeout(resolve, 100))
    if (chartRef.value) {
      initChart()
    }
  }
})

onMounted(async () => {
  await fetchAnalysis()
})

async function fetchAnalysis() {
  loading.value = true
  error.value = null
  try {
    const response = await axios.get(`/api/analysis/${code.value}`)
    stockData.value = response.data
    dataLoaded.value = true
  } catch (err: any) {
    error.value = err.response?.data?.error || '加载失败'
    stockData.value = getMockData()
    dataLoaded.value = true
  } finally {
    loading.value = false
  }
}

function initChart() {
  if (!chartRef.value || !stockData.value) return
  if (chartInstance.value) chartInstance.value.dispose()
  
  const chart = echarts.init(chartRef.value)
  chartInstance.value = chart
  
  const option = {
    backgroundColor: 'transparent',
    title: {
      text: '成功率维度分析',
      textStyle: { color: '#fff', fontSize: 14 },
      left: 'center',
      top: 10,
    },
    radar: {
      indicator: [
        { name: '突破质量', max: 100 },
        { name: '趋势动能', max: 100 },
        { name: '相对强度', max: 100 },
        { name: '资金持续', max: 100 },
        { name: '风险收益', max: 100 },
        { name: '到达概率', max: 100 },
      ],
      axisName: { color: '#00d4ff' },
      splitLine: { lineStyle: { color: 'rgba(0, 212, 255, 0.3)' } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: 'rgba(0, 212, 255, 0.3)' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: [
          stockData.value.success_rate?.dim_breakout || 0,
          stockData.value.success_rate?.dim_momentum || 0,
          stockData.value.success_rate?.dim_rs || 0,
          stockData.value.success_rate?.dim_capital || 0,
          stockData.value.success_rate?.dim_rr || 0,
          stockData.value.success_rate?.dim_reach_prob || 0,
        ],
        name: '评分',
        areaStyle: { color: 'rgba(0, 212, 255, 0.4)' },
        lineStyle: { color: '#00d4ff' },
        itemStyle: { color: '#00d4ff' },
      }],
    }],
  }
  
  chart.setOption(option)
  window.addEventListener('resize', () => chart.resize())
}

function getMockData(): AnalysisResult {
  return {
    code: code.value, name: '模拟数据', industry: '未知', concepts: [],
    quote: { name: '', price: 0, change_pct: 0, high: 0, low: 0, open: 0, pre_close: 0, volume: 0, amount: 0, turnover_rate: 0 },
    capital: { main_net_in: 0, super_net_in: 0, big_net_in: 0, flow_ratio: 0 },
    technical: { current_price: 0, target_price: 0, stop_loss: 0, expected_gain_pct: 0, stop_loss_pct: 0, space_ok: false, method_targets: {}, atr: 0, ma20: 0 },
    trend: { score: 0, level: '', ma_align: false, vol_price_ok: false, macd_positive: false, macd_strength: 0, detail: {} },
    market_pos: { score: 0, level: '', relative_strength: 0, rs_score: 0, vol_ratio: 0, vr_score: 0, benchmark: '', benchmark_name: '' },
    success_rate: { score: 0, grade: 'D', dim_breakout: 0, dim_momentum: 0, dim_rs: 0, dim_capital: 0, dim_rr: 0, dim_reach_prob: 0 },
    capital_confirmed: false, verdict: '', signal_type: '',
  }
}

const getGradeColor = (grade: string) => {
  const colors: Record<string, string> = { 'S': '#00ff88', 'A': '#00d4ff', 'B': '#ffaa00', 'C': '#ff6600', 'D': '#ff3333' }
  return colors[grade] || '#888'
}
</script>

<template>
  <div class="page-container">
    <div v-if="loading" class="loading-state">
      <div class="cyber-loader"></div>
      <p>正在加载分析数据...</p>
    </div>
    <div v-else-if="error" class="error-state">
      <p class="error-msg">{{ error }}</p>
      <button class="cyber-btn" @click="fetchAnalysis">重新加载</button>
    </div>
    <div v-else-if="stockData" class="stock-detail">
      <div class="page-header">
        <div>
          <h1 class="cyber-title">{{ stockData.code }} - {{ stockData.name }}</h1>
          <div class="stock-meta">
            <span class="cyber-badge info">{{ stockData.industry }}</span>
            <span class="cyber-badge" :style="{ borderColor: getGradeColor(stockData.success_rate.grade), color: getGradeColor(stockData.success_rate.grade) }">{{ stockData.success_rate.grade }}级</span>
            <span class="cyber-badge" :class="{ success: stockData.verdict === '达标', warning: stockData.verdict !== '达标' }">{{ stockData.verdict }}</span>
          </div>
        </div>
        <div class="price-info">
          <div class="price">¥{{ stockData.quote.price.toFixed(2) }}</div>
          <div class="change" :class="stockData.quote.change_pct >= 0 ? 'positive' : 'negative'">{{ stockData.quote.change_pct >= 0 ? '+' : '' }}{{ stockData.quote.change_pct.toFixed(2) }}%</div>
        </div>
      </div>
      <div class="data-grid">
        <div class="cyber-card">
          <div class="data-label">目标价格</div>
          <div class="data-value positive">¥{{ stockData.technical.target_price.toFixed(2) }}</div>
          <div class="data-sub">空间 +{{ stockData.technical.expected_gain_pct.toFixed(1) }}%</div>
        </div>
        <div class="cyber-card">
          <div class="data-label">止损价格</div>
          <div class="data-value negative">¥{{ stockData.technical.stop_loss.toFixed(2) }}</div>
          <div class="data-sub">风险 {{ Math.abs(stockData.technical.stop_loss_pct).toFixed(1) }}%</div>
        </div>
        <div class="cyber-card">
          <div class="data-label">成功率评分</div>
          <div class="data-value" :style="{ color: getGradeColor(stockData.success_rate.grade) }">{{ stockData.success_rate.score.toFixed(0) }}</div>
          <div class="data-sub">等级 {{ stockData.success_rate.grade }}</div>
        </div>
        <div class="cyber-card">
          <div class="data-label">趋势强度</div>
          <div class="data-value">{{ stockData.trend.score.toFixed(0) }}</div>
          <div class="data-sub">{{ stockData.trend.level }} level</div>
        </div>
      </div>
      <div class="analysis-grid">
        <div class="cyber-card chart-card">
          <h3 class="panel-title">成功率维度分析</h3>
          <div ref="chartRef" style="width: 100%; height: 400px;"></div>
        </div>
        <div class="cyber-card">
          <h3 class="panel-title">维度评分详情</h3>
          <div class="score-list">
            <div class="score-item"><span>突破质量</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_breakout + '%' }"></div></div><span>{{ stockData.success_rate.dim_breakout.toFixed(0) }}</span></div>
            <div class="score-item"><span>趋势动能</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_momentum + '%' }"></div></div><span>{{ stockData.success_rate.dim_momentum.toFixed(0) }}</span></div>
            <div class="score-item"><span>相对强度</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_rs + '%' }"></div></div><span>{{ stockData.success_rate.dim_rs.toFixed(0) }}</span></div>
            <div class="score-item"><span>资金持续</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_capital + '%' }"></div></div><span>{{ stockData.success_rate.dim_capital.toFixed(0) }}</span></div>
            <div class="score-item"><span>风险收益</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_rr + '%' }"></div></div><span>{{ stockData.success_rate.dim_rr.toFixed(0) }}</span></div>
            <div class="score-item"><span>到达概率</span><div class="score-bar"><div class="score-fill" :style="{ width: stockData.success_rate.dim_reach_prob + '%' }"></div></div><span>{{ stockData.success_rate.dim_reach_prob.toFixed(0) }}</span></div>
          </div>
        </div>
      </div>
      <div class="cyber-card">
        <h3 class="panel-title">资金流向</h3>
        <div class="capital-grid">
          <div class="capital-item"><div class="capital-label">主力净流入</div><div class="capital-value" :class="stockData.capital.main_net_in >= 0 ? 'positive' : 'negative'">{{ stockData.capital.main_net_in.toFixed(2) }}万</div></div>
          <div class="capital-item"><div class="capital-label">超大单净流入</div><div class="capital-value" :class="stockData.capital.super_net_in >= 0 ? 'positive' : 'negative'">{{ stockData.capital.super_net_in.toFixed(2) }}万</div></div>
          <div class="capital-item"><div class="capital-label">大单净流入</div><div class="capital-value" :class="stockData.capital.big_net_in >= 0 ? 'positive' : 'negative'">{{ stockData.capital.big_net_in.toFixed(2) }}万</div></div>
          <div class="capital-item"><div class="capital-label">资金强度</div><div class="capital-value">{{ stockData.capital.flow_ratio.toFixed(2) }}‱</div></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.loading-state, .error-state { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 60vh; gap: 20px; }
.error-msg { color: var(--accent-red); font-size: 16px; }
.stock-detail { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.stock-meta { display: flex; gap: 12px; margin-top: 12px; }
.price-info { text-align: right; }
.price { font-size: 36px; font-weight: 700; background: var(--gradient-cyber); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.change { font-size: 18px; font-weight: 600; margin-top: 4px; }
.data-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.analysis-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.chart-card { min-height: 480px; display: flex; flex-direction: column; }
.chart-card > div { width: 100%; height: 400px; flex: 1; }
.panel-title { font-size: 16px; color: var(--text-secondary); margin-bottom: 16px; text-transform: uppercase; }
.score-list { display: flex; flex-direction: column; gap: 16px; }
.score-item { display: flex; align-items: center; gap: 12px; }
.score-item span:first-child { width: 80px; font-size: 13px; color: var(--text-secondary); }
.score-bar { flex: 1; height: 8px; background: var(--bg-secondary); border-radius: 4px; overflow: hidden; }
.score-fill { height: 100%; background: var(--gradient-cyber); border-radius: 4px; transition: width 0.5s ease; }
.score-item span:last-child { width: 40px; text-align: right; font-weight: 600; }
.capital-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
.capital-item { padding: 16px; background: var(--bg-secondary); border-radius: 6px; text-align: center; }
.capital-label { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.capital-value { font-size: 18px; font-weight: 700; }
.positive { color: var(--accent-green); }
.negative { color: var(--accent-red); }
@media (max-width: 1024px) { .analysis-grid { grid-template-columns: 1fr; } .capital-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
