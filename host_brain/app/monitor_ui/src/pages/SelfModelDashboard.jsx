import React from 'react'
import { Brain, Heart, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../shared/ui/KeyValueRow.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  SELF_MODEL_RUNTIME_WINDOW_MINUTES,
  buildSelfModelDashboardViewModel,
} from '../features/life/selfModelDashboardModel.js'
import useAppStore from '../store/useAppStore.js'

function ListBlock({ title, items, emptyText }) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
      <div className="mt-4 space-y-2">
        {items.length > 0 ? (
          items.map((item, index) => (
            <div
              key={`${title}-${index}`}
              className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
            >
              {item}
            </div>
          ))
        ) : (
          <EmptyStateCard title="暂无条目" hint={emptyText} className="p-4" />
        )}
      </div>
    </div>
  )
}

function SummaryBlock({ title, text }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {text}
      </div>
    </div>
  )
}

export default function SelfModelDashboard() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const { runtimeSnapshot, loading, error, reload } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: SELF_MODEL_RUNTIME_WINDOW_MINUTES,
  })

  const {
    formattedRiskTags,
    heroView,
    sampleMeta,
    organModel,
    metricCards,
    growthView,
    infoRows,
    listBlocks,
  } = buildSelfModelDashboardViewModel({ runtimeSnapshot, loading, error })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">前额叶自我模型</h1>
          <p className="text-sm text-gray-500">自我模型、反思脉冲、成长里程碑与叙事护栏。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <PageHero
        icon={Brain}
        eyebrow="前额叶人格与成长"
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-primary-50 via-white to-violet-50"
        metaItems={heroView.metaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {error ? (
        <NoticeBanner variant="error" title="自我模型数据加载失败">
          {error}
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={organModel} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        {metricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} hint={card.hint} tone={card.tone} />
        ))}
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-2">
          <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">非监督成长</h2>
        </div>
        {growthView.hasAnyGrowth ? (
          <>
            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="space-y-3">
                <KeyValueRow label="成长总览" value={growthView.primarySummary} maxValueWidthClassName="max-w-[72%]" />
                <KeyValueRow label="最近周期" value={growthView.lastCycleLabel} />
                <KeyValueRow label="成长周期数" value={growthView.cycleCount} />
                <KeyValueRow label="活跃主题数" value={growthView.activeThemes.length} />
                {growthView.awarenessItems.map((item) => (
                  <KeyValueRow
                    key={item.key}
                    label={item.label}
                    value={item.value}
                    maxValueWidthClassName="max-w-[72%]"
                  />
                ))}
              </div>

              <div className="space-y-3">
                {growthView.insightSections.length > 0 ? (
                  growthView.insightSections.map((item) => (
                    <SummaryBlock key={item.key} title={item.label} text={item.value} />
                  ))
                ) : (
                  <EmptyStateCard title="暂无成长摘要" hint="当前快照还没有聚合出可展示的成长文本。" className="p-4" />
                )}
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium text-gray-500">活跃成长主题</div>
              <div className="flex flex-wrap gap-2">
                {growthView.activeThemes.length > 0 ? (
                  growthView.activeThemes.map((item) => (
                    <span
                      key={item}
                      className="rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2.5 py-1 text-xs font-medium text-fuchsia-700"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-gray-400">当前还没有稳定的主题聚合。</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyStateCard
            title="非监督成长尚在积累"
            hint="后端已经接入成长字段，但当前窗口还没有足够的成长周期、主题或感知文本。"
            className="mt-4 p-4"
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="card p-5">
        <div className="flex items-center gap-2">
          <Brain size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">前额叶自我模型</h2>
        </div>
          <div className="mt-4 space-y-3">
            {infoRows.selfRows.map((row) => (
              <KeyValueRow key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">自我反思脉冲</h2>
          </div>
          <div className="mt-4 space-y-3">
            {infoRows.reflectionRows.map((row) => (
              <KeyValueRow key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">叙事护栏</h2>
          </div>
          <div className="mt-4 space-y-3">
            {infoRows.guardrailRows.map((row) => (
              <KeyValueRow key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-gray-500">风险标签</div>
            <div className="flex flex-wrap gap-2">
              {formattedRiskTags.length > 0 ? (
                formattedRiskTags.map((item) => (
                  <span
                    key={item}
                    className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-xs font-medium text-orange-700"
                  >
                    {item}
                  </span>
                ))
              ) : (
                <span className="text-sm text-gray-400">暂无风险标签</span>
              )}
            </div>
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Heart size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">关系摘要</h2>
          </div>
          <div className="mt-4 space-y-3">
            {infoRows.relationshipRows.map((row) => (
              <KeyValueRow key={row.label} label={row.label} value={row.value} />
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        {listBlocks.map((block) => (
          <ListBlock key={block.title} title={block.title} items={block.items} emptyText={block.emptyText} />
        ))}
      </div>
    </div>
  )
}
