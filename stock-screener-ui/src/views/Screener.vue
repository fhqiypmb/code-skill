<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'

const running = ref(false)
const progress = ref(0)
const status = ref('就绪')
const results = ref<any[]>([])
const pollTimer = ref<any>(null)

// 选股参数
const selectedPeriod = ref('日线')
const signalTypes = ref({
  goldCross: true,
  doubleVolume: true,
  breakout: true,
})

async function startScreener() {
  if (running.value) return
  
  running.value = true
  progress.value = 0
  status.value = '启动选股服务...'
  results.value = []
  
  try {
    // 1. 启动选股任务，发送周期和信号类型参数
    const response = await axios.post('/api/screener/start', {
      period: selectedPeriod.value,
      signalTypes: {
        goldCross: signalTypes.value.goldCross,
        doubleVolume: signalTypes.value.doubleVolume,
        breakout: signalTypes.value.breakout,
      },
    })
    console.log('选股任务已启动:', response.data)
    
    // 2. 轮询进度
    startPolling()
  } catch (error: any) {
    console.error('启动选股失败:', error)
    status.value = '启动失败：' + (error.response?.data?.error || error.message)
    running.value = false
  }
}

function startPolling() {
  pollTimer.value = setInterval(async () => {
    try {
      const response = await axios.get('/api/screener/progress')
      const data = response.data
      
      progress.value = data.progress
      status.value = mapStatus(data.status)
      
      if (data.results_count > 0) {
        results.value = data.results
      }
      
      if (!data.running && data.progress >= 100) {
        // 完成
        clearInterval(pollTimer.value)
        status.value = '选股完成'
        running.value = false
        
        // 获取完整结果
        const resultsResponse = await axios.get('/api/screener/results')
        results.value = resultsResponse.data
      } else if (data.status === 'error') {
        clearInterval(pollTimer.value)
        status.value = '选股失败'
        running.value = false
      }
    } catch (error) {
      console.error('轮询进度失败:', error)
    }
  }, 1000)
}

function mapStatus(apiStatus: string): string {
  const statusMap: Record<string, string> = {
    'starting': '启动中...',
    'initializing': '初始化模块...',
    'screening': '选股分析中...',
    'analyzing': '深度分析中...',
    'completed': '选股完成',
    'error': '发生错误',
  }
  return statusMap[apiStatus] || '处理中...'
}

