function ImmuneGateStatCard({ label, value, valueClassName = 'text-gray-700', children }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
      {children || <div className={`text-lg font-bold ${valueClassName}`}>{value}</div>}
      <div className="mt-1 text-xs text-gray-400">{label}</div>
    </div>
  )
}

export default function ImmuneGateStatsGrid({ runtimeStatus, statusClass, stats }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
      <ImmuneGateStatCard label="运行态">
        <div className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass}`}>
          {runtimeStatus.toUpperCase() || 'UNKNOWN'}
        </div>
      </ImmuneGateStatCard>
      <ImmuneGateStatCard label="总事件" value={stats?.total ?? 0} />
      <ImmuneGateStatCard label="允许" value={stats?.allow ?? 0} valueClassName="text-green-600" />
      <ImmuneGateStatCard label="报告" value={stats?.report ?? 0} valueClassName="text-amber-600" />
      <ImmuneGateStatCard label="拦截" value={stats?.block ?? 0} valueClassName="text-red-500" />
      <ImmuneGateStatCard label="拦截率" value={`${stats?.block_rate ?? 0}%`} valueClassName="text-red-500" />
    </div>
  )
}
