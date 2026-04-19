import React from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Radar, Stethoscope, Waypoints } from 'lucide-react'

const ICONS = {
  pathology: Stethoscope,
  organ: Radar,
  timeline: Waypoints,
}

const TONE_CLASS = {
  critical: 'border-red-200 bg-red-50/90 text-red-700',
  warning: 'border-orange-200 bg-orange-50/90 text-orange-700',
  stable: 'border-emerald-200 bg-emerald-50/90 text-emerald-700',
  pending: 'border-gray-200 bg-gray-50/90 text-gray-600',
  info: 'border-blue-200 bg-blue-50/90 text-blue-700',
}

export default function LifeBodyFocusPanel({ guidance }) {
  if (!guidance?.actions?.length) return null

  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,_rgba(41,112,255,0.08),_transparent_30%),radial-gradient(circle_at_bottom_left,_rgba(16,185,129,0.08),_transparent_32%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">驾驶舱下一步</div>
            <div className="mt-1 text-sm text-gray-600">
              首页不只告诉你“现在怎么样”，还直接给出当前最值得先追的诊断路径。
            </div>
          </div>
          <div className="rounded-full border border-white/80 bg-white/80 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${guidance.actions.length} 条优先动作`}
          </div>
        </div>

        <div className="mt-4 rounded-3xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">当前判断</div>
          <div className="mt-2 text-sm leading-6 text-gray-700">{guidance.summary}</div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          {guidance.actions.map((item) => {
            const Icon = ICONS[item.id] || ArrowRight
            const toneClass = TONE_CLASS[item.tone] || TONE_CLASS.info

            return (
              <div key={item.id} className="rounded-3xl border border-white/80 bg-white/86 p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-white/80 bg-white/90 text-primary shadow-sm">
                      <Icon size={18} />
                    </span>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{item.eyebrow}</div>
                      <div className="mt-1 text-sm font-semibold text-gray-900">{item.title}</div>
                    </div>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${toneClass}`}>
                    {item.tone === 'critical' ? '重压' : item.tone === 'warning' ? '波动' : item.tone === 'stable' ? '稳定' : item.tone === 'pending' ? '待接入' : '信号'}
                  </span>
                </div>

                <div className="mt-4 space-y-3">
                  <div className="rounded-2xl border border-white/75 bg-white/82 px-3 py-3 text-sm leading-6 text-gray-700 shadow-sm">
                    {item.detail}
                  </div>
                  <div className="rounded-2xl border border-white/75 bg-white/82 px-3 py-3 text-xs leading-5 text-gray-500 shadow-sm">
                    <span className="font-medium text-gray-700">为什么先看：</span>
                    {item.impact}
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Link
                    to={item.to}
                    className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                  >
                    {item.label}
                    <ArrowRight size={12} />
                  </Link>
                  {item.secondaryTo ? (
                    <Link
                      to={item.secondaryTo}
                      className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                    >
                      {item.secondaryLabel || '继续追踪'}
                    </Link>
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
