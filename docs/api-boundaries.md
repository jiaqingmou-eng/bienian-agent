# API 边界

## 浏览器 → NestJS

| 方法 | 路径 | 身份 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 公开 | BFF 进程存活 |
| GET | `/api/ready` | 公开 | Python 与数据库连通性 |
| GET | `/api/auth/demo-accounts` | 演示模式 | 列出虚构账号 |
| POST | `/api/auth/demo-login` | 演示模式 | `{account_key}`，轮换 Session |
| POST | `/api/auth/logout` | 允许未登录 | 结束会话，返回204 |
| GET | `/api/me` | 已登录 | 当前数据库用户 |
| GET | `/api/expenses?limit=20&offset=0` | 已登录 | 权限过滤后的列表 |
| GET | `/api/expenses/:id` | 已登录 | 权限过滤后的详情 |
| POST | `/api/expenses` | 已登录 | 创建草稿，要求 UUID 格式 Idempotency-Key |

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

TypeScript 类型是编译期约束。当前客户端没有对每个成功响应做完整运行时 Schema 校验，该能力可在外部服务协议不稳定时补充。
