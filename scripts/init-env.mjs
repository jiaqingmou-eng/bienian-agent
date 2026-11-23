import { randomBytes } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const template = new URL('../.env.example', import.meta.url);
const destination = new URL('../.env', import.meta.url);
const content = (await readFile(template, 'utf8'))
  .replace('replace-with-a-random-session-secret-at-least-32-chars', randomBytes(48).toString('hex'))
  .replace('replace-with-a-different-random-jwt-secret-at-least-32-chars', randomBytes(48).toString('hex'));

try {
  // Never overwrite an existing developer configuration or print generated secrets.
  await writeFile(destination, content, { flag: 'wx', mode: 0o600 });
  console.log('Created .env with independent random secrets. Existing files are never overwritten.');
} catch (error) {
  if (error?.code === 'EEXIST') console.log('.env already exists; kept unchanged.');
  else throw error;
}
