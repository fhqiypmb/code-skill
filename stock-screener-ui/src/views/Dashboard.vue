<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import type { SignalData } from '@/types/stock'

const router = useRouter()
const signals = ref<SignalData[]>([])
const loading = ref(false)
const stats = ref({
  total: 0,
  passRate: 0,
  avgReturn: 0,
})
const autoScreenerRunning = ref(false)
const refreshTimer = ref<any>(null)
const sortBy = ref<'period' | 'score' | 'date'>('date')
const sortOrder = ref<'asc' | 'desc'>('desc')

function sortSignals(data: SignalData[]) {
  return [...data].sort((a, b) => {
    let comparison = 0
    if (sortBy.value === 'period') {
      const periods = { '5 分钟': 1, '15 分钟': 2, '30 分钟': 3, '60 分钟': 4, '日线': 5, '周线': 6, '月线': 7 }
      const aPeriod = periods[a.period as keyof typeof periods] || 0
      const bPeriod = periods[b.period as keyof typeof periods] || 0
      comparison = aPeriod - bPeriod
    } else if (sortBy.value === 'score') {
      comparison = (a.sr_score || 0) - (b.sr_score || 0)
    } else if (sortBy.value === 'date') {
      comparison = (a.date || '').localeCompare(b.date || '')
    }
    return sortOrder.value === 'asc' ? comparison : -comparison
  })
}

function setSortBy(field: 'period' | 'score' | 'date') {
  if (sortBy.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = field
    sortOrder.value = 'desc'
  }
}

onMounted(async () => {
  await checkAutoScreenerStatus()
  // 定时刷新（每 10 秒检查新数据）
  refreshTimer.value = setInterval(refreshData, 10000)
})

onUnmounted(() => {
  if (refreshTimer.value) {
    clearInterval(refreshTimer.value)
  }
})

async function checkAutoScreenerStatus() {
  try {
    const response = await axios.get('/api/auto-screener/status')
    autoScreenerRunning.value = response.data.running
  } catch (error) {
    console.error('获取自动选股状态失败:', error)
  }
}

async function toggleAutoScreener() {
  try {
    if (autoScreenerRunning.value) {
      await axios.post('/api/auto-screener/stop')
      autoScreenerRunning.value = false
    } else {
      await axios.post('/api/auto-screener/start')
      autoScreenerRunning.value = true
    }
  } catch (error: any) {
    console.error('切换自动选股失败:', error)
  }
}

async function refreshData() {
  // 静默刷新，只检查自动选股的新数据
  try {
    const response = await axios.get('/api/ml/signals', {
      params: { page: 1, pageSize: 50 }
    })
    const data = response.data || []
    if (data.length !== signals.value.length) {
      // 数据有变化
      signals.value = data
      stats.value.total = signals.value.length
      console.log(`[刷新] 当前股票数：${data.length}`)
    }
  } catch (error) {
    // 静默失败
  }
}

// 计算排序后的数据
const sortedSignals = computed(() => sortSignals(signals.value))

