import React, { useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, RadioTower, RefreshCw, Send } from 'lucide-react'
import PageHero from '../shared/ui/PageHero.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import StatusBadge from '../shared/ui/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import { EDGE_COMMAND_COPY } from '../shared/content/terminology.js'
import EdgeCommandGateRail from '../features/execution/components/EdgeCommandGateRail.jsx'
import { fetchEdgeCommandStatus, pushEdgeCommand } from '../features/execution/api.js'

function parsePayloadJson(value) {
  const text = String(value || '').trim()
  if (!text) return { valid: true, value: {}, label: 'empty object' }
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { valid: false, value: null, error: 'payload JSON 必须是对象。' }
    }
    return { valid: true, value: parsed, label: `${Object.keys(parsed).length} keys` }
  } catch (error) {
    return { valid: false, value: null, error: error.message || String(error) }
  }
}

function validateCommandForm(form) {
  const fields = ['command_type', 'target_agent', 'source_agent']
  const missing = fields.filter((key) => !String(form?.[key] || '').trim())
  if (missing.length) {
    return {
      valid: false,
      missing,
      error: `缺少 ${missing.join(', ')}。`,
      label: `${fields.length - missing.length}/${fields.length} fields`,
    }
  }
  return {
    valid: true,
    missing: [],
    label: `${form.command_type} -> ${form.target_agent}`,
  }
}

