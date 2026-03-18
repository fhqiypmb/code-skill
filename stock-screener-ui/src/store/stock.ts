/**
 * 股票数据 Pinia Store
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { AnalysisResult, SignalData } from '@/types/stock'
import * as stockApi from '@/api/stock'

export const useStockStore = defineStore('stock', () => {
  // 当前选中的股票
  const selectedStock = ref<string | null>(null)
  
  // 分析结果缓存
  const analysisResults = ref<Map<string, AnalysisResult>>(new Map())
  
  // ML 信号数据
  const mlSignals = ref<SignalData[]>([])
  
  // 选股结果
  const screenerResults = ref<any[]>([])
  
  // 加载中状态
  const loading = ref(false)
  
  // 错误信息
  const error = ref<string | null>(null)

  // 获取成功率等级颜色
  const getGradeColor = computed(() => {
    return (grade: string) => {
      const colors: Record<string, string> = {
        'S': '#00ff88',
        'A': '#00d4ff',
        'B': '#ffaa00',
        'C': '#ff6600',
        'D': '#ff3333',
      }
      return colors[grade] || '#888888'
    }
  })

  // 获取达标状态颜色
  const getVerdictColor = computed(() => {
    return (verdict: string) => {
      const colors: Record<string, string> = {
        '达标': '#00ff88',
        '空间不足': '#ffaa00',
        '趋势偏弱': '#ff6600',
      }
      return colors[verdict] || '#888888'
    }
  })

  // 获取股票分析
  async function fetchAnalysis(code: string) {
    if (analysisResults.value.has(code)) {
      return analysisResults.value.get(code)!
    }
    
    loading.value = true
    error.value = null
    
    try {
      const result = await stockApi.getStockAnalysis(code)
      analysisResults.value.set(code, result)
      selectedStock.value = code
      return result
    } catch (e: any) {
      error.value = e.message || '获取分析失败'
      throw e
    } finally {
      loading.value = false
    }
  }

  // 获取 ML 信号
  async function fetchMLSignals() {
    loading.value = true
    error.value = null
    
    try {
      const signals = await stockApi.getMLSignals()
      mlSignals.value = signals
      return signals
    } catch (e: any) {
      error.value = e.message || '获取信号失败'
      throw e
    } finally {
      loading.value = false
    }
  }

  // 获取选股结果
  async function fetchScreenerResults() {
    loading.value = true
    error.value = null
    
    try {
      const results = await stockApi.getScreenerResults()
      screenerResults.value = results
      return results
    } catch (e: any) {
      error.value = e.message || '获取结果失败'
      throw e
    } finally {
      loading.value = false
    }
  }

  // 清除缓存
  function clearCache() {
    analysisResults.value.clear()
    selectedStock.value = null
  }

  return {
    // State
    selectedStock,
    analysisResults,
    mlSignals,
    screenerResults,
    loading,
    error,
    
    // Computed
    getGradeColor,
    getVerdictColor,
    
    // Actions
    fetchAnalysis,
    fetchMLSignals,
    fetchScreenerResults,
    clearCache,
  }
})
