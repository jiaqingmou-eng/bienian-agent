# API 边界

## 浏览器 → NestJS

| 方法 | 路径                              | 身份       | 说明                                     |
| ---- | --------------------------------- | ---------- | ---------------------------------------- |
| GET  | `/api/health`                     | 公开       | BFF 进程存活                             |
| GET  | `/api/ready`                      | 公开       | Python 与数据库连通性                    |
| GET  | `/api/auth/demo-accounts`         | 演示模式   | 列出虚构账号                             |
| POST | `/api/auth/demo-login`            | 演示模式   | `{account_key}`，轮换 Session            |
| POST | `/api/auth/logout`                | 允许未登录 | 结束会话，返回204                        |
| GET  | `/api/me`                         | 已登录     | 当前数据库用户                           |
| GET  | `/api/expenses?limit=20&offset=0` | 已登录     | 权限过滤后的列表                         |
| GET  | `/api/expenses/:id`               | 已登录     | 权限过滤后的详情                         |
| POST | `/api/expenses`                   | 已登录     | 创建草稿，要求 UUID 格式 Idempotency-Key |

所有 POST 均要求 Origin 与 APP_ORIGIN 完全一致。使用 curl、脚本或接口工具测试时也需要提供该请求头。

## 草稿请求

```json
{
  "title": "上海差旅报销",
  "purpose": "客户拜访",
  "items": [
    {
      "category": "transport",
      "incurred_on": "2026-11-20",
      "description": "往返高铁",
      "amount_fen": 60000
    }
  ]
}
```

服务端不接受 status、applicant_id、tenant_id、total_fen 等权威字段。类别为 transport、lodging、meal、other。单项金额必须为1～100000000的整数，总额不超过100000000分。

列表响应为 `{items, limit, offset}`，尚不提供总条数。前端展示“本页统计”，不会把当前页金额当成全部报销金额。

错误响应为 `{code, message, requestId}`。Python 可附带不包含原始输入的参数错误位置。BFF 不暴露堆栈、Cookie、JWT 或原始数据库异常。

## NestJS → Python

业务路径前缀为 `/api/v1`；健康检查位于 `/health`、`/ready`。每次调用携带短时 Bearer JWT 与 X-Request-Id。创建请求继续传递 Idempotency-Key。

DTO 校验处理格式；Python 再次校验输入并执行业务规则。超时返回可理解的服务不可用错误，不自动重试写操作。

TypeScript 类型是编译期约束。P1 Agent 成功响应在 BFF 有运行时校验；已有财务客户端尚未对每个成功响应做完整运行时 Schema 校验。

## P1 Agent

浏览器路径以 `/api/agent/runs` 开头，对应 Python `/api/v1/agent/runs`。均要求登录；POST 仍经过 Origin 校验。

| 方法 | 后缀                 | 请求                                                | 成功                                                  |
| ---- | -------------------- | --------------------------------------------------- | ----------------------------------------------------- |
| POST | 空                   | `{client_request_id, message}`                      | 201，返回 run；原标识重试同内容复用任务               |
| GET  | `?limit=20&offset=0` | 仅分页参数，不接受租户 / 用户筛选                   | 200，当前用户任务摘要 `{items, total, limit, offset}` |
| GET  | `/:id`               | 无                                                  | 200，仅任务所有者可读                                 |
| POST | `/:id/messages`      | `{message_id, expected_version, message}`           | 200，先撤销旧确认再提取；原消息标识可重试             |
| POST | `/:id/confirm`       | `{confirmation_id, expected_version, content_hash}` | 200，版本绑定的明确确认，复用稳定业务幂等键           |
| POST | `/:id/cancel`        | `{expected_version}`                                | 200，仅未开始创建的任务可取消                         |

响应包含状态、版本、对话、缺项、可选预览和可选结果单号；未公开候选快照元数据、API Key 或模型原始响应。预览金额为整数分，合计由服务端计算。`expected_version` 在确认请求中指 `preview.version`；执行中或完成后重试原确认仍使用该预览版本，而不是新的 run.version。

任务列表按创建时间倒序、ID 倒序排序，每页 1–100 项；摘要仅含 `id/status/title/created_at`，不含完整对话、预览和确认凭据。列表与详情都由 Python 按可信身份中的企业和用户过滤，财务角色也不能读取他人的私人任务。恢复页面只执行查询，不调用模型或写工具。

`executing` 代表已持久接受确认，业务结果可能仍待对账。只能刷新或重试原确认，不能改内容或取消。未开始执行的过期预览要重新生成；已接受的执行不因确认期限过去而丢失恢复能力。

消息有 2000 字符限制、每任务最多 40 条消息；模型仅接收最近必要历史及当前候选。模型读取根 `.env` 的 `LLM_*`，不接收用户指定端点。该链路采用同步请求，P2 再补后台执行和完整 checkpoint。
