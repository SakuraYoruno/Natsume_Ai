import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import {
  buildActionPlanStatsView,
  buildPermissionGuardStatsView,
} from '../toolsOperationsModel.js'

function StatsCards({ cards }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map(({ label, value, color }) => (
        <div key={label} className="rounded bg-gray-50 p-3 text-center">
          <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
          <div className="mt-0.5 text-xs text-gray-400">{label}</div>
        </div>
      ))}
    </div>
  )
}

function DistributionRows({
  title,
  items,
  labelField,
  valueField,
  percentField = 'pct',
  barClassName,
  labelClassName = 'w-20',
  renderSuffix,
}) {
  if (items.length === 0) return null

  return (
    <div>
      <div className="mb-2 text-xs font-semibold text-gray-500">{title}</div>
      <div className="space-y-1.5">
        {items.map((item) => (
          <div key={item[labelField]} className="flex items-center gap-2">
            <div className={`${labelClassName} truncate font-mono text-xs text-gray-600`}>{item[labelField]}</div>
            <div className="h-2 flex-1 rounded-full bg-gray-100">
              <div className={`h-2 rounded-full ${barClassName}`} style={{ width: `${item[percentField]}%` }} />
            </div>
            <div className="w-10 text-right text-xs text-gray-500">{item[valueField]}</div>
            {renderSuffix ? renderSuffix(item) : null}
          </div>
        ))}
      </div>
    </div>
  )
}

export function PermissionGuardStats({
  data,
  loading,
  refreshing,
  stale,
  error,
  updatedAt,
  windowMinutes = 1440,
}) {
  const statsView = buildPermissionGuardStatsView({
    data,
    loading,
    refreshing,
    stale,
    error,
  })

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">权限拦截统计（近 24 小时）</div>
      <SampleMeta
        loadState={statsView.loadState}
        windowMinutes={windowMinutes}
        sampleSize={statsView.totalChecks}
        sampleLabel="校验"
        updatedAt={updatedAt}
        source="permission-guard"
        definition="展示工具权限校验总量、拦截量和高频被拦截工具，便于识别误拦或异常提案。"
      />

      {error ? (
        <NoticeBanner variant="warning" title="权限统计暂不可用">
          {error}
        </NoticeBanner>
      ) : null}

      {statsView.showNoSampleNotice ? (
        <NoticeBanner variant="info">当前窗口还没有权限校验样本。</NoticeBanner>
      ) : null}

      <StatsCards cards={statsView.statCards} />

      <DistributionRows
        title="高频拦截工具"
        items={statsView.topDeniedTools}
        labelField="tool_name"
        valueField="deny_count"
        barClassName="bg-violet-400"
        labelClassName="w-36"
        renderSuffix={(tool) => (
          tool.proposer_class ? (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-400">{tool.proposer_class}</span>
          ) : null
        )}
      />
    </div>
  )
}

export function ActionPlanStats({
  data,
  loading,
  refreshing,
  stale,
  error,
  updatedAt,
  windowMinutes = 1440,
}) {
  const statsView = buildActionPlanStatsView({
    data,
    loading,
    refreshing,
    stale,
    error,
  })

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-semibold text-gray-700">执行计划统计（近 24 小时）</div>
      <SampleMeta
        loadState={statsView.loadState}
        windowMinutes={windowMinutes}
        sampleSize={statsView.totalPlans}
        sampleLabel="计划"
        updatedAt={updatedAt}
        source="action-plan"
        definition="展示执行计划成功、降级和指令类型分布，用于判断规划器是否频繁 fallback。"
      />

      {error ? (
        <NoticeBanner variant="warning" title="执行计划统计暂不可用">
          {error}
        </NoticeBanner>
      ) : null}

      {statsView.showNoSampleNotice ? (
        <NoticeBanner variant="info">当前窗口还没有执行计划样本。</NoticeBanner>
      ) : null}

      <StatsCards cards={statsView.statCards} />

      <DistributionRows
        title="指令类型分布"
        items={statsView.topActionTypes}
        labelField="action_type"
        valueField="pct"
        barClassName="bg-blue-400"
      />
    </div>
  )
}
