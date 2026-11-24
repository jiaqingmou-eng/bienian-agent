import { Alert, Button, Card, Descriptions, Skeleton, Tabs, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import type { DocumentDetail } from './types';

/** 原文与整理结果可相互核对；统一按纯文本渲染，禁止执行文档中的 HTML / 脚本。 */
export function DocumentPage() {
  const { id } = useParams();
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setDocument(null);
    setError(null);
    void api<DocumentDetail>(`/knowledge/documents/${encodeURIComponent(id ?? '')}`, {
      signal: abort.signal,
    })
      .then((value) => {
        if (!abort.signal.aborted) setDocument(value);
      })
      .catch((err: unknown) => {
        if (!abort.signal.aborted) setError(describeError(err));
      });
    return () => abort.abort();
  }, [id, refresh]);
  // 详情路由切换后的首帧也不展示上一个版本，避免短暂混淆原文依据。
  const current = document?.id === id ? document : null;
  return (
    <>
      <div className="page-heading">
        <Typography.Title level={2}>制度原文</Typography.Title>
        <Link to="/knowledge/documents">
          <Button>返回制度列表</Button>
        </Link>
      </div>
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          action={<Button onClick={() => setRefresh((value) => value + 1)}>重试</Button>}
        />
      ) : !current ? (
        <Card>
          <Skeleton active />
        </Card>
      ) : (
        <>
          <Card title={current.title} className="form-section">
            <Descriptions
              column={{ xs: 1, md: 2 }}
              items={[
                { key: 'version', label: '版本', children: `v${current.version}` },
                { key: 'owner', label: '责任人', children: current.owner_name },
                { key: 'from', label: '适用开始日期', children: current.effective_from },
                {
                  key: 'until',
                  label: '适用结束日期',
                  children: current.effective_until ?? '未设定',
                },
                {
                  key: 'scope',
                  label: '阅读范围',
                  children: current.visibility === 'tenant' ? '本企业' : '仅责任人',
                },
                { key: 'status', label: '发布状态', children: '已发布' },
              ]}
            />
          </Card>
          <Card>
            <Tabs
              items={[
                {
                  key: 'original',
                  label: '原始正文',
                  children: (
                    <pre
                      style={{
                        whiteSpace: 'pre-wrap',
                        overflowWrap: 'anywhere',
                        fontFamily: 'inherit',
                      }}
                    >
                      {current.original_text}
                    </pre>
                  ),
                },
                {
                  key: 'normalized',
                  label: '整理后的章节',
                  children: current.normalized_document.blocks.map((block) => (
                    <section key={block.id} id={block.id} style={{ marginBottom: 20 }}>
                      <Typography.Text type="secondary">
                        原文第 {block.source_start_line}–{block.source_end_line} 行
                      </Typography.Text>
                      {block.kind === 'heading' ? (
                        <Typography.Title level={4}>{block.text}</Typography.Title>
                      ) : (
                        <pre
                          style={{
                            whiteSpace: 'pre-wrap',
                            overflowWrap: 'anywhere',
                            fontFamily: 'inherit',
                          }}
                        >
                          {block.text}
                        </pre>
                      )}
                    </section>
                  )),
                },
              ]}
            />
          </Card>
        </>
      )}
    </>
  );
}
