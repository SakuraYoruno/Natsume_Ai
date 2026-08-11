import assert from 'node:assert/strict'
import test from 'node:test'

import { buildGatewayViewModel } from './gatewayModel.js'

function gatewayData(statuses) {
  return {
    platforms: statuses.map((status, index) => ({
      platform: `platform-${index}`,
      status,
      inbound_count: 0,
      outbound_count: 0,
      error_count: 0,
    })),
    total_inbound: 0,
    total_outbound: 0,
    total_errors: 0,
    humanize_enabled: false,
  }
}

test('零流量且分平台全未知时不会显示为健康空态', () => {
  const view = buildGatewayViewModel({
    data: gatewayData(Array(6).fill('UNKNOWN')),
    windowMinutes: 60,
  })

  assert.equal(view.activePlatformCount, 0)
  assert.equal(view.unknownPlatformCount, 6)
  assert.match(view.heroDescription, /不能据此判断通道健康/)
  assert.equal(view.commandRailView.statusLabel, '6 个平台状态未确认')
  assert.equal(view.commandRailView.state, 'warning')
  assert.match(
    view.commandRailView.gateCards.find((item) => item.label === 'Platform Health').detail,
    /6 unknown/,
  )
})

test('明确的启用和停用状态仍可形成真实空流量健康态', () => {
  const view = buildGatewayViewModel({
    data: gatewayData(['UP', 'DISABLED', 'DISABLED', 'DISABLED', 'DISABLED', 'DISABLED']),
    windowMinutes: 60,
  })

  assert.equal(view.activePlatformCount, 1)
  assert.equal(view.unknownPlatformCount, 0)
  assert.equal(view.commandRailView.statusLabel, '空流量健康态')
})
