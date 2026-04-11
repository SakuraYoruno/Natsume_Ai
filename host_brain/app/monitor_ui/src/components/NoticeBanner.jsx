import React from 'react'
import { AlertTriangle, CheckCircle2, Info } from 'lucide-react'

const VARIANT_MAP = {
  error: {
    icon: AlertTriangle,
    container: 'border-red-200/80 bg-red-50/90 text-red-700 shadow-sm backdrop-blur-sm',
    iconColor: 'text-red-500',
  },
  warning: {
    icon: AlertTriangle,
    container: 'border-orange-200/80 bg-orange-50/90 text-orange-700 shadow-sm backdrop-blur-sm',
    iconColor: 'text-orange-500',
  },
  success: {
    icon: CheckCircle2,
    container: 'border-green-200/80 bg-green-50/90 text-green-700 shadow-sm backdrop-blur-sm',
    iconColor: 'text-green-500',
  },
  info: {
    icon: Info,
    container: 'border-blue-200/80 bg-blue-50/90 text-blue-700 shadow-sm backdrop-blur-sm',
    iconColor: 'text-blue-500',
  },
}

export default function NoticeBanner({ variant = 'info', title, children, className = '' }) {
  const cfg = VARIANT_MAP[variant] || VARIANT_MAP.info
  const Icon = cfg.icon
  return (
    <div className={`relative overflow-hidden rounded-xl border px-3 py-2 text-sm ${cfg.container} ${className}`.trim()}>
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-white/90 to-transparent" />
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
