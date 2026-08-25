import {
  Brain,
  HeartPulse,
  MessagesSquare,
  MoonStar,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../shared/ui/KeyValueRow.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import {
  RESPONSE_POLICY_RUNTIME_WINDOW_MINUTES,
  buildRenderPolicyDashboardViewModel,
} from '../features/life/renderPolicyDashboardModel.js'
import VitalLoopSummaryPanel from '../features/life/components/VitalLoopSummaryPanel.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'

const TAG_TONE_CLASS_NAMES = {
  orange: 'border-orange-200 bg-orange-50 text-orange-700',
  purple: 'border-purple-200 bg-purple-50 text-purple-700',
  gray: 'border-gray-200 bg-gray-50 text-gray-700',
}

function getTagToneClassName(tone) {
  return TAG_TONE_CLASS_NAMES[tone] || TAG_TONE_CLASS_NAMES.gray
}

function TagList({ items, tone = 'gray', emptyTitle, emptyHint }) {
  if (!items.length) {
    return <EmptyStateCard title={emptyTitle} hint={emptyHint} className="p-5" />
  }

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${getTagToneClassName(tone)}`}
        >
          {item}
        </span>
      ))}
    </div>
  )
}

function InfoRows({ rows }) {
  return rows.map((row) => <KeyValueRow key={row.label} label={row.label} value={row.value} />)
}

function TagBlockIcon({ icon }) {
  const Icon = icon === 'shield' ? ShieldCheck : Sparkles
  return <Icon size={15} className="text-gray-500" />
}

export default function RenderPolicyDashboard() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, loading, error, reload } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: RESPONSE_POLICY_RUNTIME_WINDOW_MINUTES,
  })

  const {
    heroView,
    metricCards,
    infoRows,
    tagBlocks,
    formattedDecisionChain,
  } = buildRenderPolicyDashboardViewModel({ runtimeSnapshot })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">回复策略</h1>
          <p className="text-sm text-gray-500">查看沉默机制、身体感、动机驱动与最终回复预算。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="实时回复决策"
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={heroView.metaItems}
      />

      {error ? <NoticeBanner variant="error" title="回复策略加载失败">{error}</NoticeBanner> : null}

      {runtimeSnapshot ? <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} compact showDetailLink={false} /> : null}

      {!runtimeSnapshot && !loading && !error ? (
        <EmptyStateCard
          title="暂无回复策略快照"
          hint="等待一轮完整对话采样后，这里会自动显示。"
          className="p-6"
        />
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} hint={card.hint} tone={card.tone} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MoonStar size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">沉默机制</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRows rows={infoRows.silenceRows} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <HeartPulse size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">身体感</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRows rows={infoRows.bodySenseRows} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MessagesSquare size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">回复预算</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRows rows={infoRows.renderPolicyRows} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">动机驱动</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRows rows={infoRows.motiveRows} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {tagBlocks.map((block) => (
          <div key={block.title} className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <TagBlockIcon icon={block.icon} />
              <h2 className="text-sm font-semibold text-gray-800">{block.title}</h2>
            </div>
            <TagList
              items={block.items}
              tone={block.tone}
              emptyTitle={block.emptyTitle}
              emptyHint={block.emptyHint}
            />
          </div>
        ))}
      </div>

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">决策链</h2>
        </div>
        <div className="space-y-2">
          {formattedDecisionChain.length > 0 ? (
            formattedDecisionChain.map((item, index) => (
              <div
                key={`${item}-${index}`}
                className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
              >
                {item}
              </div>
            ))
          ) : (
            <EmptyStateCard
              title="暂无决策链信息"
              hint="当前快照尚未写入完整的决策链。"
              className="p-5"
            />
          )}
        </div>
      </div>
    </div>
  )
}
