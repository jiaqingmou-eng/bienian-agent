# 架构与演进

## 三个可独立运行的应用

React 只访问 BFF。NestJS 管理浏览器会话、前端 DTO 和 HTTP 错误映射。FastAPI 拥有财务业务规则、业务权限、数据库和后续 Agent 执行。

应用之间通过 HTTP/JSON 协作，禁止跨应用导入源码。Node 使用 pnpm，Python 使用 uv；每个应用有独立 Dockerfile。

## 身份链路

1. 本地演示登录接受固定账号 key，不接受任意用户 ID、角色或企业 ID。
2. BFF 使用限定为 demo scope 的 JWT 调用 Python 演示登录接口。
3. Python 从数据库读取预置用户，BFF 轮换会话 ID，只在会话中保存用户 ID。
4. 后续请求签发 60 秒 JWT，固定 HS256、issuer 和 audience，subject 为已登录用户 ID。
5. Python 验证 JWT，用数据库中的用户企业和角色执行授权。
6. 浏览器写请求必须携带与 APP_ORIGIN 一致的 Origin；Cookie 使用 httpOnly、SameSite=Strict。

共享 HMAC 密钥适用于本地原型。后续部署可替换为非对称签名和统一身份服务，并保持后端业务授权逻辑。

## 数据所有权

PostgreSQL 的 tenants、users、expenses、expense_items、approvals、audit_logs 由 Python 管理。BFF 不连接业务表。

- 金额为整数分，第一版只支持 CNY。总额由后端计算，上限为演示系统配置的100万元，不代表企业报销政策。
- 员工只可读本人申请。财务可读同企业已提交申请，以及自己的草稿。
- 草稿创建、明细和审计同事务提交。
- 幂等键的唯一范围是企业、申请人、键；相同键不同内容返回409。
- approvals 绑定申请版本，执行审批与版本失效逻辑在第六天补齐。

## 后续 Agent

```text
HTTP Controller ───────────┐
                          ↓
Agent → Tool → Finance Service → Database
```

LangGraph 放在 Python 服务中，调用同一套财务服务。不能让 Agent 直接绕过权限写表。Checkpoint 保存执行进度，报销表保存业务状态。

- RAG：制度文档与适用版本、章节引用。
- Skills：业务步骤及工具白名单，配置不替代服务端授权。
- MCP：独立外部服务的工具适配，不重复维护本地报销主数据。
- 审批：先更新业务审批状态，再通过可重试机制唤醒对应图；不能由浏览器直接决定任意 thread 的恢复参数。
- 多 Agent：在 agents 模块中按职责拆分子图。
- 长期记忆：独立于制度库、业务事实与 checkpoint；身份、额度和审批权从权威系统读取。
- 飞书：入口与事件适配器，复用财务服务，验证签名并去重。

未来 SSE 连接断开只代表浏览器离线，不应自动取消等待审批的持久化任务。第一天尚未实现执行队列或 SSE 转发。
