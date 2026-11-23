import { ForbiddenException, Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { BackendClient } from '../backend/backend.client';
import type { DemoAccount, User } from '../backend/backend.types';

@Injectable()
export class AuthService {
  constructor(private readonly backend: BackendClient, private readonly config: ConfigService) {}

  private assertDemo(): void {
    if (this.config.get<string>('ENABLE_DEMO_LOGIN') !== 'true') {
      throw new ForbiddenException('演示登录未开启');
    }
  }

  accounts() {
    this.assertDemo();
    return this.backend.request<DemoAccount[]>('/api/v1/demo/accounts', { demo: true });
  }

  login(accountKey: string, requestId: string) {
    this.assertDemo();
    return this.backend.request<User>('/api/v1/demo/login', {
      demo: true, method: 'POST', body: { account_key: accountKey }, requestId,
    });
  }

  me(userId: string, requestId: string) {
    return this.backend.request<User>('/api/v1/me', { userId, requestId });
  }
}
