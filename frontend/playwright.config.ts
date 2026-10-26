import { defineConfig } from '@playwright/test'

// Runs against a real app (the API serving the built frontend) seeded with the budget seed
// and two users; CI starts it. Locally: PW_CHANNEL=chrome E2E_BASE_URL=http://127.0.0.1:8765.
const channel = process.env.PW_CHANNEL

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000',
    trace: 'retain-on-failure',
    reducedMotion: 'reduce',
    ...(channel ? { channel } : {}),
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: 'mobile',
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
      },
    },
  ],
})
