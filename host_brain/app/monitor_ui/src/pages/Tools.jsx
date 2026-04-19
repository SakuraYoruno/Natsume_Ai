import React, { useEffect, useMemo, useState } from 'react'
import { Wrench } from 'lucide-react'
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
      onClick={() => onClick(tool)}
      className={`w-full rounded-lg border p-3 text-left transition-all ${
        selected
          ? 'border-blue-400 bg-blue-50 shadow-sm'
          : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-gray-800">{tool.name}</div>
          <div className="mt-0.5 truncate text-xs text-gray-500">{tool.id}</div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${sideEffect.bg} ${sideEffect.text}`}>
            {sideEffect.label}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs ${auth.bg} ${auth.text}`}>{auth.label}</span>
        </div>
      </div>
      <div className="mt-1.5 line-clamp-2 text-xs text-gray-500">{tool.desc || '暂无描述'}</div>
      {tool.domain_tags?.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {tool.domain_tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
              {tag}
            </span>
          ))}
        </div>
      )}
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
        <button onClick={onClose} className="text-lg leading-none text-gray-400 hover:text-gray-600">
          ×
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
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
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
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
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

      <OrganSystemOverview model={toolsOrganModel} />

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      {health ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: '引擎状态', value: health.status, color: health.status === 'OK' ? 'text-green-600' : 'text-yellow-600' },
            { label: '工具总数', value: health.catalog_size, color: 'text-gray-800' },
            { label: '只读工具', value: health.read_tools, color: 'text-green-600' },
            { label: '写入工具', value: health.write_tools, color: 'text-orange-600' },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-lg border border-gray-200 bg-white p-3 text-center">
              <div className={`text-xl font-bold ${color}`}>{value}</div>
              <div className="mt-0.5 text-xs text-gray-500">{label}</div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex items-center gap-3">
        <input
          className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          placeholder="搜索工具名或描述…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="flex overflow-hidden rounded border border-gray-300 text-sm">
          {[
            { key: 'ALL', label: `全部 (${tools.length})` },
            { key: 'READ', label: `只读（${readCount}）` },
            { key: 'WRITE', label: `写入（${writeCount}）` },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-3 py-1.5 transition-colors ${
                filter === key ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="space-y-2 overflow-y-auto pr-1 md:col-span-1 md:max-h-[600px]">
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

        <div className="space-y-4 md:col-span-2">
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
                  className="w-full rounded border border-blue-300 px-4 py-2 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-50"
                >
                  {executeTarget?.id === selectedTool.id ? '收起执行面板' : '▶ 执行该工具'}
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
