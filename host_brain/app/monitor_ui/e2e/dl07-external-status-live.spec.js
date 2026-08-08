import { expect, test } from '@playwright/test'

const enabled = process.env.MONITOR_UI_LIVE_EXTERNAL_STATUS === '1'
const remoteEdgeServiceIds = new Set([
  'asr',
  'tts',
  'vts',
  'audio_playback',
  'barge_in',
  'resource_gate',
  'edge_rpc',
])

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
  expect(payload.not_tested).toBe(remoteEdgeServiceIds.size)
  expect(payload.unknown).toBe(0)
  const remoteEdgeServices = payload.services.filter((service) =>
    remoteEdgeServiceIds.has(service.service_id),
  )
  expect(new Set(remoteEdgeServices.map((service) => service.service_id))).toEqual(
    remoteEdgeServiceIds,
  )
  expect(
    remoteEdgeServices.every(
      (service) =>
        service.status === 'NOT_TESTED' &&
        service.reason_code === 'ACCEPTANCE_PENDING_EXTERNAL',
    ),
  ).toBe(true)

  const gateway = payload.services.find(
    (service) => service.service_id === 'unified_gateway',
  )
  expect(gateway?.status).toBe('UP')

  await page.goto(monitorUrl(baseURL, '/observability/overview'), {
    waitUntil: 'domcontentloaded',
  })
  await expect(page.getByRole('heading', { name: '外周器官' })).toBeVisible()
  await expect(page.getByText('未验收', { exact: true })).toHaveCount(
    remoteEdgeServiceIds.size,
  )
  await expect(
    page.getByText(new RegExp(`未验收 ${remoteEdgeServiceIds.size}`)),
  ).toBeVisible()
})
