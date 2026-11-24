import 'reflect-metadata';
import { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { randomUUID } from 'node:crypto';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import { BackendClient } from '../src/backend/backend.client';
import { configureApp } from '../src/bootstrap';
import { parseAgentRun, type AgentRun } from '../src/agent/agent.contract';

const user = {
  id: '20000000-0000-4000-8000-000000000001',
  tenant_id: '10000000-0000-4000-8000-000000000001',
  name: '测试员工',
  role: 'employee',
};
const run: AgentRun = {
  id: randomUUID(),
  status: 'collecting',
  version: 1,
  anchor_date: '2026-11-23',
  timezone: 'Asia/Hong_Kong',
  messages: [{ id: randomUUID(), role: 'assistant', content: '请补充用途' }],
  missing_fields: ['业务用途'],
  preview: null,
  expense_id: null,
};

describe('Agent BFF boundary', () => {
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
  beforeEach(() => {
    backend.request.mockReset();
    backend.request.mockResolvedValue(user);
  });
  afterAll(async () => {
    await app.close();
  });

  async function login() {
    const session = request.agent(app.getHttpServer());
    await session
      .post('/api/auth/demo-login')
      .set('Origin', 'http://localhost:5173')
      .send({ account_key: 'employee_a' })
      .expect(200);
    backend.request.mockClear();
    backend.request.mockResolvedValue(run);
    return session;
  }

  it('requires a session and a trusted write origin', async () => {
    await request(app.getHttpServer()).get(`/api/agent/runs/${run.id}`).expect(401);
    const session = await login();
    await session
      .post('/api/agent/runs')
      .set('Origin', 'https://untrusted.example')
      .send({ client_request_id: randomUUID(), message: '报销' })
      .expect(403);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('forwards only validated messages and the session identity', async () => {
    const session = await login();
    const body = { client_request_id: randomUUID(), message: '高铁票286元' };
    await session
      .post('/api/agent/runs')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(201);
    expect(backend.request).toHaveBeenCalledWith(
      '/api/v1/agent/runs',
      expect.objectContaining({ userId: user.id, body, timeoutMs: 60_000 }),
    );
    backend.request.mockClear();
    await session
      .post('/api/agent/runs')
      .set('Origin', 'http://localhost:5173')
      .send({ ...body, tenant_id: randomUUID() })
      .expect(400);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('rejects boolean confirmation and invalid versions before forwarding', async () => {
    const session = await login();
    await session
      .post(`/api/agent/runs/${run.id}/confirm`)
      .set('Origin', 'http://localhost:5173')
      .send({ confirmed: true })
      .expect(400);
    await session
      .post(`/api/agent/runs/${run.id}/messages`)
      .set('Origin', 'http://localhost:5173')
      .send({ message_id: randomUUID(), expected_version: '1', message: '修改' })
      .expect(400);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('uses HTTP 200 for updates and rejects malformed upstream output', async () => {
    const session = await login();
    await session
      .post(`/api/agent/runs/${run.id}/messages`)
      .set('Origin', 'http://localhost:5173')
      .send({ message_id: randomUUID(), expected_version: 1, message: '用途是客户拜访' })
      .expect(200);
    backend.request.mockResolvedValue({ ...run, status: 'completed', expense_id: null });
    await session.get(`/api/agent/runs/${run.id}`).expect(502);
  });

  it('lists private task summaries using the session identity and validates pagination', async () => {
    await request(app.getHttpServer()).get('/api/agent/runs').expect(401);
    const session = await login();
    const history = {
      items: [
        { id: run.id, status: run.status, title: '待完善任务', created_at: '2026-11-23T00:00:00Z' },
      ],
      total: 1,
      limit: 5,
      offset: 0,
    };
    backend.request.mockResolvedValue(history);
    const result = await session.get('/api/agent/runs?limit=5&offset=0').expect(200);
    expect(result.body).toEqual(history);
    expect(backend.request).toHaveBeenCalledWith(
      '/api/v1/agent/runs?limit=5&offset=0',
      expect.objectContaining({ userId: user.id }),
    );
    backend.request.mockClear();
    for (const query of ['limit=101', 'offset=-1', 'owner_id=x', 'limit=abc']) {
      await session.get(`/api/agent/runs?${query}`).expect(400);
    }
    expect(backend.request).not.toHaveBeenCalled();
    backend.request.mockResolvedValue({
      ...history,
      items: [{ ...history.items[0], status: 'unknown' }],
    });
    await session.get('/api/agent/runs').expect(502);
  });

  it('checks item money and totals in preview output', () => {
    const preview = {
      confirmation_id: randomUUID(),
      version: 1,
      content_hash: 'a'.repeat(64),
      expires_at: '2026-11-23T12:00:00Z',
      total_fen: 100,
      payload: {
        title: '测试',
        purpose: '测试',
        items: [
          {
            category: 'transport',
            incurred_on: '2026-11-22',
            description: '高铁',
            amount_fen: 101,
          },
        ],
      },
    };
    expect(() => parseAgentRun({ ...run, status: 'awaiting_confirmation', preview })).toThrow();
    expect(() =>
      parseAgentRun({
        ...run,
        status: 'awaiting_confirmation',
        preview: { ...preview, total_fen: 101 },
      }),
    ).not.toThrow();
    expect(() =>
      parseAgentRun({
        ...run,
        status: 'awaiting_confirmation',
        preview: { ...preview, total_fen: 1.5 },
      }),
    ).toThrow();
  });
});
