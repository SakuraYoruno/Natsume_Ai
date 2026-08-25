import {
  AlertTriangle,
  CheckCircle,
  HelpCircle,
  XCircle,
} from 'lucide-react'
import {
  GATEWAY_STATUS_BADGE_CLASSES,
  GATEWAY_STATUS_LABELS,
  GATEWAY_STATUS_TEXT_CLASSES,
} from '../../../../shared/statusMeta.js'

const GATEWAY_STATUS_ICONS = {
  UP: CheckCircle,
  DEGRADED: AlertTriangle,
  DOWN: XCircle,
  UNKNOWN: HelpCircle,
}

export default function GatewayStatusBadge({ status }) {
  const key = String(status || 'UNKNOWN').toUpperCase()
  const Icon = GATEWAY_STATUS_ICONS[key] ?? GATEWAY_STATUS_ICONS.UNKNOWN
  const color = GATEWAY_STATUS_TEXT_CLASSES[key] ?? GATEWAY_STATUS_TEXT_CLASSES.UNKNOWN
  const bg = GATEWAY_STATUS_BADGE_CLASSES[key] ?? GATEWAY_STATUS_BADGE_CLASSES.UNKNOWN
  const label = GATEWAY_STATUS_LABELS[key] ?? GATEWAY_STATUS_LABELS.UNKNOWN

  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${color} ${bg}`}>
      <Icon size={12} />
      {label}
    </span>
  )
}
