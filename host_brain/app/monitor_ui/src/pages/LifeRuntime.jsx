import React, { useEffect, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import PageHero from '../components/PageHero.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import { fetchLifeRuntimeBundle } from '../features/life/api.js'

const CARDS = [
  ['state', 'Digital Life State'],
  ['runtimeHealth', 'Runtime Health'],
  ['environment', 'Environment'],
  ['organs', 'Organs'],
  ['experience', 'Experience'],
  ['memoryHealth', 'Memory Health'],
  ['memoryEvents', 'Memory Events'],
]

export default function LifeRuntime() {
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    setBundle(await fetchLifeRuntimeBundle())
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <div className="space-y-5">
      <PageHero
        icon={Activity}
        eyebrow="Life Systems"
        title="Runtime"
        description="数字生命体运行态聚合页，读取 /v1/digital-life/* 与 /v1/memory/*，用于检查生命状态、器官、经验和记忆链路。"
        gradientClass="from-lime-50 via-white to-cyan-50"
        metaItems={[
          loading ? 'loading' : 'ready',
          `${bundle?.errors?.length ?? 0} degraded`,
        ]}
      />

      {bundle?.errors?.length ? (
        <NoticeBanner variant="warning" title="部分运行态接口降级">
          {bundle.errors.map((item) => item.message).join(' | ')}
        </NoticeBanner>
      ) : null}

      <div className="flex justify-end">
        <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50">
          <RefreshCw size={12} />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {CARDS.map(([key, title]) => (
          <section key={key} className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">{title}</h2>
            <JsonBlock data={bundle?.[key] || { loading }} />
          </section>
        ))}
      </div>
    </div>
  )
}
