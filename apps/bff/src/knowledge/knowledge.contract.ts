import { HttpException } from '@nestjs/common';
import { isUUID } from 'class-validator';
import type { components } from '../backend/backend.schema';

export type DocumentDetail = components['schemas']['DocumentDetail'];
export type DocumentList = components['schemas']['DocumentListResponse'];
export type SearchDocuments = components['schemas']['SearchDocumentsResponse'];
export type CitationDetail = components['schemas']['CitationDetail'];
type Summary = components['schemas']['DocumentSummary'];
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max;
const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const date = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(Date.parse(value));

/** 生成类型不做运行时检查；BFF 只向浏览器转发经过校验的已发布元数据。 */
function summary(value: unknown): value is Summary & Record<string, unknown> {
  return (
    record(value) &&
    typeof value.id === 'string' &&
    isUUID(value.id) &&
    text(value.source_key, 80) &&
    text(value.title, 200) &&
    text(value.owner_name, 100) &&
    integer(value.version) &&
    value.version >= 1 &&
    (value.format === 'markdown' || value.format === 'text') &&
    date(value.effective_from) &&
    (value.effective_until === null ||
      (date(value.effective_until) && value.effective_until >= value.effective_from)) &&
    (value.visibility === 'tenant' || value.visibility === 'owner') &&
    value.status === 'published'
  );
}

/** 列表与详情使用相同的公开字段集合，上游新增内部字段时不自动扩大浏览器协议。 */
function publicSummary(value: Summary): Summary {
  return {
    id: value.id,
    source_key: value.source_key,
    title: value.title,
    version: value.version,
    owner_name: value.owner_name,
    format: value.format,
    effective_from: value.effective_from,
    effective_until: value.effective_until,
    visibility: value.visibility,
    status: value.status,
  };
}

export function parseDocumentList(value: unknown): DocumentList {
  if (
    !record(value) ||
    !integer(value.total) ||
    !integer(value.limit) ||
    value.limit < 1 ||
    value.limit > 50 ||
    !integer(value.offset) ||
    !Array.isArray(value.items) ||
    value.items.length > value.limit ||
    value.items.length > value.total ||
    !value.items.every(summary)
  ) {
    throw new HttpException('制度服务返回了无效列表', 502);
  }
  return {
    items: (value.items as Summary[]).map(publicSummary),
    total: value.total,
    limit: value.limit,
    offset: value.offset,
  };
}

/** 校验原文和行映射，再裁剪响应；文档正文只作为数据，不解释其中的 HTML 或指令。 */
export function parseDocumentDetail(value: unknown): DocumentDetail {
  if (
    !summary(value) ||
    !record(value) ||
    !text(value.original_text, 131072) ||
    typeof value.content_hash !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.content_hash) ||
    !record(value.normalized_document) ||
    value.normalized_document.schema_version !== 'policy-text-v1' ||
    !Array.isArray(value.normalized_document.blocks)
  ) {
    throw new HttpException('制度服务返回了无效原文', 502);
  }
  const blocks = value.normalized_document.blocks as unknown[];
  const lineCount = value.original_text.replace(/\r\n?/g, '\n').split('\n').length;
  let previousEnd = 0;
  for (const block of blocks) {
    if (
      !record(block) ||
      !text(block.id, 100) ||
      !text(block.text, 131072) ||
      !['heading', 'paragraph', 'list', 'table', 'code'].includes(String(block.kind)) ||
      !Array.isArray(block.heading_path) ||
      !block.heading_path.every((item: unknown) => text(item, 131072)) ||
      !integer(block.source_start_line) ||
      block.source_start_line <= previousEnd ||
      !integer(block.source_end_line) ||
      block.source_end_line < block.source_start_line ||
      block.source_end_line > lineCount
    ) {
      throw new HttpException('制度服务返回了无效章节结构', 502);
    }
    previousEnd = block.source_end_line;
  }
  if (blocks.length < 1 || blocks.length > 1000) throw new HttpException('制度章节数量无效', 502);
  // 显式保留协议字段，避免上游今后增加管理字段时被无意转发。
  return {
    ...publicSummary(value),
    original_text: value.original_text,
    content_hash: value.content_hash,
    normalized_document: value.normalized_document as DocumentDetail['normalized_document'],
  };
}

