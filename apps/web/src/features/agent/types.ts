import type { CreateExpense } from '../../api/types';
import type { SearchDocuments } from '../knowledge/types';

/** 与 BFF 保持独立的对话协议；咨询结果不被误当成报销任务快照。 */
export interface ChatReply {
  kind: 'expense' | 'policy' | 'clarification';
  status:
    'expense' | 'answered' | 'insufficient' | 'conflict' | 'needs_clarification' | 'unsupported';
  message: string;
  as_of: string | null;
  sources: SearchDocuments['items'];
  run: AgentRun | null;
}

export interface Consultation {
  id: string;
  question: string;
  reply: ChatReply;
}

export interface AgentRunSummary {
  id: string;
  status: AgentRun['status'];
  title: string;
  created_at: string;
}

export interface AgentRunList {
  items: AgentRunSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** Browser contract is maintained locally; there are no cross-app source imports. */
export interface AgentRun {
  id: string;
  status: 'collecting' | 'awaiting_confirmation' | 'executing' | 'completed' | 'cancelled';
  version: number;
  anchor_date: string;
  timezone: string;
  messages: { id: string; role: 'user' | 'assistant'; content: string }[];
  missing_fields: string[];
  preview: {
    confirmation_id: string;
    version: number;
    content_hash: string;
    expires_at: string;
    payload: CreateExpense;
    total_fen: number;
  } | null;
  expense_id: string | null;
}
