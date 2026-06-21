import React from 'react'
import { formatRelativeTime } from '../../../models/loadState.js'

export default function FreshnessPill({ timestamp, staleAfterMs = 120000 }) {
  const value = typeof timestamp === 'number' ? timestamp : new Date(timestamp || 0).getTime()
  const ageMs = value ? Math.max(0, Date.now() - value) : null
  const isStale = ageMs != null && ageMs > staleAfterMs
  const className = isStale
    ? 'border-amber-200 bg-amber-50 text-amber-700'
    : 'border-white/80 bg-white/75 text-gray-500'

  return (
    <span className={`rounded-full border px-3 py-1 text-xs shadow-sm ${className}`}>
      {formatRelativeTime(value)}
    </span>
  )
}
