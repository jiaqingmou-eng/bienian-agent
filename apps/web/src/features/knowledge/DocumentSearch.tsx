import { Alert, Button, Card, Empty, Form, Input, Space, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { SearchDocuments } from './types';

/** 只提交一次条款检索，不修改助手任务；新查询及页面离开时取消旧请求以防结果串题。 */
export function DocumentSearch() {
  const [result, setResult] = useState<SearchDocuments | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function search(values: { query: string; as_of?: string }) {
    active.current?.abort();
    const abort = new AbortController();
    active.current = abort;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const response = await api<SearchDocuments>('/knowledge/search', {
        method: 'POST',
        signal: abort.signal,
        body: JSON.stringify({
          query: values.query.trim(),
          ...(values.as_of ? { as_of: values.as_of } : {}),
          limit: 5,
        }),
      });
      if (!abort.signal.aborted) setResult(response);
    } catch (err: unknown) {
      if (!abort.signal.aborted) setError(describeError(err));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  return (
    <Card title="查找制度条款" className="form-section">
      <Form
        layout="vertical"
        onFinish={(values: { query: string; as_of?: string }) => {
          void search(values);
        }}
      >
        <Form.Item
          name="query"
          label="问题或关键词"
          rules={[
            { required: true, whitespace: true, message: '请输入问题或关键词' },
            { max: 300 },
          ]}
        >
          <Input maxLength={300} placeholder="例如：成都住宿标准、高铁二等座" />
        </Form.Item>
        <Space align="start" wrap>
          <Form.Item name="as_of" label="适用日期（可选）">
            <Input type="date" />
          </Form.Item>
          <Form.Item label=" ">
            <Button htmlType="submit" type="primary" loading={busy}>
              查找条款
            </Button>
          </Form.Item>
        </Space>
      </Form>
      {error && <Alert type="error" showIcon message={error} />}
      {result && (
        <>
          <Typography.Paragraph type="secondary">
            问题：{result.query}；适用日期：{result.as_of}；以下为相关制度原文。
          </Typography.Paragraph>
          {result.items.length === 0 ? (
            <Empty description="未找到匹配的可用条款，请换用更具体的关键词或核对适用日期。这不代表制度没有相关规定。" />
          ) : (
            result.items.map((hit) => (
              <Card
                key={hit.citation_id}
                size="small"
                style={{ marginBottom: 12 }}
                title={
                  <Link to={`/knowledge/documents/${hit.document.id}`}>
                    {hit.document.title} · v{hit.document.version}
                  </Link>
                }
              >
                <Typography.Paragraph type="secondary">
                  {hit.heading_path.join(' / ')} · 原文第 {hit.source_start_line}–
                  {hit.source_end_line} 行
                </Typography.Paragraph>
                <Typography.Paragraph type="secondary">
                  适用：{hit.document.effective_from} 至{' '}
                  {hit.document.effective_until ?? '未设定结束日期'}
                </Typography.Paragraph>
                <pre
                  style={{
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                    fontFamily: 'inherit',
                  }}
                >
                  {hit.text}
                </pre>
              </Card>
            ))
          )}
        </>
      )}
    </Card>
  );
}
