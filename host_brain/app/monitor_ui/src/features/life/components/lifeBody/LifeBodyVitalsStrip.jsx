import { Link } from 'react-router-dom'
import {
  Activity,
  Brain,
  Database,
  Heart,
  Orbit,
  ShieldAlert,
  Sparkles,
  Waves,
} from 'lucide-react'
import { LIFE_BODY_TONE_CLASSES } from '../../../../shared/statusMeta.js'

const ICON_MAP = {
  vitality: Activity,
  continuity: Brain,
  emotion: Heart,
  bond: Orbit,
  memory: Database,
  immune: ShieldAlert,
  execution: Sparkles,
  resilience: Waves,
}

export default function LifeBodyVitalsStrip({ vitals = [] }) {
  if (!vitals.length) return null

  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.06),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(244,63,94,0.06),_transparent_32%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">生命体征</div>
            <div className="mt-1 text-sm text-gray-600">
              顶部只保留生命体级指标，每一项都能直接映射到具体器官系统。
            </div>
          </div>
          <div className="rounded-full border border-white/80 bg-white/80 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${vitals.length} 项主体体征`}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 xl:grid-cols-4">
          {vitals.map((item) => {
            const Icon = ICON_MAP[item.id] || Activity
            const toneClass = LIFE_BODY_TONE_CLASSES[item.tone] || LIFE_BODY_TONE_CLASSES.pending

            return (
              <Link
                key={item.id}
                to={item.to}
                className="group rounded-3xl border border-white/80 bg-white/86 p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-white/80 bg-white/90 text-primary shadow-sm">
                    <Icon size={18} />
                  </span>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${toneClass}`}>
                    {item.organ}
                  </span>
                </div>
                <div className="mt-4 text-xs font-medium text-gray-500">{item.label}</div>
                <div className="mt-1 text-2xl font-semibold text-gray-900">{item.value || '--'}</div>
                <div className="mt-2 text-xs leading-5 text-gray-500">{item.hint || '—'}</div>
                <div className="mt-3 text-xs font-medium text-primary transition group-hover:text-primary-700">
                  进入对应器官页
                </div>
              </Link>
            )
          })}
        </div>
      </div>
    </section>
  )
}
