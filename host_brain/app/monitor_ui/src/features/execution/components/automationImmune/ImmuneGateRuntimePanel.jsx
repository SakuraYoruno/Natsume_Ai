function RuntimeField({ label, value }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
      <div className="text-gray-400">{label}</div>
      <div className="mt-1 font-semibold text-gray-800">{value}</div>
    </div>
  )
}

export default function ImmuneGateRuntimePanel({ runtimeState, counters }) {
  return (
    <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">运行配置</div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <RuntimeField label="enabled" value={String(runtimeState?.enabled ?? '-')} />
        <RuntimeField label="blocking" value={String(runtimeState?.blocking_enabled ?? '-')} />
        <RuntimeField
          label="warn / block"
          value={`${runtimeState?.warn_threshold ?? '-'} / ${runtimeState?.block_threshold ?? '-'}`}
        />
        <RuntimeField
          label="rules"
          value={`${runtimeState?.template_rule_count ?? 0} / ${runtimeState?.total_rule_count ?? 0}`}
        />
        <RuntimeField label="last decision" value={runtimeState?.last_decision || '-'} />
        <RuntimeField label="last risk" value={runtimeState?.last_risk_level || '-'} />
      </div>
      <div className="mt-3 text-xs text-gray-500">
        counter(total/allow/report/block): {counters.total ?? 0} / {counters.allow ?? 0} / {counters.report ?? 0} / {counters.block ?? 0}
      </div>
    </div>
  )
}
