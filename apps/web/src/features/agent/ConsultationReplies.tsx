import { Alert, Card, Typography } from 'antd';
import { Link } from 'react-router-dom';
import type { Consultation } from './types';

/** 单轮咨询独立展示，不追加进服务端报销消息；来源由后端校验后补齐，纯文本渲染。 */
export function ConsultationReplies({ items }: { items: Consultation[] }) {
  if (!items.length) return null;
  return (
    <Card title="本页制度咨询" style={{ marginBottom: 16 }}>
      <Typography.Paragraph type="secondary">
        咨询不修改报销预览；本页仅保留最近10次独立问答，刷新或离开后不恢复。补充条件请重新发送完整问题。
      </Typography.Paragraph>
      {items.map(({ id, question, reply }) => (
        <section key={id} style={{ marginBottom: 20 }}>
          <Typography.Paragraph>
            <strong>你：</strong>
            {question}
          </Typography.Paragraph>
          <Alert
            type={reply.status === 'answered' ? 'success' : 'info'}
            message={reply.message}
            style={{ whiteSpace: 'pre-wrap' }}
          />
          {reply.as_of && (
            <Typography.Text type="secondary">适用日期：{reply.as_of}</Typography.Text>
          )}
          {reply.sources.map((source) => (
            <Card key={source.citation_id} size="small" style={{ marginTop: 8 }}>
              <Link to={`/knowledge/documents/${source.document.id}`}>
                {source.document.title} · v{source.document.version}
              </Link>
              <Typography.Paragraph type="secondary">
                {source.heading_path.join(' / ')} · 原文第{source.source_start_line}–
                {source.source_end_line}行 ；适用：{source.document.effective_from} 至{' '}
                {source.document.effective_until ?? '未设定结束日期'}
              </Typography.Paragraph>
              <pre
                style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'inherit' }}
              >
                {source.text}
              </pre>
            </Card>
          ))}
        </section>
      ))}
    </Card>
  );
}
