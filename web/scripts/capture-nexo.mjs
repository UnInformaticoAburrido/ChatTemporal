// Evidencia visual reproducible: solo datos sintéticos, sin cuentas del usuario.
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { LocalKey } from '../public/keys.js';
const output = new URL('../../artifacts/nexo/implementation/', import.meta.url);
await mkdir(output, { recursive: true });
const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'AlexRiver', email: 'alex@example.com', email_verified: true };
const key = LocalKey.generate(user.id), peer = LocalKey.generate(user.id), password = 'synthetic visual backup password';
const backup = await key.backup(password), phrase = Array(23).fill('abandon').concat('art').join(' ');
const rooms = ['Lucía', 'Mario', 'Sofía', 'Dani', 'Irene'].map((nick, i) => ({ id: `22345678-1234-4234-8234-123456789ab${i}`,
  peer: { nick }, mode: 'stored', status: 'active', role: 'host', accepted_at: '2026-10-15T00:00:00Z', grace_messages_used: 0, grace_message_limit: 5 }));
const history = ['¡Hola! ¿Cómo va el proyecto?', 'Muy bien. Ya tengo un primer diseño.', 'Genial, ¿me puedes enseñar la interfaz?', 'Claro. Te la comparto esta tarde ✨'].map((text, i) => ({
  message_id: crypto.randomUUID(), sender_role: i % 2 ? 'host' : 'guest', sent_at: `2026-10-15T12:4${i}:00Z`,
  ...(i % 2 ? key.encrypt(text, peer.publicKey) : peer.encrypt(text, key.publicKey)),
}));
const errors = [];
const browser = await chromium.launch({ headless: true });
try {
  for (const [label, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 393, height: 852 }]]) {
    const context = await browser.newContext({ viewport, locale: 'es-ES', timezoneId: 'UTC', deviceScaleFactor: 1 });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.name));
    page.on('console', event => { if (event.type() === 'error') errors.push('console-error'); });
    await page.route('**/api/v1/**', route => {
      const path = new URL(route.request().url()).pathname;
      const reply = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (path.endsWith('/recover')) return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
      if (path.endsWith('/users/me')) return reply(user);
      if (path.endsWith('/keys')) return reply({ public_key: key.publicKey, protocol_version: 1 });
      if (path.endsWith('/invitations/me')) return reply({ stored_code: 'NX-7K2P-9Q4M', ephemeral_code: 'NX-5D8R-1L3V' });
      if (path.endsWith('/conversations')) return reply({ items: rooms, next_cursor: null });
      if (path.endsWith('/ws-ticket')) return reply({ ticket: 'synthetic-ticket' });
      if (path.endsWith('/vote')) return reply(null);
      if (path.endsWith('/key')) return reply({ public_key: peer.publicKey, protocol_version: 1 });
      if (path.endsWith('/messages')) return reply({ items: history, next_cursor: null });
      if (rooms.some(room => path.endsWith('/conversations/' + room.id))) return reply(rooms.find(room => path.endsWith(room.id)));
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { code: 'USER_NOT_FOUND' } }) });
    });
    await page.routeWebSocket('**/ws/v1?*', ws => ws.send(JSON.stringify({ type: 'session.ready', request_id: crypto.randomUUID(), conversation_id: null, timestamp: new Date().toISOString(), payload: {} })));
    const capture = async name => {
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: new URL(`${label}-${name}.png`, output).pathname });
      if (!(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))) throw new Error('Viewport overflow: ' + name);
    };
    await page.goto('http://127.0.0.1:4173/'); await capture('welcome');
    await page.getByRole('button', { name: 'Crear cuenta', exact: true }).click(); await capture('register');
    await page.locator('#recover-tab').click(); await capture('login');
    await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
    await page.getByLabel('Palabra 1', { exact: true }).fill(phrase);
    await page.getByRole('button', { name: 'Acceder a mi cuenta', exact: true }).click();
    await page.getByRole('button', { name: 'Obtener código para invitar' }).click();
    await page.locator('#codes .invitation-card').last().waitFor(); await capture('account');
    await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
    await page.getByLabel('Copia cifrada de claves', { exact: true }).setInputFiles({ name: 'visual.chatkey', mimeType: 'application/json', buffer: Buffer.from(backup) });
    await page.getByLabel('Contraseña de la copia', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Abrir copia y comprobar clave' }).click();
    await page.locator('#rooms button').first().click();
    await page.locator('#messages .message').last().waitFor(); await capture('chat');
    await context.close();
  }
  await writeFile(new URL('console.json', output), JSON.stringify({ errors }));
  if (errors.length) throw new Error('Browser console errors');
  // Comparaciones lado a lado, con el mismo viewport y escala, sin editar las capturas.
  const comparison = await browser.newPage({ viewport: { width: 2880, height: 900 }, deviceScaleFactor: 1 });
  for (const [number, state] of [[1,'welcome'],[2,'register'],[3,'login'],[4,'account'],[5,'chat']]) {
    const source = await readFile(new URL(`../../artifacts/nexo/reference/native-${number}.png`, import.meta.url));
    const current = await readFile(new URL(`desktop-${state}.png`, output));
    await comparison.setContent(`<style>body{margin:0;display:flex}img{width:1440px;height:900px}</style><img src="data:image/png;base64,${source.toString('base64')}"><img src="data:image/png;base64,${current.toString('base64')}">`);
    await comparison.locator('img').last().waitFor();
    await comparison.screenshot({ path: new URL(`comparison-${state}.png`, output).pathname });
    const focus = state === 'welcome' ? { x: 64, y: 176, width: 760, height: 284 }
      : state === 'account' ? { x: 78, y: 590, width: 1270, height: 270 }
      : state === 'chat' ? { x: 450, y: 110, width: 940, height: 520 } : null;
    if (focus) {
      const focusPage = await browser.newPage({ viewport: { width: focus.width * 2, height: focus.height }, deviceScaleFactor: 1 });
      await focusPage.setContent(`<style>body{margin:0;display:flex}div{width:${focus.width}px;height:${focus.height}px;overflow:hidden;position:relative}img{position:absolute;width:1440px;height:900px;left:-${focus.x}px;top:-${focus.y}px}</style><div><img src="data:image/png;base64,${source.toString('base64')}"></div><div><img src="data:image/png;base64,${current.toString('base64')}"></div>`);
      await focusPage.locator('img').last().waitFor();
      await focusPage.screenshot({ path: new URL(`focus-${state}.png`, output).pathname }); await focusPage.close();
    }
  }
} finally { key.lock(); peer.lock(); await browser.close(); }
