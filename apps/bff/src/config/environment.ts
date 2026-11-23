/** Validate configuration before accepting requests; never log secret values. */
export function validateEnvironment(input: Record<string, unknown>) {
  const required = (key: string): string => {
    const value = input[key];
    if (typeof value !== 'string' || value.length === 0) {
      throw new Error(`Missing configuration: ${key}`);
    }
    return value;
  };
  for (const key of ['SESSION_SECRET', 'INTERNAL_JWT_SECRET']) {
    const value = required(key);
    if (value.length < 32 || value.startsWith('replace-')) {
      throw new Error(`${key} must be a generated secret of at least 32 characters`);
    }
  }
  if (input.SESSION_SECRET === input.INTERNAL_JWT_SECRET) {
    throw new Error('Session and internal JWT secrets must be different');
  }
  const origin = new URL(required('APP_ORIGIN'));
  const backend = new URL(required('BACKEND_URL'));
  if (!['http:', 'https:'].includes(origin.protocol) || origin.origin !== input.APP_ORIGIN) {
    throw new Error('APP_ORIGIN must be an HTTP(S) origin without a trailing slash');
  }
  if (!['http:', 'https:'].includes(backend.protocol) || backend.username || backend.password) {
    throw new Error('BACKEND_URL must be an HTTP(S) URL without embedded credentials');
  }
  const port = Number(input.BFF_PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('BFF_PORT must be a valid TCP port');
  }
  if (input.ENABLE_DEMO_LOGIN !== 'true' && input.ENABLE_DEMO_LOGIN !== 'false') {
    throw new Error('ENABLE_DEMO_LOGIN must be true or false');
  }
  // Memory sessions and account switching are deliberately limited to local demos.
  if (input.NODE_ENV === 'production') {
    throw new Error('Configure persistent sessions and real authentication before production');
  }
  return { ...input, BFF_PORT: port };
}
