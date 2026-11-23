# 开发规范

## 代码组织

- Controller 只做传输层适配；业务规则放到 Service。
- Python finance 服务供 HTTP 和未来 Agent Tools 复用。
- 应用之间使用协议，不跨目录导入内部源码。
- 新增模块时按业务职责组织，避免建立无实际职责的空目录。
- TypeScript strict 和 noUncheckedIndexedAccess 保持开启；禁止使用 any 掩盖协议问题。

## 注释与命名

- 类与模块写明职责；复杂条件解释原因和约束，不复述每一行代码。
- 金额命名显式包含 fen，不以 amount 表示不确定单位。
- Python 使用 snake_case，TypeScript 本地变量使用 camelCase；跨服务 JSON 保留协议字段名。
- 涉及幂等、权限、事务、身份传播的规则必须保留注释和相应测试。

## 变更流程

1. 修改需求对应的模型、业务服务与接口。
2. 数据库变化新增 Alembic revision；已发布的迁移不原地修改。
3. 协议变化重新生成 OpenAPI 和 BFF 类型。
4. 执行格式化、lint、typecheck 与相关测试；解决实际风险后停止重复测试。
5. 提交有意义的 Git diff，提交消息可使用 feat、fix、docs、test、chore 前缀。

首次联网安装时提交锁文件，之后使用 frozen/locked 安装。当前仓库没有伪造未解析依赖的锁文件。

## 错误与日志

- 错误对用户给出可理解的原因，包含请求编号。
- 日志只记录必要操作元数据，不记录 Cookie、Bearer Token、报销正文或完整发票。
- 不把原始异常直接返回前端；数据库连接启用参数隐藏。
- 不提交 .env、凭据、真实发票或真实员工数据。

## 身份与会话

当前是本地演示登录，MemoryStore 不具备跨实例共享和重启恢复能力。生产认证、持久化会话、HTTPS、安全 Cookie 和真实审批分权需要在上线前实现。

## 数据库测试

SQLite 用于快速验证真实迁移与 API 行为，不能证明 PostgreSQL 的并发语义。配置 TEST_POSTGRES_URL 后，集成测试会创建随机命名 schema，并在结束后仅删除该 schema。CI 使用专用 PostgreSQL 容器。
