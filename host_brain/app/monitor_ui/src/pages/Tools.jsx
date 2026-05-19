import React, { useEffect, useMemo, useState } from 'react'
import { Play, RefreshCw, Search, Wrench, X } from 'lucide-react'
import {
  executeTool,
} from '../api/tools.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import ViewportLazySection from '../components/obs/ViewportLazySection.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { buildToolsOrganSystemModel } from '../models/organSystemModel.js'
import {
  refreshActionPlanStatsQuery,
  refreshPermissionStatsQuery,
  useToolsCatalogQuery,
} from '../queries/toolsQueries.js'
import useAppStore from '../store/useAppStore.js'

const SIDE_EFFECT_STYLES = {
  READ: { bg: 'bg-green-100', text: 'text-green-700', label: '只读' },
  WRITE: { bg: 'bg-orange-100', text: 'text-orange-700', label: '写入' },
}

const AUTH_LEVEL_STYLES = {
  NONE: { bg: 'bg-gray-100', text: 'text-gray-600', label: '无需确认' },
  APPROVAL: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: '审批确认' },
  CONFIRM: { bg: 'bg-red-100', text: 'text-red-700', label: '高风险确认' },
}

const ToolsOperationsSection = React.lazy(() => import('../components/tools/ToolsOperationsSection.jsx'))

function ToolCard({ tool, selected, onClick }) {
  const sideEffect = SIDE_EFFECT_STYLES[tool.side_effect_level] || SIDE_EFFECT_STYLES.READ
  const auth = AUTH_LEVEL_STYLES[tool.auth_level] || AUTH_LEVEL_STYLES.NONE

  return (
    <button
      type="button"
      onClick={() => onClick(tool)}
      className={`grid w-full grid-cols-1 gap-2 px-3 py-2.5 text-left transition-colors md:grid-cols-[minmax(0,1fr)_8rem_7.5rem] md:items-center ${
        selected
          ? 'bg-blue-50'
          : 'bg-white hover:bg-gray-50'
      }`}
    >
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-gray-800">{tool.name}</div>
        <div className="mt-0.5 truncate font-mono text-xs text-gray-500">{tool.id}</div>
        <div className="mt-1 line-clamp-1 text-xs text-gray-500">{tool.desc || '暂无描述'}</div>
      </div>
      <div className="flex flex-wrap gap-1 md:justify-start">
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${sideEffect.bg} ${sideEffect.text}`}>
          {sideEffect.label}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-xs ${auth.bg} ${auth.text}`}>{auth.label}</span>
      </div>
      <div className="flex min-w-0 flex-wrap gap-1 md:justify-end">
        {tool.domain_tags?.length > 0 ? (
          tool.domain_tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
              {tag}
            </span>
          ))
        ) : (
          <span className="text-xs text-gray-400">无标签</span>
        )}
      </div>
    </button>
  )
}

