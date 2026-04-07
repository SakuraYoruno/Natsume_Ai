const CJK_RE = /[\u3400-\u9fff]/g
const SUSPICIOUS_RE = /[À-ÿ\u0080-\u009f]/g
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f]/g

function isPlainObject(value) {
  return Object.prototype.toString.call(value) === '[object Object]'
}

function scoreText(value) {
  if (typeof value !== 'string') {
    return 0
  }

  const cjkCount = (value.match(CJK_RE) || []).length
  const asciiCount = (value.match(/[A-Za-z0-9]/g) || []).length
  const suspiciousCount = (value.match(SUSPICIOUS_RE) || []).length
  const controlCount = (value.match(CONTROL_RE) || []).length
  const replacementCount = (value.match(/�/g) || []).length

  return cjkCount * 6 + asciiCount - suspiciousCount * 4 - controlCount * 6 - replacementCount * 8
}

function decodeUtf8FromLatin1(value) {
  const bytes = Uint8Array.from(Array.from(value, (char) => char.charCodeAt(0) & 0xff))
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes)
}

export function repairMojibakeString(value) {
  if (typeof value !== 'string' || value.length < 2) {
    return value
  }

  if ((value.match(CJK_RE) || []).length > 0) {
    return value
  }

  const suspiciousCount = (value.match(SUSPICIOUS_RE) || []).length
  if (suspiciousCount < 2) {
    return value
  }

  try {
    const decoded = decodeUtf8FromLatin1(value)
    if (!decoded || decoded === value) {
      return value
    }
    return scoreText(decoded) > scoreText(value) ? decoded : value
  } catch {
    return value
  }
}

export function deepRepairMojibake(value) {
  if (typeof value === 'string') {
    return repairMojibakeString(value)
  }

  if (Array.isArray(value)) {
    return value.map((item) => deepRepairMojibake(item))
  }

  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, deepRepairMojibake(item)])
    )
  }

  return value
}
