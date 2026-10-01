// Solo servidor de assets del ensayo; no sustituye Caddy ni expone el checkout.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
const root = new URL('../public/', import.meta.url);
const files = new Map([['/', 'index.html'], ['/index.html', 'index.html'], ['/styles.css', 'styles.css'], ['/app.js', 'app.js'], ['/api.js', 'api.js'], ['/keys.js', 'keys.js'], ['/keys-ui.js', 'keys-ui.js'], ['/chat.js', 'chat.js'], ['/chat-ui.js', 'chat-ui.js'], ['/vendor/sodium.js', 'vendor/sodium.js'], ['/vendor/sodium.LICENSE.txt', 'vendor/sodium.LICENSE.txt']]);
createServer(async (request, response) => {
  const file = files.get(request.url);
  if (!file) { response.writeHead(404).end(); return; }
  const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
  response.writeHead(200, { 'Content-Type': type + '; charset=utf-8', 'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" });
  response.end(await readFile(new URL(file, root)));
}).listen(4173, '127.0.0.1');
