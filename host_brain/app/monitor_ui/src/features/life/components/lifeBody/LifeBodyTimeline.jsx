import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Brain,
  Database,
  Heart,
  MessageSquareQuote,
  Orbit,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import FreshnessPill from '../../../../shared/ui/observability/FreshnessPill.jsx'
import { buildLifeTimelinePath } from '../../../../models/lifeBodyRoutes.js'
import {
  LIFE_BODY_SEVERITY_CLASSES,
  LIFE_BODY_TIMELINE_SEVERITY_LABELS,
} from '../../../../shared/statusMeta.js'

const ICON_MAP = {
  snapshot: Activity,
  emotion: Heart,
  bond: Orbit,
  request: MessageSquareQuote,
  memory: Database,
  tick: Brain,
  execution: Sparkles,
  pathology: ShieldAlert,
  immune: ShieldAlert,
  self: Orbit,
}

export default function LifeBodyTimeline({
  events = [],
  title = '生命时间轴预览',
  description = 'Phase A 先把现有 snapshot、记忆、节律和闭环信号收成一条可读时间线，后续再升级成完整事件页。',
}) {
  if (!events.length) return null

  const shortId = (value, length = 12) => {
    const text = String(value || '').trim()
    if (!text) return ''
    return text.length > length ? `${text.slice(0, length)}…` : text
  }

  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_34%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.06),_transparent_30%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{title}</div>
            <div className="mt-1 text-sm text-gray-600">{description}</div>
          </div>
          <div className="rounded-full border border-white/80 bg-white/85 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${events.length} 个最近生命事件`}
          </div>
        </div>

        <div className="mt-5 space-y-4">
          {events.map((item, index) => {
            const Icon = ICON_MAP[item.type] || Activity

            return (
              <div key={item.id} className="relative pl-10">
                {index !== events.length - 1 ? (
                  <span className="absolute left-[17px] top-8 h-[calc(100%+12px)] w-px bg-gradient-to-b from-primary-200 via-primary-100 to-transparent" />
                ) : null}
                <span className="absolute left-0 top-1 inline-flex h-9 w-9 items-center justify-center rounded-2xl border border-white/80 bg-white/90 text-primary shadow-sm">
                  <Icon size={16} />
                </span>

                <div className="rounded-3xl border border-white/80 bg-white/86 p-4 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        {item.typeLabel ? (
                          <span className="rounded-full border border-white/80 bg-white/85 px-2.5 py-1 text-[11px] font-medium text-gray-500 shadow-sm">
                            {item.typeLabel}
                          </span>
                        ) : null}
                        {item.organLabel ? (
                          <span className="rounded-full border border-white/80 bg-white/85 px-2.5 py-1 text-[11px] font-medium text-gray-500 shadow-sm">
                            {item.organLabel}
                          </span>
                        ) : null}
                        {item.sourceSessionId ? (
                          <Link
                            to={buildLifeTimelinePath({ type: item.type, sessionId: item.sourceSessionId })}
                            className="rounded-full border border-white/80 bg-white/85 px-2.5 py-1 text-[11px] font-medium text-gray-500 shadow-sm hover:border-primary-200 hover:bg-primary-50 hover:text-primary-700"
                          >
                            {`session ${shortId(item.sourceSessionId)}`}
                          </Link>
                        ) : null}
                        {item.sourceTickId ? (
                          <Link
                            to={buildLifeTimelinePath({ type: item.type || 'tick', tickId: item.sourceTickId })}
                            className="rounded-full border border-white/80 bg-white/85 px-2.5 py-1 text-[11px] font-medium text-gray-500 shadow-sm hover:border-primary-200 hover:bg-primary-50 hover:text-primary-700"
                          >
                            {`tick ${shortId(item.sourceTickId)}`}
                          </Link>
                        ) : null}
                        {item.severity ? (
                          <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${LIFE_BODY_SEVERITY_CLASSES[item.severity] || LIFE_BODY_SEVERITY_CLASSES.info}`}>
                            {LIFE_BODY_TIMELINE_SEVERITY_LABELS[item.severity] || LIFE_BODY_TIMELINE_SEVERITY_LABELS.info}
                          </span>
                        ) : null}
                      </div>
                      <div className="text-sm font-semibold text-gray-900">{item.title}</div>
                      <div className="mt-2 text-sm leading-6 text-gray-600">{item.detail}</div>
                    </div>
                    <FreshnessPill timestamp={item.timestamp} staleAfterMs={180000} />
                  </div>

                  {item.representativeRefs?.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {item.representativeRefs.map((ref) => (
                        <Link
                          key={`${item.id}-${ref.id}`}
                          to={ref.to}
                          className="rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                        >
                          {ref.subtitle}
                        </Link>
                      ))}
                    </div>
                  ) : null}

                  {item.to || item.secondaryTo ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {item.to ? (
                        <Link
                          to={item.to}
                          className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                        >
                          {item.linkLabel || '查看详情'}
                        </Link>
                      ) : null}
                      {item.secondaryTo ? (
                        <Link
                          to={item.secondaryTo}
                          className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                        >
                          {item.secondaryLinkLabel || '继续追踪'}
                        </Link>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
