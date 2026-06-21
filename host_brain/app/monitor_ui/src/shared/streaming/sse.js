export function parseSseBlock(block) {
  const data = String(block || '')
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n')

  if (!data) return null

  try {
    return JSON.parse(data)
  } catch (_) {
    return { raw: data }
  }
}

export function consumeSseBuffer(buffer) {
  const chunks = String(buffer || '').split('\n\n')
  const remainder = chunks.pop() ?? ''
  const events = chunks.map(parseSseBlock).filter(Boolean)
  return { events, remainder }
}