onMounted(() => {
  // 页面卸载时清理定时器
  return () => {
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
    }
  }
})
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="cyber-title">🎯 智能选股</h1>
    </div>

    <div class="screener-container">
      <!-- 控制面板 -->
      <div class="cyber-card control-panel">
        <h2 class="cyber-title" style="font-size: 18px;">控制面板</h2>
        
        <div class="control-group">
          <label>选股周期</label>
          <select v-model="selectedPeriod" class="cyber-select">
            <option value="日线">日线</option>
            <option value="周线">周线</option>
            <option value="月线">月线</option>
            <option value="5 分钟">5 分钟</option>
            <option value="15 分钟">15 分钟</option>
            <option value="30 分钟">30 分钟</option>
            <option value="60 分钟">60 分钟</option>
          </select>
        </div>

        <div class="control-group">
          <label>信号类型</label>
          <div class="checkbox-group">
            <label><input type="checkbox" v-model="signalTypes.goldCross"> 金叉信号</label>
            <label><input type="checkbox" v-model="signalTypes.doubleVolume"> 倍量阳线</label>
            <label><input type="checkbox" v-model="signalTypes.breakout"> 突破信号</label>
          </div>
        </div>

        <div class="control-group">
          <label>ML 模型筛选</label>
          <div class="toggle-switch">
            <input type="checkbox" id="ml-toggle" checked>
            <label for="ml-toggle"></label>
          </div>
        </div>

        <button 
          class="cyber-btn start-btn" 
          @click="startScreener"
          :disabled="running"
        >
          {{ running ? '选股中...' : '开始选股' }}
        </button>
      </div>

      <!-- 进度显示 -->
      <div class="cyber-card progress-panel">
        <h2 class="cyber-title" style="font-size: 18px;">分析进度</h2>
        
        <div class="progress-info">
          <span>{{ status }}</span>
          <span>{{ progress }}%</span>
        </div>
        
        <div class="cyber-progress">
          <div class="cyber-progress-bar" :style="{ width: progress + '%' }"></div>
        </div>

        <div class="stats-grid" v-if="progress > 0">
          <div class="stat-item">
            <div class="stat-label">已发现</div>
            <div class="stat-value">{{ results.length }}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">已分析</div>
            <div class="stat-value positive">{{ results.filter(r => r.analysis).length }}</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">进度</div>
            <div class="stat-value">{{ progress }}%</div>
          </div>
        </div>
      </div>

      <!-- 结果预览 -->
      <div class="cyber-card results-panel">
        <h2 class="cyber-title" style="font-size: 18px;">选股结果</h2>
        
        <div v-if="results.length > 0" class="results-list">
          <div class="result-item" v-for="stock in results" :key="stock.code + stock.timestamp">
            <div class="result-header">
              <span class="stock-code">{{ stock.code }}</span>
              <span class="stock-name">{{ stock.name }}</span>
              <span class="cyber-badge success" v-if="stock.analysis?.verdict === '达标'">达标</span>
              <span class="cyber-badge warning" v-else-if="stock.analysis">分析中</span>
              <span class="cyber-badge info" v-else>待分析</span>
            </div>
            <div class="result-details" v-if="stock.analysis">
              <span>评分：{{ stock.analysis.success_rate?.grade || '-' }} {{ stock.analysis.success_rate?.score?.toFixed(0) || '-' }}</span>
              <span>目标：¥{{ stock.analysis.technical?.target_price?.toFixed(2) || '-' }}</span>
              <span>止损：¥{{ stock.analysis.technical?.stop_loss?.toFixed(2) || '-' }}</span>
              <span>空间：+{{ stock.analysis.technical?.expected_gain_pct?.toFixed(1) || '-' }}%</span>
            </div>
            <div class="result-details" v-else>
              <span class="loading">等待分析...</span>
            </div>
          </div>
        </div>

        <div v-else-if="progress === 100" class="empty-state">
          <div class="empty-icon">📊</div>
          <p>未找到符合条件的股票</p>
        </div>

        <div v-else class="empty-state">
          <div class="empty-icon">🎯</div>
          <p>点击"开始选股"运行选股策略</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.screener-container {
  display: grid;
  grid-template-columns: 300px 1fr 1fr;
  gap: 20px;
}

.control-panel {
  height: fit-content;
}

.control-group {
  margin-bottom: 20px;
}

.control-group label {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  text-transform: uppercase;
  margin-bottom: 8px;
}

.cyber-select {
  width: 100%;
  padding: 10px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  color: var(--text-primary);
  font-size: 14px;
}

.checkbox-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.checkbox-group label {
  display: flex;
  align-items: center;
  gap: 8px;
  text-transform: none;
  cursor: pointer;
}

.checkbox-group input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent-cyan);
}

.toggle-switch {
  position: relative;
  width: 60px;
  height: 30px;
}

.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-switch label {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-color);
  transition: 0.4s;
  border-radius: 30px;
}

.toggle-switch label:before {
  position: absolute;
  content: "";
  height: 22px;
  width: 22px;
  left: 3px;
  bottom: 3px;
  background-color: var(--accent-cyan);
  transition: 0.4s;
  border-radius: 50%;
}

.toggle-switch input:checked + label {
  border-color: var(--accent-cyan);
}

.toggle-switch input:checked + label:before {
  transform: translateX(30px);
}

.start-btn {
  width: 100%;
  margin-top: 20px;
}

.start-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
  font-size: 14px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 24px;
}

.stat-item {
  text-align: center;
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: 6px;
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 500px;
  overflow-y: auto;
}

.result-item {
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: 6px;
  border-left: 3px solid var(--accent-cyan);
  transition: all 0.3s ease;
}

.result-item:has(.cyber-badge.success) {
  border-left-color: var(--accent-green);
}

.result-item:hover {
  transform: translateX(4px);
  background: var(--bg-hover);
}

.result-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
}

.stock-code {
  font-weight: 700;
  font-size: 16px;
  color: var(--accent-cyan);
}

.stock-name {
  color: var(--text-secondary);
  flex: 1;
}

.result-details {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}

.result-details .loading {
  color: var(--text-muted);
  font-style: italic;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-muted);
}

.empty-icon {
  font-size: 64px;
  margin-bottom: 16px;
  opacity: 0.3;
}

@media (max-width: 1200px) {
  .screener-container {
    grid-template-columns: 1fr;
  }
}
</style>
