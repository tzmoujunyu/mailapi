import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(here);
const output = path.join(root, 'mailapi_shared/static/vendor/tiptap');
await fs.mkdir(output, { recursive: true });
const result = await build({
  absWorkingDir: here, entryPoints: ['vendor-tiptap.mjs'], bundle: true,
  format: 'iife', globalName: 'MailapiTiptap', target: 'es2020', minify: true,
  legalComments: 'none', metafile: true, outfile: path.join(output, 'tiptap.js'),
});
const lock = JSON.parse(await fs.readFile(path.join(here, 'package-lock.json'), 'utf8'));
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.startsWith('node_modules/')) continue;
  const parts = input.split('/');
  const directory = parts.slice(0, parts[1].startsWith('@') ? 3 : 2).join('/');
  packages.set(directory, lock.packages[directory]);
}
const dependencies = [], licenses = [];
for (const [directory, item] of [...packages].sort()) {
  const folder = path.join(here, directory);
  const pkg = JSON.parse(await fs.readFile(path.join(folder, 'package.json'), 'utf8'));
  const filename = (await fs.readdir(folder)).find(name => /^license(?:\.(?:txt|md))?$/i.test(name));
  if (!filename) throw new Error(`Missing license: ${pkg.name}`);
  dependencies.push({ package: pkg.name, version: pkg.version, license: pkg.license,
    source: item.resolved, integrity: item.integrity });
  licenses.push(`${pkg.name} ${pkg.version}\n${await fs.readFile(path.join(folder, filename), 'utf8')}`);
}
await fs.writeFile(path.join(output, 'dependencies.json'), JSON.stringify(dependencies, null, 2) + '\n');
await fs.writeFile(path.join(output, 'LICENSES.txt'), licenses.join('\n\n-----\n\n'));
const sha256 = {};
for (const filename of ['tiptap.js', 'dependencies.json', 'LICENSES.txt']) {
  sha256[filename] = createHash('sha256').update(await fs.readFile(path.join(output, filename))).digest('hex');
}
const manifestPath = path.join(root, 'mailapi_shared/static/vendor/manifest.json');
const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'))
  .filter(item => item.package !== 'tiptap');
manifest.push({ package: 'tiptap', version: lock.packages['node_modules/@tiptap/core'].version,
  license: 'MIT', source: 'frontend/vendor-tiptap.mjs', sha256 });
await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
console.log(`Built Tiptap with ${dependencies.length} pinned runtime packages`);