/** 条款正文与来源同时校验；引用固定到版本，不能把模型或上游的任意对象当作证据。 */
export function parseSearchDocuments(value: unknown): SearchDocuments {
  if (
    !record(value) ||
    !text(value.query, 300) ||
    !date(value.as_of) ||
    !integer(value.limit) ||
    value.limit < 1 ||
    value.limit > 10 ||
    !Array.isArray(value.items) ||
    value.items.length > value.limit
  ) {
    throw new HttpException('制度服务返回了无效检索结果', 502);
  }
  const items: SearchDocuments['items'] = [];
  const citations = new Set<string>();
  for (const hit of value.items as unknown[]) {
    if (
      !record(hit) ||
      !summary(hit.document) ||
      !text(hit.citation_id, 64) ||
      !/^[a-f0-9]{64}$/.test(hit.citation_id) ||
      !text(hit.matched_chunk_id, 64) ||
      !/^[a-f0-9]{64}$/.test(hit.matched_chunk_id) ||
      hit.rule_version !== 'policy-chunks-v1' ||
      !text(hit.text, 131072) ||
      !Array.isArray(hit.heading_path) ||
      !hit.heading_path.every((item: unknown) => text(item, 131072)) ||
      !integer(hit.source_start_line) ||
      hit.source_start_line < 1 ||
      !integer(hit.source_end_line) ||
      hit.source_end_line < hit.source_start_line ||
      hit.document.effective_from > value.as_of ||
      (hit.document.effective_until !== null && hit.document.effective_until < value.as_of) ||
      citations.has(hit.citation_id)
    ) {
      throw new HttpException('制度服务返回了无效条款引用', 502);
    }
    citations.add(hit.citation_id);
    items.push({
      document: publicSummary(hit.document),
      citation_id: hit.citation_id,
      matched_chunk_id: hit.matched_chunk_id,
      rule_version: hit.rule_version,
      text: hit.text,
      heading_path: hit.heading_path as string[],
      source_start_line: hit.source_start_line,
      source_end_line: hit.source_end_line,
    });
  }
  return { query: value.query, as_of: value.as_of, limit: value.limit, items };
}

/** 引用详情不按今天的日期筛掉历史版本；验证目标、版本元数据及一基闭区间后显式裁剪。
 * content_hash用于后续原文页比对同一份内容，不接受以模型输出或客户端行号替代它。
 */
export function parseCitationDetail(
  value: unknown,
  documentId: string,
  citationId: string,
): CitationDetail {
  if (
    !record(value) ||
    !summary(value.document) ||
    value.document.id.toLowerCase() !== documentId.toLowerCase() ||
    typeof value.content_hash !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.content_hash) ||
    typeof value.citation_id !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.citation_id) ||
    value.citation_id !== citationId ||
    value.rule_version !== 'policy-chunks-v1' ||
    !text(value.text, 131072) ||
    !Array.isArray(value.heading_path) ||
    !value.heading_path.every((item: unknown) => text(item, 131072)) ||
    !integer(value.source_start_line) ||
    value.source_start_line < 1 ||
    !integer(value.source_end_line) ||
    value.source_end_line < value.source_start_line ||
    value.text.split('\n').length !== value.source_end_line - value.source_start_line + 1
  ) {
    throw new HttpException('制度服务返回了无效引用详情', 502);
  }
  return {
    document: publicSummary(value.document),
    content_hash: value.content_hash,
    citation_id: value.citation_id,
    rule_version: value.rule_version,
    text: value.text,
    heading_path: value.heading_path as string[],
    source_start_line: value.source_start_line,
    source_end_line: value.source_end_line,
  };
}
