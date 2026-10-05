import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
const result = await build({ stdin: { contents: "export { default as QRCode } from 'qrcode'; export { default as jsQR } from 'jsqr';", resolveDir: process.cwd() },
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022', minify: true, write: false,
  banner: { js: '// Generated from pinned npm lock. See qr.LICENSE.txt. Do not edit.' } });
const outputs = new Map([
  ['public/vendor/qr.js', result.outputFiles[0].text],
  ['public/vendor/qr.LICENSE.txt', await readFile('node_modules/qrcode/license', 'utf8')
    + '\n' + await readFile('node_modules/jsqr/LICENSE', 'utf8')
    + '\n' + await readFile('node_modules/dijkstrajs/LICENSE.md', 'utf8')],
]);
for (const [path, content] of outputs) {
  if (process.argv.includes('--check')) {
    if (await readFile(path, 'utf8') !== content) throw Error('QR bundle differs from lock: ' + path);
  } else await writeFile(path, content);
}
