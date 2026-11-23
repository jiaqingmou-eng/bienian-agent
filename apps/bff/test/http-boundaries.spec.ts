import 'reflect-metadata';
import { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { randomUUID } from 'node:crypto';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import { BackendClient } from '../src/backend/backend.client';
import { configureApp } from '../src/bootstrap';

const user = {
  id: '20000000-0000-4000-8000-000000000001',
  tenant_id: '10000000-0000-4000-8000-000000000001',
  name: '员工小林', role: 'employee',
};

describe('BFF HTTP boundaries', () => {
  let app: INestApplication;
  const backend = { request: jest.fn(), ready: jest.fn() };

  beforeAll(async () => {
    const module = await Test.createTestingModule({ imports: [AppModule] })
      .overrideProvider(BackendClient).useValue(backend).compile();
    app = module.createNestApplication({ logger: false });
    configureApp(app);
    await app.init();
  });

  beforeEach(() => { backend.request.mockReset(); backend.request.mockResolvedValue(user); });
  afterAll(async () => { await app.close(); });

  it('rejects unauthenticated requests', async () => {
    await request(app.getHttpServer()).get('/api/expenses').expect(401);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('rejects a forged browser origin, including on demo login', async () => {
    await request(app.getHttpServer()).post('/api/auth/demo-login')
      .set('Origin', 'https://untrusted.example').send({ account_key: 'employee_a' }).expect(403);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('rejects role injection at login', async () => {
    await request(app.getHttpServer()).post('/api/auth/demo-login')
      .set('Origin', 'http://localhost:5173')
      .send({ account_key: 'employee_a', role: 'finance' }).expect(400);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('stores identity in an httpOnly session and clears it on logout', async () => {
    const agent = request.agent(app.getHttpServer());
    const response = await agent.post('/api/auth/demo-login')
      .set('Origin', 'http://localhost:5173').send({ account_key: 'employee_a' }).expect(200);
    expect(String(response.headers['set-cookie'])).toContain('HttpOnly');
    await agent.get('/api/me').expect(200);
    await agent.post('/api/auth/logout').set('Origin', 'http://localhost:5173').expect(204);
    await agent.get('/api/me').expect(401);
  });

  it('validates nested money and forbids authoritative fields before forwarding', async () => {
    const agent = request.agent(app.getHttpServer());
    await agent.post('/api/auth/demo-login').set('Origin', 'http://localhost:5173')
      .send({ account_key: 'employee_a' }).expect(200);
    backend.request.mockClear();
    const payload = {
      title: '报销', purpose: '出差',
      items: [{ category: 'meal', incurred_on: '2026-11-20', description: '餐饮', amount_fen: 1.5 }],
    };
    await agent.post('/api/expenses').set('Origin', 'http://localhost:5173')
      .set('Idempotency-Key', randomUUID()).send(payload).expect(400);
    await agent.post('/api/expenses').set('Origin', 'http://localhost:5173')
      .set('Idempotency-Key', randomUUID()).send({ ...payload, status: 'approved' }).expect(400);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('forwards the session identity and requires an idempotency key for writes', async () => {
    const agent = request.agent(app.getHttpServer());
    await agent.post('/api/auth/demo-login').set('Origin', 'http://localhost:5173')
      .send({ account_key: 'employee_a' }).expect(200);
    backend.request.mockClear();
    const payload = {
      title: '报销', purpose: '出差',
      items: [{ category: 'meal', incurred_on: '2026-11-20', description: '餐饮', amount_fen: 100 }],
    };
    await agent.post('/api/expenses').set('Origin', 'http://localhost:5173')
      .send(payload).expect(400);
    expect(backend.request).not.toHaveBeenCalled();
    const key = randomUUID();
    await agent.post('/api/expenses').set('Origin', 'http://localhost:5173')
      .set('Idempotency-Key', key).send(payload).expect(201);
    expect(backend.request).toHaveBeenCalledWith('/api/v1/expenses', expect.objectContaining({
      userId: user.id, idempotencyKey: key, method: 'POST',
    }));
  });
});
