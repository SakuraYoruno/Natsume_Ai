import React from 'react'

function stringifyJsonBlock(data) {
  if (typeof data === 'string') return data
  try {
    return JSON.stringify(data ?? {}, null, 2)
  } catch {
    return String(data)
  }
}

const VARIANT_CLASS_NAMES = Object.freeze({
  dark: 'max-h-96 overflow-auto rounded-lg border border-gray-200 bg-gray-950 p-3 text-xs text-gray-100',
  light: 'max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-primary-100/70 bg-white/75 p-3 text-xs text-gray-700',
})

export default function JsonBlock({ data, className = '', emptyLabel = null, variant = 'dark' }) {
  if ((data == null || data === '') && emptyLabel) {
    return <div className="text-xs text-gray-400">{emptyLabel}</div>
  }
  const variantClassName = VARIANT_CLASS_NAMES[variant] || VARIANT_CLASS_NAMES.dark
  return (
    <pre className={`${variantClassName} ${className}`}>
      {stringifyJsonBlock(data)}
    </pre>
  )
}
