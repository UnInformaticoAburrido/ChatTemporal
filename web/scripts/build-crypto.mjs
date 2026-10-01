import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
const result = await build({ stdin: { contents: "export { default } from 'libsodium-wrappers';", resolveDir: process.cwd() },
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022', minify: true, write: false,
  banner: { js: '// Generated from pinned npm lock. See sodium.LICENSE.txt. Do not edit.' } });
const outputs = new Map([
  ['public/vendor/sodium.js', result.outputFiles[0].text],
  ['public/vendor/sodium.LICENSE.txt', await readFile('node_modules/libsodium/LICENSE', 'utf8')
    + '\n' + await readFile('node_modules/libsodium-wrappers/LICENSE', 'utf8')],
]);
for (const [path, content] of outputs) {
  if (process.argv.includes('--check')) {
    if (await readFile(path, 'utf8') !== content) throw Error('Crypto bundle differs from lock: ' + path);
  } else await writeFile(path, content);
}
