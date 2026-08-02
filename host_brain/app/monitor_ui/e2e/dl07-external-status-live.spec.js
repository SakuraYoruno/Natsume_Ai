import { expect, test } from '@playwright/test'

const enabled = process.env.MONITOR_UI_LIVE_EXTERNAL_STATUS === '1'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

test('真实 Monitor 将未启动的远端 Edge 显示为未验收', async ({ page, baseURL }) => {
  test.skip(!enabled, 'requires the running DL-07 Host/Obs/Nginx stack')

  const response = await page.request.get(
    new URL('/obs/services/health?side=edge', baseURL).toString(),
  )
  expect(response.status()).toBe(200)
  const payload = await response.json()
  expect(payload.not_tested).toBe(5)
  expect(payload.unknown).toBe(0)
  expect(payload.services).toHaveLength(5)
  expect(payload.services.every((service) => service.status === 'NOT_TESTED')).toBe(true)

  await page.goto(monitorUrl(baseURL, '/observability/overview'), {
    waitUntil: 'domcontentloaded',
  })
  await expect(page.getByRole('heading', { name: '外周器官' })).toBeVisible()
  await expect(page.getByText('未验收', { exact: true })).toHaveCount(5)
  await expect(page.getByText(/未验收 5/)).toBeVisible()
})
