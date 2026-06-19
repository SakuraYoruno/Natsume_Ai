import React from 'react'
import { Archive, Cpu, Database, Minimize2 } from 'lucide-react'
import EmptyStateCard from '../EmptyStateCard.jsx'
import InsightSection from './InsightSection.jsx'
import {
  useContextCompressorStatsQuery,
  useEpisodicStoreStatsQuery,
} from '../../features/observability/diagnosticQueries.js'
import {
  formatTime,
  SectionQueryMeta,
  StatCard,
} from './insightsGroupShared.jsx'

function normalizeCardStatus(rate, status) {
  const raw = String(status || '').toUpperCase()
  if (raw === 'UP' || raw === 'OK' || raw === 'HEALTHY') return 'up'
  if (raw === 'DEGRADED' || raw === 'WARN') return 'degraded'
  if (raw === 'DOWN' || raw === 'FAIL' || raw === 'FAILED' || raw === 'ERROR') return 'down'
  if (rate == null) return 'unknown'
  if (rate >= 90) return 'up'
  if (rate >= 70) return 'degraded'
  return 'down'
}

function statusColor(rate, status) {
  const normalized = normalizeCardStatus(rate, status)
  if (normalized === 'up') return 'text-green-600'
  if (normalized === 'degraded') return 'text-orange-500'
  if (normalized === 'down') return 'text-red-500'
  return 'text-gray-400'
}

function statusBg(rate, status) {
  const normalized = normalizeCardStatus(rate, status)
  if (normalized === 'up') return 'bg-green-50 border-green-200'
  if (normalized === 'degraded') return 'bg-orange-50 border-orange-200'
  if (normalized === 'down') return 'bg-red-50 border-red-200'
  return 'bg-gray-100'
}

function subsystemSummary(item) {
  if ((item?.total ?? 0) <= 0) return '近窗口无调用'
  if (item?.detail) return item.detail
  const parts = [`${item?.success ?? 0}/${item?.total ?? 0} 正常`]
  if ((item?.degraded ?? 0) > 0) parts.push(`${item.degraded} 降级`)
  if ((item?.failed ?? 0) > 0) parts.push(`${item.failed} 故障`)
  if ((item?.blocked ?? 0) > 0) parts.push(`拦截 ${item.blocked}`)
  return parts.join(' · ')
}


function ContextCompressorStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useContextCompressorStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={Minimize2} title="上下文压缩效率统计" color="text-teal-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_compressions', 'summary_generated_count', 'strategy_distribution']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="总压缩次数" value={data?.total_compressions ?? '—'} color="text-gray-700" />
        <StatCard label="活跃压缩" value={data?.active_count ?? '—'} color="text-green-600" />
        <StatCard label="平均压缩率" value={data?.avg_compression_ratio != null ? `${(data.avg_compression_ratio * 100).toFixed(1)}%` : '—'} color="text-teal-600" />
        <StatCard label="平均节省词元" value={data?.avg_token_saving != null ? data.avg_token_saving.toFixed(1) : '—'} color="text-blue-600" />
      </div>
      <div className="mb-4 grid grid-cols-3 gap-4">
        <StatCard label="总节省词元" value={data?.total_tokens_saved ?? '—'} color="text-teal-700" />
        <StatCard label="摘要生成次数" value={data?.summary_generated_count ?? '—'} color="text-indigo-600" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.strategy_distribution ?? []).length > 0 && (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">压缩策略分布</div>
          <div className="space-y-2">
            {(data?.strategy_distribution ?? []).map((item) => (
              <div key={item.strategy} className="flex items-center gap-2">
                <div className="w-24 truncate text-xs font-mono text-gray-600">{item.strategy}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-teal-400" style={{ width: `${item.pct}%` }} />
                </div>
                <div className="w-10 text-right text-xs text-gray-500">{item.pct}%</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function EpisodicStoreStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useEpisodicStoreStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={Archive} title="情景记忆写入统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_attempts', 'written_count', 'memory_guard_blocked_count']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="总尝试次数" value={data?.total_attempts ?? '—'} color="text-gray-700" />
        <StatCard label="写入成功" value={data?.written_count ?? '—'} color="text-green-600" />
        <StatCard label="跳过次数" value={data?.skipped_count ?? '—'} color="text-gray-500" />
        <StatCard label="写入率" value={data?.write_rate != null ? `${data.write_rate}%` : '—'} color={data?.write_rate < 50 ? 'text-orange-500' : 'text-green-600'} />
      </div>
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="记忆守卫拦截" value={data?.memory_guard_blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="梦境拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
        <StatCard label="仅叙事拦截" value={data?.narrative_only_blocked_count ?? '—'} color="text-orange-500" />
        <StatCard label="蒸馏资格" value={data?.distill_eligible_count ?? '—'} color="text-blue-600" />
      </div>
    </InsightSection>
  )
}

export default function InsightsMemoryGroup({
  windowMinutes,
  subsysData,
  memoryData,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }

  return (
    <>
      <InsightSection icon={Cpu} title="器官健康矩阵" color="text-purple-600">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {(subsysData?.subsystems ?? []).map((item) => (
            <div key={item.subsystem} className={`card border p-3 ${statusBg(item.success_rate, item.status)}`}>
              <div className="mb-2 truncate text-xs font-medium text-gray-700" title={item.display_name}>
                {item.display_name}
              </div>
              <div className={`text-xl font-bold ${statusColor(item.success_rate, item.status)}`}>
                {item.success_rate != null ? `${item.success_rate}%` : '—'}
              </div>
              <div className="mt-1 text-xs text-gray-400">{subsystemSummary(item)}</div>
              {item.p95_ms != null && <div className="text-xs text-gray-400">P95 {item.p95_ms}ms</div>}
              {item.last_ts && (
                <div className="mt-1 truncate text-xs text-gray-300" title={formatTime(item.last_ts)}>
                  {formatTime(item.last_ts)}
                </div>
              )}
            </div>
          ))}
          {(subsysData?.subsystems ?? []).length === 0 && (
            <EmptyStateCard title="暂无器官数据" hint="当前窗口未采集到器官健康样本。" className="col-span-full" />
          )}
        </div>
      </InsightSection>

      <InsightSection icon={Database} title="记忆器官统计" color="text-teal-600">
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="情景写入量" value={memoryData?.total_writes ?? '—'} color="text-teal-600" />
          <StatCard
            label="记忆守卫拒绝率"
            value={memoryData?.guard_reject_rate != null ? `${memoryData.guard_reject_rate}%` : '—'}
            color={memoryData?.guard_reject_rate > 30 ? 'text-red-500' : (memoryData?.guard_reject_rate > 10 ? 'text-orange-500' : 'text-green-600')}
          />
          <StatCard label="上下文压缩率" value={memoryData?.compress_rate != null ? `${memoryData.compress_rate}%` : '—'} color="text-blue-600" />
          <StatCard label="长期蒸馏次数" value={memoryData?.distill_count ?? '—'} color="text-purple-600" />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {(memoryData?.subsystems ?? []).map((item) => (
            <div key={item.subsystem} className={`card border p-3 ${statusBg(item.success_rate)}`}>
              <div className="mb-2 truncate text-xs font-medium text-gray-700" title={item.display_name}>
                {item.display_name}
              </div>
              <div className={`text-xl font-bold ${statusColor(item.success_rate)}`}>
                {item.success_rate != null ? `${item.success_rate}%` : '—'}
              </div>
              <div className="mt-1 text-xs text-gray-400">
                {item.total > 0 ? `${item.success}/${item.total} 成功` : '无数据'}
              </div>
              {item.extra?.reject_rate != null && <div className="mt-0.5 text-xs text-red-500">拒绝率 {item.extra.reject_rate}%</div>}
              {item.extra?.avg_compress_rate_pct != null && <div className="mt-0.5 text-xs text-blue-500">压缩率 {item.extra.avg_compress_rate_pct}%</div>}
              {item.extra?.distill_count != null && <div className="mt-0.5 text-xs text-purple-500">蒸馏 {item.extra.distill_count} 次</div>}
              {item.last_ts && (
                <div className="mt-1 truncate text-xs text-gray-300" title={formatTime(item.last_ts)}>
                  {formatTime(item.last_ts)}
                </div>
              )}
            </div>
          ))}
          {(memoryData?.subsystems ?? []).length === 0 && (
            <EmptyStateCard title="暂无记忆子系统数据" hint="等待记忆链路产出统计后展示。" icon={Database} className="col-span-full p-6" />
          )}
        </div>
      </InsightSection>

      <EpisodicStoreStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <ContextCompressorStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
