import assert from 'node:assert/strict';
import { test } from 'node:test';
import { validateEnvironment } from '../apps/bff/src/config/environment.ts';

const valid = {
  NODE_ENV: 'development', APP_ORIGIN: 'http://localhost:5173', BACKEND_URL: 'http://127.0.0.1:8000',
  SESSION_SECRET: 'local-session-secret-0000000000000000000000',
  INTERNAL_JWT_SECRET: 'local-internal-secret-111111111111111111111',
  ENABLE_DEMO_LOGIN: 'true', BFF_PORT: '3000',
};

test('valid local configuration parses the port', () => {
  assert.equal(validateEnvironment(valid).BFF_PORT, 3000);
});

test('unsafe secrets, invalid origins, ports and production demos fail fast', () => {
  for (const changes of [
    { SESSION_SECRET: 'short' },
    { SESSION_SECRET: valid.INTERNAL_JWT_SECRET },
    { INTERNAL_JWT_SECRET: 'replace-with-a-placeholder-secret-0000000000' },
    { APP_ORIGIN: 'http://localhost:5173/' },
    { BACKEND_URL: 'file:///tmp/backend' },
    { BFF_PORT: 'not-a-port' },
    { BFF_PORT: '0' },
    { ENABLE_DEMO_LOGIN: 'yes' },
    { NODE_ENV: 'production' },
  ]) {
    assert.throws(() => validateEnvironment({ ...valid, ...changes }));
  }
});
