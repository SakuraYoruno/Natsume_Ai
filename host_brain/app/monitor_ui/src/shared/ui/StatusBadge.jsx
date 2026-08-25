import { AlertTriangle, CheckCircle, Loader2, MinusCircle, XCircle } from 'lucide-react'

const STATUS_MAP = {
  healthy: {
    cls: 'badge-success',
    icon: CheckCircle,
    label: '健康',
  },
  up: {
    cls: 'badge-success',
    icon: CheckCircle,
    label: '健康',
  },
  degraded: {
    cls: 'badge-warning',
    icon: AlertTriangle,
    label: '降级',
  },
  down: {
    cls: 'badge-danger',
    icon: XCircle,
    label: '离线',
  },
  error: {
    cls: 'badge-danger',
    icon: XCircle,
    label: '故障',
  },
  running: {
    cls: 'badge-info',
    icon: Loader2,
    label: '运行中',
    spin: true,
  },
  not_tested: {
    cls: 'badge-info',
    icon: MinusCircle,
    label: '未验收',
  },
  unknown: {
    cls: 'badge-gray',
    icon: MinusCircle,
    label: '无数据',
  },
}

export default function StatusBadge({ status = 'unknown', label }) {
  const normalized = String(status || '').toLowerCase()
  const config = STATUS_MAP[normalized] ?? STATUS_MAP.unknown
  const Icon = config.icon

  return (
    <span className={config.cls}>
      <Icon size={11} className={config.spin ? 'animate-spin' : ''} />
      {label ?? config.label}
    </span>
  )
}
