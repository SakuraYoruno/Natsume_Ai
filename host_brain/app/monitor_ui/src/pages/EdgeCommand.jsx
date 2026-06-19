import React, { useEffect, useState } from 'react'
import { RadioTower, RefreshCw, Send } from 'lucide-react'
import PageHero from '../components/PageHero.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import { fetchEdgeCommandStatus, pushEdgeCommand } from '../features/execution/api.js'

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

  const push = async () => {
    setPushing(true)
    setPushResult(null)
    try {
      const payload = JSON.parse(payloadText || '{}')
      const result = await pushEdgeCommand({ ...form, payload })
      setPushResult({ ok: true, data: result })
    } catch (pushError) {
      setPushResult({ ok: false, error: pushError.message || String(pushError) })
    } finally {
      setPushing(false)
    }
  }

  const canPush = controlGate && status?.enabled

  return (
    <div className="space-y-5">
      <PageHero
        icon={RadioTower}
        eyebrow="Execution"
        title="Edge Command"
        description="Edge 下行指令链路状态与 gated control。push 入口只有在状态可用并显式打开控制门后才启用。"
        gradientClass="from-sky-50 via-white to-orange-50"
        metaItems={[
          status?.enabled ? 'enabled' : 'disabled',
          status?.edge_base_url || 'no edge url',
          loading ? 'loading' : 'ready',
        ]}
      />

      {error ? <NoticeBanner variant="warning" title="Edge Command 降级">{error}</NoticeBanner> : null}
      {pushResult ? (
        <NoticeBanner variant={pushResult.ok ? 'success' : 'warning'} title={pushResult.ok ? 'Push 完成' : 'Push 失败'}>
          {pushResult.ok ? <JsonBlock data={pushResult.data} className="mt-2" /> : pushResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold text-gray-800">Status</h2>
            <div className="flex items-center gap-2">
              <StatusBadge status={status?.enabled ? 'healthy' : 'degraded'} label={status?.enabled ? 'enabled' : 'disabled'} />
              <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50">
                <RefreshCw size={12} />
                刷新
              </button>
            </div>
          </div>
          <JsonBlock data={status || { loading }} />
        </section>

        <aside className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <h2 className="mb-3 text-base font-semibold text-gray-800">Gated Push</h2>
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
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                  value={form[key]}
                  onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))}
                />
              </label>
            ))}
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-gray-500">payload JSON</span>
              <textarea
                className="h-28 w-full resize-none rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={payloadText}
                onChange={(event) => setPayloadText(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={!canPush || pushing}
              onClick={push}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send size={14} />
              {pushing ? '推送中...' : 'Push'}
            </button>
            {!status?.enabled ? <div className="text-xs text-orange-600">Edge pusher 当前不可用。</div> : null}
          </div>
        </aside>
      </div>
    </div>
  )
}
