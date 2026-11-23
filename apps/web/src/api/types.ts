/** Browser-facing API shapes. The web application has no imports from BFF source. */
export interface User {
  id: string;
  tenant_id: string;
  name: string;
  role: 'employee' | 'finance';
}

export interface DemoAccount { key: string; name: string; role: User['role'] }
export type Category = 'transport' | 'lodging' | 'meal' | 'other';
export type ExpenseStatus = 'draft' | 'pending' | 'approved' | 'rejected';
export interface ExpenseItem {
  category: Category;
  incurred_on: string;
  description: string;
  amount_fen: number;
}
export interface CreateExpense { title: string; purpose: string; items: ExpenseItem[] }
export interface Expense extends CreateExpense {
  id: string;
  applicant_id: string;
  currency: 'CNY';
  status: ExpenseStatus;
  total_fen: number;
  version: number;
  created_at: string;
}
export interface ExpensePage { items: Expense[]; limit: number; offset: number }
