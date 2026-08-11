import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import { resolvePlaywrightArtifactPolicy } from './playwrightArtifactPolicy.js'

test('ordinary Playwright runs retain failure diagnostics in the project output directory', () => {
  assert.deepEqual(resolvePlaywrightArtifactPolicy({
    environment: {},
    processId: 42,
    temporaryDirectory: '/tmp',
  }), {
    outputDir: 'test-results',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  })
})

test('operator-session runs disable credential-bearing artifacts and default to a temporary directory', () => {
  const policy = resolvePlaywrightArtifactPolicy({
    environment: { MONITOR_UI_OPERATOR_SESSION_TOKEN: 'ephemeral-secret' },
    processId: 314,
    temporaryDirectory: '/secure-temp',
  })

  assert.equal(policy.outputDir, path.join('/secure-temp', 'natsume-monitor-playwright-314'))
  assert.equal(policy.trace, 'off')
  assert.equal(policy.screenshot, 'off')
  assert.equal(policy.video, 'off')
})

test('release runner may provide its own temporary output directory without re-enabling artifacts', () => {
  const policy = resolvePlaywrightArtifactPolicy({
    environment: {
      MONITOR_UI_OPERATOR_SESSION_TOKEN: 'ephemeral-secret',
      MONITOR_UI_PLAYWRIGHT_OUTPUT_DIR: '/runner-workspace/playwright-results',
    },
    processId: 2718,
    temporaryDirectory: '/secure-temp',
  })

  assert.equal(policy.outputDir, '/runner-workspace/playwright-results')
  assert.equal(policy.trace, 'off')
  assert.equal(policy.screenshot, 'off')
  assert.equal(policy.video, 'off')
})
