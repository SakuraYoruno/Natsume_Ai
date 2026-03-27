import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Search, Database, BookOpen, Brain, Archive, Filter } from 'lucide-react'
import { fetchMemoryStats, fetchEpisodicStoreStats, fetchDistillationStats, fetchCanonDocuments, fetchRecallPackStats, fetchContextCompressorStats } from '../api/obs.js'
import BarChart from '../components/charts/BarChart.jsx'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 }, { label: '6h', value: 360 },
  { label: '24h', value: 1440 }, { label: '7d', value: 10080 },
]

function StatCard({ label, value, sub, color = 'text-gray-800' }) {
  return (
    <div className="card p-4 text-center">
      <div className={`text-2xl font-bold ${color}`}>{value ?? '—'}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function MemoryBrowser() {
  const pollingEnabled = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [memData, setMemData] = useState(null)
  const [episodicData, setEpisodicData] = useState(null)
  const [distillData, setDistillData] = useState(null)
  const [canonDocs, setCanonDocs] = useState(null)
  const [recallData, setRecallData] = useState(null)
  const [compressData, setCompressData] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [mem, ep, dist, canon, recall, compress] = await Promise.all([
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchEpisodicStoreStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchDistillationStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchCanonDocuments().catch(() => null),
        fetchRecallPackStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchContextCompressorStats({ window_minutes: windowMinutes }).catch(() => null),
      ])
      setMemData(mem); setEpisodicData(ep); setDistillData(dist)
      setCanonDocs(canon); setRecallData(recall); setCompressData(compress)
    } catch {} finally { setLoading(false) }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const subsystems = memData?.subsystems || []
  const docs = canonDocs?.documents || []
  const slots = recallData?.slots || []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">记忆浏览器</h1>
          <p className="text-sm text-gray-500">情景记忆 · 蒸馏历史 · Canon 文档 · RecallPack 预算</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="text-xs border rounded px-2 py-1.5" value={windowMinutes} onChange={e => setWindowMinutes(Number(e.target.value))}>
            {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      {/* Memory 子系统统计 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {subsystems.map(s => (
          <StatCard key={s.subsystem} label={s.display_name}
            value={s.success_rate != null ? `${s.success_rate}%` : '—'}
            sub={`${s.total} 次操作`}
            color={s.success_rate >= 90 ? 'text-green-600' : s.success_rate >= 70 ? 'text-orange-500' : 'text-red-500'} />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* RecallPack 槽位预算 */}
        {slots.length > 0 && (
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-3"><Database size={14} className="inline mr-1" />RecallPack Token 预算</h2>
            <div className="space-y-2">
              {slots.map(s => (
                <div key={s.slot} className="flex items-center gap-2">
                  <span className="text-xs text-gray-600 w-32 shrink-0 truncate">{s.slot}</span>
                  <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full bg-primary rounded-full" style={{ width: `${Math.min(100, s.avg_tokens / 8)}%` }} />
                  </div>
                  <span className="text-xs text-gray-500 w-16 text-right">{s.avg_tokens?.toFixed(0)} tok</span>
                </div>
              ))}
            </div>
            {recallData && (
              <div className="flex gap-4 mt-3 pt-3 border-t text-xs text-gray-500">
                <span>总 Pack: {recallData.total_packs}</span>
                <span>截断率: {recallData.truncation_rate ?? '—'}%</span>
                <span>冲突率: {recallData.conflict_rate ?? '—'}%</span>
              </div>
            )}
          </div>
        )}

        {/* Canon 文档树 */}
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3"><BookOpen size={14} className="inline mr-1" />Canon 文档 (SSOT)</h2>
          {docs.length > 0 ? (
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {docs.map(d => (
                <div key={d.doc_id} className="flex items-center justify-between text-xs bg-gray-50 rounded px-3 py-1.5">
                  <span className="font-mono text-gray-700 truncate flex-1">{d.doc_id}</span>
                  <span className="text-gray-400 shrink-0 ml-2">v{d.version}</span>
                  <span className={`ml-2 px-1.5 py-0.5 rounded text-[10px] ${d.status === 'ACTIVE' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>{d.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-gray-400 py-6 text-xs">暂无 Canon 文档</div>
          )}
        </div>

        {/* 上下文压缩统计 */}
        {compressData && (
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-3"><Archive size={14} className="inline mr-1" />上下文压缩</h2>
            <div className="grid grid-cols-3 gap-3 text-center text-xs">
              <div><div className="text-lg font-bold text-blue-600">{compressData.total_compressions ?? 0}</div><div className="text-gray-500">压缩次数</div></div>
              <div><div className="text-lg font-bold text-green-600">{compressData.avg_compress_rate ?? '—'}%</div><div className="text-gray-500">平均压缩率</div></div>
              <div><div className="text-lg font-bold text-purple-600">{compressData.total_tokens_saved ?? 0}</div><div className="text-gray-500">节省 Token</div></div>
            </div>
          </div>
        )}

        {/* 蒸馏统计 */}
        {distillData && (
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-3"><Brain size={14} className="inline mr-1" />长期蒸馏</h2>
            <div className="grid grid-cols-3 gap-3 text-center text-xs">
              <div><div className="text-lg font-bold text-indigo-600">{distillData.total_cycles ?? 0}</div><div className="text-gray-500">蒸馏循环</div></div>
              <div><div className="text-lg font-bold text-green-600">{distillData.success_rate ?? '—'}%</div><div className="text-gray-500">成功率</div></div>
              <div><div className="text-lg font-bold text-gray-600">{distillData.total_proposals ?? 0}</div><div className="text-gray-500">提案数</div></div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