function ExecutePanel({ tool, onClose }) {
  const [form, setForm] = useState({
    intent_id: `intent::manual::${Date.now()}`,
    session_id: 'manual_session',
    args_json: '{}',
    approval_token_ref: '',
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const handleExecute = async () => {
    setResult(null)
    let args = {}
    try {
      args = JSON.parse(form.args_json || '{}')
    } catch (error) {
      setResult({ ok: false, error: `参数 JSON 不合法：${error.message}` })
      return
    }

    if (tool.side_effect_level === 'WRITE' && !form.approval_token_ref.trim()) {
      setResult({ ok: false, error: '写入工具必须填写审批令牌引用。' })
      return
    }

    setLoading(true)
    try {
      const data = await executeTool({
        tool_id: tool.id,
        intent_id: form.intent_id,
        session_id: form.session_id,
        args,
        approval_token_ref: form.approval_token_ref || undefined,
      })
      setResult({ ok: true, data })
    } catch (error) {
      setResult({ ok: false, error: error.message || '执行失败' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">执行工具：{tool.name}</h3>
        <button onClick={onClose} className="inline-flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-50 hover:text-gray-600">
          <X size={15} />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-gray-500">意图 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.intent_id}
            onChange={(event) => setForm((prev) => ({ ...prev, intent_id: event.target.value }))}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-500">会话 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.session_id}
            onChange={(event) => setForm((prev) => ({ ...prev, session_id: event.target.value }))}
          />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-gray-500">参数（JSON）</label>
        <textarea
          className="h-20 w-full resize-none rounded border border-gray-300 px-2 py-1.5 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.args_json}
          onChange={(event) => setForm((prev) => ({ ...prev, args_json: event.target.value }))}
        />
      </div>

      {tool.side_effect_level === 'WRITE' ? (
        <div>
          <label className="mb-1 block text-xs text-gray-500">
            审批令牌引用 <span className="text-orange-500">（写入工具必填）</span>
          </label>
          <input
            className="w-full rounded border border-orange-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-orange-300"
            value={form.approval_token_ref}
            onChange={(event) => setForm((prev) => ({ ...prev, approval_token_ref: event.target.value }))}
            placeholder="approval_token_ref"
          />
        </div>
      ) : null}

      <button
        onClick={handleExecute}
        disabled={loading}
        className="inline-flex w-full items-center justify-center gap-1.5 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        <Play size={14} />
        {loading ? '执行中…' : '执行'}
      </button>

      {result ? (
        result.ok ? (
          <NoticeBanner variant="success" title="执行完成">
            <pre className="mt-1 whitespace-pre-wrap break-all rounded-lg border border-green-200 bg-white/70 p-3 text-xs font-mono text-green-900">
              {JSON.stringify(result.data, null, 2)}
            </pre>
          </NoticeBanner>
        ) : (
          <NoticeBanner variant="error" title="执行失败">
            {result.error}
          </NoticeBanner>
        )
      ) : null}
    </div>
  )
}

export default function Tools() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [selectedTool, setSelectedTool] = useState(null)
  const [executeTarget, setExecuteTarget] = useState(null)
  const [showRuntimeContext, setShowRuntimeContext] = useState(false)
  const [filter, setFilter] = useState('ALL')
  const [query, setQuery] = useState('')
  const {
    health,
    tools,
    catalogError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch: refetchCatalog,
  } = useToolsCatalogQuery({
    query,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    if (!selectedTool) return
    const nextSelectedTool = tools.find((tool) => tool.id === selectedTool.id)
    if (!nextSelectedTool) {
      setSelectedTool(null)
      setExecuteTarget(null)
      return
    }
    if (nextSelectedTool !== selectedTool) {
      setSelectedTool(nextSelectedTool)
    }
    if (executeTarget?.id === nextSelectedTool.id && executeTarget !== nextSelectedTool) {
      setExecuteTarget(nextSelectedTool)
    }
  }, [executeTarget, selectedTool, tools])

  const filteredTools = useMemo(
    () =>
      tools.filter((tool) => {
        if (filter === 'READ' && tool.side_effect_level !== 'READ') return false
        if (filter === 'WRITE' && tool.side_effect_level !== 'WRITE') return false
        return true
      }),
    [tools, filter],
  )

  const readCount = useMemo(() => tools.filter((tool) => tool.side_effect_level === 'READ').length, [tools])
  const writeCount = useMemo(() => tools.filter((tool) => tool.side_effect_level === 'WRITE').length, [tools])
  const toolsLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: catalogError,
    hasData: Boolean(health || tools.length > 0),
    hasSamples: Number(health?.catalog_size ?? 0) > 0 || tools.length > 0,
    available: Boolean(health || tools.length > 0),
    stale,
  })
  const toolsOrganModel = useMemo(() => buildToolsOrganSystemModel({
    loadState: toolsLoadState,
    updatedAt: lastUpdatedAt,
    healthStatus: health?.status,
    catalogSize: health?.catalog_size ?? tools.length,
    readCount,
    writeCount,
    filter,
    query,
    runtimeSummary: runtimeSnapshot?.runtime_summary?.headline,
  }), [
    filter,
    health?.catalog_size,
    health?.status,
    lastUpdatedAt,
    query,
    readCount,
    runtimeSnapshot?.runtime_summary?.headline,
    tools.length,
    toolsLoadState,
    writeCount,
  ])

  const handleRefresh = async () => {
    await Promise.allSettled([
      refetchCatalog(),
      refreshPermissionStatsQuery({ windowMinutes: 1440 }),
      refreshActionPlanStatsQuery({ windowMinutes: 1440 }),
      reloadRuntime(),
    ])
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">工具管理</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看工具目录、权限等级、健康状态与手动执行入口。</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={loading || refreshing || runtimeLoading}
          className="btn-secondary"
        >
          <RefreshCw size={14} className={loading || refreshing || runtimeLoading ? 'animate-spin' : ''} />
          {loading || refreshing || runtimeLoading
            ? '刷新中…'
            : '刷新'}
        </button>
      </div>

      <PageHero
        icon={Wrench}
        eyebrow="工具与权限"
        title={health?.status === 'OK' ? '工具引擎健康' : '工具引擎待关注'}
        description="可在这里检查工具 catalog、读写权限分布、自定义 provider 状态，以及人工执行入口。"
        gradientClass="from-emerald-50 via-white to-cyan-50"
        metaItems={[
          `工具总数：${health?.catalog_size ?? tools.length}`,
          `只读工具：${readCount}`,
          `写入工具：${writeCount}`,
          `当前筛选：${filter}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={toolsLoadState}
            sampleSize={health?.catalog_size ?? tools.length}
            sampleLabel="工具"
            updatedAt={lastUpdatedAt}
            source="tool catalog"
            definition="工具页把工具目录、权限拦截与执行计划拆成独立观测面；搜索为空结果不会被误判成系统无样本。"
          />
        )}
      />

      {catalogError ? <NoticeBanner variant="error">{catalogError}</NoticeBanner> : null}

      {!catalogError && !loading && !refreshing && toolsLoadState === LOAD_STATES.UNAVAILABLE ? (
        <NoticeBanner variant="warning">工具目录接口当前未返回可用快照，页面会保留中性占位。</NoticeBanner>
      ) : null}

      {!catalogError && !loading && !refreshing && toolsLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前系统还没有可观测的工具目录样本。</NoticeBanner>
      ) : null}

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowRuntimeContext(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex min-w-0 items-center gap-2">
            <Wrench size={14} className="text-gray-400" />
            工具器官与运行时快照
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showRuntimeContext ? '已展开' : '展开'}
          </span>
        </summary>
        {showRuntimeContext ? (
          <div className="space-y-4 border-t border-gray-100 p-4">
            <OrganSystemOverview model={toolsOrganModel} />
            <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
          </div>
        ) : null}
      </details>

      {health ? (
        <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
          <div className="grid grid-cols-2 divide-x divide-y divide-gray-100 md:grid-cols-4 md:divide-y-0">
          {[
            { label: '引擎状态', value: health.status, color: health.status === 'OK' ? 'text-green-600' : 'text-yellow-600' },
            { label: '工具总数', value: health.catalog_size, color: 'text-gray-800' },
            { label: '只读工具', value: health.read_tools, color: 'text-green-600' },
            { label: '写入工具', value: health.write_tools, color: 'text-orange-600' },
          ].map(({ label, value, color }) => (
            <div key={label} className="px-4 py-3">
              <div className={`text-xl font-bold ${color}`}>{value}</div>
              <div className="mt-0.5 text-xs text-gray-500">{label}</div>
            </div>
          ))}
          </div>
        </div>
      ) : null}

      <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="搜索工具名或描述..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="flex shrink-0 overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1 text-sm">
          {[
            { key: 'ALL', label: `全部 (${tools.length})` },
            { key: 'READ', label: `只读（${readCount}）` },
            { key: 'WRITE', label: `写入（${writeCount}）` },
          ].map(({ key, label }) => (
            <button
              type="button"
              key={key}
              onClick={() => setFilter(key)}
              className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
                filter === key ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
              }`}
            >
              {label}
            </button>
          ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
        <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
          <div className="grid grid-cols-1 gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500 md:grid-cols-[minmax(0,1fr)_8rem_7.5rem]">
            <span>工具</span>
            <span>权限</span>
            <span className="md:text-right">标签</span>
          </div>
          <div className="max-h-[620px] divide-y divide-gray-100 overflow-y-auto">
          {loading && filteredTools.length === 0 ? (
            <NoticeBanner variant="info" title="加载中">
              正在同步工具目录…
            </NoticeBanner>
          ) : filteredTools.length === 0 ? (
            <EmptyStateCard title="暂无工具" hint="调整筛选条件后再试试。" className="p-6" />
          ) : (
            filteredTools.map((tool) => (
              <ToolCard
                key={tool.id}
                tool={tool}
                selected={selectedTool?.id === tool.id}
                onClick={(value) => {
                  setSelectedTool(value)
                  setExecuteTarget(null)
                }}
              />
            ))
          )}
          </div>
        </div>

        <div className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          {selectedTool ? (
            <>
              <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-base font-semibold text-gray-800">{selectedTool.name}</h2>
                    <div className="mt-0.5 font-mono text-xs text-gray-400">{selectedTool.id}</div>
                  </div>
                  <div className="flex gap-2">
                    <span
                      className={`rounded px-2 py-1 text-xs font-medium ${
                        (SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || SIDE_EFFECT_STYLES.READ).bg
                      } ${(SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || SIDE_EFFECT_STYLES.READ).text}`}
                    >
                      {(SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || SIDE_EFFECT_STYLES.READ).label}
                    </span>
                    <span
                      className={`rounded px-2 py-1 text-xs ${
                        (AUTH_LEVEL_STYLES[selectedTool.auth_level] || AUTH_LEVEL_STYLES.NONE).bg
                      } ${(AUTH_LEVEL_STYLES[selectedTool.auth_level] || AUTH_LEVEL_STYLES.NONE).text}`}
                    >
                      {(AUTH_LEVEL_STYLES[selectedTool.auth_level] || AUTH_LEVEL_STYLES.NONE).label}
                    </span>
                  </div>
                </div>

                <p className="text-sm text-gray-600">{selectedTool.desc || '暂无描述'}</p>

                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded bg-gray-50 p-2">
                    <div className="mb-0.5 text-gray-400">版本</div>
                    <div className="font-mono text-gray-700">{selectedTool.version || '—'}</div>
                  </div>
                  <div className="rounded bg-gray-50 p-2">
                    <div className="mb-0.5 text-gray-400">Schema 摘要</div>
                    <div className="truncate font-mono text-gray-700">
                      {selectedTool.schema_hash ? `${selectedTool.schema_hash.slice(0, 16)}…` : '—'}
                    </div>
                  </div>
                </div>

                {selectedTool.domain_tags?.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedTool.domain_tags.map((tag) => (
                      <span key={tag} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600">
                        {tag}
                      </span>
                    ))}
                  </div>
                ) : null}

                <button
                  onClick={() => setExecuteTarget(executeTarget?.id === selectedTool.id ? null : selectedTool)}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded border border-blue-300 px-4 py-2 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-50"
                >
                  <Play size={14} />
                  {executeTarget?.id === selectedTool.id ? '收起执行面板' : '执行该工具'}
                </button>
              </div>

              {executeTarget?.id === selectedTool.id ? (
                <ExecutePanel tool={selectedTool} onClose={() => setExecuteTarget(null)} />
              ) : null}
            </>
          ) : (
            <EmptyStateCard title="请选择一个工具" hint="左侧点击任意工具后可查看详情和执行入口。" />
          )}
        </div>
      </div>

      <ViewportLazySection
        rootMargin="0px 0px"
        placeholder={(
          <NoticeBanner variant="info" title="按需加载中">
            自定义 provider 与工具侧深层统计会在滚动接近后再展开。
          </NoticeBanner>
        )}
      >
        <React.Suspense
          fallback={(
            <NoticeBanner variant="info" title="按需加载中">
              正在装配工具运维深层分区…
            </NoticeBanner>
          )}
        >
          <ToolsOperationsSection
            pollingEnabled={pollingEnabled}
            pollingInterval={pollingInterval}
            pollingTick={pollingTick}
            permissionWindowMinutes={1440}
            actionPlanWindowMinutes={1440}
          />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
