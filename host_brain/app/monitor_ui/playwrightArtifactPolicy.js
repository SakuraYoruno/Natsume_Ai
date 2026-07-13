import path from 'node:path'
import { tmpdir } from 'node:os'

export function resolvePlaywrightArtifactPolicy({
  environment = process.env,
  processId = process.pid,
  temporaryDirectory = tmpdir(),
} = {}) {
  const hasOperatorSession = Boolean(
    String(environment.MONITOR_UI_OPERATOR_SESSION_TOKEN || '').trim(),
  )
  const configuredOutputDir = String(
    environment.MONITOR_UI_PLAYWRIGHT_OUTPUT_DIR || '',
  ).trim()

  return Object.freeze({
    outputDir: configuredOutputDir || (
      hasOperatorSession
        ? path.join(temporaryDirectory, `natsume-monitor-playwright-${processId}`)
        : 'test-results'
    ),
    trace: hasOperatorSession ? 'off' : 'retain-on-failure',
    screenshot: hasOperatorSession ? 'off' : 'only-on-failure',
    video: hasOperatorSession ? 'off' : 'retain-on-failure',
  })
}
