import { expect, test } from '@playwright/test'
import { installRequiredOperatorSession } from './operator-session.js'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

async function waitForAuthenticatedJson(page, baseURL, path, isTrusted) {
  let payload = null
  await expect.poll(async () => {
    const response = await page.request.get(new URL(path, baseURL).toString())
    const status = response.status()
    const auth = response.headers()['x-natsume-query-auth']
    if (status !== 200 || auth !== 'AUTHENTICATED') {
      await response.dispose()
      return `${status}:${auth || 'NO_AUTH_HEADER'}`
    }
    try {
      payload = await response.json()
    } catch {
      await response.dispose()
      return 'INVALID_JSON'
    }
    await response.dispose()
    return isTrusted(payload) ? 'TRUSTED' : 'UNTRUSTED_PAYLOAD'
  }, {
    message: `${path} must return an authenticated trusted projection`,
    timeout: 20_000,
  }).toBe('TRUSTED')
  return payload
}

function hasTrustedProjection(payload, kind) {
  return hasProjectionIdentity(payload, kind)
    && payload.projection.available === true
}

function hasProjectionIdentity(payload, kind) {
  return payload?.projection?.schema_version === 'Natsume.operator.query_projection.v1'
    && payload?.projection?.kind === kind
    && Number(payload?.projection?.projection_version || 0) > 0
}

function hasCoherentReadableProjection(payload, kind) {
  if (!hasProjectionIdentity(payload, kind)) return false
  const projection = payload.projection
  if (projection.available === true) {
    return projection.degraded_state !== 'STALE' && !projection.reason_code
  }
  return projection.available === false
    && projection.degraded_state === 'STALE'
    && projection.reason_code === 'OPERATOR_PROJECTION_STALE'
}

function healthTargets(baseURL) {
  const current = new URL(baseURL)
  const configuredProxy = String(process.env.MONITOR_UI_PRODUCTION_PROXY_URL || '').trim()
  const productionProxy = configuredProxy || (current.port === '18180' ? current.origin : '')
  const obsBase = String(
    process.env.MONITOR_UI_OBS_BASE_URL
      || process.env.VITE_DEV_OBS_API_TARGET
      || (productionProxy ? `${current.protocol}//${current.hostname}:18082` : current.origin),
  ).replace(/\/$/, '')
  const configuredHost = String(
    process.env.MONITOR_UI_HOST_BASE_URL
      || process.env.VITE_DEV_HOST_API_TARGET
      || productionProxy,
  ).replace(/\/$/, '')
  const hostBase = configuredHost && configuredHost !== obsBase ? configuredHost : ''
  return { hostBase, obsBase, productionProxy }
}

