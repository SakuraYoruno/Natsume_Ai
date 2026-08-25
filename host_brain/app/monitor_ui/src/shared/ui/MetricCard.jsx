
export default function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'text-gray-900',
  variant = 'inline',
  className = '',
}) {
  if (variant === 'stacked') {
    return (
      <div className={`card relative overflow-hidden p-4 text-center ${className}`.trim()}>
        <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-primary-200 to-transparent" />
        {Icon ? (
          <span className="mx-auto mb-1 inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/70 bg-white/75 text-primary shadow-sm">
            <Icon size={15} />
          </span>
        ) : null}
        <div className={`text-2xl font-bold ${tone}`}>{value ?? '--'}</div>
        <div className="mt-1 text-xs text-gray-500">{label}</div>
        <div className="mt-0.5 text-xs text-gray-400">{hint ?? '--'}</div>
      </div>
    )
  }

  return (
    <div className={`card relative overflow-hidden p-4 ${className}`.trim()}>
      <div className="pointer-events-none absolute inset-x-4 top-0 h-px bg-gradient-to-r from-transparent via-primary-200 to-transparent" />
      <div className="flex items-center gap-2 text-xs text-gray-500">
        {Icon ? (
          <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-white/70 bg-white/70 text-primary shadow-sm">
            <Icon size={13} />
          </span>
        ) : null}
        <span>{label}</span>
      </div>
      <div className={`mt-2 text-2xl font-bold ${tone}`}>{value ?? '--'}</div>
      <div className="mt-1 text-xs text-gray-400">{hint ?? '--'}</div>
    </div>
  )
}
