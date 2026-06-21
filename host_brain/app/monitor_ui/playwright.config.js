import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.MONITOR_UI_BASE_URL || 'http://127.0.0.1:4173/monitor/'
const browserChannel = process.env.MONITOR_UI_PLAYWRIGHT_CHANNEL ?? 'chrome'
const shouldStartPreview = process.env.MONITOR_UI_SKIP_WEB_SERVER !== '1'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: shouldStartPreview
    ? {
        command: 'npm run preview -- --host 127.0.0.1 --port 4173',
        url: baseURL,
        reuseExistingServer: true,
        timeout: 30_000,
      }
    : undefined,
  projects: [
    {
      name: 'monitor-routes',
      use: {
        ...devices['Desktop Chrome'],
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
  ],
})
