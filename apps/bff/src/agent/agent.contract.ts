import { HttpException } from '@nestjs/common';
import { isUUID } from 'class-validator';
import type { components } from '../backend/backend.schema';

export type AgentRun = components['schemas']['RunResponse'];
export type AgentRunList = components['schemas']['RunListResponse'];
const states = new Set([
  'collecting',
  'awaiting_confirmation',
  'executing',
  'completed',
  'cancelled',
]);
const categories = new Set(['transport', 'lodging', 'meal', 'other']);
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const text = (value: unknown, limit: number): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= limit;
const uuid = (value: unknown): value is string => typeof value === 'string' && isUUID(value);

export function parseAgentRunList(value: unknown): AgentRunList {
  const valid =
    record(value) &&
    integer(value.total) &&
    integer(value.limit) &&
    value.limit >= 1 &&
    value.limit <= 100 &&
    integer(value.offset) &&
    Array.isArray(value.items) &&
    value.items.length <= value.limit &&
    value.items.length <= value.total &&
    value.items.every(
      (item: unknown) =>
        record(item) &&
        uuid(item.id) &&
        typeof item.status === 'string' &&
        states.has(item.status) &&
        text(item.title, 100) &&
        typeof item.created_at === 'string' &&
        Number.isFinite(Date.parse(item.created_at)),
    );
  if (!valid) throw new HttpException('助手服务返回了无效任务列表，请重试', 502);
  return value as AgentRunList;
}

/** Validate model-related transport at runtime; generated TS types alone cannot protect this boundary. */
export function parseAgentRun(value: unknown): AgentRun {
  let valid =
    record(value) &&
    uuid(value.id) &&
    integer(value.version) &&
    typeof value.status === 'string' &&
    states.has(value.status) &&
    typeof value.anchor_date === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value.anchor_date) &&
    text(value.timezone, 64) &&
    Array.isArray(value.messages) &&
    value.messages.length <= 40 &&
    value.messages.every(
      (m: unknown) =>
        record(m) &&
        uuid(m.id) &&
        (m.role === 'user' || m.role === 'assistant') &&
        text(m.content, 2000),
    ) &&
    Array.isArray(value.missing_fields) &&
    value.missing_fields.length <= 260 &&
    value.missing_fields.every((f: unknown) => text(f, 200)) &&
    (value.expense_id === null || uuid(value.expense_id));
  if (valid && record(value)) {
    const preview = value.preview;
    if (preview !== null) {
      valid =
        record(preview) &&
        uuid(preview.confirmation_id) &&
        integer(preview.version) &&
        typeof preview.content_hash === 'string' &&
        /^[a-f0-9]{64}$/.test(preview.content_hash) &&
        typeof preview.expires_at === 'string' &&
        Number.isFinite(Date.parse(preview.expires_at)) &&
        integer(preview.total_fen) &&
        preview.total_fen > 0 &&
        preview.total_fen <= 100_000_000 &&
        record(preview.payload);
      if (valid && record(preview) && record(preview.payload)) {
        const payload = preview.payload;
        valid =
          text(payload.title, 100) &&
          text(payload.purpose, 1000) &&
          Array.isArray(payload.items) &&
          payload.items.length > 0 &&
          payload.items.length <= 50 &&
          payload.items.every(
            (i: unknown) =>
              record(i) &&
              typeof i.category === 'string' &&
              categories.has(i.category) &&
              typeof i.incurred_on === 'string' &&
              /^\d{4}-\d{2}-\d{2}$/.test(i.incurred_on) &&
              text(i.description, 200) &&
              integer(i.amount_fen) &&
              i.amount_fen > 0 &&
              i.amount_fen <= 100_000_000,
          );
        if (valid && Array.isArray(payload.items)) {
          const total = payload.items.reduce(
            (sum: number, item: unknown) =>
              sum + (record(item) && typeof item.amount_fen === 'number' ? item.amount_fen : 0),
            0,
          );
          valid = total === preview.total_fen;
        }
      }
    }
    if (value.status === 'completed') valid = valid && uuid(value.expense_id);
    if (value.status === 'awaiting_confirmation' || value.status === 'executing')
      valid = valid && preview !== null;
  }
  if (!valid) throw new HttpException('助手服务返回了无效结果，请刷新任务状态', 502);
  return value as AgentRun;
}
