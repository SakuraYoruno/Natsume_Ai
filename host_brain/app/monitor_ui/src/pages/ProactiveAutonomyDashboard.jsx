import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  Heart,
  RefreshCw,
  SendHorizontal,
  Sparkles,
  Waves,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../shared/ui/KeyValueRow.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useAppStore from '../store/useAppStore.js'
import { endpointGet } from '../shared/api/endpointClient.js'
import { ENDPOINT_REGISTRY } from '../shared/api/endpointRegistry.js'
import { asArray, compactText, formatBoolean, formatNumber } from '../utils/digitalLife.js'

const OUTCOME_LABELS = {
  responded: '被回应',
  annoyed: '被嫌烦',
  ignored: '被忽略',
  '': '—',
}

const VERDICT_LABELS = {
  leaked: '带着内心',
  partial: '部分',
  flat: '平淡/助手味',
  '': '—',
}

export default function ProactiveAutonomyDashboard() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await endpointGet(ENDPOINT_REGISTRY.observability.proactiveAutonomy)
      setData(payload || {})
    } catch (err) {
      setError(err?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    if (!pollingEnabled) return undefined
    const id = setInterval(reload, Math.max(3000, Number(pollingInterval) || 15000))
    return () => clearInterval(id)
  }, [pollingEnabled, pollingInterval, reload])

  const weather = data?.inner_weather || {}
  const calibration = data?.reach_out_calibration || {}
  const closure = data?.dialogue_closure || {}
  const projects = asArray(data?.active_projects)
  const topIntention = data?.top_intention || null
  const readiness = data?.proactive_readiness

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">主动自主性</h1>
          <p className="text-sm text-gray-500">她为什么找你、找得好不好——意图、就绪度、内在天气与落地校准。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={SendHorizontal}
        eyebrow="主动交互专题"
        title={weather.label || '平稳'}
        description={
          topIntention
            ? `当前有一条想说的事：${compactText(topIntention.title, 60)}`
            : '当前没有强意图，主要由内在状态决定是否开口。'
        }
        gradientClass="from-primary-50 via-white to-rose-50"
        metaItems={[
          `主动就绪度：${readiness != null ? formatNumber(readiness, 3) : '—'}`,
          `落地校准：${formatNumber(calibration.value, 3)}`,
          `收缩中：${formatBoolean(weather.withdrawn)}`,
        ]}
      />

      {error ? <NoticeBanner variant="error" title="主动自主性加载失败">{error}</NoticeBanner> : null}
      {data && data.available === false ? (
        <NoticeBanner variant="warning" title="life-state 暂无信号">
          内在循环可能尚未运行，主动就绪度按纯空闲门处理。
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          icon={Activity}
          label="主动就绪度"
          value={readiness != null ? formatNumber(readiness, 3) : '—'}
          hint="天气冲动 / 项目成熟取大，按校准阻尼"
          tone="text-rose-600"
        />
        <MetricCard
          icon={Waves}
          label="外呼冲动"
          value={formatNumber(weather.reach_out_bias, 3)}
          hint={weather.label || '内在天气'}
          tone="text-blue-600"
        />
        <MetricCard
          icon={Heart}
          label="落地校准"
          value={formatNumber(calibration.value, 3)}
          hint={`${OUTCOME_LABELS[String(calibration.last_outcome || '')] ?? calibration.last_outcome ?? '—'} · ${calibration.samples ?? 0} 次`}
          tone="text-pink-600"
        />
        <MetricCard
          icon={Sparkles}
          label="记忆失真偏置"
          value={formatNumber(weather.memory_distortion_bias, 3)}
          hint="越高对话浮现记忆越少"
          tone="text-indigo-600"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Waves size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">内在天气</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="天气标签" value={weather.label || '—'} />
            <KeyValueRow label="疲劳" value={formatNumber(weather.fatigue, 3)} />
            <KeyValueRow label="孤独" value={formatNumber(weather.loneliness, 3)} />
            <KeyValueRow label="烦躁" value={formatNumber(weather.agitation, 3)} />
            <KeyValueRow label="回复简短偏置" value={formatNumber(weather.reply_brevity_bias, 3)} />
            <KeyValueRow label="是否收缩" value={formatBoolean(weather.withdrawn)} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Heart size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">落地校准 & 对话闭环</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="校准值" value={formatNumber(calibration.value, 3)} />
            <KeyValueRow label="最近落地" value={OUTCOME_LABELS[String(calibration.last_outcome || '')] ?? calibration.last_outcome ?? '—'} />
            <KeyValueRow label="样本数" value={calibration.samples ?? 0} />
            <KeyValueRow label="对话内心态裁决" value={VERDICT_LABELS[String(closure.verdict || '')] ?? closure.verdict ?? '—'} />
            <KeyValueRow label="presence / coherence" value={`${formatNumber(closure.presence_closure, 2)} / ${formatNumber(closure.coherence_closure, 2)}`} />
            <KeyValueRow label="闭环总分" value={formatNumber(closure.closure_score, 3)} />
          </div>
        </div>

        <div className="card p-5 lg:col-span-2">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">活跃自主项目（她惦记的事）</h2>
          </div>
          {topIntention ? (
            <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              想开口的意图：「{compactText(topIntention.title, 60)}」（强度 {formatNumber(topIntention.pull, 2)}）
            </div>
          ) : null}
          <div className="mt-4 space-y-2">
            {projects.length > 0 ? (
              projects.slice(0, 8).map((project, index) => (
                <div
                  key={`${project.project_id || index}`}
                  className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-gray-800">{compactText(project.title || project.project_id, 48)}</span>
                    <span className="text-xs text-gray-400">
                      {project.origin === 'emergent' ? '涌现' : '核心'} · {formatNumber(project.progress, 2)}
                    </span>
                  </div>
                  {project.next_step ? (
                    <div className="mt-1 text-xs text-gray-500">{compactText(project.next_step, 120)}</div>
                  ) : null}
                </div>
              ))
            ) : (
              <EmptyStateCard title="暂无活跃自主项目" hint="她还没长出在惦记的事。" className="p-4" />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
