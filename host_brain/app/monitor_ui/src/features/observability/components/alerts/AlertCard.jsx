import {
  AlertTriangle,
  Bell,
  BellOff,
  XCircle,
} from 'lucide-react'
import { buildAlertCardView } from '../../alertsModel.js'
import { classNames, formatDateTime, humanizeToken } from '../../../../utils/digitalLife.js'

const SEVERITY_ICONS = {
  critical: XCircle,
  warning: AlertTriangle,
  info: Bell,
  resolved: BellOff,
}

export default function AlertCard({ alert }) {
  const {
    severity,
    badgeClass,
    cardClass,
    severityLabel,
    title,
    detail,
  } = buildAlertCardView(alert)
  const Icon = SEVERITY_ICONS[severity] || SEVERITY_ICONS.info

  return (
    <div className={classNames('card border p-4 transition-colors', cardClass)}>
      <div className="flex items-start gap-3">
        <div className={classNames('mt-0.5 rounded-xl border p-2', badgeClass)}>
          <Icon size={15} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-semibold text-gray-800">{title}</span>
              <span className={classNames('rounded-full border px-2 py-0.5 text-[11px] font-medium', badgeClass)}>
                {severityLabel}
              </span>
            </div>
            <span className="shrink-0 text-xs text-gray-400">{formatDateTime(alert.ts_ms)}</span>
          </div>

          <div className="mt-2 text-sm text-gray-600">{detail}</div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
            {alert?.source ? (
              <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5">
                来源：{humanizeToken(alert.source)}
              </span>
            ) : null}
            {alert?.reason_code ? (
              <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 font-mono">
                {alert.reason_code}
              </span>
            ) : null}
            {alert?.request_id ? (
              <a href={`#/observability/trace/${alert.request_id}`} className="text-primary hover:underline">
                打开 Trace
              </a>
            ) : null}
            {!alert?.request_id && alert?.to ? (
              <a href={`#${alert.to}`} className="text-primary hover:underline">
                打开相关页面
              </a>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
