import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

test('descarga las 24 palabras numeradas sin confirmar su guardado ni enviarlas', async ({ page }) => {
  const words = Array(23).fill('abandon').concat('art');
  const requests = [];
  await page.route('**/api/v1/**', route => {
    requests.push(route.request().postData() || '');
    return route.fulfill({ status: 201, json: {
      tokens: { access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 },
      user: { nick: 'ana', email: 'ana@example.com', email_verified: false },
      recovery_mnemonic: words.join(' '),
    } });
  });
  await page.goto('/#registro');
  await page.getByLabel('Nombre de usuario', { exact: true }).fill('ana');
  await page.getByLabel('Correo electrónico', { exact: true }).fill('ana@example.com');
  await page.getByRole('button', { name: 'Crear mi cuenta' }).click();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Descargar mis palabras numeradas' }).click();
  const result = await download;
  expect(result.suggestedFilename()).toBe('nexo-palabras-de-acceso.txt');
  const content = await readFile(await result.path(), 'utf8');
  expect(content.split('\n').filter(line => /^\d+\. /.test(line))).toEqual(words.map((w, i) => `${i + 1}. ${w}`));
  await expect(page.getByRole('checkbox')).not.toBeChecked();
  expect(requests.some(value => value.includes('abandon'))).toBe(false);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});
