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

function renderNoticeContent(value) {
  if (value == null || value === false) return null
  if (React.isValidElement(value)) return value
  if (Array.isArray(value)) {
    return value.map((item, index) => (
      <React.Fragment key={index}>{renderNoticeContent(item)}</React.Fragment>
    ))
  }
  if (value instanceof Error) return value.message || String(value)
  if (typeof value === 'object') {
    if (typeof value.message === 'string') return value.message
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return value
}

export default function NoticeBanner({ variant = 'info', title, children, className = '' }) {
  const cfg = VARIANT_MAP[variant] || VARIANT_MAP.info
  const Icon = cfg.icon
  const safeTitle = renderNoticeContent(title)
  const safeChildren = renderNoticeContent(children)
  return (
    <div className={`relative overflow-hidden rounded-xl border px-3 py-2 text-sm ${cfg.container} ${className}`.trim()}>
      <div className="pointer-events-none absolute inset-x-3 top-0 h-px bg-gradient-to-r from-transparent via-white/90 to-transparent" />
      <div className="flex items-start gap-2">
        <Icon size={14} className={`mt-0.5 shrink-0 ${cfg.iconColor}`.trim()} />
        <div className="min-w-0">
          {safeTitle ? <div className="font-medium">{safeTitle}</div> : null}
          {safeChildren ? <div className={safeTitle ? 'mt-0.5' : ''}>{safeChildren}</div> : null}
        </div>
      </div>
    </div>
  )
}
