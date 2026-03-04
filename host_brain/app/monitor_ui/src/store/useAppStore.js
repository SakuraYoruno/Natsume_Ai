import { create } from 'zustand'

/**
 * 全局应用状态
 * - 30s 自动轮询开关
 * - 全局时间范围过滤（Metrics 页使用）
 */
const useAppStore = create((set) => ({
  // ===== 轮询状态 =====
  pollingEnabled: true,
  pollingInterval: 30, // 秒
  setPollingEnabled: (enabled) => set({ pollingEnabled: enabled }),

  // ===== 全局时间范围 =====
  // 可选值: '1h' | '6h' | '24h' | '7d'
  timeRange: '1h',
  setTimeRange: (range) => set({ timeRange: range }),

  // ===== Trace 页：当前选中的 requestId =====
  selectedRequestId: null,
  setSelectedRequestId: (id) => set({ selectedRequestId: id }),
}))

export default useAppStore
