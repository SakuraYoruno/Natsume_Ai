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
    failureCount: 0,
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

function updateEntry(cacheKey, patch) {
  const current = ensureEntry(cacheKey)
  const next = {
    ...current,
    ...patch,
    listeners: current.listeners,
  }
  queryCache.set(cacheKey, next)
  emit(next)
  return next
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
  const promise = Promise.resolve()
    .then(() => queryFn())
    .then((data) => {
      updateEntry(cacheKey, {
        data,
        error: null,
        failureCount: 0,
        status: 'success',
        updatedAt: Date.now(),
        promise: null,
      })
      return data
    })
    .catch((error) => {
      const current = ensureEntry(cacheKey)
      updateEntry(cacheKey, {
        error,
        failureCount: (current.failureCount || 0) + 1,
        status: hasResolvedData ? 'stale' : 'error',
        promise: null,
      })
      throw error
    })

  updateEntry(cacheKey, {
    status: hasResolvedData ? 'refreshing' : 'loading',
    error: null,
    promise,
  })
  return promise
}
