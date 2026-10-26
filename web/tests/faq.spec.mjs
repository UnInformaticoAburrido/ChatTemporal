import { test, expect } from '@playwright/test';

test('la ayuda es accesible sin cuenta, explica las claves y admite enlaces a una respuesta', async ({ page }) => {
  const apiCalls = [], errors = [];
  page.on('request', request => { if (request.url().includes('/api/v1/')) apiCalls.push(request.url()); });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('link', { name: 'Preguntas frecuentes', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Preguntas frecuentes', exact: true })).toBeVisible();
  await expect(page.locator('.topbar')).toBeVisible();
  await page.getByRole('link', { name: 'Abrir mis claves', exact: true }).click();
  await expect(page).toHaveURL(/#preguntas\/claves$/);
  await expect(page.locator('#faq-claves')).toHaveAttribute('open', '');
  await expect(page.locator('#faq-claves')).toContainText('chat-claves.chatkey');
  await expect(page.locator('#faq-claves')).toContainText('No introduzcas aquí tus 24 palabras');
  await expect(page.locator('.topbar')).toBeVisible();
  expect(await page.locator('.topbar').evaluate(element => element.getBoundingClientRect().top)).toBeGreaterThanOrEqual(0);
  await page.getByRole('link', { name: 'Dónde quedan los mensajes', exact: true }).click();
  await expect(page.locator('#faq-privacidad')).toHaveAttribute('open', '');
  await expect(page.locator('#faq-privacidad')).toContainText('se pierden al recargar');
  await page.reload();
  await expect(page.locator('#faq-privacidad')).toHaveAttribute('open', '');
  await page.getByRole('link', { name: 'Nexo, inicio', exact: true }).click();
  await expect(page.locator('#landing')).toBeVisible();
  await expect(page.locator('#faq')).toBeHidden();
  await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bienvenido de nuevo' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Cerrar acceso', exact: true }).click();
  await page.setViewportSize({ width: 320, height: 740 });
  await expect(page.getByRole('link', { name: 'Preguntas frecuentes', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(apiCalls).toEqual([]); expect(errors).toEqual([]);
});

test('el asistente envía solo la pregunta, muestra texto seguro y permite borrar', async ({ page }) => {
  let body;
  await page.route('**/help-api/question', async route => {
    body = route.request().postDataJSON();
    expect(route.request().headers().authorization).toBeUndefined();
    await route.fulfill({ json: { answer: '<img src=x onerror=alert(1)> Abre Mi cuenta.' } });
  });
  await page.goto('/#preguntas');
  await page.getByLabel('¿Qué necesitas saber?').fill('¿Cómo cambio mi nombre?');
  await page.getByRole('button', { name: 'Preguntar', exact: true }).click();
  await expect(page.locator('#help-answer')).toContainText('<img src=x');
  await expect(page.locator('#help-answer img')).toHaveCount(0);
  expect(body).toEqual({ question: '¿Cómo cambio mi nombre?' });
  await page.getByRole('button', { name: 'Borrar consulta' }).click();
  await expect(page.locator('#help-answer')).toBeEmpty();
  await expect(page.getByLabel('¿Qué necesitas saber?')).toHaveValue('');
});

test('el asistente explica su ausencia y la ayuda sigue accesible', async ({ page }) => {
  await page.route('**/help-api/question', route => route.fulfill({ status: 503,
    json: { error: 'El asistente local aún no está conectado. Consulta las preguntas frecuentes.' } }));
  await page.goto('/#preguntas');
  await page.getByLabel('¿Qué necesitas saber?').fill('¿Cómo entro?');
  await page.getByRole('button', { name: 'Preguntar', exact: true }).click();
  await expect(page.locator('#help-status')).toContainText('aún no está conectado');
  await expect(page.getByRole('button', { name: 'Preguntar', exact: true })).toBeEnabled();
  await page.getByRole('link', { name: 'Consultar primeros pasos', exact: true }).click();
  await expect(page.locator('#faq-primera-vez')).toHaveAttribute('open', '');
});
