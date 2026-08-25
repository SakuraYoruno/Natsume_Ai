import { Activity, Layers3, Stethoscope, Waypoints } from 'lucide-react'
import { LIFE_COPY } from '../../../shared/content/terminology.js'

const MODE_ICONS = {
  priority: Stethoscope,
  panorama: Layers3,
  events: Waypoints,
}

function CountCard({ icon: Icon, label, value, total, hint }) {
  return (
    <div className="rounded-lg border border-white/80 bg-white/86 px-3 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-semibold text-gray-500">
        <Icon size={14} className="text-primary" />
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-lg font-semibold text-gray-950">{value}</span>
        <span className="text-xs text-gray-400">/ {total}</span>
      </div>
      <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div>
    </div>
  )
}

export default function DigitalLifeFocusLens({
  model,
  onModeChange,
}) {
  if (!model) return null

  return (
    <section
      aria-label="Cockpit Focus Lens"
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Life Systems lens</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{LIFE_COPY.focusLensTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{LIFE_COPY.focusLensDescription}</p>
        </div>
        <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-1">
          {model.options.map((item) => {
            const Icon = MODE_ICONS[item.key] || Activity
            const selected = model.mode === item.key
            return (
              <button
                key={item.key}
                type="button"
                aria-pressed={selected}
                onClick={() => onModeChange?.(item.key)}
                className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs font-semibold transition ${
                  selected ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon size={13} />
                {item.label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)]">
        <div className="rounded-lg border border-primary-100 bg-primary-50/55 p-4">
          <div className="text-lg font-semibold leading-7 text-gray-950">{model.headline}</div>
          <p className="mt-1 text-sm leading-6 text-gray-600">{model.description}</p>
          <div className="mt-3 text-xs leading-5 text-gray-500">
            下方生命体本体图、病灶面板和时间轴会跟随当前 lens 收敛；顶部 Mission Control 保留全局判断。
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <CountCard
            icon={Layers3}
            label="器官"
            value={model.counts.organs}
            total={model.counts.totalOrgans}
            hint="当前视角可见"
          />
          <CountCard
            icon={Stethoscope}
            label="病灶"
            value={model.counts.pathologies}
            total={model.counts.totalPathologies}
            hint="可复核异常"
          />
          <CountCard
            icon={Waypoints}
            label="事件"
            value={model.counts.events}
            total={model.counts.totalEvents}
            hint="时间轴样本"
          />
        </div>
      </div>
    </section>
  )
}