export default function EdgeCommand() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [controlGate, setControlGate] = useState(false)
  const [payloadText, setPayloadText] = useState('{"emotion_tag":"CALM","intensity":0.4}')
  const [form, setForm] = useState({
    command_type: 'emotion_state_command',
    target_agent: 'edge.somatic',
    source_agent: 'host_brain',
  })
  const [pushResult, setPushResult] = useState(null)
  const [pushing, setPushing] = useState(false)
  const commandTypeRef = useRef(null)
  const targetAgentRef = useRef(null)
  const sourceAgentRef = useRef(null)
  const payloadRef = useRef(null)
  const payloadValidation = useMemo(() => parsePayloadJson(payloadText), [payloadText])
  const formValidation = useMemo(() => validateCommandForm(form), [form])

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setStatus(await fetchEdgeCommandStatus())
    } catch (loadError) {
      setStatus(null)
      setError(loadError.message || String(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const focusCommandField = () => {
    const firstMissing = formValidation.missing[0]
    const refMap = {
      command_type: commandTypeRef,
      target_agent: targetAgentRef,
      source_agent: sourceAgentRef,
    }
    refMap[firstMissing]?.current?.focus()
  }

  const canPush = controlGate && status?.enabled && formValidation.valid && payloadValidation.valid
  const disabledReason = !status?.enabled
    ? 'Edge pusher 当前不可用。'
    : !controlGate
      ? '开启控制门后才能发送。'
      : !formValidation.valid
        ? `命令字段无效：${formValidation.error}`
        : !payloadValidation.valid
          ? `payload JSON 无效：${payloadValidation.error}`
          : ''

  const push = async () => {
    if (!formValidation.valid) {
      setPushResult({ ok: false, error: formValidation.error })
      focusCommandField()
      return
    }
    if (!payloadValidation.valid) {
      setPushResult({ ok: false, error: payloadValidation.error })
      payloadRef.current?.focus()
      return
    }
    if (!canPush) return
    setPushing(true)
    setPushResult(null)
    try {
      const result = await pushEdgeCommand({ ...form, payload: payloadValidation.value })
      setPushResult({ ok: true, data: result })
    } catch (pushError) {
      setPushResult({ ok: false, error: pushError.message || String(pushError) })
    } finally {
      setPushing(false)
    }
  }

  return (
    <div className="space-y-5">
      <PageHero
        icon={RadioTower}
        eyebrow={EDGE_COMMAND_COPY.heroEyebrow}
        title={EDGE_COMMAND_COPY.heroTitle}
        description={EDGE_COMMAND_COPY.heroDescription}
        gradientClass="from-sky-50 via-white to-orange-50"
        metaItems={[
          status?.enabled ? 'edge enabled' : 'edge disabled',
          controlGate ? 'control gate open' : 'control gate closed',
          payloadValidation.valid ? `payload ${payloadValidation.label}` : 'payload invalid',
          status?.edge_base_url || 'no edge url',
          loading ? 'status loading' : 'status ready',
        ]}
      />

      <EdgeCommandGateRail
        controlGate={controlGate}
        error={error}
        formValidation={formValidation}
        loading={loading}
        onEnableGate={() => setControlGate(true)}
        onFocusCommand={focusCommandField}
        onFocusPayload={() => payloadRef.current?.focus()}
        payloadValidation={payloadValidation}
        pushing={pushing}
        pushResult={pushResult}
        status={status}
      />

      {error ? <NoticeBanner variant="warning" title="Edge Command 降级">{error}</NoticeBanner> : null}
      {pushResult ? (
        <NoticeBanner variant={pushResult.ok ? 'success' : 'warning'} title={pushResult.ok ? 'Push 完成' : 'Push 失败'}>
          {pushResult.ok ? <JsonBlock data={pushResult.data} className="mt-2" /> : pushResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-gray-800">Status</h2>
              <p className="mt-1 text-xs text-gray-500">原始状态保留在下方，便于核对 enabled、edge_base_url 和后端返回字段。</p>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge status={status?.enabled ? 'healthy' : 'degraded'} label={status?.enabled ? 'enabled' : 'disabled'} />
              <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50">
                <RefreshCw size={12} />
                刷新
              </button>
            </div>
          </div>
          <div className="mb-3 grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <div className="text-xs font-semibold text-gray-500">Base URL</div>
              <div className="mt-1 truncate text-sm font-semibold text-gray-800">{status?.edge_base_url || '未配置'}</div>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <div className="text-xs font-semibold text-gray-500">Command Type</div>
              <div className="mt-1 truncate text-sm font-semibold text-gray-800">{form.command_type}</div>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <div className="text-xs font-semibold text-gray-500">Target</div>
              <div className="mt-1 truncate text-sm font-semibold text-gray-800">{form.target_agent}</div>
            </div>
          </div>
          <JsonBlock data={status || { loading }} />
        </section>

        <aside className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-gray-800">Gated Push</h2>
              <p className="mt-1 text-xs text-gray-500">只有 Gate 全绿时才允许发送，失败结果会留在本页。</p>
            </div>
            {payloadValidation.valid ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                <CheckCircle2 size={12} />
                payload ok
              </span>
            ) : (
              <span className="rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-700">payload invalid</span>
            )}
          </div>
          <div className="space-y-3">
            <label className="inline-flex items-center gap-2 text-sm font-semibold text-gray-700">
              <input
                type="checkbox"
                checked={controlGate}
                onChange={(event) => setControlGate(event.target.checked)}
              />
              开启控制门
            </label>
            {['command_type', 'target_agent', 'source_agent'].map((key) => (
              <label key={key} className="block">
                <span className="mb-1 block text-xs font-semibold text-gray-500">{key}</span>
                <input
                  ref={key === 'command_type' ? commandTypeRef : key === 'target_agent' ? targetAgentRef : sourceAgentRef}
                  aria-invalid={!String(form[key] || '').trim()}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                  value={form[key]}
                  onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))}
                />
                {!String(form[key] || '').trim() ? (
                  <span className="mt-1 block text-xs text-red-600">必填字段。</span>
                ) : null}
              </label>
            ))}
            <div
              aria-label="Edge command review"
              className={`rounded-lg border px-3 py-2 text-xs leading-5 ${formValidation.valid ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'}`}
            >
              {formValidation.valid
                ? `Command envelope 可发送：${formValidation.label}`
                : `Command envelope 需要补齐：${formValidation.error}`}
            </div>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-gray-500">payload JSON</span>
              <textarea
                ref={payloadRef}
                aria-invalid={!payloadValidation.valid}
                className="h-28 w-full resize-none rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={payloadText}
                onChange={(event) => setPayloadText(event.target.value)}
              />
            </label>
            <div className={`rounded-lg border px-3 py-2 text-xs ${payloadValidation.valid ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'}`}>
              {payloadValidation.valid ? `payload JSON 可解析：${payloadValidation.label}` : `payload JSON 无效：${payloadValidation.error}`}
            </div>
            <button
              type="button"
              disabled={!canPush || pushing}
              onClick={push}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send size={14} />
              {pushing ? '推送中...' : '发送到 Edge'}
            </button>
            {disabledReason ? <div className="text-xs text-orange-600">{disabledReason}</div> : null}
          </div>
        </aside>
      </div>
    </div>
  )
}
