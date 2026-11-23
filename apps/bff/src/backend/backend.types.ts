import type { components } from './backend.schema';

// BFF 使用 Python 后端导出的 OpenAPI 类型，避免重复手写跨服务协议。
export type User = components['schemas']['UserResponse'];
export type DemoAccount = components['schemas']['DemoAccountResponse'];
export type Expense = components['schemas']['ExpenseResponse'];
export type ExpensePage = components['schemas']['ExpenseListResponse'];
export type CreateExpense = components['schemas']['CreateExpenseRequest'];
