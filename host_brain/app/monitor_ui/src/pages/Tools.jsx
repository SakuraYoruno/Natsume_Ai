import React, { useState, useEffect, useCallback } from 'react'
import {
  fetchToolMetaPack,
  fetchToolHealth,
  executeTool,
  fetchPermissionStats,
  fetchActionPlanStats,
  fetchCustomSkillProviders,
  fetchCustomSkillProviderTemplate,
  installCustomSkillProvider,
} from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

// ── 副作用级别样式 ────────────────────────────────────────────────────────
const SIDE_EFFECT_STYLES = {
  READ:  { bg: 'bg-green-100', text: 'text-green-700', label: 'READ' },
  WRITE: { bg: 'bg-orange-100', text: 'text-orange-700', label: 'WRITE' },
}

const AUTH_LEVEL_STYLES = {
  NONE:     { bg: 'bg-gray-100', text: 'text-gray-600' },
  APPROVAL: { bg: 'bg-yellow-100', text: 'text-yellow-700' },
  CONFIRM:  { bg: 'bg-red-100', text: 'text-red-700' },
}

// ── 工具卡片 ──────────────────────────────────────────────────────────────
function ToolCard({ tool, selected, onClick }) {
  const se = SIDE_EFFECT_STYLES[tool.side_effect_level] || SIDE_EFFECT_STYLES.READ
  const auth = AUTH_LEVEL_STYLES[tool.auth_level] || AUTH_LEVEL_STYLES.NONE

  return (
    <button
      onClick={() => onClick(tool)}
      className={`w-full text-left p-3 rounded-lg border transition-all ${
        selected
          ? 'border-blue-400 bg-blue-50 shadow-sm'
          : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-gray-800 truncate">{tool.name}</div>
          <div className="text-xs text-gray-500 mt-0.5 truncate">{tool.id}</div>
        </div>
        <div className="flex flex-col gap-1 items-end shrink-0">
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${se.bg} ${se.text}`}>
            {se.label}
          </span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${auth.bg} ${auth.text}`}>
            {tool.auth_level}
          </span>
        </div>
      </div>
      <div className="text-xs text-gray-500 mt-1.5 line-clamp-2">{tool.desc}</div>
      {tool.domain_tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {tool.domain_tags.slice(0, 3).map(tag => (
            <span key={tag} className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
              {tag}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}

// ── 工具执行面板 ──────────────────────────────────────────────────────────
function ExecutePanel({ tool, onClose }) {
  const [form, setForm] = useState({
    intent_id: `intent::manual::${Date.now()}`,
    session_id: 'manual_session',
    args_json: '{}',
    approval_token_ref: '',
  })
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleExecute = async () => {
    setLoading(true)
    setResult(null)
    try {
      let args = {}
      try { args = JSON.parse(form.args_json) } catch { args = {} }
      const r = await executeTool({
        tool_id: tool.id,
        intent_id: form.intent_id,
        session_id: form.session_id,
        args,
        approval_token_ref: form.approval_token_ref || undefined,
      })
      setResult({ ok: true, data: r })
    } catch (e) {
      setResult({ ok: false, error: e.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">执行工具：{tool.name}</h3>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg leading-none">×</button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Intent ID</label>
          <input
            className="w-full border border-gray-300 rounded px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.intent_id}
            onChange={e => setForm(f => ({ ...f, intent_id: e.target.value }))}
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Session ID</label>
          <input
            className="w-full border border-gray-300 rounded px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.session_id}
            onChange={e => setForm(f => ({ ...f, session_id: e.target.value }))}
          />
        </div>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">参数 (JSON)</label>
        <textarea
          className="w-full border border-gray-300 rounded px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-300 h-20 resize-none"
          value={form.args_json}
          onChange={e => setForm(f => ({ ...f, args_json: e.target.value }))}
        />
      </div>

      {tool.side_effect_level === 'WRITE' && (
        <div>
          <label className="block text-xs text-gray-500 mb-1">
            Approval Token Ref <span className="text-orange-500">（WRITE 工具必填）</span>
          </label>
          <input
            className="w-full border border-orange-300 rounded px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-orange-300"
            value={form.approval_token_ref}
            onChange={e => setForm(f => ({ ...f, approval_token_ref: e.target.value }))}
            placeholder="approval_token_ref"
          />
        </div>
      )}

      <button
        onClick={handleExecute}
        disabled={loading}
        className="w-full bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
      >
        {loading ? '执行中...' : '执行'}
      </button>

      {result && (
        <div className={`p-3 rounded text-xs font-mono ${result.ok ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
          {result.ok
            ? <pre className="whitespace-pre-wrap">{JSON.stringify(result.data, null, 2)}</pre>
            : <span>✗ {result.error}</span>
          }
        </div>
      )}
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
    <span className={`text-xs px-2 py-0.5 rounded font-medium ${styles[status] || 'bg-gray-100 text-gray-600'}`}>
      {status || 'UNKNOWN'}
    </span>
  )
}

