import React, { useEffect, useState } from 'react'
import { Gamepad2, RefreshCw, Send } from 'lucide-react'
import PageHero from '../components/PageHero.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import {
  fetchBodyIntegrationBundle,
  sendMateEngineAction,
  sendMateEngineSay,
} from '../features/integrations/api.js'

export default function BodyIntegration() {
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState('state')
  const [sayText, setSayText] = useState('Natsume body bridge test')
  const [result, setResult] = useState(null)
  const [sending, setSending] = useState(false)

  const load = async () => {
    setLoading(true)
    setBundle(await fetchBodyIntegrationBundle())
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [])

  const sendAction = async () => {
    setSending(true)
    setResult(null)
    try {
      const payload = action === 'say'
        ? await sendMateEngineSay({ text: sayText, emotion: 'neutral', ttl: 6 })
        : await sendMateEngineAction({ action, state: action === 'state' ? 'idle' : undefined, preset: action === 'pose' ? 'idle' : undefined })
      setResult({ ok: true, data: payload })
    } catch (error) {
      setResult({ ok: false, error: error.message || String(error) })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-5">
      <PageHero
        icon={Gamepad2}
        eyebrow="Integrations"
        title="Body / MateEngine"
        description="桌宠身体桥、MateEngine 能力和本地动作链路状态。写入动作保留在显式按钮后触发。"
        gradientClass="from-amber-50 via-white to-cyan-50"
        metaItems={[
          bundle?.health?.status || (loading ? 'loading' : 'unknown'),
          `${bundle?.capabilities?.events?.length ?? 0} events`,
          `${bundle?.capabilities?.actions?.length ?? 0} actions`,
        ]}
      />

      {bundle?.errors?.length ? (
        <NoticeBanner variant="warning" title="部分集成接口降级">
          {bundle.errors.map((item) => item.message).join(' | ')}
        </NoticeBanner>
      ) : null}
      {result ? (
        <NoticeBanner variant={result.ok ? 'success' : 'warning'} title={result.ok ? '发送完成' : '发送失败'}>
          {result.ok ? <JsonBlock data={result.data} className="mt-2" /> : result.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold text-gray-800">MateEngine Status</h2>
            <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50">
              <RefreshCw size={12} />
              刷新
            </button>
          </div>
          <div className="mb-3 flex items-center gap-2">
            <StatusBadge status={bundle?.health?.ok ? 'healthy' : 'degraded'} label={bundle?.health?.ok ? 'bridge ok' : 'bridge degraded'} />
            <span className="text-xs text-gray-500">{bundle?.health?.host || '127.0.0.1'}:{bundle?.health?.port || '18770'}</span>
          </div>
          <JsonBlock data={bundle || { loading }} />
        </section>

        <aside className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <h2 className="mb-3 text-base font-semibold text-gray-800">Action Console</h2>
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-gray-500">Action</span>
              <select
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={action}
                onChange={(event) => setAction(event.target.value)}
              >
                <option value="state">state</option>
                <option value="pose">pose</option>
                <option value="say">say</option>
              </select>
            </label>
            {action === 'say' ? (
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-gray-500">Text</span>
                <textarea
                  className="h-24 w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                  value={sayText}
                  onChange={(event) => setSayText(event.target.value)}
                />
              </label>
            ) : null}
            <button
              type="button"
              disabled={sending}
              onClick={sendAction}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50"
            >
              <Send size={14} />
              {sending ? '发送中...' : '发送'}
            </button>
          </div>
        </aside>
      </div>
    </div>
  )
}
