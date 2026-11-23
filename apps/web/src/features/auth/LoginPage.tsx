import { Alert, Button, Card, Radio, Space, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { DemoAccount } from '../../api/types';
import { useAuth } from '../../app/auth';

export function LoginPage() {
  const auth = useAuth();
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const [selected, setSelected] = useState('employee_a');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void api<DemoAccount[]>('/auth/demo-accounts', { signal: abort.signal })
      .then(setAccounts).catch((err: unknown) => { if (!abort.signal.aborted) setError(describeError(err)); });
    return () => abort.abort();
  }, []);
  if (auth.user) return <Navigate to="/expenses" replace />;
  return (
    <div className="login-page">
      <section className="login-intro">
        <span className="eyebrow">知账 · 企业财务工作台</span>
        <h1>每一笔支出，<br />都有清晰的来处。</h1>
        <p>整理费用、保存报销申请、跟踪处理进度。<br />让财务协作从一张清晰的申请单开始。</p>
        <div className="login-decoration">¥<span>清晰 · 有据 · 可追溯</span></div>
      </section>
      <Card className="login-card" bordered={false}>
        <Tag color="green">本地演示环境</Tag>
        <Typography.Title level={2}>进入工作台</Typography.Title>
        <Typography.Paragraph type="secondary">选择预置账号，体验员工与财务视角。所有业务数据均为虚构。</Typography.Paragraph>
        {(error || auth.error) && <Alert type="error" showIcon message={error || auth.error} style={{ marginBottom: 20 }} />}
        <Radio.Group value={selected} onChange={(e) => setSelected(String(e.target.value))} className="account-options">
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {accounts.map((account) => <Radio key={account.key} value={account.key} className="account-option">
              {account.name}<span className="account-role">{account.role === 'finance' ? '财务' : '员工'}</span>
            </Radio>)}
          </Space>
        </Radio.Group>
        <Button block type="primary" size="large" loading={busy} disabled={accounts.length === 0}
          onClick={() => {
            setBusy(true); setError(null);
            void auth.login(selected).catch((err: unknown) => setError(describeError(err))).finally(() => setBusy(false));
          }}>进入工作台</Button>
      </Card>
    </div>
  );
}