async function loadData(reset = false) {
  if (loading.value || loadingMore.value) return
  
  if (reset) {
    loading.value = true
    page.value = 1
    signals.value = []
  } else {
    loadingMore.value = true
    page.value++
  }
  
  try {
    const response = await axios.get('/api/ml/signals', {
      params: {
        page: page.value,
        pageSize: pageSize
      }
    })
    const data = response.data || []
    
    if (Array.isArray(data)) {
      if (reset) {
        signals.value = data
      } else {
        signals.value = [...signals.value, ...data]
      }
      
      // 计算统计（只在第一次加载时）
      if (reset) {
        stats.value.total = signals.value.length
        const verdicts = signals.value.filter(s => s.verdict === '达标')
        stats.value.passRate = signals.value.length > 0 
          ? Math.round(verdicts.length / signals.value.length * 100) 
          : 0
      }
      
      // 判断是否还有更多
      hasMore.value = data.length === pageSize
    } else {
      hasMore.value = false
    }
  } catch (error) {
    console.error('加载数据失败:', error)
    hasMore.value = false
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

function handleScroll() {
  // 距离底部 300px 时加载更多
  const scrollTop = window.scrollY || window.pageYOffset
  const clientHeight = document.documentElement.clientHeight
  const scrollHeight = document.documentElement.scrollHeight
  
  if (scrollTop + clientHeight >= scrollHeight - 300 && hasMore.value && !loadingMore.value) {
    loadData(false)
  }
}

const getGradeClass = (grade: string) => {
  const classes: Record<string, string> = {
    'S': 'grade-s',
    'A': 'grade-a',
    'B': 'grade-b',
    'C': 'grade-c',
    'D': 'grade-d',
  }
  return classes[grade] || ''
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="cyber-title">🚀 股票智能选股系统</h1>
      <div class="header-actions">
        <button 
          class="cyber-btn" 
          :class="autoScreenerRunning ? 'running' : ''"
          @click="toggleAutoScreener"
        >
          {{ autoScreenerRunning ? '🟢 自动选股中' : '⚪ 启动自动选股' }}
        </button>
        <router-link to="/screener" class="cyber-btn">手动选股</router-link>
      </div>
    </div>

    <!-- 统计卡片 -->
    <div class="data-grid">
      <div class="cyber-card stat-card">
        <div class="stat-icon">📊</div>
        <div class="stat-info">
          <div class="stat-label">信号总数</div>
          <div class="stat-value">{{ stats.total }}</div>
        </div>
      </div>
      <div class="cyber-card stat-card">
        <div class="stat-icon">🎯</div>
        <div class="stat-info">
          <div class="stat-label">达标率</div>
          <div class="stat-value positive">{{ stats.passRate }}%</div>
        </div>
      </div>
      <div class="cyber-card stat-card">
        <div class="stat-icon">📈</div>
        <div class="stat-info">
          <div class="stat-label">平均收益</div>
          <div class="stat-value positive" v-if="stats.avgReturn > 0">+{{ stats.avgReturn.toFixed(1) }}%</div>
          <div class="stat-value" v-else>{{ stats.avgReturn.toFixed(1) }}%</div>
        </div>
      </div>
      <div class="cyber-card stat-card">
        <div class="stat-icon">🤖</div>
        <div class="stat-info">
          <div class="stat-label">ML 模型</div>
          <div class="stat-value">运行中</div>
        </div>
      </div>
    </div>

    <!-- 最新信号 -->
    <div class="cyber-card">
      <h2 class="cyber-title" style="font-size: 20px;">最新选股信号</h2>
      
      <div class="table-wrapper">
        <table class="cyber-table">
          <thead>
            <tr>
              <th @click="setSortBy('date')" style="cursor: pointer; user-select: none;">日期 <span v-if="sortBy === 'date'">{{ sortOrder === 'asc' ? '↑' : '↓' }}</span></th>
              <th>代码</th>
              <th>名称</th>
              <th>行业</th>
              <th @click="setSortBy('period')" style="cursor: pointer; user-select: none;">周期 <span v-if="sortBy === 'period'">{{ sortOrder === 'asc' ? '↑' : '↓' }}</span></th>
              <th @click="setSortBy('score')" style="cursor: pointer; user-select: none;">评分 <span v-if="sortBy === 'score'">{{ sortOrder === 'asc' ? '↑' : '↓' }}</span></th>
              <th>目标价</th>
              <th>止损价</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="signal in sortedSignals" :key="signal.code + signal.date">
              <td>{{ signal.date }}</td>
              <td>{{ signal.code }}</td>
              <td>{{ signal.name }}</td>
              <td>{{ signal.industry }}</td>
              <td>{{ signal.period }}</td>
              <td>
                <span :class="['cyber-badge', getGradeClass(signal.sr_grade)]">
                  {{ signal.sr_grade }} {{ signal.sr_score.toFixed(0) }}
                </span>
              </td>
              <td class="positive">¥{{ signal.target_price.toFixed(2) }}</td>
              <td class="negative">¥{{ signal.stop_loss.toFixed(2) }}</td>
              <td>
                <button 
                  class="cyber-btn-outline" 
                  style="padding: 6px 16px; font-size: 12px;"
                  @click="router.push(`/stock/${signal.code}`)"
                >
                  详情
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      
      <!-- 空状态 - 自动选股未启动 -->
      <div class="empty-state" v-if="sortedSignals.length === 0 && !autoScreenerRunning">
        <div class="empty-icon">📊</div>
        <p>暂无选股数据</p>
        <p class="empty-sub">点击"启动自动选股"开始扫描</p>
      </div>
      
      <!-- 空状态 - 自动选股运行中 -->
      <div class="empty-state" v-if="sortedSignals.length === 0 && autoScreenerRunning">
        <div class="empty-icon">🔄</div>
        <p>正在扫描股票市场...</p>
        <p class="empty-sub">发现符合条件的股票将自动显示</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  gap: 20px;
}

.stat-icon {
  font-size: 48px;
  opacity: 0.8;
}

.stat-info {
  flex: 1;
}

.stat-label {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  background: var(--gradient-cyber);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.grade-s { background: rgba(0, 255, 136, 0.2); color: #00ff88; border-color: #00ff88; }
.grade-a { background: rgba(0, 212, 255, 0.2); color: #00d4ff; border-color: #00d4ff; }
.grade-b { background: rgba(255, 170, 0, 0.2); color: #ffaa00; border-color: #ffaa00; }
.grade-c { background: rgba(255, 102, 0, 0.2); color: #ff6600; border-color: #ff6600; }
.grade-d { background: rgba(255, 68, 68, 0.2); color: #ff4444; border-color: #ff4444; }

.positive { color: var(--accent-green); }
.negative { color: var(--accent-red); }

.header-actions { display: flex; gap: 12px; align-items: center; }
.cyber-btn.running { background: var(--accent-green); animation: pulse 2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
th { cursor: pointer; transition: color 0.2s; }
th:hover { color: var(--accent-cyan); }

.table-wrapper {
  overflow-x: auto;
}

.empty-state {
  padding: 80px 20px;
  text-align: center;
  color: var(--text-muted);
}

.empty-icon {
  font-size: 64px;
  margin-bottom: 16px;
  opacity: 0.3;
}

.empty-sub {
  font-size: 14px;
  margin-top: 8px;
  opacity: 0.6;
}

.load-more,
.no-more {
  text-align: center;
  padding: 20px;
  color: var(--text-secondary);
  font-size: 14px;
}

.loading-text {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.mini-loader {
  width: 16px;
  height: 16px;
  border: 2px solid var(--bg-secondary);
  border-top-color: var(--accent-cyan);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.scroll-hint {
  opacity: 0.5;
  animation: bounce 1s ease infinite;
}

@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(5px); }
}

.no-more {
  opacity: 0.5;
}
</style>