function CustomSkillInstaller() {
  const [providers, setProviders] = useState([])
  const [providersLoading, setProvidersLoading] = useState(false)
  const [templateLoading, setTemplateLoading] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [form, setForm] = useState({
    filename: 'my_skill_provider.py',
    source_code: '',
    activate_now: true,
  })

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true)
    try {
      const res = await fetchCustomSkillProviders()
      setProviders(res.items || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setProvidersLoading(false)
    }
  }, [])

  const loadTemplate = useCallback(async (force = false) => {
    setTemplateLoading(true)
    try {
      const res = await fetchCustomSkillProviderTemplate()
      setForm(prev => ({
        ...prev,
        filename: prev.filename || res.filename_hint || 'my_skill_provider.py',
        source_code: force || !prev.source_code ? (res.source_code || '') : prev.source_code,
      }))
    } catch (e) {
      setError(e.message)
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
    setError(null)
    setResult(null)
    try {
      const res = await installCustomSkillProvider({
        filename: form.filename,
        source_code: form.source_code,
        activate_now: form.activate_now,
      })
      setResult({ ok: true, data: res })
      await loadProviders()
    } catch (e) {
      setResult({ ok: false, error: e.message })
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-800">自定义 Skill 安装</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            直接写入 <code className="font-mono">host_brain/skills/custom_providers</code>，可选立即刷新 SkillRegistry。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => loadTemplate(true)}
            disabled={templateLoading}
            className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50"
          >
            {templateLoading ? '载入中...' : '载入模板'}
          </button>
          <button
            onClick={loadProviders}
            disabled={providersLoading}
            className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50"
          >
            {providersLoading ? '刷新中...' : '刷新列表'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Provider 文件名</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
              value={form.filename}
              onChange={e => setForm(prev => ({ ...prev, filename: e.target.value }))}
              placeholder="my_skill_provider.py"
            />
            <div className="text-xs text-gray-400 mt-1">会自动规范为 <code className="font-mono">*_provider.py</code></div>
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-1">Provider 源码</label>
            <textarea
              className="w-full min-h-[420px] border border-gray-300 rounded px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-300"
              value={form.source_code}
              onChange={e => setForm(prev => ({ ...prev, source_code: e.target.value }))}
              spellCheck={false}
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={form.activate_now}
              onChange={e => setForm(prev => ({ ...prev, activate_now: e.target.checked }))}
            />
            安装后立即刷新 SkillRegistry
          </label>

          <div className="flex items-center gap-2">
            <button
              onClick={handleInstall}
              disabled={installing}
              className="bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {installing ? '安装中...' : '安装 Skill Provider'}
            </button>
            <div className="text-xs text-gray-400">
              Natsume 也可通过 <code className="font-mono">file_write</code> + <code className="font-mono">shell_run</code> 自助安装。
            </div>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>
          )}

          {result && (
            <div className={`rounded border p-3 text-xs ${result.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800'}`}>
              {result.ok ? (
                <div className="space-y-1">
                  <div className="font-semibold">安装完成</div>
                  <div>{result.data.message}</div>
                  <div>文件：<code className="font-mono">{result.data.path}</code></div>
                  {(result.data.declared_skill_names || []).length > 0 && (
                    <div>声明技能：{result.data.declared_skill_names.join(', ')}</div>
                  )}
                  {(result.data.active_skill_names || []).length > 0 && (
                    <div>当前激活：{result.data.active_skill_names.join(', ')}</div>
                  )}
                </div>
              ) : (
                <div>✗ {result.error}</div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-3">
          <div className="text-sm font-semibold text-gray-700">已安装 Custom Providers</div>
          {providers.length === 0 ? (
            <div className="text-sm text-gray-400 bg-gray-50 rounded border border-dashed border-gray-200 p-6 text-center">
              暂无自定义 provider
            </div>
          ) : (
            <div className="space-y-3 max-h-[620px] overflow-y-auto pr-1">
              {providers.map((provider) => (
                <div key={provider.filename} className="border border-gray-200 rounded-lg p-3 bg-gray-50 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-medium text-sm text-gray-800">{provider.filename}</div>
                      <div className="text-xs text-gray-500 font-mono mt-0.5">{provider.module_name}</div>
                    </div>
                    <ProviderStatusBadge status={provider.status} />
                  </div>

                  <div className="text-xs text-gray-500">
                    <div>路径：<code className="font-mono">{provider.path}</code></div>
                    <div>更新时间：{provider.updated_at_ms ? new Date(provider.updated_at_ms).toLocaleString('zh-CN') : '—'}</div>
                  </div>

                  {(provider.declared_skill_names || []).length > 0 && (
                    <div>
                      <div className="text-xs font-semibold text-gray-500 mb-1">声明技能</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.declared_skill_names.map((name) => (
                          <span key={name} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">{name}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {(provider.active_skill_names || []).length > 0 && (
                    <div>
                      <div className="text-xs font-semibold text-gray-500 mb-1">当前激活</div>
                      <div className="flex flex-wrap gap-1.5">
                        {provider.active_skill_names.map((name) => (
                          <span key={name} className="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full">{name}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {provider.error && (
                    <div className="text-xs bg-red-50 border border-red-200 rounded p-2 text-red-700">
                      {provider.error}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────
export default function Tools() {
  const pollingEnabled  = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)

  const [health, setHealth]         = useState(null)
  const [tools, setTools]           = useState([])
  const [selectedTool, setSelectedTool] = useState(null)
  const [executeTarget, setExecuteTarget] = useState(null)
  const [filter, setFilter]         = useState('ALL')  // ALL / READ / WRITE
  const [query, setQuery]           = useState('')
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, pack] = await Promise.all([
        fetchToolHealth(),
        fetchToolMetaPack({ include_write: true, k: 20, query }),
      ])
      setHealth(h)
      setTools(pack.tool_meta_pack || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!pollingEnabled) return
    const id = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(id)
  }, [pollingEnabled, pollingInterval, load])

  const filteredTools = tools.filter(t => {
    if (filter === 'READ' && t.side_effect_level !== 'READ') return false
    if (filter === 'WRITE' && t.side_effect_level !== 'WRITE') return false
    return true
  })

  const readCount  = tools.filter(t => t.side_effect_level === 'READ').length
  const writeCount = tools.filter(t => t.side_effect_level === 'WRITE').length

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">工具管理</h1>
          <p className="text-sm text-gray-500 mt-0.5">ToolBroker 注册工具元数据与执行入口</p>
        </div>
        <button onClick={load} disabled={loading}
          className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50">
          {loading ? '刷新中...' : '刷新'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>
      )}

      {/* 健康状态卡片 */}
      {health && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: '引擎状态', value: health.status, color: health.status === 'OK' ? 'text-green-600' : 'text-yellow-600' },
            { label: '工具总数', value: health.catalog_size, color: 'text-gray-800' },
            { label: 'READ 工具', value: health.read_tools, color: 'text-green-600' },
            { label: 'WRITE 工具', value: health.write_tools, color: 'text-orange-600' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-white rounded-lg border border-gray-200 p-3 text-center">
              <div className={`text-xl font-bold ${color}`}>{value}</div>
              <div className="text-xs text-gray-500 mt-0.5">{label}</div>
            </div>
          ))}
        </div>
      )}

      {/* 搜索 + 过滤 */}
      <div className="flex items-center gap-3">
        <input
          className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          placeholder="搜索工具名称或描述..."
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="flex rounded border border-gray-300 overflow-hidden text-sm">
          {[
            { key: 'ALL', label: `全部 (${tools.length})` },
            { key: 'READ', label: `READ (${readCount})` },
            { key: 'WRITE', label: `WRITE (${writeCount})` },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-3 py-1.5 transition-colors ${
                filter === key
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* 工具列表 + 详情 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* 工具列表 */}
        <div className="md:col-span-1 space-y-2 max-h-[600px] overflow-y-auto pr-1">
          {filteredTools.length === 0 ? (
            <div className="text-gray-400 text-sm text-center py-8">暂无工具</div>
          ) : (
            filteredTools.map(tool => (
              <ToolCard
                key={tool.id}
                tool={tool}
                selected={selectedTool?.id === tool.id}
                onClick={t => { setSelectedTool(t); setExecuteTarget(null) }}
              />
            ))
          )}
        </div>

        {/* 工具详情 */}
        <div className="md:col-span-2 space-y-4">
          {selectedTool ? (
            <>
              <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-base font-semibold text-gray-800">{selectedTool.name}</h2>
                    <div className="text-xs text-gray-400 mt-0.5 font-mono">{selectedTool.id}</div>
                  </div>
                  <div className="flex gap-2">
                    <span className={`text-xs px-2 py-1 rounded font-medium ${(SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || SIDE_EFFECT_STYLES.READ).bg} ${(SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || SIDE_EFFECT_STYLES.READ).text}`}>
                      {selectedTool.side_effect_level}
                    </span>
                    <span className={`text-xs px-2 py-1 rounded ${(AUTH_LEVEL_STYLES[selectedTool.auth_level] || AUTH_LEVEL_STYLES.NONE).bg} ${(AUTH_LEVEL_STYLES[selectedTool.auth_level] || AUTH_LEVEL_STYLES.NONE).text}`}>
                      {selectedTool.auth_level}
                    </span>
                  </div>
                </div>

                <p className="text-sm text-gray-600">{selectedTool.desc}</p>

                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className="bg-gray-50 rounded p-2">
                    <div className="text-gray-400 mb-0.5">版本</div>
                    <div className="font-mono text-gray-700">{selectedTool.version}</div>
                  </div>
                  <div className="bg-gray-50 rounded p-2">
                    <div className="text-gray-400 mb-0.5">Schema Hash</div>
                    <div className="font-mono text-gray-700 truncate">{selectedTool.schema_hash?.slice(0, 16)}...</div>
                  </div>
                </div>

                {selectedTool.domain_tags?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedTool.domain_tags.map(tag => (
                      <span key={tag} className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">{tag}</span>
                    ))}
                  </div>
                )}

                <button
                  onClick={() => setExecuteTarget(executeTarget?.id === selectedTool.id ? null : selectedTool)}
                  className="w-full border border-blue-300 text-blue-600 rounded px-4 py-2 text-sm font-medium hover:bg-blue-50 transition-colors"
                >
                  {executeTarget?.id === selectedTool.id ? '收起执行面板' : '▶ 执行此工具'}
                </button>
              </div>

          {executeTarget?.id === selectedTool.id && (
                <ExecutePanel tool={selectedTool} onClose={() => setExecuteTarget(null)} />
              )}
            </>
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-400 text-sm">
              点击左侧工具查看详情
            </div>
          )}
        </div>
      </div>

      <CustomSkillInstaller />

      {/* ===== Ref #54: PermissionGuard 权限拒绝统计 ===== */}
      <PermissionGuardStats />

      {/* ===== Ref #56: ActionPlan 执行统计 ===== */}
      <ActionPlanStats />
    </div>
  )
}

// ── Ref #54: PermissionGuard 权限拒绝统计子组件 ────────────────────────
function PermissionGuardStats() {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchPermissionStats({ window_minutes: 1440 }).then(setData).catch(() => {})
  }, [])
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4">
      <div className="text-sm font-semibold text-gray-700">🔒 PermissionGuard 权限拒绝统计（近 24h）</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: '总校验次数', value: data?.total_checks, color: 'text-gray-700' },
          { label: '允许次数', value: data?.allowed_count, color: 'text-green-600' },
          { label: '拒绝次数', value: data?.denied_count, color: 'text-red-600' },
          { label: 'SCHEDULER 拒绝', value: data?.scheduler_denied_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-gray-50 rounded p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {(data?.top_denied_tools ?? []).length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-500 mb-2">Top 拒绝工具</div>
          <div className="space-y-1.5">
            {(data?.top_denied_tools ?? []).map((t) => (
              <div key={t.tool_name} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{t.tool_name}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: `${t.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{t.deny_count}</div>
                {t.proposer_class && <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{t.proposer_class}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #56: ActionPlan 执行统计子组件 ───────────────────────────────
function ActionPlanStats() {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    fetchActionPlanStats({ window_minutes: 1440 }).then(setData).catch(() => {})
  }, [])
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4">
      <div className="text-sm font-semibold text-gray-700">🤖 ActionPlan 执行统计（近 24h）</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: '总计划数', value: data?.total_plans, color: 'text-gray-700' },
          { label: '成功数', value: data?.success_count, color: 'text-green-600' },
          { label: '降级数', value: data?.fallback_count, color: 'text-orange-500' },
          { label: '成功率', value: data?.success_rate != null ? `${data.success_rate}%` : '—', color: data?.success_rate > 80 ? 'text-green-600' : 'text-red-500' },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-gray-50 rounded p-3 text-center">
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {(data?.top_action_types ?? []).length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-500 mb-2">指令类型分布</div>
          <div className="space-y-1.5">
            {(data?.top_action_types ?? []).map((a) => (
              <div key={a.action_type} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-20 truncate">{a.action_type}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-blue-400 h-2 rounded-full" style={{ width: `${a.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{a.pct}%</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
