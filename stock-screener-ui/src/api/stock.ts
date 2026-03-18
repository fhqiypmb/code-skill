/**
 * API 服务层 - 与 Python 后端对接
 */
import axios from 'axios'
import type { AnalysisResult, SignalData } from '@/types/stock'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

/**
 * 获取股票分析结果
 */
export async function getStockAnalysis(code: string): Promise<AnalysisResult> {
  const response = await api.get(`/analysis/${code}`)
  return response.data
}

/**
 * 获取所有选股结果
 */
export async function getScreenerResults(): Promise<any[]> {
  const response = await api.get('/screener/results')
  return response.data
}

/**
 * 获取 ML 信号数据
 */
export async function getMLSignals(): Promise<SignalData[]> {
  const response = await api.get('/ml/signals')
  return response.data
}

/**
 * 获取个股 K 线数据
 */
export async function getKLineData(code: string, period?: string): Promise<any> {
  const response = await api.get(`/kline/${code}`, {
    params: { period },
  })
  return response.data
}

/**
 * 开始选股任务
 */
export async function startScreener(): Promise<{ task_id: string }> {
  const response = await api.post('/screener/start')
  return response.data
}

/**
 * 获取选股进度
 */
export async function getScreenerProgress(taskId: string): Promise<{
  progress: number
  status: string
  results: any[]
}> {
  const response = await api.get(`/screener/progress/${taskId}`)
  return response.data
}
