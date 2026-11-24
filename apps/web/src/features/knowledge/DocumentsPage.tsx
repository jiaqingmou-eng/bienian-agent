import { Alert, Button, Card, Empty, Table, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { DocumentList } from './types';
import { DocumentSearch } from './DocumentSearch';

/** 最小制度目录：浏览器不自行推断权限，只展示服务端返回的已发布版本。 */
export function DocumentsPage() {
  const [data, setData] = useState<DocumentList>({ items: [], total: 0, limit: 20, offset: 0 });
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setBusy(true);
    setError(null);
    void api<DocumentList>(`/knowledge/documents?limit=20&offset=${(page - 1) * 20}`, {
      signal: abort.signal,
    })
      .then((value) => {
        if (!abort.signal.aborted) setData(value);
      })
      .catch((err: unknown) => {
        if (!abort.signal.aborted) setError(describeError(err));
      })
      .finally(() => {
        if (!abort.signal.aborted) setBusy(false);
      });
    // 切页、离开页面或身份变化后，旧请求不能再更新当前视图。
    return () => abort.abort();
  }, [page, refresh]);
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>报销制度</Typography.Title>
          <Typography.Text type="secondary">
            查阅制度原文，使用前请核对版本与适用日期。
          </Typography.Text>
        </div>
        <Button onClick={() => setRefresh((value) => value + 1)}>刷新</Button>
      </div>
      <DocumentSearch />
      {error ? (
        <Alert type="error" showIcon message={error} />
      ) : (
        <Card>
          <Table
            rowKey="id"
            loading={busy}
            dataSource={data.items}
            scroll={{ x: 650 }}
            locale={{ emptyText: <Empty description="暂无可查阅的已发布制度" /> }}
            pagination={{
              current: page,
              pageSize: 20,
              total: data.total,
              showSizeChanger: false,
              onChange: setPage,
            }}
            columns={[
              {
                title: '制度名称',
                dataIndex: 'title',
                render: (title: string, row) => (
                  <Link to={`/knowledge/documents/${row.id}`}>{title}</Link>
                ),
              },
              { title: '版本', dataIndex: 'version', render: (version: number) => `v${version}` },
              { title: '适用开始日期', dataIndex: 'effective_from' },
              {
                title: '适用结束日期',
                dataIndex: 'effective_until',
                render: (value: string | null) => value ?? '未设定',
              },
              { title: '责任人', dataIndex: 'owner_name' },
            ]}
          />
        </Card>
      )}
    </>
  );
}
