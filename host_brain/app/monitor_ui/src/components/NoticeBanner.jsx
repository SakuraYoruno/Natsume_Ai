import React from 'react'
import { AlertTriangle, CheckCircle2, Info } from 'lucide-react'

const VARIANT_MAP = {
  error: {
    icon: AlertTriangle,
    container: 'border-red-200 bg-red-50 text-red-700',
    iconColor: 'text-red-500',
  },
  warning: {
    icon: AlertTriangle,
    container: 'border-orange-200 bg-orange-50 text-orange-700',
    iconColor: 'text-orange-500',
  },
  success: {
    icon: CheckCircle2,
    container: 'border-green-200 bg-green-50 text-green-700',
    iconColor: 'text-green-500',
  },
  info: {
    icon: Info,
    container: 'border-blue-200 bg-blue-50 text-blue-700',
    iconColor: 'text-blue-500',
  },
}

export default function NoticeBanner({ variant = 'info', title, children, className = '' }) {
  const cfg = VARIANT_MAP[variant] || VARIANT_MAP.info
  const Icon = cfg.icon
  return (
    <div className={`rounded-xl border px-3 py-2 text-sm ${cfg.container} ${className}`.trim()}>
      <div className="flex items-start gap-2">
        <Icon size={14} className={`mt-0.5 shrink-0 ${cfg.iconColor}`.trim()} />
        <div className="min-w-0">
          {title ? <div className="font-medium">{title}</div> : null}
          {children ? <div className={title ? 'mt-0.5' : ''}>{children}</div> : null}
        </div>
      </div>
    </div>
  )
}
