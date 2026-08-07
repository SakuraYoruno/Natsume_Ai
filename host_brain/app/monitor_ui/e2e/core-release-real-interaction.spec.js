import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { installRequiredOperatorSession } from './operator-session.js'

const includeSecurityAcceptance = process.env.MONITOR_UI_INCLUDE_SECURITY_ACCEPTANCE !== '0'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

async function captureAcceptanceScreenshot(page, fileName) {
  const artifactRoot = String(process.env.MONITOR_UI_ACCEPTANCE_ARTIFACT_DIR || '').trim()
  if (!artifactRoot) return
  await mkdir(artifactRoot, { recursive: true })
  await page.screenshot({ path: path.join(artifactRoot, fileName), fullPage: true })
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
  const unavailableReasons = {
    SOURCE_UNAVAILABLE: 'OPERATOR_PROJECTION_SOURCE_UNAVAILABLE',
    STALE: 'OPERATOR_PROJECTION_STALE',
  }
  return projection.available === false
    && unavailableReasons[projection.degraded_state] === projection.reason_code
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
  test.setTimeout(120_000)
  await installRequiredOperatorSession(context, baseURL)
  const { hostBase, obsBase } = healthTargets(baseURL)

  const obsResponse = await page.request.get(`${obsBase}/health`)
  expect(obsResponse.status()).toBe(200)
  expect(obsResponse.headers()['content-type']).toContain('application/json')
  const obsHealth = await obsResponse.json()
  expect(['OK', 'DEGRADED']).toContain(obsHealth.status)
  expect(obsHealth.service).toBe('obs')
  expect(obsHealth.projection?.schema_version).toBe('Natsume.operator.query_projection.v1')
  expect(obsHealth.projection?.kind).toBeTruthy()
  expect(obsHealth.composition?.projection_daemon?.status).toBe('RUNNING')
  expect(obsHealth.composition?.projection_daemon?.consecutive_failure_waves).toBe(0)
  await expect.poll(async () => {
    const response = await page.request.get(`${obsBase}/health`)
    const health = await response.json()
    await response.dispose()
    return health.composition?.projection_daemon?.last_wave?.failed_count ?? 'PENDING'
  }, {
    message: 'the real projection daemon must complete one failure-free wave',
    timeout: 90_000,
  }).toBe(0)
  expect(obsHealth.composition?.typed_unavailable_job_count).toBe(0)
  if (obsHealth.status === 'DEGRADED') {
    expect(obsHealth.composition?.declared_partial_job_count).toBeGreaterThan(0)
  }

  if (hostBase) {
    const hostResponse = await page.request.get(`${hostBase}/health`)
    expect(hostResponse.status()).toBe(200)
    expect(hostResponse.headers()['content-type']).toContain('application/json')
    const hostHealth = await hostResponse.json()
    expect(['OK', 'ACCEPTANCE_PENDING_EXTERNAL']).toContain(hostHealth.status)
    expect(hostHealth.version).toBeTruthy()
    const expectedRuntimeStatus = hostHealth.status === 'OK'
      ? 'UP'
      : hostHealth.status
    expect(hostHealth.details?.summary?.overall_status).toBe(expectedRuntimeStatus)
    expect(hostHealth.details?.summary?.required_unavailable).toBe(0)
  }

  const protectedResponse = await page.request.get(new URL('/obs/task-runtime/executions', baseURL).toString())
  expect(protectedResponse.status()).toBe(200)
  expect(protectedResponse.headers()['content-type']).toContain('application/json')
  expect(protectedResponse.headers()['x-natsume-query-auth']).toBe('AUTHENTICATED')
  const executionIndex = await protectedResponse.json()
  expect(executionIndex.read_only).toBe(true)
  expect(executionIndex.executions.length).toBeGreaterThan(0)
  const selectedExecution = executionIndex.executions[0]
  const executionDetailResponse = await page.request.get(new URL(
    `/obs/task-runtime/executions/${encodeURIComponent(selectedExecution.graph_id)}`
      + `?revision=${Number(selectedExecution.revision || 1)}&after_cursor=0&event_limit=200`,
    baseURL,
  ).toString())
  expect(executionDetailResponse.status()).toBe(200)
  expect(executionDetailResponse.headers()['x-natsume-query-auth']).toBe('AUTHENTICATED')
  const executionDetail = await executionDetailResponse.json()
  expect(executionDetail.read_only).toBe(true)
  expect(executionDetail.mutation_contract?.direct_database_write).toBe(false)
  expect(executionDetail.mutation_contract?.automatic_resume).toBe(false)
  expect(executionDetail.mutation_contract?.resume_requires_bound_token).toBe(true)

  const runtimeSnapshotResponse = await page.request.get(
    new URL('/obs/runtime-snapshot', baseURL).toString(),
  )
  expect(runtimeSnapshotResponse.status()).toBe(200)
  expect(runtimeSnapshotResponse.headers()['content-type']).toContain('application/json')
  expect(runtimeSnapshotResponse.headers()['x-natsume-query-auth']).toBe('AUTHENTICATED')

  let securityRuntime = null
  let securityAssets = null
  let securityIncidents = null
  if (includeSecurityAcceptance) {
    securityRuntime = await waitForAuthenticatedJson(
      page,
      baseURL,
      '/obs/security/runtime',
      (payload) => payload?.schema_version === 'Natsume.security_immune.runtime.v1'
        && (
          payload?.projection?.available === false
          || Number(payload?.generated_ts_ms || 0) > 0
        )
        && Array.isArray(payload?.assets)
        && Array.isArray(payload?.incidents)
        && hasCoherentReadableProjection(payload, 'security_immune'),
    )
    securityAssets = await waitForAuthenticatedJson(
      page,
      baseURL,
      '/obs/security/assets',
      (payload) => payload?.schema_version === 'Natsume.security_immune.assets.v1'
        && Array.isArray(payload?.assets)
        && payload?.asset_count === payload.assets.length
        && hasCoherentReadableProjection(payload, 'security_immune'),
    )
    securityIncidents = await waitForAuthenticatedJson(
      page,
      baseURL,
      '/obs/security/incidents?min_risk=low',
      (payload) => payload?.schema_version === 'Natsume.security_immune.incidents.v1'
        && Array.isArray(payload?.incidents)
        && payload?.incident_count === payload.incidents.length
        && hasCoherentReadableProjection(payload, 'security_immune'),
    )
    expect(securityRuntime.asset_count).toBe(securityAssets.asset_count)
  }

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
  if (includeSecurityAcceptance) {
    await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '安全免疫', exact: true })).toBeVisible()
    const securityStatus = page.getByRole('region', { name: 'Security Immune hero status' })
    const securityProjectionAvailable = securityRuntime.projection.available === true
    await expect(securityStatus.getByText(
      securityProjectionAvailable ? 'connected' : 'degraded',
      { exact: true },
    )).toBeVisible()
    await expect(securityStatus.getByText(`${securityAssets.asset_count} assets`, { exact: true })).toBeVisible()
    await expect(securityStatus.getByText(`${securityIncidents.incident_count} open`, { exact: true })).toBeVisible()
    if (securityProjectionAvailable) {
      await expect(securityStatus.getByText('Security Immune 降级运行', { exact: true })).toHaveCount(0)
    } else {
      await expect(securityStatus.getByText('Security Immune 降级运行', { exact: true })).toBeVisible()
    }
  } else {
    await page.goto(monitorUrl(baseURL, '/cockpit'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '现在先处理什么' })).toBeVisible()
  }

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

  await page.goto(monitorUrl(baseURL, '/observability/overview'), { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '观测总览', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '外周器官', exact: true })).toBeVisible()
  await captureAcceptanceScreenshot(page, '01-observability-overview.png')

  await page.goto(monitorUrl(baseURL, '/execution/gateway'), { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '统一消息网关', exact: true })).toBeVisible()
  const gatewayGate = page.getByRole('region', { name: 'Gateway Routing Gate' })
  await expect(gatewayGate).toBeVisible()
  await expect(page.getByText('正在同步网关快照', { exact: true })).toHaveCount(0, { timeout: 40_000 })
  await expect(page.getByText('6 平台', { exact: true }).first()).toBeVisible()
  await expect(gatewayGate.getByText('Platform Health', { exact: true })).toBeVisible()
  await expect(page.getByText('网关观测接口当前未返回可用数据', { exact: false })).toHaveCount(0)
  await captureAcceptanceScreenshot(page, '02-gateway-live.png')

  await page.goto(monitorUrl(baseURL, '/execution/task-runtime'), { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: '执行审查', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'TaskGraph selector' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'TaskGraph DAG' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Task budget reservations' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Task verification status' })).toBeVisible()
  await captureAcceptanceScreenshot(page, '03-task-runtime-live.png')
})
