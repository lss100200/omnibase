import { defineConfig, devices } from '@playwright/test'

const port = 4174

export default defineConfig({
  testDir: './visual-tests',
  outputDir: './test-results/p7-visual',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['line'], ['json', { outputFile: 'test-results/p7-visual-report.json' }]]
    : [['line']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    colorScheme: 'dark',
    locale: 'zh-CN',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'p7-1440x900',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'p7-1024x700',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 700 } },
    },
    {
      name: 'p7-zoom-200',
      use: {
        ...devices['Desktop Chrome'],
        deviceScaleFactor: 2,
        viewport: { width: 720, height: 450 },
      },
    },
  ],
  webServer: {
    command: `pnpm exec next start -H 127.0.0.1 -p ${port}`,
    url: `http://127.0.0.1:${port}/desktop`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
