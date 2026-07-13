import { expect, test } from '@playwright/test'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

test('Core Release performs an unstubbed production-preview interaction', async ({ page, baseURL }) => {
  // This release smoke intentionally installs no page.route handlers.  API
  // traffic therefore reaches the isolated preview origin and cannot be
  // satisfied by the fixture-heavy route regression suite.
  const healthUrl = new URL('/health', baseURL).toString()
  const healthResponse = await page.request.get(healthUrl)
  expect(healthResponse.status()).toBe(200)
  expect(healthResponse.headers()['content-type']).toContain('application/json')
  const health = await healthResponse.json()
  expect(health.status).toBe('OK')
  expect(health.projection?.schema_version).toBe('Natsume.operator.query_projection.v1')
  expect(health.projection?.kind).toBeTruthy()

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(monitorUrl(baseURL, '/security/pentest'), { waitUntil: 'domcontentloaded' })

  const mainNav = page.getByRole('navigation', { name: '主导航' })
  await expect(mainNav).toBeVisible()
  await mainNav.getByRole('link', { name: '进入 Observability' }).click()
  await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/overview')
  await expect(mainNav.getByText('Observability', { exact: true })).toBeVisible()

  await mainNav.getByRole('link', { name: /链路 Trace/ }).click()
  await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace')
  await expect(page.getByRole('heading', { name: 'Trace 排障入口' })).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1))
    .toBe(true)

})
