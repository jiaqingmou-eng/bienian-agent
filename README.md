# 知账 · Enterprise Agent

企业财务报销 Agent 原型。已有 React、NestJS BFF、FastAPI 和报销草稿数据库链路，P1 新增自然语言整理、澄清、预览与明确确认后创建草稿。

## 当前能力

- 演示员工与财务账号登录、会话退出。
- 员工查看自己的申请，新增报销草稿，查看费用明细。
- 财务查看同企业已提交的申请；其他员工的草稿保持私有。
- 服务端按整数“分”计算总额；幂等创建；同事务记录审计。
- 六张财务业务表、Agent 任务快照表、新增 Alembic 迁移和可重复执行的虚构种子数据。
- OpenAPI 导出、TypeScript 类型生成入口、独立 Dockerfile 和 Compose。
- 报销助手页面、OpenAI 兼容模型适配器、LangGraph 字段整理、持久预览与确认门禁；模型配置放在根目录 `.env`。

模型接入、配置与验收见 [P1 说明](docs/p1.md)。真实模型需要填写 `LLM_API_KEY`，当前尚未完成真实模型端到端验收。RAG、正式审批执行和发票识别尚未接入；待审批样例由种子脚本生成。

## 工程边界

财务 Pydantic 请求和响应模型位于 `services/backend/src/enterprise_agent/api/schemas.py`，Agent 协议位于 `agent/schemas.py`。FastAPI 校验请求并按 response_model 序列化响应；财务内部命令模型保留在 `finance/schemas.py`。

```text
浏览器 → React → NestJS BFF → FastAPI → PostgreSQL
                                └→ P1 LangGraph / 后续 RAG、MCP
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

P0 的详细操作步骤、测试用例、预期状态码和结果记录方式见 [P0 验收指南](docs/p0-verification.md)。

## 协议生成

Pydantic 模型是 Python HTTP 协议的来源。`pnpm contracts:generate` 先导出 `contracts/backend.openapi.json`，再更新 BFF 的 `backend.schema.ts`。

仓库协议快照已由生成命令更新；BFF 生成步骤同时使用 Prettier 保持输出稳定。前端独立维护 BFF 响应类型，协议见 `docs/api-boundaries.md`。

## 演示环境约束

- 演示登录必须显式开启 `ENABLE_DEMO_LOGIN=true`。它是预置账号切换，不是真实身份认证。
- BFF 使用本地内存 Session Store；BFF 重启后需要重新登录，报销数据仍然保留。
- BFF 在 `NODE_ENV=production` 下拒绝启动。生产部署前需要真实认证、持久化会话与 HTTPS 配置。
- Python 只接受短时、限定签发者及接收方的 BFF JWT；角色和企业信息重新从数据库读取。
- `.env` 不提交、不打包；外部追踪系统尚未启用。
- 两份锁文件已存在，本地安装使用 `pnpm install --frozen-lockfile` 与 `uv sync --locked --directory services/backend`。CI 和 Docker 的锁定安装改造仍是 P0 待办。

## 后续计划

后续以根目录 [plam.md](plam.md) 为主计划，按阶段验收推进。下一阶段先核验现有底座，再实现单 Agent 的“自然语言描述 → 缺项澄清 → 草稿预览 → 用户明确确认 → 调用财务服务创建草稿”。之后补齐持久化执行与恢复，再建设可追溯 RAG、正式审批、记忆、Skills、MCP 和生产运行能力。

## 数据库端口

本地数据库默认映射到 `127.0.0.1:55432`，容器内部仍使用 `5432`。宿主机运行的 Python 后端通过 `.env` 中的 `DATABASE_URL` 访问 `55432`；Compose 内的后端通过 `postgres:5432` 访问数据库。

若本机端口冲突，同时调整 `.env` 中的 `POSTGRES_HOST_PORT` 和 `DATABASE_URL` 的端口，然后重新执行 `docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres`。无需删除数据卷或停止其他项目的数据库。
