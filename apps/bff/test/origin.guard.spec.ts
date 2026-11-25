import 'reflect-metadata';
import { ForbiddenException, type ExecutionContext } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { OriginGuard } from '../src/common/guards';

/** 验证本机别名兼容的边界，不启动外部服务，也不依赖测试环境的默认 NODE_ENV。 */
function check(origin: string | undefined, overrides: Record<string, string> = {}): boolean {
  const guard = new OriginGuard(
    new ConfigService({
      APP_ORIGIN: 'http://localhost:5173',
      NODE_ENV: 'development',
      ENABLE_DEMO_LOGIN: 'true',
      ...overrides,
    }),
  );
  const context = {
    switchToHttp: () => ({ getRequest: () => ({ method: 'POST', headers: { origin } }) }),
  } as unknown as ExecutionContext;
  return guard.canActivate(context);
}

describe('本地演示来源校验', () => {
  it('仅在开发演示中兼容同协议、同端口的两个回环地址', () => {
    expect(check('http://localhost:5173')).toBe(true);
    expect(check('http://127.0.0.1:5173')).toBe(true);
    expect(check('http://localhost:5173', { APP_ORIGIN: 'http://127.0.0.1:5173' })).toBe(true);
  });

  it('其他端口、协议、域名、非规范来源和缺失来源仍被拒绝', () => {
    for (const origin of [
      undefined,
      'null',
      'not-a-url',
      'http://127.0.0.1:5174',
      'http://localhost:3000',
      'https://127.0.0.1:5173',
      'http://localhost.evil.example:5173',
      'http://127.0.0.1.evil.example:5173',
      'http://192.168.1.2:5173',
      'http://127.0.0.1:5173/',
      'http://user@127.0.0.1:5173',
      'http://127.1:5173',
    ]) {
      expect(() => check(origin)).toThrow(ForbiddenException);
    }
  });

  it('关闭演示或使用非开发环境时仅允许精确配置的来源', () => {
    const cases: Record<string, string>[] = [
      { ENABLE_DEMO_LOGIN: 'false' },
      { NODE_ENV: 'test' },
      { NODE_ENV: 'production' },
    ];
    for (const overrides of cases) {
      expect(check('http://localhost:5173', overrides)).toBe(true);
      expect(() => check('http://127.0.0.1:5173', overrides)).toThrow(ForbiddenException);
    }
  });

  it('配置为其他域名时不会额外信任回环地址', () => {
    expect(() =>
      check('http://127.0.0.1:5173', {
        APP_ORIGIN: 'https://app.example:5173',
      }),
    ).toThrow(ForbiddenException);
  });
});
