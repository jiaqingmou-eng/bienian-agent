import { Injectable } from '@nestjs/common';
import { BackendClient } from '../backend/backend.client';
import type { Expense, ExpensePage } from '../backend/backend.types';
import { CreateExpenseDto, ExpenseListQueryDto } from './expenses.dto';

@Injectable()
export class ExpensesService {
  constructor(private readonly backend: BackendClient) {}

  list(userId: string, query: ExpenseListQueryDto, requestId: string) {
    const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
    return this.backend.request<ExpensePage>(`/api/v1/expenses?${params}`, { userId, requestId });
  }

  detail(userId: string, expenseId: string, requestId: string) {
    return this.backend.request<Expense>(`/api/v1/expenses/${expenseId}`, { userId, requestId });
  }

  create(userId: string, dto: CreateExpenseDto, idempotencyKey: string, requestId: string) {
    return this.backend.request<Expense>('/api/v1/expenses', {
      userId, method: 'POST', body: dto, idempotencyKey, requestId,
    });
  }
}
