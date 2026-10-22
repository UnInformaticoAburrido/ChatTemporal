import { defineConfig, devices } from '@playwright/test';
const origin = process.env.CHAT_E2E_BASE_URL;
const main = process.env.CHAT_E2E_MAIN_URL;
const local = value => value && new URL(value).hostname === '127.0.0.1' && new URL(value).protocol === 'http:' && new URL(value).origin === value;
if (!local(origin) || !local(main) || origin === main || process.env.APP_ENV !== 'test' || process.env.RUN_WEB_E2E !== '1'
  || !process.env.CHAT_E2E_CONTROL) throw Error('Use the isolated web E2E runner');
export default defineConfig({
  testDir: './integration', testMatch: '*.spec.mjs', fullyParallel: false,
  retries: 0, workers: 1, reporter: 'list', timeout: 90000, preserveOutput: 'never',
  expect: { timeout: 12000 },
  use: { baseURL: origin, trace: 'off', screenshot: 'off', video: 'off' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
});
