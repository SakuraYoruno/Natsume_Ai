import React from 'react'
import {
  RefreshCw,
  Zap,
} from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../shared/ui/LoadingNotice.jsx'
import MetricCard from '../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import OrganSystemOverview from './lifeBody/OrganSystemOverview.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import { formatDateTime } from '../../../shared/formatters.js'
import { formatRuntimeModeSourceText } from '../runtimeModeModel.js'
import { formatMode } from '../../../utils/digitalLife.js'

function ModeButton({ view, onSwitch, switching }) {
  return (
    <button
      className={[
        'relative flex min-h-[112px] flex-col items-center justify-center gap-1 rounded-xl border px-4 py-3 text-center transition-all duration-200',
        view.isActive ? `${view.colors.active} shadow-sm` : 'border-gray-200 bg-white',
        view.canSwitch ? `${view.colors.hover} cursor-pointer` : '',
        view.isDisabled ? 'cursor-not-allowed opacity-40' : '',
      ].join(' ').trim()}
      onClick={() => view.canSwitch && onSwitch(view.mode)}
      disabled={view.isDisabled || switching}
      title={view.title}
    >
      <span className="text-2xl">{view.meta.emoji}</span>
      <span className="text-sm font-semibold text-gray-800">{view.meta.label}</span>
      <span className="text-[11px] leading-5 text-gray-500">{view.meta.desc}</span>
      {view.isActive ? (
        <span className="absolute -right-1 -top-1 flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-green-500" />
        </span>
      ) : null}
    </button>
  )
}

function RuntimeModeSwitchNotice({ switchResult }) {
  if (!switchResult) return null
  return (
    <NoticeBanner
      variant={switchResult.success ? 'success' : 'error'}
      title={switchResult.success ? '模式切换成功' : '模式切换失败'}
    >
      {switchResult.success
        ? `${formatMode(switchResult.from_mode)} → ${formatMode(switchResult.to_mode)}`
        : switchResult.error || '未知错误'}
    </NoticeBanner>
  )
}

export default function RuntimeModeSurface({
  autoRuleRows,
  consoleError,
  endocrineOrganModel,
  heroDescription,
  heroMetaItems,
  heroTitle,
  history,
  lastUpdatedAt,
  loading,
  metricCards,
  modeButtons,
  refreshing,
  runtimeModeLoadState,
  showUnavailableNotice,
  stateAxisItems,
  status,
  switchResult,
  switching,
  onRefresh,
  onSwitchMode,
  onToggleRule,
}) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">生命体存在态轴</h1>
          <p className="text-sm text-gray-500">管理数字生命体的硬状态，不直接覆盖离线时长、动机和回复姿态。</p>
        </div>
        <button className="btn-secondary" onClick={() => onRefresh?.()} disabled={switching || loading || refreshing}>
          <RefreshCw size={14} className={switching || loading || refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Zap}
        eyebrow="当前生命体状态"
        title={heroTitle}
        description={heroDescription}
        gradientClass="from-primary-50 via-white to-indigo-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={runtimeModeLoadState}
            sampleSize={history.length}
            sampleLabel="切换记录"
            updatedAt={lastUpdatedAt}
            source="sys-mode"
            definition="存在态轴页聚合当前 sys_mode、自动切换规则与最近切换历史；它表达的是最外层硬状态，不等于完整人格状态。"
          />
        )}
      />

      {loading && !status && !consoleError ? (
        <LoadingNotice>正在同步生命体存在态轴快照…</LoadingNotice>
      ) : null}

      <RuntimeModeSwitchNotice switchResult={switchResult} />

      {consoleError ? <NoticeBanner variant={status ? 'warning' : 'error'} title="状态同步异常">{consoleError}</NoticeBanner> : null}

      {showUnavailableNotice ? (
        <NoticeBanner variant="warning">生命体存在态轴当前未返回可用快照。</NoticeBanner>
      ) : null}

      <OrganSystemOverview model={endocrineOrganModel} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard
            key={card.key}
            label={card.label}
            value={card.value}
            hint={card.hint}
            tone={card.tone}
          />
        ))}
      </div>

      <div className="card p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-800">可切换模式</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {modeButtons.map((view) => (
            <ModeButton
              key={view.mode}
              view={view}
              onSwitch={onSwitchMode}
              switching={switching}
            />
          ))}
        </div>
        <div className="mt-3 text-xs text-gray-500">
          灰色不可点击表示当前状态机不支持直接跳转；这是最外层硬状态，不等于完整人格状态。
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-800">自动切换规则</h2>
          <div className="space-y-3">
            {autoRuleRows.map((rule) => (
              <div key={rule.key} className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-gray-700">{rule.label}</div>
                  <div className="text-xs leading-5 text-gray-400">{rule.desc}</div>
                </div>
                <button
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                    rule.enabled ? 'bg-primary' : 'bg-gray-300'
                  }`}
                  onClick={() => onToggleRule(rule.key, rule.enabled)}
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                      rule.enabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-800">不是只有存在态轴</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {stateAxisItems.map((item) => (
              <div key={item.title} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <div className="text-sm font-semibold text-gray-800">{item.title}</div>
                <p className="mt-2 text-xs leading-5 text-gray-500">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-800">切换历史</h2>
        {history.length > 0 ? (
          <div className="space-y-2">
            {history.map((item, index) => (
              <div key={item.event_id || index} className={`flex items-center gap-3 rounded-lg px-3 py-2 text-xs ${item.success ? 'bg-gray-50' : 'bg-red-50'}`}>
                <span className="font-medium text-gray-700">
                  {formatMode(item.from_mode)} → {formatMode(item.to_mode)}
                </span>
                <span className="rounded bg-gray-200 px-1.5 py-0.5 text-gray-600">{formatRuntimeModeSourceText(item.source)}</span>
                <span className="text-gray-400">{formatDateTime(item.ts_ms)}</span>
                {!item.success ? <span className="text-red-500">失败</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <EmptyStateCard title="暂无切换历史" hint="状态切换后会在这里留下记录。" className="p-6" />
        )}
      </div>
    </div>
  )
}
