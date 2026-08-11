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
          <h1 className="text-xl font-semibold text-gray-900">社交脑</h1>
          <p className="text-sm text-gray-500">查看迁移窗口内的历史 Bond 投影；真实关系由 Digital Life 生命周期维护。</p>
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
        eyebrow="社交脑快照"
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
            <h2 className="text-sm font-semibold text-gray-800">用户列表</h2>
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
            <EmptyStateCard title="暂无历史关系投影" hint="不会通过人工补录或自动加分创建关系数据。" className="p-4" />
          )}
        </div>

        <div className="space-y-4">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">阶段分布</h2>
            </div>
            <StageDistribution items={stageDist} />
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Heart size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">用户详情</h2>
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
              <EmptyStateCard title="请选择一个用户" hint="选择后这里会显示该用户的关系快照。" className="p-4" />
            )}
          </div>
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">关系写入边界</h2>
          </div>
          <NoticeBanner variant="info">
            人工补录和自动加分已退役。裂痕、修复、主动权限与关系纹理由 canonical Digital Life relationship episode / aftereffect 生命周期产生，历史 Bond 仅在回滚窗口内只读。
          </NoticeBanner>
        </div>
      </div>
    </div>
  )
}
