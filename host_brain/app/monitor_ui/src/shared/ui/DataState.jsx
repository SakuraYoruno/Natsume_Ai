import { DATA_STATE_COPY } from '../content/terminology.js'
import EmptyStateCard from './EmptyStateCard.jsx'
import { LoadingNotice } from './LoadingNotice.jsx'
import NoticeBanner from './NoticeBanner.jsx'

function resolveState({ state, loading, error, empty }) {
  if (state) return state
  if (loading) return 'loading'
  if (error) return 'error'
  if (empty) return 'empty'
  return 'ready'
}

function InlineState({ meta, title, hint, className = '' }) {
  return (
    <div className={`rounded-lg border px-3 py-2 text-sm ${className}`.trim()}>
      <div className="font-semibold">{title || meta.title}</div>
      {hint || meta.hint ? <div className="mt-0.5 text-xs opacity-80">{hint || meta.hint}</div> : null}
    </div>
  )
}

export default function DataState({
  loading,
  error,
  empty,
  state,
  title,
  hint,
  emptyTitle = '暂无数据',
  emptyHint = '当前接口没有返回样本。',
  action = null,
  compact = false,
  className = '',
  children,
}) {
  const resolvedState = resolveState({ state, loading, error, empty })
  if (resolvedState === 'ready') {
    return children
  }

  const meta = DATA_STATE_COPY[resolvedState] || DATA_STATE_COPY.unavailable
  const resolvedTitle = title || (resolvedState === 'empty' ? emptyTitle : meta.title)
  const resolvedHint = hint || (resolvedState === 'empty' ? emptyHint : meta.hint)

  if (resolvedState === 'loading' || resolvedState === 'refreshing') {
    return <LoadingNotice title={resolvedTitle} className={className}>{resolvedHint}</LoadingNotice>
  }

  if (resolvedState === 'error') {
    return (
      <NoticeBanner variant="error" title={resolvedTitle} className={className}>
        {String(error?.message || error || resolvedHint)}
      </NoticeBanner>
    )
  }

  if (compact) {
    const stateClassName = {
      empty: 'border-gray-100 bg-gray-50 text-gray-600',
      no_sample: 'border-gray-100 bg-gray-50 text-gray-600',
      unavailable: 'border-slate-200 bg-slate-50 text-slate-600',
      backend_off: 'border-orange-200 bg-orange-50 text-orange-700',
      lab_disabled: 'border-orange-200 bg-orange-50 text-orange-700',
      unauthorized: 'border-amber-200 bg-amber-50 text-amber-700',
    }[resolvedState] || 'border-gray-100 bg-gray-50 text-gray-600'
    return (
      <InlineState
        meta={meta}
        title={resolvedTitle}
        hint={resolvedHint}
        className={`${stateClassName} ${className}`.trim()}
      />
    )
  }

  if (meta.variant === 'empty') {
    return (
      <div className={className}>
        <EmptyStateCard title={resolvedTitle} hint={resolvedHint} />
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    )
  }

  return (
    <NoticeBanner variant={meta.variant || 'info'} title={resolvedTitle} className={className}>
      {resolvedHint}
      {action ? <div className="mt-2">{action}</div> : null}
    </NoticeBanner>
  )
}
