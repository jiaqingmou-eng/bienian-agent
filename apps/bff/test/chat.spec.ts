import 'reflect-metadata';
import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { randomUUID } from 'node:crypto';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import { BackendClient } from '../src/backend/backend.client';
import { configureApp } from '../src/bootstrap';
import { parseChatReply } from '../src/agent/chat.contract';

const user = { id: randomUUID(), tenant_id: randomUUID(), name: '虚构员工', role: 'employee' };
const reply = {
  kind: 'policy',
  status: 'answered',
  message: '成都住宿每人每晚不超过400.00元。',
  as_of: '2026-11-25',
  run: null,
  sources: [
    {
      document: {
        id: randomUUID(),
        source_key: 'fictional',
        title: '虚构制度',
        version: 1,
        owner_name: '虚构责任人',
        format: 'markdown',
        visibility: 'tenant',
        status: 'published',
        effective_from: '2026-01-01',
        effective_until: null,
      },
      citation_id: 'a'.repeat(64),
      matched_chunk_id: 'b'.repeat(64),
      rule_version: 'policy-chunks-v1',
      text: '第四条 成都住宿每人每晚不超过400.00元。',
      heading_path: ['住宿'],
      source_start_line: 3,
      source_end_line: 3,
    },
  ],
};

describe('统一对话 BFF 边界', () => {
  let app: INestApplication;
  const backend = { request: jest.fn(), ready: jest.fn() };
  beforeAll(async () => {
    const module = await Test.createTestingModule({ imports: [AppModule] })
      .overrideProvider(BackendClient)
      .useValue(backend)
      .compile();
    app = module.createNestApplication({ logger: false });
    configureApp(app);
    await app.init();
  });
  beforeEach(() => backend.request.mockReset());
  afterAll(async () => {
    await app.close();
  });

  async function login() {
    backend.request.mockResolvedValue(user);
    const session = request.agent(app.getHttpServer());
    await session
      .post('/api/auth/demo-login')
      .set('Origin', 'http://localhost:5173')
      .send({ account_key: 'employee_a' })
      .expect(200);
    backend.request.mockReset();
    backend.request.mockResolvedValue(reply);
    return session;
  }

  it('认证、Origin、白名单和任务版本配对在转发前检查', async () => {
    const body = { message_id: randomUUID(), message: '成都住宿标准' };
    await request(app.getHttpServer())
      .post('/api/agent/chat')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(401);
    const session = await login();
    await session
      .post('/api/agent/chat')
      .set('Origin', 'https://untrusted.invalid')
      .send(body)
      .expect(403);
    for (const extra of [
      { tenant_id: randomUUID() },
      { intent: 'expense' },
      { expected_version: 0 },
      { run_id: randomUUID() },
      { run_id: randomUUID(), expected_version: true },
      { run_id: null, expected_version: null },
    ]) {
      await session
        .post('/api/agent/chat')
        .set('Origin', 'http://localhost:5173')
        .send({ ...body, ...extra })
        .expect(400);
    }
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('转发稳定消息和可信身份，校验并裁剪制度响应', async () => {
    const session = await login();
    const body = {
      message_id: randomUUID(),
      message: '成都住宿标准',
      run_id: randomUUID(),
      expected_version: 2,
    };
    backend.request.mockResolvedValue({ ...reply, internal: 'must not leak' });
    const response = await session
      .post('/api/agent/chat')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(200);
    expect(response.body).toEqual(reply);
    expect(backend.request).toHaveBeenCalledWith(
      '/api/v1/agent/chat',
      expect.objectContaining({
        body,
        userId: user.id,
        timeoutMs: 135_000,
      }),
    );
    backend.request.mockResolvedValue({ ...reply, sources: [] });
    await session
      .post('/api/agent/chat')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(502);
  });

  it('不接受混合响应、无效引用、错误适用日期或缺失字段', () => {
    for (const value of [
      { ...reply, run: {} },
      { ...reply, kind: 'unknown' },
      { ...reply, as_of: '2025-01-01' },
      { ...reply, status: 'insufficient' },
      { ...reply, sources: [{ ...reply.sources[0], citation_id: 'forged' }] },
      { ...reply, sources: undefined },
    ])
      expect(() => parseChatReply(value)).toThrow();
    expect(
      parseChatReply({
        kind: 'clarification',
        status: 'needs_clarification',
        message: '请选择目标',
        sources: [],
        as_of: null,
        run: null,
      }).kind,
    ).toBe('clarification');
  });
});
