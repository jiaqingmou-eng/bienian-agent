import { HttpException, Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'node:crypto';
import { sign } from 'jsonwebtoken';

@Injectable()
export class BackendClient {
  constructor(private readonly config: ConfigService) {}

  async request<T>(
    path: string,
    options: {
      userId?: string;
      demo?: boolean;
      method?: 'GET' | 'POST';
      body?: unknown;
      requestId?: string;
      idempotencyKey?: string;
      timeoutMs?: number;
    } = {},
  ): Promise<T> {
    const token = sign(
      { scope: options.demo ? 'demo' : 'user' },
      this.config.getOrThrow<string>('INTERNAL_JWT_SECRET'),
      {
        algorithm: 'HS256',
        subject: options.demo ? 'demo-bootstrap' : (options.userId ?? 'bff-health'),
        issuer: 'enterprise-bff',
        audience: 'enterprise-backend',
        expiresIn: 60,
      },
    );
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      'X-Request-Id': options.requestId ?? randomUUID(),
      Accept: 'application/json',
    };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
    let response: Response;
    try {
      response = await fetch(new URL(path, this.config.getOrThrow<string>('BACKEND_URL')), {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
        redirect: 'error',
      });
    } catch {
      throw new HttpException('财务服务暂不可用，请稍后重试', 503);
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new HttpException('财务服务返回了无效响应', 502);
    }
    if (!response.ok) {
      const message =
        typeof body === 'object' &&
        body !== null &&
        'message' in body &&
        typeof body.message === 'string'
          ? body.message
          : '财务服务请求失败';
      throw new HttpException(message, response.status >= 500 ? 503 : response.status);
    }
    return body as T;
  }

  async ready(): Promise<unknown> {
    return this.request('/ready');
  }
}
