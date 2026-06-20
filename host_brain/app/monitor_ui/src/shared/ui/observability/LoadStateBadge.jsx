import React from 'react'
import { getLoadStateMeta } from '../../../models/loadState.js'

export default function LoadStateBadge({ state, detail }) {
  const meta = getLoadStateMeta(state)

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${meta.className}`}
      title={detail || meta.description}
    >
      {meta.label}
    </span>
  )
}
