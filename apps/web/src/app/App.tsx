import { Spin } from 'antd';
import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { ExpenseDetailPage } from '../features/expenses/ExpenseDetailPage';
import { ExpensesPage } from '../features/expenses/ExpensesPage';
import { NewExpensePage } from '../features/expenses/NewExpensePage';
import { LoginPage } from '../features/auth/LoginPage';
import { AppLayout } from './AppLayout';
import { useAuth } from './auth';
import { AssistantPage } from '../features/agent/AssistantPage';
import { DocumentsPage } from '../features/knowledge/DocumentsPage';
import { DocumentPage } from '../features/knowledge/DocumentPage';

function Protected() {
  const { user } = useAuth();
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}

export function App() {
  const { loading } = useAuth();
  if (loading)
    return (
      <div className="loading-screen">
        <Spin size="large" tip="正在加载工作台">
          <div />
        </Spin>
      </div>
    );
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Protected />}>
        <Route element={<AppLayout />}>
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/knowledge/documents" element={<DocumentsPage />} />
          <Route path="/knowledge/documents/:id" element={<DocumentPage />} />
          <Route path="/expenses" element={<ExpensesPage />} />
          <Route path="/expenses/new" element={<NewExpensePage />} />
          <Route path="/expenses/:id" element={<ExpenseDetailPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/expenses" replace />} />
    </Routes>
  );
}
