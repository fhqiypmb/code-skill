<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { SignalData } from '@/types/stock'

const signals = ref<SignalData[]>([])
const filter = ref('all')

onMounted(() => {
  // 模拟数据
  signals.value = [
    {
      date: '2026-03-12',
      code: '002177',
      name: '御银股份',
      period: '日线',
      signal_type: '普通',
      close: 9.65,
      verdict: '达标',
      industry: '计算机设备',
      sr_score: 60.8,
      sr_grade: 'B',
      target_price: 11.54,
      stop_loss: 8.49,
      reached_target: null,
      actual_return: null,
    },
    {
      date: '2026-03-12',
      code: '301165',
      name: '锐捷网络',
      period: '日线',
      signal_type: '普通',
      close: 52.30,
      verdict: '达标',
      industry: '通信设备',
      sr_score: 72.5,
      sr_grade: 'A',
      target_price: 62.76,
      stop_loss: 45.80,
      reached_target: null,
      actual_return: null,
    },
  ]
})

const filteredSignals = ref(signals.value)

const getGradeClass = (grade: string) => {
  return `grade-${grade.toLowerCase()}`
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="cyber-title">🤖 ML 信号分析</h1>
      <div class="filter-group">
        <button 
          :class="['cyber-btn-outline', { active: filter === 'all' }]"
          @click="filter = 'all'"
        >
          全部
        </button>
        <button 
          :class="['cyber-btn-outline', { active: filter === '达标' }]"
          @click="filter = '达标'"
        >
          达标
        </button>
      </div>
    </div>

    <div class="cyber-card">
      <table class="cyber-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>代码</th>
            <th>名称</th>
            <th>行业</th>
            <th>周期</th>
            <th>评分</th>
            <th>收盘价</th>
            <th>目标价</th>
            <th>预期收益</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="signal in signals" :key="signal.code + signal.date">
            <td>{{ signal.date }}</td>
            <td>{{ signal.code }}</td>
            <td>{{ signal.name }}</td>
            <td>{{ signal.industry }}</td>
            <td>{{ signal.period }}</td>
            <td>
              <span :class="['grade-badge', getGradeClass(signal.sr_grade)]">
                {{ signal.sr_grade }}
              </span>
            </td>
            <td>{{ signal.close.toFixed(2) }}</td>
            <td class="positive">{{ signal.target_price.toFixed(2) }}</td>
            <td class="positive">
              +{{ ((signal.target_price / signal.close - 1) * 100).toFixed(1) }}%
            </td>
            <td>
              <span class="cyber-badge success" v-if="signal.verdict === '达标'">达标</span>
              <span class="cyber-badge warning" v-else>{{ signal.verdict }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.filter-group {
  display: flex;
  gap: 12px;
}

.cyber-btn-outline.active {
  background: var(--accent-cyan);
  color: var(--bg-primary);
}

.grade-badge {
  display: inline-block;
  width: 32px;
  height: 32px;
  line-height: 32px;
  text-align: center;
  border-radius: 50%;
  font-weight: 700;
  font-size: 14px;
}

.grade-s { background: rgba(0, 255, 136, 0.2); color: #00ff88; }
.grade-a { background: rgba(0, 212, 255, 0.2); color: #00d4ff; }
.grade-b { background: rgba(255, 170, 0, 0.2); color: #ffaa00; }
.grade-c { background: rgba(255, 102, 0, 0.2); color: #ff6600; }
.grade-d { background: rgba(255, 68, 68, 0.2); color: #ff4444; }
</style>
