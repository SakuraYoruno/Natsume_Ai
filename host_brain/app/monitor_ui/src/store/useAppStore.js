import { create } from 'zustand'

/**
 * 全局应用状态
 * - 自动轮询开关 + 间隔
 * - 全局时间范围过滤（Metrics 页使用）
 */
const useAppStore = create((set) => ({
  // ===== 轮询状态 =====
  pollingEnabled: true,
  pollingInterval: 30, // 秒
  pollingTick: 0,
  pageVisible: true,
  setPollingEnabled:  (enabled)  => set({ pollingEnabled: enabled }),
  setPollingInterval: (interval) => set({ pollingInterval: interval }),
  bumpPollingTick: () => set((state) => ({ pollingTick: state.pollingTick + 1 })),
  setPageVisible: (visible) => set({ pageVisible: visible }),

  // ===== 全局时间范围 =====
  // 可选值: '1h' | '6h' | '24h' | '7d'
  timeRange: '1h',
  setTimeRange: (range) => set({ timeRange: range }),

  // ===== Trace 页：当前选中的 requestId =====
  selectedRequestId: null,
  setSelectedRequestId: (id) => set({ selectedRequestId: id }),
}))

export default useAppStore
