import React from 'react'

export default function JsonBlock({ data, className = '' }) {
  return (
    <pre className={`max-h-96 overflow-auto rounded-lg border border-gray-200 bg-gray-950 p-3 text-xs text-gray-100 ${className}`}>
      {typeof data === 'string' ? data : JSON.stringify(data ?? {}, null, 2)}
    </pre>
  )
}
