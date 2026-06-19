import React from 'react'

function stringifyValue(value) {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export default function RenderValue({ value, empty = '—' }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-gray-400">{empty}</span>
  }
  if (React.isValidElement(value)) {
    return value
  }
  if (Array.isArray(value) && value.every(React.isValidElement)) {
    return <>{value}</>
  }
  if (typeof value === 'boolean') {
    return <span>{value ? 'true' : 'false'}</span>
  }
  if (typeof value === 'number' || typeof value === 'string') {
    return <span>{String(value)}</span>
  }
  return (
    <code className="break-all rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-600">
      {stringifyValue(value)}
    </code>
  )
}
