import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Empty, Space, Statistic, Table, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { ExpensePage } from '../../api/types';
import { useAuth } from '../../app/auth';
import { ExpenseStatusTag } from '../../components/ExpenseStatus';
import { formatMoney } from '../../lib/money';

export function ExpensesPage() {
  const { user } = useAuth();
  const [data, setData] = useState<ExpensePage>({ items: [], limit: 20, offset: 0 });
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setBusy(true); setError(null);
    void api<ExpensePage>(`/expenses?limit=20&offset=${(page - 1) * 20}`, { signal: abort.signal })
      .then(setData).catch((err: unknown) => { if (!abort.signal.aborted) setError(describeError(err)); })
      .finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [page, refresh]);
  const isFinance = user?.role === 'finance';
  return <>
    <div className="page-heading"><div><span className="eyebrow">EXPENSE MANAGEMENT</span>
      <Typography.Title level={2}>{isFinance ? '财务工作台' : '我的报销'}</Typography.Title>
      <Typography.Text type="secondary">{isFinance ? '查看企业已提交的申请，核对费用明细。' : '记录每一笔费用，让报销进度一目了然。'}</Typography.Text>
    </div><Space><Button icon={<ReloadOutlined />} onClick={() => setRefresh((value) => value + 1)}>刷新</Button>
      {!isFinance && <Link to="/expenses/new"><Button type="primary" icon={<PlusOutlined />}>新建报销</Button></Link>}
    </Space></div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 20 }} />}
    <div className="summary-grid">
      <Card><Statistic title="本页申请" value={data.items.length} suffix="笔" /></Card>
      <Card><Statistic title="本页金额" value={formatMoney(data.items.reduce((sum, item) => sum + item.total_fen, 0))} /></Card>
      <Card><Statistic title="本页待审批" value={data.items.filter((item) => item.status === 'pending').length} suffix="笔" /></Card>
    </div>
    <Card className="table-card" title="报销申请">
      <Table rowKey="id" loading={busy} dataSource={data.items} pagination={false} scroll={{ x: 680 }}
        locale={{ emptyText: <Empty description="暂无报销申请" /> }}
        columns={[
          { title: '报销事由', dataIndex: 'title', render: (title: string, record) => <Link to={`/expenses/${record.id}`}>{title}</Link> },
          { title: '金额', dataIndex: 'total_fen', render: (value: number) => <strong>{formatMoney(value)}</strong> },
          { title: '状态', dataIndex: 'status', render: (status) => <ExpenseStatusTag status={status} /> },
          { title: '创建时间', dataIndex: 'created_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
          { title: '', key: 'action', render: (_, record) => <Link to={`/expenses/${record.id}`}>查看详情 →</Link> },
        ]} />
      <div className="pagination"><Button disabled={page === 1 || busy} onClick={() => setPage(page - 1)}>上一页</Button>
        <span>第 {page} 页</span><Button disabled={data.items.length < 20 || busy} onClick={() => setPage(page + 1)}>下一页</Button></div>
    </Card>
  </>;
}
