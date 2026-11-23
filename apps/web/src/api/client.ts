export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly requestId?: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, {
    ...init,
    credentials: 'same-origin',
    headers,
    signal: init.signal ?? AbortSignal.timeout(15_000),
  });
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== '/me' && path !== '/auth/demo-login') {
      window.dispatchEvent(new Event('auth-expired'));
    }
    const payload = body as { message?: string; requestId?: string } | null;
    throw new ApiError(payload?.message || '请求失败，请稍后重试', response.status, payload?.requestId);
  }
  return body as T;
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.message}${error.requestId ? `（请求编号：${error.requestId}）` : ''}`;
  }
  return '无法连接服务，请检查网络及服务运行状态。';
}
