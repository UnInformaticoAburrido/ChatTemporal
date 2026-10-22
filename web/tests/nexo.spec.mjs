import { test, expect } from '@playwright/test';
const phrase = Array(23).fill('abandon').concat('art').join(' ');

test('bienvenida y modales permiten cambiar de acceso, cerrar con Escape y consultar privacidad', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.name));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Una conversación puede ser el comienzo de todo.' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Crear cuenta', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bienvenido de nuevo' })).toBeVisible();
  await expect(page.locator('.word-input input')).toHaveCount(24);
  expect(await page.locator('.word-input input').evaluateAll(fields => fields.every(field => field.type === 'password' && field.autocomplete === 'off'))).toBe(true);
  await page.getByLabel('Palabra 1', { exact: true }).fill(phrase);
  expect(await page.locator('.word-input input').evaluateAll(fields => fields.map(field => field.value).join(' '))).toBe(phrase);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await expect(page.getByLabel('Palabra 1', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Cerrar acceso' }).click();
  if (!await page.locator('#privacy-link').isVisible()) await page.getByLabel('Más información', { exact: true }).click();
  await page.getByRole('button', { name: 'Privacidad', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tu privacidad, bajo tu control' })).toBeVisible();
  await page.getByRole('button', { name: 'Cerrar información' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('editar perfil guarda nick y correo por API y actualiza la cabecera', async ({ page }) => {
  let user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'AlexRiver', email: 'alex@example.com', email_verified: true };
  const changes = [];
  await page.route('**/api/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith('/recover')) return route.fulfill({ json: { access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 } });
    if (path.endsWith('/users/me')) {
      if (request.method() === 'PATCH') { const data = request.postDataJSON(); changes.push(data); user = { ...user, ...data }; }
      return route.fulfill({ json: user });
    }
    throw new Error('Unexpected API path');
  });
  await page.goto('/'); await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Palabra 1', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Acceder a mi cuenta', exact: true }).click();
  await page.getByRole('button', { name: 'Editar perfil' }).click();
  await page.locator('#profile-nick').fill('AlexNuevo');
  await page.getByRole('button', { name: 'Guardar cambios' }).click();
  await expect(page.locator('#user-name')).toHaveText('AlexNuevo');
  await expect(page.locator('#session-status')).toContainText('AlexNuevo');
  expect(changes).toEqual([{ nick: 'AlexNuevo', email: 'alex@example.com' }]);
});


test('enlaces directos e historial conectan bienvenida, registro y acceso sin conservar la frase', async ({ page }) => {
  await page.goto('/#registro');
  await expect(page.getByRole('heading', { name: 'Crear tu cuenta' })).toBeVisible();
  await page.locator('#recover-tab').click();
  await expect(page).toHaveURL(/#acceso$/);
  await page.getByLabel('Palabra 1', { exact: true }).fill('synthetic-secret');
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Crear tu cuenta' })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', { name: 'Bienvenido de nuevo' })).toBeVisible();
  await expect(page.getByLabel('Palabra 1', { exact: true })).toHaveValue('');
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/#inicio$/);
  await expect(page.locator('#landing')).toBeVisible();
  await page.goto('/#cuenta');
  await expect(page).toHaveURL(/#acceso$/);
  await expect(page.getByRole('heading', { name: 'Bienvenido de nuevo' })).toBeVisible();
  await page.goto('/#chat');
  await expect(page).toHaveURL(/#acceso$/);
  await expect(page.locator('#workspace')).toBeHidden();
});
