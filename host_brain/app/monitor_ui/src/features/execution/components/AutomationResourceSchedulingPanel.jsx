import { Cpu, Gauge, ShieldCheck } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { formatDateTime, formatPercent } from '../../../shared/formatters.js'
import {
  buildResourceGateDecisionRowView,
  buildResourceSchedulingView,
  getResourceGroupBarWidth,
} from '../automationResourceModel.js'
import AutomationSection from './AutomationSection.jsx'

function formatCount(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '0'
  return String(number)
}

export default function AutomationResourceSchedulingPanel({ summary, resourceManager }) {
  const {
    pressureStatus,
    pressureTone,
    gateMetricClass,
    managerRuntime,
    managerDecision,
  } = buildResourceSchedulingView(summary, resourceManager)
  const {
    decision,
    cannotRelease,
    canRelease,
    needsConfirm,
  } = managerDecision

  return (
    <AutomationSection
      icon={Gauge}
      title="资源调度解释层"
      description="把整机资源压力、任务资源画像和资源管家最近 gate 决策放在同一屏。"
    >
      {!summary.hasManager ? (
        <NoticeBanner variant="warning">资源管家状态暂不可读，任务画像仍会按注册表展示。</NoticeBanner>
      ) : null}
      {summary.hasManager && summary.deferred > 0 ? (
        <NoticeBanner variant="warning">
          最近资源 gate 已推迟 {summary.deferred} 个后台任务，说明当前系统正在给主链、训练或关键进程让路。
        </NoticeBanner>
      ) : summary.hasManager ? (
        <NoticeBanner variant="success">
          最近资源 gate 未推迟任务；轻量生命 tick、状态巡检和主链可正常推进。
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          {
            label: '资源压力',
            value: pressureStatus,
            hint: `来源 ${summary.source}`,
            icon: Gauge,
            valueClassName: pressureTone,
          },
          {
            label: '内存',
            value: formatPercent(summary.pressure?.memory_percent),
            hint: '整机占用',
            icon: Cpu,
            valueClassName: pressureTone,
          },
          {
            label: '显存',
            value: formatPercent(summary.pressure?.vram_percent),
            hint: `GPU ${formatPercent(summary.pressure?.gpu_percent)}`,
            icon: Cpu,
            valueClassName: pressureTone,
          },
          {
            label: '最近 gate',
            value: `${summary.allowed}/${summary.recentGateDecisions.length}`,
            hint: `放行 / 最近决策，推迟 ${summary.deferred}`,
            icon: ShieldCheck,
            valueClassName: gateMetricClass,
          },
        ].map(({ label, value, hint, icon: Icon, valueClassName }) => (
          <div key={label} className="rounded-xl border border-primary-100/70 bg-white/76 p-3">
            <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
              <span>{label}</span>
              <Icon size={14} className="text-gray-400" />
            </div>
            <div className={`mt-2 text-xl font-bold ${valueClassName}`}>{value}</div>
            <div className="mt-0.5 text-xs text-gray-400">{hint}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.8fr)]">
        <div className="rounded-xl border border-primary-100/70 bg-white/76 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-gray-800">任务资源画像</h3>
              <p className="mt-1 text-xs text-gray-500">调度器会按画像决定高压时保留谁、推迟谁。</p>
            </div>
            <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
              {summary.groups.length} 类
            </span>
          </div>
          <div className="mt-4 space-y-3">
            {summary.groups.length === 0 ? (
              <EmptyStateCard title="暂无资源画像" hint="等待节律任务注册后会自动出现。" className="p-4" />
            ) : summary.groups.map((group) => (
              <div key={group.key} className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className={`rounded border px-2 py-0.5 font-medium ${group.chipClass}`}>{group.label}</span>
                  <span className="text-gray-500">总数 {group.count}</span>
                  <span className="text-gray-400">启用 {group.enabled}</span>
                  {group.llm > 0 ? <span className="text-blue-600">LLM {group.llm}</span> : null}
                  {group.gpu > 0 ? <span className="text-rose-600">GPU {group.gpu}</span> : null}
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className={`h-full rounded-full ${group.barClass}`}
                    style={{ width: getResourceGroupBarWidth(group, summary.groups) }}
                  />
                </div>
                <div className="text-xs leading-5 text-gray-500">{group.description}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-xl border border-primary-100/70 bg-white/76 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-gray-800">资源管家判断</h3>
                <p className="mt-1 text-xs text-gray-500">只展示非破坏性建议；训练、Host、Edge、系统进程默认保护。</p>
              </div>
              <span className={`rounded-full border px-2 py-0.5 text-xs ${managerRuntime.className}`}>
                {managerRuntime.label}
              </span>
            </div>
            <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs leading-6 text-gray-600">
              {decision.summary || '暂无最近一次资源分析摘要。'}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-2 py-2 text-emerald-700">
                <div className="font-bold">{formatCount(canRelease.length)}</div>
                <div>可自动处理</div>
              </div>
              <div className="rounded-lg border border-amber-100 bg-amber-50 px-2 py-2 text-amber-700">
                <div className="font-bold">{formatCount(needsConfirm.length)}</div>
                <div>需确认</div>
              </div>
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-2 py-2 text-slate-600">
                <div className="font-bold">{formatCount(cannotRelease.length)}</div>
                <div>受保护</div>
              </div>
            </div>
            {cannotRelease.length > 0 ? (
              <div className="mt-3 space-y-1.5">
                {cannotRelease.map((item, index) => (
                  <div key={`${item?.pid || index}-${item?.name || 'protected'}`} className="flex min-w-0 items-center gap-2 rounded-lg border border-slate-100 bg-white/80 px-2 py-1.5 text-xs text-gray-500">
                    <span className="truncate font-medium text-gray-700">{item?.name || 'process'}</span>
                    <span className="font-mono text-gray-400">pid {item?.pid ?? '—'}</span>
                    <span className="ml-auto truncate text-slate-500">{item?.why || 'protected'}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-primary-100/70 bg-white/76">
        <div className="grid grid-cols-1 gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500 md:grid-cols-[minmax(0,1fr)_7rem_8rem_7rem_8rem]">
          <span>最近 gate 决策</span>
          <span>画像</span>
          <span>压力</span>
          <span>决策</span>
          <span className="md:text-right">时间</span>
        </div>
        {summary.recentGateDecisions.length === 0 ? (
          <EmptyStateCard title="暂无 gate 决策" hint="调度器运行后会记录资源 gate 的最近判断。" className="p-4" />
        ) : (
          <div className="divide-y divide-gray-100">
            {summary.recentGateDecisions.map((row, index) => {
              const {
                meta,
                decisionText,
                decisionClass,
                pressureText,
              } = buildResourceGateDecisionRowView(row)
              return (
                <div key={`${row?.task_id || 'gate'}-${row?.ts_ms || index}`} className="grid grid-cols-1 gap-2 px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_7rem_8rem_7rem_8rem] md:items-center">
                  <div className="min-w-0">
                    <div className="truncate font-mono font-semibold text-gray-800">{row?.task_id || 'unknown_task'}</div>
                    <div className="truncate text-gray-500">{row?.detail || row?.reason_code || 'resource gate checked'}</div>
                  </div>
                  <div>
                    <span className={`rounded border px-2 py-0.5 ${meta.chipClass}`}>{meta.shortLabel}</span>
                  </div>
                  <div className="text-gray-500">
                    {pressureText}
                    {row?.retry_after_s ? ` · ${row.retry_after_s}s` : ''}
                  </div>
                  <div>
                    <span className={`rounded border px-2 py-0.5 ${decisionClass}`}>{decisionText}</span>
                  </div>
                  <div className="text-gray-400 md:text-right">{formatDateTime(row?.ts_ms)}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </AutomationSection>
  )
}