test('Core Release validates live health contracts and canonical production navigation', async ({
  context,
  page,
  baseURL,
}) => {
  await installRequiredOperatorSession(context, baseURL)
  const { hostBase, obsBase } = healthTargets(baseURL)

  const obsResponse = await page.request.get(`${obsBase}/health`)
  expect(obsResponse.status()).toBe(200)
  expect(obsResponse.headers()['content-type']).toContain('application/json')
  const obsHealth = await obsResponse.json()
  // The production OBS contract deliberately reports DEGRADED when one of
  // its declared read-model sources is only partial.  A real Monitor run
  // must not mask that state as OK, but it remains usable when the daemon
  // has no failed/missing/unavailable jobs and the health projection itself
  // is current.  This keeps the live acceptance test aligned with the
  // server-owned health semantics rather than a fixture-only ideal state.
  expect(['OK', 'DEGRADED']).toContain(obsHealth.status)
  expect(obsHealth.service).toBe('obs')
  expect(obsHealth.projection?.schema_version).toBe('Natsume.operator.query_projection.v1')
  expect(obsHealth.projection?.kind).toBeTruthy()
  expect(obsHealth.projection?.available).toBe(true)
  expect(obsHealth.projection?.degraded_state).toBe('HEALTHY')
  expect(obsHealth.composition?.missing_jobs).toEqual([])
  expect(obsHealth.composition?.unavailable_jobs).toEqual([])

  if (hostBase) {
    const hostResponse = await page.request.get(`${hostBase}/health?skip_dependency_probes=1`)
    expect(hostResponse.status()).toBe(200)
    expect(hostResponse.headers()['content-type']).toContain('application/json')
    const hostHealth = await hostResponse.json()
    expect(hostHealth.status).toBe('OK')
    expect(hostHealth.version).toBeTruthy()
    expect(hostHealth.details?.summary?.overall_status).toBe('UP')
  }

  const protectedResponse = await page.request.get(new URL('/obs/task-runtime/executions', baseURL).toString())
  expect(protectedResponse.status()).toBe(200)
  expect(protectedResponse.headers()['content-type']).toContain('application/json')
  expect(protectedResponse.headers()['x-natsume-query-auth']).toBe('AUTHENTICATED')

  const runtimeSnapshotResponse = await page.request.get(
    new URL('/obs/runtime-snapshot', baseURL).toString(),
  )
  expect(runtimeSnapshotResponse.status()).toBe(200)
  expect(runtimeSnapshotResponse.headers()['content-type']).toContain('application/json')
  expect(runtimeSnapshotResponse.headers()['x-natsume-query-auth']).toBe('AUTHENTICATED')

  const securityRuntime = await waitForAuthenticatedJson(
    page,
    baseURL,
    '/obs/security/runtime',
    (payload) => payload?.schema_version === 'Natsume.security_immune.runtime.v1'
      && Number(payload?.generated_ts_ms || 0) > 0
      && Array.isArray(payload?.assets)
      && Array.isArray(payload?.incidents)
      && hasTrustedProjection(payload, 'security_immune'),
  )
  const securityAssets = await waitForAuthenticatedJson(
    page,
    baseURL,
    '/obs/security/assets',
    (payload) => payload?.schema_version === 'Natsume.security_immune.assets.v1'
      && Array.isArray(payload?.assets)
      && payload?.asset_count === payload.assets.length
      && hasTrustedProjection(payload, 'security_immune'),
  )
  const securityIncidents = await waitForAuthenticatedJson(
    page,
    baseURL,
    '/obs/security/incidents?min_risk=low',
    (payload) => payload?.schema_version === 'Natsume.security_immune.incidents.v1'
      && Array.isArray(payload?.incidents)
      && payload?.incident_count === payload.incidents.length
      && hasTrustedProjection(payload, 'security_immune'),
  )
  expect(securityRuntime.asset_count).toBe(securityAssets.asset_count)

  const traceFeed = await waitForAuthenticatedJson(
    page,
    baseURL,
    '/obs/trace-feed/snapshot?limit=6&window_minutes=1440&active_within_ms=60000',
    (payload) => Array.isArray(payload?.items)
      && payload?.count === payload.items.length
      && Number(payload?.timestamp || 0) > 0
      && hasCoherentReadableProjection(payload, 'trace_feed'),
  )
  const traceSessions = await waitForAuthenticatedJson(
    page,
    baseURL,
    '/obs/sessions?page=1&page_size=12&window_minutes=1440',
    (payload) => Array.isArray(payload?.items)
      && Number(payload?.total || 0) >= payload.items.length
      && payload?.page === 1
      && payload?.page_size === 12
      && hasCoherentReadableProjection(payload, 'session'),
  )
  expect(traceFeed.count).toBeGreaterThanOrEqual(0)
  expect(traceSessions.total).toBeGreaterThanOrEqual(0)

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '安全免疫', exact: true })).toBeVisible()
  const securityStatus = page.getByRole('region', { name: 'Security Immune hero status' })
  await expect(securityStatus.getByText('connected', { exact: true })).toBeVisible()
  await expect(securityStatus.getByText(`${securityAssets.asset_count} assets`, { exact: true })).toBeVisible()
  await expect(securityStatus.getByText(`${securityIncidents.incident_count} open`, { exact: true })).toBeVisible()
  await expect(page.getByText('部分接口降级', { exact: true })).toHaveCount(0)

  const mainNav = page.getByRole('navigation', { name: '主导航' })
  await expect(mainNav).toBeVisible()
  await expect(mainNav.getByRole('link', { name: /渗透实验室/ })).toHaveCount(0)
  await mainNav.getByRole('link', { name: '进入 Observability' }).click()
  await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/overview')
  await mainNav.getByRole('link', { name: /链路 Trace/ }).click()
  await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace')
  await expect(page.getByRole('heading', { name: 'Trace 排障入口' })).toBeVisible()
  await expect(page.getByText('SSE 实时流已连接', { exact: true })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/^\d+ 条活跃请求$/)).toBeVisible()
  await expect(page.getByText('活跃链路流加载失败', { exact: true })).toHaveCount(0)
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1))
    .toBe(true)
})
