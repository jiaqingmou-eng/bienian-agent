import 'reflect-metadata';
import { HttpException, INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { randomUUID } from 'node:crypto';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import { BackendClient } from '../src/backend/backend.client';
import { configureApp } from '../src/bootstrap';

const user = {
  id: '20000000-0000-4000-8000-000000000001',
  tenant_id: '10000000-0000-4000-8000-000000000001',
  name: '员工',
  role: 'employee',
};
const document = {
  id: randomUUID(),
  source_key: 'demo-policy',
  title: '虚构制度',
  version: 1,
  owner_name: '财务',
  format: 'text',
  effective_from: '2026-01-01',
  effective_until: null,
  visibility: 'tenant',
  status: 'published',
  content_hash: 'a'.repeat(64),
  original_text: '<script>alert(1)</script>\n不得自批。',
  normalized_document: {
    schema_version: 'policy-text-v1',
    blocks: [
      {
        id: 'line-1',
        kind: 'paragraph',
        text: '<script>alert(1)</script>\n不得自批。',
        heading_path: [],
        source_start_line: 1,
        source_end_line: 2,
      },
    ],
  },
};

/** 旧版已过适用期仍可核对原文，引用读取不能沿用检索时的当天日期过滤。 */
const citation = {
  document: { ...document, effective_until: '2026-06-30' },
  content_hash: document.content_hash,
  citation_id: 'd'.repeat(64),
  rule_version: 'policy-chunks-v1',
  text: '第一条 成都住宿不超过400.00元。\n超标部分不能自动报销。',
  heading_path: ['虚构制度'],
  source_start_line: 3,
  source_end_line: 4,
};

describe('制度只读 BFF 边界', () => {
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
  });
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
    backend.request.mockClear();
    return session;
  }

  it('检索只转发可信身份、问题和日期，拒绝伪造权限及来源', async () => {
    await request(app.getHttpServer())
      .post('/api/knowledge/search')
      .set('Origin', 'http://localhost:5173')
      .send({ query: '成都住宿' })
      .expect(401);
    const session = await login();
    const body = { query: '成都住宿', as_of: '2026-11-24', limit: 5 };
    const response = {
      ...body,
      items: [
        {
          document,
          citation_id: 'b'.repeat(64),
          matched_chunk_id: 'c'.repeat(64),
          rule_version: 'policy-chunks-v1',
          text: '成都住宿不超过 400.00 元。',
          heading_path: ['住宿'],
          source_start_line: 3,
          source_end_line: 3,
        },
      ],
    };
    backend.request.mockResolvedValue(response);
    const result = await session
      .post('/api/knowledge/search')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(200);
    expect(result.body.items[0].text).toContain('400.00');
    expect(result.body.items[0].document.original_text).toBeUndefined();
    expect(backend.request).toHaveBeenCalledWith(
      '/api/v1/knowledge/search',
      expect.objectContaining({ method: 'POST', userId: user.id, body }),
    );
    backend.request.mockClear();
    for (const changed of [
      { tenant_id: user.tenant_id },
      { as_of: '2026-02-30' },
      { limit: true },
      { status: 'draft' },
    ]) {
      await session
        .post('/api/knowledge/search')
        .set('Origin', 'http://localhost:5173')
        .send({ ...body, ...changed })
        .expect(400);
    }
    await session
      .post('/api/knowledge/search')
      .set('Origin', 'https://untrusted.example')
      .send(body)
      .expect(403);
    expect(backend.request).not.toHaveBeenCalled();
    backend.request.mockResolvedValue({
      ...response,
      items: [{ ...response.items[0], citation_id: 'forged' }],
    });
    await session
      .post('/api/knowledge/search')
      .set('Origin', 'http://localhost:5173')
      .send(body)
      .expect(502);
  });

  it('需要登录，且只转发会话身份与有效分页', async () => {
    await request(app.getHttpServer()).get('/api/knowledge/documents').expect(401);
    const session = await login();
    const item: Record<string, unknown> = { ...document };
    delete item.original_text;
    delete item.normalized_document;
    delete item.content_hash;
    backend.request.mockResolvedValue({
      items: [{ ...item, tenant_id: 'internal-only' }],
      total: 1,
      limit: 5,
      offset: 0,
    });
    const response = await session.get('/api/knowledge/documents?limit=5&offset=0').expect(200);
    expect(response.body.items[0]).toEqual(item);
    expect(backend.request).toHaveBeenCalledWith(
      '/api/v1/knowledge/documents?limit=5&offset=0',
      expect.objectContaining({ userId: user.id }),
    );
    backend.request.mockClear();
    for (const query of ['tenant_id=x', 'owner_id=x', 'status=draft', 'limit=51', 'offset=-1']) {
      await session.get(`/api/knowledge/documents?${query}`).expect(400);
    }
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('只读详情绑定 UUID，原文作为数据返回，不开放写入口', async () => {
    const session = await login();
    backend.request.mockResolvedValue({ ...document, tenant_id: 'internal-only' });
    const result = await session.get(`/api/knowledge/documents/${document.id}`).expect(200);
    expect(result.body).toEqual(document);
    expect(result.body.original_text).toContain('<script>');
    expect(backend.request).toHaveBeenCalledWith(
      `/api/v1/knowledge/documents/${document.id}`,
      expect.objectContaining({ userId: user.id }),
    );
    backend.request.mockClear();
    await session.get('/api/knowledge/documents/not-a-uuid').expect(400);
    await session
      .post('/api/knowledge/documents')
      .set('Origin', 'http://localhost:5173')
      .send({})
      .expect(404);
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('拒绝未发布响应及超出原文的行映射', async () => {
    const session = await login();
    backend.request.mockResolvedValue({ ...document, status: 'draft' });
    await session.get(`/api/knowledge/documents/${document.id}`).expect(502);
    backend.request.mockResolvedValue({
      ...document,
      normalized_document: {
        ...document.normalized_document,
        blocks: [{ ...document.normalized_document.blocks[0], source_end_line: 999 }],
      },
    });
    await session.get(`/api/knowledge/documents/${document.id}`).expect(502);
    backend.request.mockResolvedValue({ items: [document], total: 0, limit: 20, offset: 0 });
    await session.get('/api/knowledge/documents').expect(502);
  });

  it('引用读取需要登录，只转发固定路径及可信身份并裁剪内部字段', async () => {
    const path = `/api/knowledge/documents/${document.id}/citations/${citation.citation_id}`;
    await request(app.getHttpServer()).get(path).expect(401);
    expect(backend.request).not.toHaveBeenCalled();
    const session = await login();
    backend.request.mockResolvedValue({
      ...citation,
      document: { ...citation.document, tenant_id: 'must-not-leak' },
      internal: 'must-not-leak',
    });
    const result = await session.get(path).expect(200);
    expect(result.body.text).toEqual(citation.text);
    expect(result.body.document.effective_until).toBe('2026-06-30');
    expect(result.body.document.tenant_id).toBeUndefined();
    expect(result.body.document.original_text).toBeUndefined();
    expect(result.body.internal).toBeUndefined();
    expect(backend.request).toHaveBeenCalledWith(
      `/api/v1/knowledge/documents/${document.id}/citations/${citation.citation_id}`,
      expect.objectContaining({ userId: user.id, requestId: expect.any(String) }),
    );
    expect(backend.request).toHaveBeenCalledTimes(1);
  });

  it('引用路径和查询参数在转发前校验，不接受客户端指定身份 / 行号 / 版本', async () => {
    const session = await login();
    const path = `/api/knowledge/documents/${document.id}/citations/${citation.citation_id}`;
    for (const target of [
      `/api/knowledge/documents/not-a-uuid/citations/${citation.citation_id}`,
      `/api/knowledge/documents/${document.id}/citations/invalid`,
      `${path}?tenant_id=forged`,
      `${path}?source_start_line=1`,
      `${path}?version=2`,
    ]) {
      await session.get(target).expect(400);
    }
    expect(backend.request).not.toHaveBeenCalled();
  });

  it('引用详情拒绝目标错位、错误哈希、无效范围或损坏正文', async () => {
    const session = await login();
    const path = `/api/knowledge/documents/${document.id}/citations/${citation.citation_id}`;
    for (const changed of [
      { document: { ...citation.document, id: randomUUID() } },
      { document: { ...citation.document, status: 'draft' } },
      { citation_id: 'e'.repeat(64) },
      { content_hash: 'invalid' },
      { rule_version: 'unknown-rule' },
      { source_start_line: 0 },
      { source_end_line: 2 },
      { source_end_line: 5 },
      { text: undefined },
    ]) {
      backend.request.mockResolvedValue({ ...citation, ...changed });
      await session.get(path).expect(502);
    }
  });

  it('不可访问和索引异常保留后端错误语义，不重试或换来源', async () => {
    const session = await login();
    const path = `/api/knowledge/documents/${document.id}/citations/${citation.citation_id}`;
    for (const status of [404, 503]) {
      backend.request.mockClear();
      backend.request.mockRejectedValue(new HttpException('引用读取失败', status));
      await session.get(path).expect(status);
      expect(backend.request).toHaveBeenCalledTimes(1);
    }
  });
});
