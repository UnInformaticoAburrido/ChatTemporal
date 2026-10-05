// Solo servidor de assets del ensayo; no sustituye Caddy ni expone el checkout.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const root = new URL('../public/', import.meta.url);
const files = new Map(['index.html', 'styles.css', 'app.js', 'api.js', 'keys.js', 'keys-ui.js', 'chat.js', 'chat-ui.js',
  'transfers.js', 'transfers-ui.js', 'qr.js', 'push.js', 'push-ui.js', 'service-worker.js', 'bootstrap.js', 'vendor/qr.js', 'vendor/qr.LICENSE.txt', 'vendor/sodium.js', 'vendor/sodium.LICENSE.txt']
  .map(file => ['/' + file, file]));
files.set('/', 'index.html');
createServer(async (request, response) => {
  const file = files.get(request.url);
  if (!file) { response.writeHead(404).end(); return; }
  const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
  response.writeHead(200, { 'Content-Type': type + '; charset=utf-8', 'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" });
  response.end(await readFile(new URL(file, root)));
}).listen(4173, '127.0.0.1');

// Segundo origen solo del ensayo: no existe en los assets/rutas de producción.
const mainFiles = new Map([
  ['/', new URL('./fixtures/main-app.html', import.meta.url)],
  ['/main-app.js', new URL('./fixtures/main-app.js', import.meta.url)],
  ['/launch-chat.mjs', new URL('../../integrations/main-app/launch-chat.mjs', import.meta.url)],
]);
createServer(async (request, response) => {
  const file = mainFiles.get(new URL(request.url, 'http://127.0.0.1:4174').pathname);
  if (!file) { response.writeHead(404).end(); return; }
  response.writeHead(200, { 'Content-Type': file.pathname.endsWith('.html') ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8',
    'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'" });
  response.end(await readFile(file));
}).listen(4174, '127.0.0.1');
