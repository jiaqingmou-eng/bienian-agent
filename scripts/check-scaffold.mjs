/** Dependency-free preflight: validate JSON, local imports and independent app boundaries. */
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { dirname, extname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
async function walk(directory) {
  const paths = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (['node_modules', '.git', '.venv', 'work', 'dist', '__pycache__'].includes(entry.name)) continue;
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) paths.push(...await walk(path));
    else paths.push(path);
  }
  return paths;
}

const paths = await walk(root);
let imports = 0;
for (const path of paths) {
  const extension = extname(path);
  if (extension === '.json') JSON.parse(await readFile(path, 'utf8'));
  if (!['.ts', '.tsx'].includes(extension)) continue;
  const text = await readFile(path, 'utf8');
  for (const match of text.matchAll(/(?:from\s*|import\s*)['"]([^'"]+)['"]/g)) {
    const specifier = match[1];
    if (!specifier.startsWith('.')) continue;
    const target = resolve(dirname(path), specifier);
    assert.ok(
      ['', '.ts', '.tsx', '.css', '/index.ts', '/index.tsx'].some((suffix) => existsSync(target + suffix)),
      `Missing local import: ${relative(root, path)} -> ${specifier}`,
    );
    const sourceApp = relative(root, path).replaceAll('\\', '/').split('/').slice(0, 2).join('/');
    assert.ok(relative(root, target).replaceAll('\\', '/').startsWith(`${sourceApp}/`),
      `Cross-application source import: ${relative(root, path)}`);
    imports++;
  }
}
console.log(`Scaffold preflight passed: ${paths.length} files, ${imports} local imports.`);
console.log('This preflight does not replace dependency installation, type checking or integration tests.');
