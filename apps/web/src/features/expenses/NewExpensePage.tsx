import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Select, Space, Typography } from 'antd';
import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { Category, CreateExpense, Expense } from '../../api/types';
import { yuanToFen } from '../../lib/money';

interface ExpenseForm {
  title: string;
  purpose: string;
  items: { category: Category; incurred_on: string; description: string; amount_yuan: string }[];
}

function localDate(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

export function NewExpensePage() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retry = useRef<{ payload: string; key: string } | null>(null);

  async function save(values: ExpenseForm) {
    setError(null);
    setBusy(true);
    try {
      const payload: CreateExpense = {
        title: values.title.trim(), purpose: values.purpose.trim(),
        items: values.items.map(({ amount_yuan, ...item }) => ({
          ...item, description: item.description.trim(), amount_fen: yuanToFen(amount_yuan),
        })),
      };
      const serialized = JSON.stringify(payload);
      // Preserve the key on network retries; changing the payload starts a new operation.
      const operation = retry.current && retry.current.payload === serialized
        ? retry.current : { payload: serialized, key: crypto.randomUUID() };
      retry.current = operation;
      const result = await api<Expense>('/expenses', {
        method: 'POST', body: serialized, headers: { 'Idempotency-Key': operation.key },
      });
      navigate(`/expenses/${result.id}`, { replace: true });
    } catch (err) {
      setError(describeError(err));
    } finally { setBusy(false); }
  }

  return <>
    <div className="page-heading"><div><span className="eyebrow">NEW EXPENSE</span>
      <Typography.Title level={2}>新建报销</Typography.Title>
      <Typography.Text type="secondary">填写事由与费用明细，先保存为草稿。</Typography.Text>
    </div><Link to="/expenses"><Button>返回列表</Button></Link></div>
    {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 20 }} />}
    <Form<ExpenseForm> layout="vertical" onFinish={(values) => { void save(values); }}
      initialValues={{ items: [{ category: 'transport', incurred_on: localDate(), amount_yuan: '', description: '' }] }}>
      <Card title="申请信息" className="form-section">
        <Form.Item name="title" label="报销标题" rules={[{ required: true, whitespace: true, max: 100 }]}>
          <Input placeholder="例如：上海客户拜访差旅报销" maxLength={100} />
        </Form.Item>
        <Form.Item name="purpose" label="业务事由" rules={[{ required: true, whitespace: true, max: 1000 }]}>
          <Input.TextArea placeholder="说明出差或费用产生的业务背景" rows={3} maxLength={1000} showCount />
        </Form.Item>
      </Card>
      <Card title="费用明细" className="form-section">
        <Form.List name="items" rules={[{ validator: async (_, items: unknown[]) => {
          if (!items || items.length < 1 || items.length > 50) throw new Error('请填写1～50条费用明细');
        } }]}>
          {(fields, { add, remove }, { errors }) => <>
            {fields.map((field, index) => <div className="expense-item" key={field.key}>
              <div className="item-heading"><strong>费用 {index + 1}</strong>
                <Button type="text" danger icon={<DeleteOutlined />} aria-label={`删除费用${index + 1}`}
                  disabled={fields.length === 1 || busy} onClick={() => remove(field.name)} /></div>
              <div className="item-grid">
                <Form.Item name={[field.name, 'category']} label="费用类别" rules={[{ required: true }]}>
                  <Select options={[
                    { value: 'transport', label: '交通' }, { value: 'lodging', label: '住宿' },
                    { value: 'meal', label: '餐饮' }, { value: 'other', label: '其他' },
                  ]} />
                </Form.Item>
                <Form.Item name={[field.name, 'incurred_on']} label="费用日期" rules={[{ required: true }]}>
                  <Input type="date" />
                </Form.Item>
                <Form.Item name={[field.name, 'amount_yuan']} label="金额（元）" rules={[
                  { required: true, message: '请填写金额' },
                  { validator: async (_, value: unknown) => {
                    if (typeof value !== 'string') throw new Error('请填写金额');
                    yuanToFen(value);
                  } },
                ]}>
                  <Input inputMode="decimal" prefix="¥" placeholder="0.00" maxLength={16} />
                </Form.Item>
              </div>
              <Form.Item name={[field.name, 'description']} label="费用说明" rules={[{ required: true, whitespace: true, max: 200 }]}>
                <Input placeholder="例如：上海往返高铁" maxLength={200} />
              </Form.Item>
            </div>)}
            <Form.ErrorList errors={errors} />
            <Button type="dashed" block icon={<PlusOutlined />} disabled={fields.length >= 50 || busy}
              onClick={() => add({ category: 'transport', incurred_on: localDate(), description: '', amount_yuan: '' })}>添加费用</Button>
          </>}
        </Form.List>
      </Card>
      <div className="form-actions"><Typography.Text type="secondary">保存后可在申请详情中核对总金额。</Typography.Text>
        <Space><Link to="/expenses"><Button disabled={busy}>取消</Button></Link>
          <Button type="primary" htmlType="submit" loading={busy}>保存草稿</Button></Space></div>
    </Form>
  </>;
}
