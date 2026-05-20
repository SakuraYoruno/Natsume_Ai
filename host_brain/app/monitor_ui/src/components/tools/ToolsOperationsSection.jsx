import React, { useEffect, useState } from 'react'
import { installCustomSkillProvider } from '../../api/tools.js'
import EmptyStateCard from '../EmptyStateCard.jsx'
import LoadingNotice from '../LoadingNotice.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import SampleMeta from '../obs/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import {
  useActionPlanStatsQuery,
  useCustomSkillProviderTemplateQuery,
  useCustomSkillProvidersQuery,
  usePermissionStatsQuery,
} from '../../queries/toolsQueries.js'

const PROVIDER_STATUS_LABELS = {
  ACTIVE: '已启用',
  STAGED: '待启用',
  ERROR: '异常',
}

function ProviderStatusBadge({ status }) {
  const styles = {
    ACTIVE: 'bg-green-100 text-green-700',
    STAGED: 'bg-yellow-100 text-yellow-700',
    ERROR: 'bg-red-100 text-red-700',
  }
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${styles[status] || 'bg-gray-100 text-gray-600'}`}>
      {PROVIDER_STATUS_LABELS[status] || status || '未知'}
    </span>
  )
}

function ProviderLoadingState() {
  return (
    <LoadingNotice>
      正在同步自定义提供器列表…
    </LoadingNotice>
  )
}

function CustomSkillInstaller() {
  const [installing, setInstalling] = useState(false)
  const [result, setResult] = useState(null)
  const [form, setForm] = useState({
    filename: 'my_skill_provider.py',
    source_code: '',
    activate_now: true,
  })
  const {
    providersData,
    providers,
    providersError,
    loading: providersLoading,
    refreshing: providersRefreshing,
    stale: providersStale,
    lastUpdatedAt: providersUpdatedAt,
    refetch: refetchProviders,
  } = useCustomSkillProvidersQuery()
  const {
    templateData,
    templateError,
    loading: templateLoading,
    refreshing: templateRefreshing,
    stale: templateStale,
    lastUpdatedAt: templateUpdatedAt,
    refetch: refetchTemplate,
  } = useCustomSkillProviderTemplateQuery()

  useEffect(() => {
    if (!templateData) return
    setForm((prev) => ({
      ...prev,
      filename: prev.filename || templateData.filename_hint || 'my_skill_provider.py',
      source_code: prev.source_code || templateData.source_code || '',
    }))
  }, [templateData])

  const providersLoadState = resolveLoadState({
    loading: providersLoading || providersRefreshing,
    error: providersError,
    hasData: Boolean(providersData),
    hasSamples: providers.length > 0,
    available: Boolean(providersData),
    stale: providersStale,
  })
  const templateLoadState = resolveLoadState({
    loading: templateLoading || templateRefreshing,
    error: templateError,
    hasData: Boolean(templateData),
    hasSamples: Boolean(templateData?.source_code),
    available: Boolean(templateData),
    stale: templateStale,
  })

  const handleLoadTemplate = async () => {
    const nextTemplate = await refetchTemplate()
    if (!nextTemplate) return
    setForm((prev) => ({
      ...prev,
      filename: prev.filename || nextTemplate.filename_hint || 'my_skill_provider.py',
      source_code: prev.source_code || nextTemplate.source_code || '',
    }))
  }

  const handleInstall = async () => {
    setInstalling(true)
    setResult(null)
    try {
      const data = await installCustomSkillProvider({
        filename: form.filename,
        source_code: form.source_code,
        activate_now: form.activate_now,
      })
      setResult({ ok: true, data })
      await refetchProviders()
    } catch (installError) {
      setResult({ ok: false, error: installError.message || '安装失败' })
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-700">自定义技能提供器</div>
          <div className="mt-1 text-xs text-gray-500">
            直接写入 <code className="font-mono">host_brain/skills/custom_providers</code>，并可选自动刷新技能注册表。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleLoadTemplate()}
            disabled={templateLoading || templateRefreshing}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {templateLoading || templateRefreshing ? '同步中…' : '加载模板'}
          </button>
          <button
            onClick={() => void refetchProviders()}
            disabled={providersLoading || providersRefreshing}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {providersLoading || providersRefreshing ? '刷新中…' : '刷新列表'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <div className="space-y-4">
          <SampleMeta
            loadState={templateLoadState}
            sampleSize={templateData?.source_code ? 1 : 0}
            sampleLabel="模板"
            updatedAt={templateUpdatedAt}
            source="custom provider template"
            definition="模板区用于生成新的 custom provider 脚手架；这里的草稿是本地状态，不会被列表刷新覆盖。"
          />

          <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
            <div>
              <label className="mb-1 block text-xs text-gray-500">文件名</label>
              <input
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
                value={form.filename}
                onChange={(event) => setForm((prev) => ({ ...prev, filename: event.target.value }))}
                placeholder="my_skill_provider.py"
              />
              <div className="mt-1 text-xs text-gray-400">
                文件名会规范为 <code className="font-mono">*_provider.py</code>。
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs text-gray-500">源码</label>
              <textarea
                className="h-80 w-full resize-y rounded border border-gray-300 px-3 py-2 font-mono text-xs leading-5 focus:outline-none focus:ring-1 focus:ring-blue-300"
                value={form.source_code}
                onChange={(event) => setForm((prev) => ({ ...prev, source_code: event.target.value }))}
                placeholder="# 在这里粘贴 provider 源码"
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-600">
              <input
                type="checkbox"
                checked={form.activate_now}
                onChange={(event) => setForm((prev) => ({ ...prev, activate_now: event.target.checked }))}
              />
              安装后立即激活
            </label>

            <button
              onClick={() => void handleInstall()}
              disabled={installing}
              className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {installing ? '安装中…' : '安装技能提供器'}
            </button>

            {result ? (
              result.ok ? (
                <NoticeBanner variant="success" title="安装完成">
                  <pre className="mt-1 whitespace-pre-wrap break-all rounded-lg border border-green-200 bg-white/70 p-3 text-xs font-mono text-green-900">
                    {JSON.stringify(result.data, null, 2)}
                  </pre>
                </NoticeBanner>
              ) : (
                <NoticeBanner variant="error" title="安装失败">
                  {result.error}
                </NoticeBanner>
              )
            ) : null}

            {templateError ? (
              <NoticeBanner variant="warning" title="模板暂不可用">
                {templateError}
              </NoticeBanner>
            ) : null}
          </div>
        </div>

        <div className="space-y-4">
          <SampleMeta
            loadState={providersLoadState}
            sampleSize={providers.length}
            sampleLabel="提供器"
            updatedAt={providersUpdatedAt}
            source="custom providers"
            definition="提供器列表已独立进入 query 层；刷新列表只会刷新 provider 清单，不会影响模板编辑区的本地草稿。"
          />

          {providersError ? (
            <NoticeBanner variant={providers.length > 0 ? 'warning' : 'error'}>
              {providersError}
            </NoticeBanner>
          ) : null}

          {providersLoading && providers.length === 0 ? (
            <ProviderLoadingState />
          ) : providers.length === 0 ? (
            <EmptyStateCard title="暂无自定义提供器" hint="安装后会在这里出现。" className="p-6" />
          ) : (
            <div className="space-y-3">
              {providers.map((provider) => (
                <div key={provider.filename} className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-800">{provider.filename}</div>
                      <div className="mt-0.5 font-mono text-xs text-gray-500">{provider.module_name}</div>
                    </div>
                    <ProviderStatusBadge status={provider.status} />
                  </div>

                  <div className="space-y-1 text-xs text-gray-500">
                    <div>
                      路径：<code className="font-mono">{provider.path}</code>
                    </div>
                    <div>
                      最近更新时间：
                      {provider.updated_at_ms ? new Date(provider.updated_at_ms).toLocaleString('zh-CN') : '—'}
                    </div>
                  </div>

                  {(provider.declared_skill_names || []).length > 0 ? (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-gray-500">声明技能</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.declared_skill_names.map((name) => (
                          <span key={`${provider.filename}-declared-${name}`} className="rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-700">
                            {name}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {(provider.active_skill_names || []).length > 0 ? (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-gray-500">当前启用技能</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.active_skill_names.map((name) => (
                          <span key={`${provider.filename}-active-${name}`} className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">
                            {name}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {provider.error ? <NoticeBanner variant="error">{provider.error}</NoticeBanner> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function PermissionGuardStats({
  data,
  loading,
  refreshing,
  stale,
  error,
  updatedAt,
  windowMinutes = 1440,
}) {
  const loadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: Number(data?.total_checks ?? 0) > 0,
    available: Boolean(data),
    stale,
  })

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">权限拦截统计（近 24 小时）</div>
      <SampleMeta
        loadState={loadState}
        windowMinutes={windowMinutes}
        sampleSize={data?.total_checks ?? 0}
        sampleLabel="校验"
        updatedAt={updatedAt}
        source="permission-guard"
        definition="展示工具权限校验总量、拦截量和高频被拦截工具，便于识别误拦或异常提案。"
      />

      {error ? (
        <NoticeBanner variant="warning" title="权限统计暂不可用">
          {error}
        </NoticeBanner>
      ) : null}

      {!error && !loading && !refreshing && loadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前窗口还没有权限校验样本。</NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: '总校验次数', value: data?.total_checks, color: 'text-gray-700' },
          { label: '允许次数', value: data?.allowed_count, color: 'text-green-600' },
          { label: '拦截次数', value: data?.denied_count, color: 'text-red-600' },
          { label: '调度器拦截', value: data?.scheduler_denied_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded bg-gray-50 p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="mt-0.5 text-xs text-gray-400">{label}</div>
          </div>
        ))}
      </div>

      {(data?.top_denied_tools ?? []).length > 0 ? (
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">高频拦截工具</div>
          <div className="space-y-1.5">
            {(data?.top_denied_tools ?? []).map((tool) => (
              <div key={tool.tool_name} className="flex items-center gap-2">
                <div className="w-36 truncate font-mono text-xs text-gray-600">{tool.tool_name}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-violet-400" style={{ width: `${tool.pct}%` }} />
                </div>
                <div className="w-8 text-right text-xs text-gray-500">{tool.deny_count}</div>
                {tool.proposer_class ? (
                  <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-400">{tool.proposer_class}</span>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ActionPlanStats({
  data,
  loading,
  refreshing,
  stale,
  error,
  updatedAt,
  windowMinutes = 1440,
}) {
  const loadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: Number(data?.total_plans ?? 0) > 0,
    available: Boolean(data),
    stale,
  })

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">执行计划统计（近 24 小时）</div>
      <SampleMeta
        loadState={loadState}
        windowMinutes={windowMinutes}
        sampleSize={data?.total_plans ?? 0}
        sampleLabel="计划"
        updatedAt={updatedAt}
        source="action-plan"
        definition="展示执行计划成功、降级和指令类型分布，用于判断规划器是否频繁 fallback。"
      />

      {error ? (
        <NoticeBanner variant="warning" title="执行计划统计暂不可用">
          {error}
        </NoticeBanner>
      ) : null}

      {!error && !loading && !refreshing && loadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前窗口还没有执行计划样本。</NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: '总计划数', value: data?.total_plans, color: 'text-gray-700' },
          { label: '成功数', value: data?.success_count, color: 'text-green-600' },
          { label: '降级数', value: data?.fallback_count, color: 'text-orange-500' },
          {
            label: '成功率',
            value: data?.success_rate != null ? `${data.success_rate}%` : '—',
            color: data?.success_rate > 80 ? 'text-green-600' : 'text-red-500',
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded bg-gray-50 p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="mt-0.5 text-xs text-gray-400">{label}</div>
          </div>
        ))}
      </div>

      {(data?.top_action_types ?? []).length > 0 ? (
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">指令类型分布</div>
          <div className="space-y-1.5">
            {(data?.top_action_types ?? []).map((action) => (
              <div key={action.action_type} className="flex items-center gap-2">
                <div className="w-20 truncate font-mono text-xs text-gray-600">{action.action_type}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-blue-400" style={{ width: `${action.pct}%` }} />
                </div>
                <div className="w-10 text-right text-xs text-gray-500">{action.pct}%</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function ToolsOperationsSection({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  permissionWindowMinutes = 1440,
  actionPlanWindowMinutes = 1440,
}) {
  const {
    permissionStats,
    statsError: permissionError,
    loading: permissionLoading,
    refreshing: permissionRefreshing,
    stale: permissionStale,
    lastUpdatedAt: permissionUpdatedAt,
  } = usePermissionStatsQuery({
    windowMinutes: permissionWindowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    actionPlanStats,
    statsError: actionPlanError,
    loading: actionPlanLoading,
    refreshing: actionPlanRefreshing,
    stale: actionPlanStale,
    lastUpdatedAt: actionPlanUpdatedAt,
  } = useActionPlanStatsQuery({
    windowMinutes: actionPlanWindowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  return (
    <div className="space-y-4">
      <CustomSkillInstaller />
      <PermissionGuardStats
        data={permissionStats}
        loading={permissionLoading}
        refreshing={permissionRefreshing}
        stale={permissionStale}
        error={permissionError}
        updatedAt={permissionUpdatedAt}
        windowMinutes={permissionWindowMinutes}
      />
      <ActionPlanStats
        data={actionPlanStats}
        loading={actionPlanLoading}
        refreshing={actionPlanRefreshing}
        stale={actionPlanStale}
        error={actionPlanError}
        updatedAt={actionPlanUpdatedAt}
        windowMinutes={actionPlanWindowMinutes}
      />
    </div>
  )
}
