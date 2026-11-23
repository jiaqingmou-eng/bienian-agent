# 知账 · Enterprise Agent

企业财务报销 Agent 原型。第一天实现 React 页面、NestJS BFF、FastAPI 业务后端，以及报销草稿的数据库链路。

## 当前能力

- 演示员工与财务账号登录、会话退出。
- 员工查看自己的申请，新增报销草稿，查看费用明细。
- 财务查看同企业已提交的申请；其他员工的草稿保持私有。
- 服务端按整数“分”计算总额；幂等创建；同事务记录审计。
- 六张业务表、Alembic 初始迁移和可重复执行的虚构种子数据。
- OpenAPI 导出、TypeScript 类型生成入口、独立 Dockerfile 和 Compose。

第一天没有接入模型、RAG、审批执行或发票识别。页面中的待审批样例由种子脚本生成，不能在当前版本操作批准或驳回。

## 工程边界

Pydantic 请求和响应模型集中在 `services/backend/src/enterprise_agent/api/schemas.py`：FastAPI 依据请求 schema 校验 body、query、path/header 参数，并依据 response_model 过滤和校验成功响应；统一错误响应也使用同一组 schema。财务服务的内部命令模型保留在 `finance/schemas.py`。

```text
浏览器 → React → NestJS BFF → FastAPI → PostgreSQL
                                └→ 后续 LangGraph / RAG / MCP
```

| 目录               | 职责                                      |
| ------------------ | ----------------------------------------- |
| `apps/web`         | React、Ant Design、路由和报销交互         |
| `apps/bff`         | NestJS、DTO 校验、会话、Python API 客户端 |
| `services/backend` | FastAPI、财务规则、权限、数据库和迁移     |
| `contracts`        | 生成的协议快照，以及事件约定              |
| `infra`            | 本地容器配置                              |
| `docs`             | 架构、接口边界、开发规范和验收说明        |

## 开发环境

- Node.js 22.12+（推荐 Node 22 LTS）。
- pnpm 10.15.1。
- uv，以及 Python 3.12；uv 可以自动安装 Python。
- Docker Desktop（启动 PostgreSQL），或自行提供 PostgreSQL 16。

默认使用 `http://localhost:5173`。不要交替使用 localhost 与 127.0.0.1 访问页面；写接口会校验浏览器 Origin。

## 本地启动

在工程根目录执行：

```powershell
# 首次配置：生成不同的随机密钥，不覆盖已有 .env。
node scripts/init-env.mjs

# 安装 JS 与 Python 依赖。
pnpm install
uv sync --directory services/backend

# 启动数据库，创建表并导入虚构演示数据。
pnpm db:up
pnpm db:migrate
pnpm db:seed

# 同时启动三个应用。
pnpm dev
```

打开 http://localhost:5173，选择员工小林、员工小陈或财务小周。

也可以在三个终端分别执行 `pnpm dev:web`、`pnpm dev:bff`、`pnpm dev:backend`。后端路由文档位于 http://127.0.0.1:8000/docs。

如果数据库还在初始化，等待 `docker compose --env-file .env -f infra/compose.yaml ps` 显示 postgres 为 healthy，再执行迁移。

## 全容器启动

```powershell
node scripts/init-env.mjs
docker compose --env-file .env -f infra/compose.yaml up -d postgres
docker compose --env-file .env -f infra/compose.yaml build
docker compose --env-file .env -f infra/compose.yaml run --rm backend alembic upgrade head
docker compose --env-file .env -f infra/compose.yaml run --rm backend python -m enterprise_agent.db.seed
docker compose --env-file .env -f infra/compose.yaml up -d
```

页面同样位于 http://localhost:5173。数据保存在 `postgres-data` volume。停止服务使用 `pnpm db:down`，不会删除 volume。

## 代码质量与测试

```powershell
# 不需要第三方依赖的预检查与小范围单元测试。
node scripts/check-scaffold.mjs
pnpm test:offline

# 安装依赖后执行的完整检查。
pnpm format
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm contracts:generate
```

`pnpm format` 会统一格式，首次联网安装后应执行并检查 diff。测试分三层：

1. Node 原生测试：金额文本转换与 BFF 环境校验。
2. Jest + Supertest：BFF 会话、来源校验、DTO 和匿名请求拦截。
3. pytest：运行真实 Alembic 迁移后，验证金额、权限、审计和幂等。默认使用内存 SQLite；设置 `TEST_POSTGRES_URL` 可运行隔离 schema 中的 PostgreSQL 集成测试。

无依赖预检查只验证目录、JSON 和本地导入，不能代替 TypeScript 编译、Python 测试或端到端验证。当前执行结果见 `docs/day-1.md`。

## 协议生成

Pydantic 模型是 Python HTTP 协议的来源。`pnpm contracts:generate` 先导出 `contracts/backend.openapi.json`，再更新 BFF 的 `backend.schema.ts`。

仓库目前包含一份手工对齐的初始类型快照，尚未冒充自动生成结果。首次安装成功后请运行生成命令、检查差异，并提交协议快照。前端目前独立维护精简的 BFF 响应类型，协议见 `docs/api-boundaries.md`。

## 演示环境约束

- 演示登录必须显式开启 `ENABLE_DEMO_LOGIN=true`。它是预置账号切换，不是真实身份认证。
- BFF 使用本地内存 Session Store；BFF 重启后需要重新登录，报销数据仍然保留。
- BFF 在 `NODE_ENV=production` 下拒绝启动。生产部署前需要真实认证、持久化会话与 HTTPS 配置。
- Python 只接受短时、限定签发者及接收方的 BFF JWT；角色和企业信息重新从数据库读取。
- `.env` 不提交、不打包；外部追踪系统尚未启用。
- 当前环境无法联网安装依赖，因此没有生成可信的 `pnpm-lock.yaml`、`uv.lock`。首次联网安装后应提交锁文件，CI 和镜像构建再切换为锁定安装。当前 Dockerfile 的依赖安装尚未锁定。

## 第二天

添加虚构财务制度、文档入库和 pgvector 检索；从现有 FastAPI 扩展带引用的制度问答，不改变报销业务服务的所有权。

## 数据库端口

本地数据库默认映射到 `127.0.0.1:55432`，容器内部仍使用 `5432`。宿主机运行的 Python 后端通过 `.env` 中的 `DATABASE_URL` 访问 `55432`；Compose 内的后端通过 `postgres:5432` 访问数据库。

若本机端口冲突，同时调整 `.env` 中的 `POSTGRES_HOST_PORT` 和 `DATABASE_URL` 的端口，然后重新执行 `docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres`。无需删除数据卷或停止其他项目的数据库。
