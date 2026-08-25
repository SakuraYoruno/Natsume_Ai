import { Heart, RefreshCw, ShieldCheck, Users } from 'lucide-react'

import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../../../shared/ui/KeyValueRow.jsx'
import MetricCard from '../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import StageDistribution from './bond/BondStageDistribution.jsx'
import OrganSystemOverview from './lifeBody/OrganSystemOverview.jsx'

export default function BondDashboardSurface({
  heroView,
  sampleMeta,
  noticeView,
  organModel,
  metricCards,
  detailView,
  users,
  stageDist,
  selectedUser,
  userDetail,
  loading,
  refreshing,
  detailLoading,
  detailRefreshing,
  onRefresh,
  onSelectUser,
}) {
  const refreshBusy = loading || refreshing || detailLoading || detailRefreshing

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">关系状态</h1>
          <p className="text-sm text-gray-500">查看 canonical runtime-snapshot 中的关系状态；真实关系由 Digital Life 生命周期维护。</p>
        </div>
        <button
          className="btn-secondary"
          onClick={() => { void onRefresh() }}
          disabled={refreshBusy}
        >
          <RefreshCw size={14} className={refreshBusy ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Heart}
        eyebrow="关系快照"
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-primary-50 via-white to-rose-50"
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

      {noticeView.overviewError ? (
        <NoticeBanner variant="error" title="关系数据加载失败">{noticeView.overviewError}</NoticeBanner>
      ) : null}

      {noticeView.noSample ? (
        <NoticeBanner variant="info" title="关系链路待采样">
          当前还没有可用的 Bond 用户或阶段样本，页面会保留中性空态。
        </NoticeBanner>
      ) : null}

      {noticeView.unavailable ? (
        <NoticeBanner variant="warning" title="关系观测暂不可用">
          Bond 相关接口当前未返回可用快照，建议稍后重试。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={organModel} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} hint={card.hint} tone={card.tone} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)_360px]">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Users size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">关系主体</h2>
          </div>
          {users.length > 0 ? (
            <div className="max-h-[420px] space-y-2 overflow-y-auto">
              {users.map((userId) => (
                <button
                  key={userId}
                  className={`w-full rounded-xl border px-3 py-2 text-left text-sm transition-colors ${
                    selectedUser === userId
                      ? 'border-primary-200 bg-primary-50 text-primary'
                      : 'border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100'
                  }`}
                  onClick={() => onSelectUser(userId)}
                >
                  {userId}
                </button>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无关系快照" hint="关系状态由 canonical Digital Life 生命周期产出。" className="p-4" />
          )}
        </div>

        <div className="space-y-4">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">当前阶段</h2>
            </div>
            <StageDistribution items={stageDist} />
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Heart size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">关系详情</h2>
            </div>
            {detailLoading || detailRefreshing ? (
              <NoticeBanner variant="info">正在加载用户关系详情…</NoticeBanner>
            ) : detailView.errorMessage ? (
              <NoticeBanner variant="warning" title="用户详情暂不可用">
                {detailView.errorMessage}
              </NoticeBanner>
            ) : userDetail ? (
              <div className="space-y-3">
                {detailView.rows.map((row) => (
                  <KeyValueRow key={row.label} label={row.label} value={row.value} />
                ))}
              </div>
            ) : (
              <EmptyStateCard title="暂无关系主体" hint="等待 canonical runtime-snapshot 产出关系快照。" className="p-4" />
            )}
          </div>
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">关系权限边界</h2>
          </div>
          <NoticeBanner variant="info">
            人工补录和自动加分不属于当前入口。裂痕、修复、主动权限与关系纹理由 canonical Digital Life relationship episode / aftereffect 生命周期产生，本页只读展示运行快照。
          </NoticeBanner>
        </div>
      </div>
    </div>
  )
}
