/**
 * 股票分析相关类型定义
 */

// K 线数据
export interface KLineBar {
  day: string
  open: string
  high: string
  low: string
  close: string
  volume: string
}

// 报价信息
export interface QuoteInfo {
  name: string
  price: number
  change_pct: number
  high: number
  low: number
  open: number
  pre_close: number
  volume: number
  amount: number
  turnover_rate: number
}

// 资金流向
export interface CapitalFlow {
  main_net_in: number
  super_net_in: number
  big_net_in: number
  flow_ratio: number
}

// 技术目标
export interface TechnicalTarget {
  current_price: number
  target_price: number
  stop_loss: number
  expected_gain_pct: number
  stop_loss_pct: number
  space_ok: boolean
  method_targets: Record<string, number>
  atr: number
  ma20: number
}

// 趋势强度
export interface TrendStrength {
  score: number
  level: string
  ma_align: boolean
  vol_price_ok: boolean
  macd_positive: boolean
  macd_strength: number
  detail: Record<string, number>
}

// 市场位置
export interface MarketPosition {
  score: number
  level: string
  relative_strength: number
  rs_score: number
  vol_ratio: number
  vr_score: number
  benchmark: string
  benchmark_name: string
}

// 成功率评分
export interface SuccessRate {
  score: number
  grade: string
  dim_breakout: number
  dim_momentum: number
  dim_rs: number
  dim_capital: number
  dim_rr: number
  dim_reach_prob: number
}

// 分析结果
export interface AnalysisResult {
  code: string
  name: string
  industry: string
  concepts: string[]
  quote: QuoteInfo
  capital: CapitalFlow
  technical: TechnicalTarget
  trend: TrendStrength
  market_pos: MarketPosition
  success_rate: SuccessRate
  capital_confirmed: boolean
  verdict: string
  signal_type: string
}

// ML 信号数据
export interface SignalData {
  date: string
  code: string
  name: string
  period: string
  signal_type: string
  close: number
  verdict: string
  industry: string
  sr_score: number
  sr_grade: string
  target_price: number
  stop_loss: number
  reached_target?: boolean | null
  actual_return?: number | null
}

// 选股结果
export interface ScreenerResult {
  code: string
  name: string
  signal_date: string
  period: string
  signal_type: string
  analysis?: AnalysisResult
}
