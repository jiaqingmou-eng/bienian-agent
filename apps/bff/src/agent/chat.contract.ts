import { HttpException } from '@nestjs/common';
import type { components } from '../backend/backend.schema';
import { parseSearchDocuments } from '../knowledge/knowledge.contract';
import { parseAgentRun } from './agent.contract';

export type ChatReply = components['schemas']['ChatResponse'];

/** 运行时检查响应分支，避免只读答案携带意外任务对象或未校验来源；显式裁剪输出字段。 */
export function parseChatReply(value: unknown): ChatReply {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new HttpException('对话服务返回了无效响应', 502);
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.message !== 'string' ||
    !row.message.trim() ||
    row.message.length > 2000 ||
    !Array.isArray(row.sources)
  ) {
    throw new HttpException('对话服务返回了无效内容', 502);
  }
  if (
    row.kind === 'expense' &&
    row.status === 'expense' &&
    row.as_of === null &&
    row.sources.length === 0
  ) {
    return {
      kind: 'expense',
      status: 'expense',
      message: row.message,
      run: parseAgentRun(row.run),
      as_of: null,
      sources: [],
    };
  }
  if (
    row.kind === 'clarification' &&
    ['needs_clarification', 'unsupported'].includes(String(row.status)) &&
    row.run === null &&
    row.as_of === null &&
    row.sources.length === 0
  ) {
    return {
      kind: 'clarification',
      status: row.status as ChatReply['status'],
      message: row.message,
      run: null,
      as_of: null,
      sources: [],
    };
  }
  if (
    row.kind === 'policy' &&
    ['answered', 'insufficient', 'conflict', 'needs_clarification'].includes(String(row.status)) &&
    row.run === null
  ) {
    const checked = parseSearchDocuments({
      query: '制度问答',
      as_of: row.as_of,
      limit: 5,
      items: row.sources,
    });
    const hasSources = checked.items.length > 0;
    if ((row.status === 'answered') !== hasSources) {
      throw new HttpException('对话服务返回了无效引用状态', 502);
    }
    return {
      kind: 'policy',
      status: row.status as ChatReply['status'],
      message: row.message,
      as_of: checked.as_of,
      sources: checked.items,
      run: null,
    };
  }
  throw new HttpException('对话服务返回了未知响应分支', 502);
}
