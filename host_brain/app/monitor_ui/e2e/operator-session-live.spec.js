import { expect, test } from '@playwright/test'

test('普通本机浏览器自动建立 Monitor 会话并贯通 HTTP 与 WebSocket', async ({
  baseURL,
  context,
  page,
}) => {
  await context.clearCookies()
  const bootstrapResponse = page.waitForResponse((response) => (
    response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/obs/operator-session/bootstrap'
  ))

  await page.goto(new URL('#/cockpit', baseURL).toString(), {
    waitUntil: 'domcontentloaded',
  })
  const bootstrap = await bootstrapResponse
  expect(bootstrap.status()).toBe(200)
  await expect(page.getByRole('heading', { name: '现在先处理什么' })).toBeVisible()
  await expect(page.getByText('本机控制台尚未连接')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /待连接/ })).toHaveCount(0)
  await expect(page.getByText('Runtime 待恢复', { exact: true })).toHaveCount(0)

  const cookies = await context.cookies(new URL(baseURL).origin)
  const sessionCookie = cookies.find((cookie) => cookie.name === 'natsume_operator_session')
  expect(sessionCookie).toMatchObject({
    httpOnly: true,
    sameSite: 'Strict',
  })
  expect(String(sessionCookie?.value || '').length).toBeGreaterThanOrEqual(32)

  const protectedQuery = await page.evaluate(async () => {
    const response = await fetch('/obs/runtime-snapshot', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    const payload = await response.json()
    return {
      ok: response.ok,
      status: response.status,
      contentType: response.headers.get('content-type') || '',
      hasRuntimeSnapshot: Boolean(payload && typeof payload === 'object'),
    }
  })
  expect(protectedQuery).toMatchObject({
    ok: true,
    status: 200,
    hasRuntimeSnapshot: true,
  })
  expect(protectedQuery.contentType).toContain('application/json')

  const lifeBodyQuery = await page.evaluate(async () => {
    const response = await fetch('/obs/life-body?window_minutes=360', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    const payload = await response.json()
    return {
      ok: response.ok,
      projectionAvailable: payload?.meta?.projection?.available === true,
      hasRuntimeSnapshot: Boolean(payload?.runtimeSnapshot),
      reasonCode: String(payload?.meta?.projection?.reason_code || ''),
    }
  })
  expect(lifeBodyQuery.ok).toBe(true)
  if (lifeBodyQuery.projectionAvailable) {
    expect(lifeBodyQuery.hasRuntimeSnapshot).toBe(true)
    expect(lifeBodyQuery.reasonCode).toBe('')
  }

  const websocketResult = await page.evaluate(() => new Promise((resolve) => {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${window.location.host}/obs/ws/health`)
    const timer = window.setTimeout(() => {
      socket.close()
      resolve({ ok: false, reason: 'timeout' })
    }, 5000)
    socket.addEventListener('message', (event) => {
      window.clearTimeout(timer)
      let payload = null
      try {
        payload = JSON.parse(String(event.data || ''))
      } catch {
        payload = null
      }
      socket.close()
      resolve({
        ok: Boolean(payload && typeof payload === 'object'),
        hasServices: Array.isArray(payload?.data?.services),
        type: String(payload?.type || ''),
      })
    }, { once: true })
    socket.addEventListener('error', () => {
      window.clearTimeout(timer)
      resolve({ ok: false, reason: 'socket_error' })
    }, { once: true })
  }))
  expect(websocketResult).toEqual({
    ok: true,
    hasServices: true,
    type: 'health_snapshot',
  })
})
