const queryCache = new Map()

export function serializeQueryKey(queryKey) {
  if (typeof queryKey === 'string') {
    return queryKey
  }
  return JSON.stringify(queryKey)
}

function createEntry(cacheKey) {
  return {
    cacheKey,
    data: null,
    error: null,
    status: 'idle',
    updatedAt: null,
    promise: null,
    listeners: new Set(),
  }
}

function ensureEntry(cacheKey) {
  if (!queryCache.has(cacheKey)) {
    queryCache.set(cacheKey, createEntry(cacheKey))
  }
  return queryCache.get(cacheKey)
}

function emit(entry) {
  entry.listeners.forEach((listener) => listener())
}

export function subscribeQuery(cacheKey, listener) {
  const entry = ensureEntry(cacheKey)
  entry.listeners.add(listener)
  return () => {
    entry.listeners.delete(listener)
  }
}

export function getQuerySnapshot(cacheKey) {
  return ensureEntry(cacheKey)
}

export async function executeQuery({
  cacheKey,
  queryFn,
  staleTimeMs = 0,
  force = false,
}) {
  const entry = ensureEntry(cacheKey)
  const now = Date.now()
  const isFresh = entry.updatedAt != null && staleTimeMs > 0 && (now - entry.updatedAt) <= staleTimeMs

  if (!force && entry.promise) {
    return entry.promise
  }

  if (!force && isFresh && entry.status === 'success') {
    return entry.data
  }

  const hasResolvedData = entry.updatedAt != null
  entry.status = hasResolvedData ? 'refreshing' : 'loading'
  entry.error = null
  emit(entry)

  const promise = Promise.resolve()
    .then(() => queryFn())
    .then((data) => {
      entry.data = data
      entry.error = null
      entry.status = 'success'
      entry.updatedAt = Date.now()
      entry.promise = null
      emit(entry)
      return data
    })
    .catch((error) => {
      entry.error = error
      entry.status = hasResolvedData ? 'stale' : 'error'
      entry.promise = null
      emit(entry)
      throw error
    })

  entry.promise = promise
  return promise
}
