import { Alert, Button, Card, Descriptions, Skeleton, Table, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { Category, Expense } from '../../api/types';
import { ExpenseStatusTag } from '../../components/ExpenseStatus';
import { formatMoney } from '../../lib/money';

const categories: Record<Category, string> = { transport: '交通', lodging: '住宿', meal: '餐饮', other: '其他' };

export function ExpenseDetailPage() {
  const { id } = useParams();
  const [expense, setExpense] = useState<Expense | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setExpense(null); setError(null);
    void api<Expense>(`/expenses/${encodeURIComponent(id ?? '')}`, { signal: abort.signal })
      .then(setExpense).catch((err: unknown) => { if (!abort.signal.aborted) setError(describeError(err)); });
    return () => abort.abort();
  }, [id]);
  return <>
    <div className="page-heading"><div><span className="eyebrow">EXPENSE DETAILS</span>
      <Typography.Title level={2}>报销申请详情</Typography.Title></div>
      <Link to="/expenses"><Button>返回列表</Button></Link></div>
    {error ? <Alert type="error" message={error} showIcon /> : !expense ? <Card><Skeleton active /></Card> : <>
      <Card className="form-section" title={expense.title} extra={<ExpenseStatusTag status={expense.status} />}>
        <Descriptions column={{ xs: 1, md: 2 }} items={[
          { key: 'purpose', label: '业务事由', children: expense.purpose, span: 2 },
          { key: 'id', label: '申请编号', children: <Typography.Text copyable>{expense.id}</Typography.Text>, span: 2 },
          { key: 'created', label: '创建时间', children: new Date(expense.created_at).toLocaleString('zh-CN') },
          { key: 'version', label: '申请版本', children: `v${expense.version}` },
          { key: 'total', label: '总金额', children: <strong className="total-amount">{formatMoney(expense.total_fen)}</strong> },
        ]} />
      </Card>
      <Card title="费用明细"><Table pagination={false} rowKey={(_, index) => String(index)} dataSource={expense.items}
        scroll={{ x: 550 }} columns={[
          { title: '类别', dataIndex: 'category', render: (value: Category) => categories[value] },
          { title: '日期', dataIndex: 'incurred_on' },
          { title: '说明', dataIndex: 'description' },
          { title: '金额', dataIndex: 'amount_fen', render: (value: number) => formatMoney(value), align: 'right' },
        ]} /></Card>
    </>}
  </>;
}
