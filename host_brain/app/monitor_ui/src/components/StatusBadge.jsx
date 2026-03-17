import React from 'react'
import { CheckCircle, AlertTriangle, XCircle, Loader2, MinusCircle } from 'lucide-react'

/**
 * 状态徽章
 * @param {'healthy'|'degraded'|'error'|'running'|'unknown'|'up'|'down'} status
 * @param {string} [label] - 自定义文字，默认使用 status 映射
 */
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
    label: '故障',
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
  unknown: {
    cls: 'badge-gray',
    icon: MinusCircle,
    label: '无数据',
  },
}

export default function StatusBadge({ status = 'unknown', label }) {
  const normalized = (status || '').toLowerCase()
  const cfg = STATUS_MAP[normalized] ?? STATUS_MAP.unknown
  const Icon = cfg.icon
  return (
    <span className={cfg.cls}>
      <Icon size={11} className={cfg.spin ? 'animate-spin' : ''} />
      {label ?? cfg.label}
    </span>
  )
}
