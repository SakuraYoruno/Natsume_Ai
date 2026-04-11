import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Wrench } from 'lucide-react'
import {
  fetchActionPlanStats,
  fetchCustomSkillProviders,
  fetchCustomSkillProviderTemplate,
  fetchPermissionStats,
  fetchToolHealth,
  fetchToolMetaPack,
  executeTool,
  installCustomSkillProvider,
} from '../api/obs.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
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

const PROVIDER_STATUS_LABELS = {
  ACTIVE: '已启用',
  STAGED: '待启用',
  ERROR: '异常',
}

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
    <NoticeBanner variant="info" title="加载中">
      正在同步自定义提供器列表…
    </NoticeBanner>
  )
}

function CustomSkillInstaller() {
  const [providers, setProviders] = useState([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [templateLoading, setTemplateLoading] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [form, setForm] = useState({
    filename: 'my_skill_provider.py',
    source_code: '',
    activate_now: true,
  })

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true)
    setError('')
    try {
      const response = await fetchCustomSkillProviders()
      setProviders(response.items || [])
    } catch (fetchError) {
      setError(fetchError.message || '获取提供器列表失败')
    } finally {
      setProvidersLoading(false)
    }
  }, [])

  const loadTemplate = useCallback(async (force = false) => {
    setTemplateLoading(true)
    setError('')
    try {
      const response = await fetchCustomSkillProviderTemplate()
      setForm((prev) => ({
        ...prev,
        filename: prev.filename || response.filename_hint || 'my_skill_provider.py',
        source_code: force || !prev.source_code ? response.source_code || '' : prev.source_code,
      }))
    } catch (fetchError) {
      setError(fetchError.message || '加载模板失败')
    } finally {
      setTemplateLoading(false)
    }
  }, [])

  useEffect(() => {
    loadProviders()
    loadTemplate(false)
  }, [loadProviders, loadTemplate])

  const handleInstall = async () => {
    setInstalling(true)
    setError('')
    setResult(null)
    try {
      const data = await installCustomSkillProvider({
        filename: form.filename,
        source_code: form.source_code,
        activate_now: form.activate_now,
      })
      setResult({ ok: true, data })
      await loadProviders()
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
          <h2 className="text-base font-semibold text-gray-800">自定义技能提供器安装</h2>
          <p className="mt-0.5 text-sm text-gray-500">
            直接写入 <code className="font-mono">host_brain/skills/custom_providers</code>，并可选自动刷新技能注册表。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => loadTemplate(true)}
            disabled={templateLoading}
            className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
          >
            {templateLoading ? '加载中…' : '加载模板'}
          </button>
          <button
            onClick={loadProviders}
            disabled={providersLoading}
            className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
          >
            {providersLoading ? '刷新中…' : '刷新列表'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-gray-500">提供器文件名</label>
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
            <label className="mb-1 block text-xs text-gray-500">提供器源码</label>
            <textarea
              className="min-h-[420px] w-full rounded border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
              value={form.source_code}
              onChange={(event) => setForm((prev) => ({ ...prev, source_code: event.target.value }))}
              spellCheck={false}
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={form.activate_now}
              onChange={(event) => setForm((prev) => ({ ...prev, activate_now: event.target.checked }))}
            />
            安装后立即刷新技能注册表
          </label>

          <div className="flex items-center gap-2">
            <button
              onClick={handleInstall}
              disabled={installing}
              className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {installing ? '安装中…' : '安装技能提供器'}
            </button>
            <div className="text-xs text-gray-400">
              也支持通过 <code className="font-mono">file_write</code> + <code className="font-mono">shell_run</code> 手动完成安装。
            </div>
          </div>

          {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

          {result ? (
            result.ok ? (
              <NoticeBanner variant="success" title="安装完成">
                <div>{result.data.message}</div>
                <div>
                  文件：<code className="font-mono">{result.data.path}</code>
                </div>
                {(result.data.declared_skill_names || []).length > 0 ? (
                  <div>声明技能：{result.data.declared_skill_names.join(', ')}</div>
                ) : null}
                {(result.data.active_skill_names || []).length > 0 ? (
                  <div>当前激活：{result.data.active_skill_names.join(', ')}</div>
                ) : null}
              </NoticeBanner>
            ) : (
              <NoticeBanner variant="error" title="安装失败">
                {result.error}
              </NoticeBanner>
            )
          ) : null}
        </div>

        <div className="space-y-3">
          <div className="text-sm font-semibold text-gray-700">已安装自定义提供器</div>
          {providersLoading && providers.length === 0 ? (
            <ProviderLoadingState />
          ) : providers.length === 0 ? (
            <EmptyStateCard title="暂无自定义提供器" hint="点击“加载模板”并安装后会显示在这里。" className="p-6" />
          ) : (
            <div className="max-h-[620px] space-y-3 overflow-y-auto pr-1">
              {providers.map((provider) => (
                <div key={provider.filename} className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium text-gray-800">{provider.filename}</div>
                      <div className="mt-0.5 font-mono text-xs text-gray-500">{provider.module_name}</div>
                    </div>
                    <ProviderStatusBadge status={provider.status} />
                  </div>

                  <div className="text-xs text-gray-500">
                    <div>
                      路径：<code className="font-mono">{provider.path}</code>
                    </div>
                    <div>
                      更新时间：
                      {provider.updated_at_ms ? new Date(provider.updated_at_ms).toLocaleString('zh-CN') : '—'}
                    </div>
                  </div>

                  {(provider.declared_skill_names || []).length > 0 ? (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-gray-500">声明技能</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.declared_skill_names.map((name) => (
                          <span key={name} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                            {name}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {(provider.active_skill_names || []).length > 0 ? (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-gray-500">当前激活</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.active_skill_names.map((name) => (
                          <span key={name} className="rounded-full bg-green-50 px-2 py-0.5 text-xs text-green-700">
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

function PermissionGuardStats() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetchPermissionStats({ window_minutes: 1440 }).then(setData).catch(() => {})
  }, [])

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">权限拦截统计（近 24 小时）</div>
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

function ActionPlanStats() {
  const [data, setData] = useState(null)

  useEffect(() => {
    fetchActionPlanStats({ window_minutes: 1440 }).then(setData).catch(() => {})
  }, [])

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">执行计划统计（近 24 小时）</div>
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

export default function Tools() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [health, setHealth] = useState(null)
  const [tools, setTools] = useState([])
  const [selectedTool, setSelectedTool] = useState(null)
  const [executeTarget, setExecuteTarget] = useState(null)
  const [filter, setFilter] = useState('ALL')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [healthResponse, pack] = await Promise.all([
        fetchToolHealth(),
        fetchToolMetaPack({ include_write: true, k: 20, query }),
      ])
      setHealth(healthResponse)
      setTools(pack.tool_meta_pack || [])
    } catch (fetchError) {
      setError(fetchError.message || '加载工具信息失败')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, load])

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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">工具管理</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看工具目录、权限等级、健康状态与手动执行入口。</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? '刷新中…' : '刷新'}
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
      />

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

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

      <CustomSkillInstaller />
      <PermissionGuardStats />
      <ActionPlanStats />
    </div>
  )
}
