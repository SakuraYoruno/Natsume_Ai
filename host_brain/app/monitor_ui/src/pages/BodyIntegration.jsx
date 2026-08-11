import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Gamepad2, RefreshCw, Send } from 'lucide-react'
import PageHero from '../shared/ui/PageHero.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import StatusBadge from '../shared/ui/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import { INTEGRATIONS_COPY } from '../shared/content/terminology.js'
import {
  fetchBodyIntegrationBundle,
  getMateDispatchAvailability,
} from '../features/integrations/api.js'
import { buildBodyIntegrationModel } from '../features/integrations/bodyIntegrationModel.js'
import BodyIntegrationCommandRail from '../features/integrations/components/BodyIntegrationCommandRail.jsx'

export default function BodyIntegration() {
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState('state')
  const [sayText, setSayText] = useState('Natsume body bridge test')
  const consoleRef = useRef(null)
  const sayTextRef = useRef(null)
  const dispatchAvailability = getMateDispatchAvailability()

  const load = async () => {
    setLoading(true)
    setBundle(await fetchBodyIntegrationBundle())
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [])

  const model = useMemo(() => buildBodyIntegrationModel({
    action,
    bundle,
    loading,
    result: null,
    sayText,
    sending: false,
    dispatchAvailability,
  }), [action, bundle, dispatchAvailability, loading, sayText])

  const focusActionConsole = () => {
    consoleRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const focusSayText = () => {
    focusActionConsole()
    window.setTimeout(() => sayTextRef.current?.focus(), 150)
  }

  return (
    <div className="space-y-5">
      <PageHero
        icon={Gamepad2}
        eyebrow={INTEGRATIONS_COPY.bodyEyebrow}
        title={INTEGRATIONS_COPY.bodyTitle}
        description={INTEGRATIONS_COPY.bodyDescription}
        gradientClass="from-amber-50 via-white to-cyan-50"
        metaItems={[
          model.summary.healthLabel,
          `${model.summary.eventCount} events`,
          `${model.summary.actionCount} actions`,
          model.summary.hostLabel,
        ]}
      />

      <BodyIntegrationCommandRail
        loading={loading}
        model={model}
        onFocusConsole={focusActionConsole}
        onFocusText={focusSayText}
        onRefresh={() => void load()}
      />

      {bundle?.errors?.length ? (
        <NoticeBanner variant="warning" title="部分集成接口降级">
          {bundle.errors.map((item) => item.message).join(' | ')}
        </NoticeBanner>
      ) : null}
      {!dispatchAvailability.available ? (
        <NoticeBanner variant="warning" title="MateEngine 人工直推已迁移">
          本页继续展示真实 health 与 capabilities；动作只允许由受治理 TaskGraph 经 {dispatchAvailability.replacementAuthority || 'Edge command'} 下发。
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-gray-800">MateEngine Status</h2>
              <p className="mt-1 text-xs leading-5 text-gray-500">原始 bundle 保留在这里，便于核对 health、capabilities 和降级错误。</p>
            </div>
            <button type="button" onClick={load} className="btn-secondary text-xs" disabled={loading}>
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>
          <div className="mb-3 flex items-center gap-2">
            <StatusBadge status={bundle?.health?.ok ? 'healthy' : 'degraded'} label={bundle?.health?.ok ? 'bridge ok' : 'bridge degraded'} />
            <span className="text-xs text-gray-500">{bundle?.health?.host || '127.0.0.1'}:{bundle?.health?.port || '18770'}</span>
          </div>
          <JsonBlock data={bundle || { loading }} />
        </section>

        <aside ref={consoleRef} className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3">
            <h2 className="text-base font-semibold text-gray-800">受治理动作说明</h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">可在这里核对动作类型；真正写入由 TaskGraph 生成，不再从 Monitor 直接发送。</p>
          </div>
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-gray-500">Action</span>
              <select
                aria-label="Body action"
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
                  ref={sayTextRef}
                  aria-label="Body say text"
                  aria-invalid={dispatchAvailability.available && !sayText.trim()}
                  className="h-24 w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                  value={sayText}
                  onChange={(event) => setSayText(event.target.value)}
                />
                {dispatchAvailability.available && !sayText.trim() ? (
                  <span className="mt-1 block text-xs text-red-600">Say 文本不能为空。</span>
                ) : null}
              </label>
            ) : null}
            {!model.canSend ? (
              <div className="rounded-lg border border-orange-200 bg-orange-50/70 px-3 py-2 text-xs leading-5 text-orange-700">
                {model.focus?.detail || '当前动作暂不可发送。'}
              </div>
            ) : null}
            <button
              type="button"
              disabled
              className="btn-primary w-full justify-center disabled:opacity-50"
            >
              <Send size={14} />
              人工发送已迁移
            </button>
          </div>
        </aside>
      </div>
    </div>
  )
}
