import { ReloadOutlined, SendOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Pagination,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, describeError } from '../../api/client';
import { useAuth } from '../../app/auth';
import { formatMoney } from '../../lib/money';
import { recoverTaskParams, taskStorageKey } from './taskNavigation';
import type { AgentRun, AgentRunList, ChatReply, Consultation } from './types';
import { ConsultationReplies } from './ConsultationReplies';

const statusLabels: Record<AgentRun['status'], string> = {
  collecting: '待补充信息',
  awaiting_confirmation: '请核对预览',
  executing: '正在确认创建结果',
  completed: '草稿已创建',
  cancelled: '已取消',
};
const categoryLabels: Record<string, string> = {
  transport: '交通',
  lodging: '住宿',
  meal: '餐饮',
  other: '其他',
};

interface PendingMessage {
  text: string;
  id: string;
  runId?: string;
  version: number;
}

export function AssistantPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const runId = params.get('run');
  const isNew = params.get('new') === '1';
  const [history, setHistory] = useState<AgentRunList | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [taskBusy, setTaskBusy] = useState(false);
  // 咨询生命周期属于本次页面，和报销任务分开；切换任务不会把咨询写入另一任务。
  const [consultations, setConsultations] = useState<Consultation[]>([]);
  const storageKey = taskStorageKey(user?.tenant_id ?? '', user?.id ?? '');

  useEffect(() => {
    if (!runId && !isNew) return;
    try {
      sessionStorage.setItem(storageKey, runId ?? 'new');
    } catch {
      // History remains available when browser storage is disabled.
    }
  }, [runId, isNew, storageKey]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setHistoryLoading(true);
    void api<AgentRunList>(`/agent/runs?limit=5&offset=${(page - 1) * 5}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (active) {
          setHistory(result);
          setHistoryError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) setHistoryError(describeError(err));
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [page, revision]);

  useEffect(() => {
    if (runId || isNew) return;
    let remembered: string | null = null;
    try {
      remembered = sessionStorage.getItem(storageKey);
    } catch {
      // A new browser session falls back to the latest server-side task.
    }
    if (!remembered && !history) return;
    setParams(recoverTaskParams(params, remembered, history?.items[0]?.id ?? null), {
      replace: true,
    });
  }, [runId, isNew, history, params, setParams, storageKey]);

  function newTask() {
    setConsultations([]);
    setParams({ new: '1' });
  }

  return (
    <>
      <Card
        title="我的助手任务"
        style={{ marginBottom: 20 }}
        loading={historyLoading && !history}
        extra={
          <Space>
            <Button disabled={taskBusy} onClick={() => setRevision((value) => value + 1)}>
              刷新任务列表
            </Button>
            <Button type="primary" disabled={taskBusy} onClick={newTask}>
              新任务
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary">
          已发送的对话和预览保存在服务端。可以同时保留多项任务，选择历史任务继续处理。
        </Typography.Paragraph>
        {historyError && <Alert type="error" showIcon message={historyError} />}
        {history?.items.length === 0 && (
          <Empty description="暂无助手任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
        <Space wrap>
          {history?.items.map((item) => (
            <Button
              key={item.id}
              type={runId === item.id ? 'primary' : 'default'}
              disabled={taskBusy}
              title={`${item.title} · ${new Date(item.created_at).toLocaleString('zh-CN')} · ${item.id}`}
              onClick={() => setParams({ run: item.id })}
            >
              {item.title.length > 18 ? `${item.title.slice(0, 18)}…` : item.title}
              {' · '}
              {statusLabels[item.status]}
              {' · '}
              {item.id.slice(0, 8)}
            </Button>
          ))}
        </Space>
        {!!history?.total && (
          <Pagination
            size="small"
            current={page}
            total={history.total}
            pageSize={5}
            showSizeChanger={false}
            disabled={taskBusy || historyLoading}
            onChange={setPage}
            style={{ marginTop: 16 }}
          />
        )}
      </Card>
      <ConsultationReplies items={consultations} />
      {runId || isNew ? (
        <AssistantTask
          key={runId ?? 'new'}
          runId={runId}
          onBusyChange={setTaskBusy}
          onConsultation={(item) => setConsultations((previous) => [...previous, item].slice(-10))}
          onNew={newTask}
          onAccepted={(next) => {
            setRevision((value) => value + 1);
            if (next.id !== runId) {
              setPage(1);
              setParams({ run: next.id }, { replace: true });
            }
          }}
        />
      ) : (
        <Card loading={!historyError}>正在恢复助手任务；列表加载失败时可点击重试或新建任务。</Card>
      )}
    </>
  );
}

interface AssistantTaskProps {
  runId: string | null;
  onAccepted: (run: AgentRun) => void;
  onNew: () => void;
  onBusyChange: (busy: boolean) => void;
  onConsultation: (item: Consultation) => void;
}

function AssistantTask({
  runId,
  onAccepted,
  onNew,
  onBusyChange,
  onConsultation,
}: AssistantTaskProps) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<PendingMessage | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    onBusyChange(busy);
    return () => {
      onBusyChange(false);
    };
  }, [busy, onBusyChange]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    let active = true;
    const controller = new AbortController();
    setBusy(true);
    void api<AgentRun>(`/agent/runs/${encodeURIComponent(runId)}`, { signal: controller.signal })
      .then((next) => {
        if (active) {
          setRun(next);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) setError(describeError(err));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [runId]);

  function accept(next: AgentRun) {
    // A response from a page left behind must never navigate away from the current task.
    if (!mounted.current) return;
    setRun(next);
    setError(null);
    onAccepted(next);
  }

  async function refresh() {
    if (!runId || busy) return;
    setBusy(true);
    try {
      accept(await api<AgentRun>(`/agent/runs/${encodeURIComponent(runId)}`));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy || (runId && !run)) return;
    setBusy(true);
    setError(null);
    // Preserve message identity after a timeout. A retry cannot silently become a second operation.
    const previous = pending.current;
    const operation =
      previous && previous.text === text && previous.runId === run?.id
        ? previous
        : { text, id: crypto.randomUUID(), runId: run?.id, version: run?.version ?? 0 };
    pending.current = operation;
    try {
      // 自然语言先进入编排接口；只有 expense 响应才能更新任务快照和右侧预览。
      const body = {
        message_id: operation.id,
        message: text,
        ...(operation.runId
          ? { run_id: operation.runId, expected_version: operation.version }
          : {}),
      };
      const reply = await api<ChatReply>('/agent/chat', {
        method: 'POST',
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(130_000),
      });
      if (!mounted.current) return;
      if (reply.kind === 'expense' && reply.run) accept(reply.run);
      else onConsultation({ id: operation.id, question: text, reply });
      setInput('');
      pending.current = null;
    } catch (err) {
      setError(describeError(err));
      // Accepted edits invalidate old confirmations even if extraction later fails.
      if (run) {
        try {
          setRun(await api<AgentRun>(`/agent/runs/${run.id}`));
        } catch {
          /* Require an explicit status refresh if this read also fails. */
        }
      }
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!run?.preview || busy) return;
    setBusy(true);
    setError(null);
    try {
      accept(
        await api<AgentRun>(`/agent/runs/${run.id}/confirm`, {
          method: 'POST',
          body: JSON.stringify({
            confirmation_id: run.preview.confirmation_id,
            expected_version: run.preview.version,
            content_hash: run.preview.content_hash,
          }),
        }),
      );
    } catch (err) {
      setError(`${describeError(err)} 可刷新状态或重试原确认，系统会复用同一创建操作。`);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!run || busy) return;
    setBusy(true);
    try {
      accept(
        await api<AgentRun>(`/agent/runs/${run.id}/cancel`, {
          method: 'POST',
          body: JSON.stringify({ expected_version: run.version }),
        }),
      );
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const editable = !run || run.status === 'collecting' || run.status === 'awaiting_confirmation';
  const preview = run?.preview;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">EXPENSE ASSISTANT</span>
          <Typography.Title level={2}>报销助手</Typography.Title>
          <Typography.Text type="secondary">
            可以描述费用整理草稿，也可以咨询报销制度；咨询时请明确地点和适用条件。
          </Typography.Text>
        </div>
        <Space wrap>
          {runId && (
            <Button
              icon={<ReloadOutlined />}
              disabled={busy}
              onClick={() => {
                void refresh();
              }}
            >
              刷新状态
            </Button>
          )}
          <Button
            disabled={busy}
            onClick={() => {
              onNew();
              setRun(null);
              setInput('');
              setError(null);
              pending.current = null;
            }}
          >
            新任务
          </Button>
          <Link to="/expenses/new">
            <Button>使用表单</Button>
          </Link>
        </Space>
      </div>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      {run && (
        <Tag color={run.status === 'completed' ? 'green' : 'blue'}>{statusLabels[run.status]}</Tag>
      )}
      <div className="assistant-grid">
        <Card title="对话" className="assistant-conversation" aria-busy={busy}>
          {!run && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="例如：昨天成都到上海的高铁票286元，出差拜访客户。"
            />
          )}
          <div className="assistant-messages" aria-live="polite">
            {run?.messages.map((message) => (
              <div className={`assistant-message ${message.role}`} key={message.id}>
                <strong>{message.role === 'user' ? '你' : '报销助手'}</strong>
                <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  {message.content}
                </Typography.Paragraph>
              </div>
            ))}
          </div>
          {run?.status === 'completed' && run.expense_id && (
            <Alert
              type="success"
              showIcon
              message="草稿创建成功"
              description={
                <Link to={`/expenses/${run.expense_id}`}>查看报销单 {run.expense_id}</Link>
              }
            />
          )}
          {run?.status === 'cancelled' && (
            <Alert type="info" message="此任务已取消，可以开始新任务。" />
          )}
          {run?.status === 'executing' && (
            <Alert
              type="info"
              message="正在核实创建结果，请刷新或重试原确认。此时不能修改或取消。"
            />
          )}
          <Input.TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            rows={4}
            maxLength={2000}
            showCount
            disabled={busy || (!!runId && !run)}
            aria-label="描述报销费用"
            placeholder="描述报销费用，或问：成都出差的住宿标准是多少？"
            ref={(element) => {
              inputRef.current = element?.resizableTextArea?.textArea ?? null;
            }}
          />
          <div className="assistant-actions">
            <Typography.Text type="secondary">
              输入“确认”不会创建，请核对右侧预览后点击按钮。
            </Typography.Text>
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={busy}
              disabled={!input.trim() || (!!runId && !run)}
              onClick={() => {
                void send();
              }}
            >
              发送
            </Button>
          </div>
        </Card>
        <Card title="草稿预览">
          {preview ? (
            <>
              <Descriptions
                column={1}
                size="small"
                items={[
                  { key: 'title', label: '标题', children: preview.payload.title },
                  { key: 'purpose', label: '用途', children: preview.payload.purpose },
                  {
                    key: 'total',
                    label: '合计',
                    children: <strong>{formatMoney(preview.total_fen)}</strong>,
                  },
                ]}
              />
              <Table
                size="small"
                pagination={false}
                scroll={{ x: 480 }}
                dataSource={preview.payload.items.map((item, index) => ({ ...item, key: index }))}
                columns={[
                  { title: '日期', dataIndex: 'incurred_on' },
                  {
                    title: '类别',
                    dataIndex: 'category',
                    render: (value: string) => categoryLabels[value] ?? value,
                  },
                  { title: '说明', dataIndex: 'description' },
                  {
                    title: '金额',
                    dataIndex: 'amount_fen',
                    render: (value: number) => formatMoney(value),
                  },
                ]}
              />
              {run?.status === 'awaiting_confirmation' && (
                <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
                  确认有效至 {new Date(preview.expires_at).toLocaleString('zh-CN')}
                  。修改内容后请核对新的预览。
                </Typography.Paragraph>
              )}
              {(run?.status === 'awaiting_confirmation' || run?.status === 'executing') && (
                <Space wrap style={{ marginTop: 16 }}>
                  <Button
                    type="primary"
                    loading={busy}
                    disabled={!!input.trim()}
                    onClick={() => {
                      void confirm();
                    }}
                  >
                    {run.status === 'executing' ? '重试原确认' : '确认创建'}
                  </Button>
                  {run.status === 'awaiting_confirmation' && (
                    <Button disabled={busy} onClick={() => inputRef.current?.focus()}>
                      继续修改
                    </Button>
                  )}
                </Space>
              )}
            </>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="信息齐全后会在这里展示可核对的预览。"
            />
          )}
          {run && editable && (
            <Button
              danger
              disabled={busy}
              onClick={() => {
                void cancel();
              }}
              style={{ marginTop: 20 }}
            >
              取消任务
            </Button>
          )}
        </Card>
      </div>
    </>
  );
}
