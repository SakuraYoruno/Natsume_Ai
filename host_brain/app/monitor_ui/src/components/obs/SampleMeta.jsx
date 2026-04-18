import React from 'react'
import LoadStateBadge from './LoadStateBadge.jsx'
import FreshnessPill from './FreshnessPill.jsx'
import { formatWindowLabel } from '../../models/loadState.js'

export default function SampleMeta({
  loadState,
  loadStateDetail,
  windowMinutes,
  sampleSize,
  sampleLabel = '样本',
  updatedAt,
  source,
  definition,
  className = '',
}) {
  return (
    <div className={`rounded-2xl border border-white/80 bg-white/75 p-3 shadow-sm backdrop-blur-sm ${className}`.trim()}>
      <div className="flex flex-wrap items-center gap-2">
        {loadState ? <LoadStateBadge state={loadState} detail={loadStateDetail} /> : null}
        {windowMinutes ? (
          <span className="rounded-full border border-white/80 bg-white/75 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {formatWindowLabel(windowMinutes)}
          </span>
        ) : null}
        {sampleSize != null ? (
          <span className="rounded-full border border-white/80 bg-white/75 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${sampleLabel} ${sampleSize}`}
          </span>
        ) : null}
        {source ? (
          <span className="rounded-full border border-white/80 bg-white/75 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`来源 ${source}`}
          </span>
        ) : null}
        {updatedAt ? <FreshnessPill timestamp={updatedAt} /> : null}
      </div>

      {definition ? (
        <div className="mt-2 text-xs leading-5 text-gray-500">
          {definition}
        </div>
      ) : null}
    </div>
  )
}
