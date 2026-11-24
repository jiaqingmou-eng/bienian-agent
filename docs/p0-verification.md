# P0 验收操作指南与测试用例

编写日期：2026-11-23。对应主计划：[plam.md 的 P0](../plam.md#p0确认可扩展的业务底座)。

本文给出可执行步骤与验收标准，不代表这些检查已经执行通过。当前 CI / Docker 锁定安装尚待修改；下面涉及该改造的内容是实施要求与验证方法。P0 任务状态按实际执行结果更新。

## 1. 验收范围、顺序与运行环境

执行顺序：环境 → 锁定安装 → 静态检查和自动化测试 → 本地 PostgreSQL / 迁移 → 三端业务和权限 → PostgreSQL 专项 → 协议生成 → CI / Docker 改造与验证 → 记录结果。

所有宿主机命令均在 `D:\project\EnterpriseAgent` 执行。除特别说明外使用 PowerShell；第 5 节的 API 示例需要 PowerShell 7，因为使用 `-SkipHttpErrorCheck`。通过 `pwsh` 启动 PowerShell 7；Windows PowerShell 5.1 用户可先按第 4 节浏览器步骤验收，API 自动化部分另在 PowerShell 7 中执行。

只使用本地虚构数据。API 验收会新增标题以 `P0-` 开头的演示草稿，不清空现有数据库。PostgreSQL 专项使用独立测试库和随机 schema，不使用生产数据库。本文不要求删除数据卷、重置 Git 或手工改业务状态。

```powershell
Set-Location D:\project\EnterpriseAgent
node --version
pnpm --version
uv --version
docker version
git status --short
```

预期：Node 满足 `>=22.12 <25`，pnpm 为 `10.15.1`，uv 可用，Docker client / server 均可访问。记录当前 Git commit 及工作区差异，便于定位测试基线。不要输出 `.env`、Cookie 或 JWT 作为证据。

```powershell
git rev-parse HEAD
Test-Path pnpm-lock.yaml
Test-Path services/backend/uv.lock
node scripts/init-env.mjs
```

两个文件应存在。环境初始化脚本不会覆盖已有 `.env`。核对配置时在本地编辑器查看，文档只记录“配置已核对”，不粘贴密钥。默认端口为 web `5173`、BFF `3000`、backend `8000`、PostgreSQL `55432`，以实际 `.env` 为准。

### 1.1 如何判定命令成功

每条命令运行完成后检查输出和退出码，失败时先定位，不把后续成功当作前一步通过：

```powershell
pnpm typecheck
if ($LASTEXITCODE -ne 0) { throw '类型检查失败，请先处理或记录阻塞' }
```

PowerShell 的 `$ErrorActionPreference = 'Stop'` 不能在所有版本中自动使退出码非零的原生命令终止。步骤中的独立命令请逐条执行，遇到非零退出码记录失败。

## 2. 锁定安装与基础检查

### 2.1 验证已有锁文件可用

```powershell
pnpm install --frozen-lockfile
uv sync --locked --directory services/backend
git diff -- pnpm-lock.yaml services/backend/uv.lock
```

预期：两个安装命令退出码为 0；锁文件没有因安装新增变更。若锁文件之前已有未提交修改，比较本次安装前后的差异，不把原有 diff 误认成本次修改。

若 frozen / locked 提示 manifest 与锁文件不一致，这是应处理的失败；查清 `package.json` / `pyproject.toml` 的实际变更，需要更新锁文件时单独更新并审查后重新冻结安装。网络超时、镜像源不可达属于环境阻塞，不等于锁文件错误。不要直接去掉冻结参数掩盖失败。

### 2.2 逐项运行工程检查

| ID     | 命令                  | 预期与覆盖范围                               |
| ------ | --------------------- | -------------------------------------------- |
| ENV-01 | `pnpm check:scaffold` | JSON、目录和本地导入预检查通过；不能代替编译 |
| ENV-02 | `pnpm test:offline`   | 金额文本转换、环境解析相关测试通过           |
| ENV-03 | `pnpm format:check`   | Prettier 和 Ruff 格式均通过                  |
| ENV-04 | `pnpm lint`           | TS ESLint 与 Python Ruff 规则通过            |
| ENV-05 | `pnpm typecheck`      | 两个 TS 应用通过 strict 类型检查             |
| ENV-06 | `pnpm test`           | web、BFF、Python 测试通过；检查是否存在 skip |
| ENV-07 | `pnpm build`          | React / NestJS 构建通过；不包含 Docker 构建  |

首次格式检查可能发现存量格式问题。需要修复时运行 `pnpm format`，检查 diff，然后只重跑受影响检查。不要将格式失败写成“代码逻辑有问题”，也不要将 bundle 体积 warning 写成 build 失败；分别记录。

单独定位各层测试：

```powershell
pnpm --filter @enterprise/web test
pnpm --filter @enterprise/bff test
uv run --locked --directory services/backend pytest -q -rs
```

现有测试的边界：

- web 测试主要是金额转换，不是浏览器端到端测试。
- BFF Jest / Supertest 使用 mock BackendClient，验证会话、Origin、DTO、转发身份和幂等头；不能证明真实 Python / 数据库链路可用。
- `test_finance_api.py` 默认使用真实 Alembic 迁移后的 SQLite，覆盖授权、金额和幂等，但不能证明 PostgreSQL 并发行为。
- 未配置 `TEST_POSTGRES_URL` 时，PostgreSQL 用例会 skip。skip 不是通过；第 6 节必须单独运行。

## 3. 启动数据库与三端

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d --wait postgres
docker compose --env-file .env -f infra/compose.yaml ps
pnpm db:migrate
pnpm db:seed
uv run --locked --directory services/backend alembic current
uv run --locked --directory services/backend alembic heads
```

预期：postgres 为 healthy；迁移和种子退出码为 0；`current` 与 `heads` 的 revision 一致。当前只有基础 revision，后续以代码实际 head 为准，不在脚本里写死永远只有一个迁移。

在三个独立终端启动，便于只重启某一端：

```powershell
# 终端 A
pnpm dev:backend
```

```powershell
# 终端 B
pnpm dev:bff
```

```powershell
# 终端 C
pnpm dev:web
```

在第四个终端验证：

```powershell
(Invoke-WebRequest http://127.0.0.1:8000/ready -UseBasicParsing).StatusCode
(Invoke-WebRequest http://127.0.0.1:3000/api/ready -UseBasicParsing).StatusCode
(Invoke-WebRequest http://localhost:5173 -UseBasicParsing).StatusCode
```

三项均预期 200。`/ready` 只证明探针链路，不证明草稿创建和业务授权。

## 4. 浏览器人工验收

固定访问 `http://localhost:5173`，不要交替使用 `127.0.0.1:5173`。本项目写请求要求 Origin 与 `APP_ORIGIN` 完全相同；使用 Cookie 的页面身份也受 host 影响。

### UI-01：员工创建草稿

1. 选择员工小林登录，打开“新建报销”。
2. 标题填 `P0-浏览器草稿-本次时间`，用途填 `虚构客户拜访，仅用于P0验收`。
3. 第一项：交通，日期 `2026-11-20`，说明 `虚构高铁票`，金额（元）`286.00`。
4. 第二项：餐饮，同一日期，说明 `虚构工作餐`，金额（元）`14.50`。
5. 点击“保存草稿”，保存详情 URL / 单号，随后刷新页面。
6. DevTools → Network 检查该 POST 的 payload / response，仅在本地查看，不复制凭据。

预期：跳转详情；两条明细；页面总额 `300.50元`；请求的金额分别为 `28600` 和 `1450` 分；响应 `total_fen=30050`、`status=draft`、申请人为小林；刷新后仍可读。

### UI-02：其他员工不能访问私人草稿

1. 退出小林，登录员工小陈。
2. 查看列表，并直接粘贴 UI-01 的详情 URL。
3. 在 Network 查看详情请求的真实 HTTP 状态。

预期：列表没有该草稿；详情接口返回 404，页面显示可理解错误且不泄漏明细。只看到前端隐藏菜单不算授权验证。

### UI-03：财务看待审批样例，但不能看他人草稿

1. 退出小陈，登录财务小周。
2. 查看列表中的 pending 演示样例。
3. 直接访问 UI-01 私人草稿 URL。

预期：看得到同企业已提交样例；私人草稿接口返回 404。当前批准 / 驳回还没有实现，不能据此验收审批能力。

### UI-04：表单金额和必填校验

分别验证：空标题、空用途、无说明、金额 `0`、负数、三位小数 `1.001`、字母、超过单项上限。预期页面阻止保存并提示原因。Network 应没有成功创建请求；同时用第 5 节绕过页面直接测接口，证明不能绕过前端校验。

### UI-05 / UI-06：重启会话与持久数据

1. 小林登录并保留详情 URL。
2. 只在终端 B 按 Ctrl+C，再执行 `pnpm dev:bff`。
3. 刷新页面。预期旧会话失效，需要登录；重新登录后草稿仍存在。
4. 只重启终端 A 的 Python 服务。预期草稿仍存在；单独重启 Python 不应清除 BFF 会话。

注意：同一浏览器的普通标签页共享 Cookie，不适合同时保持小林和小陈身份。使用依次退出 / 登录、不同浏览器或独立隐私会话。

## 5. 真实 BFF API 验收（PowerShell 7）

该节验证真实 BFF → Python → PostgreSQL；按顺序在同一个 PowerShell 7 会话执行。测试失败时 `throw`，不要把红色异常当成已通过。执行期间不要另外创建草稿，便于核对新增数量。

执行前先保存三表计数（只读查询）：

```powershell
$p0DbUser = (docker compose --env-file .env -f infra/compose.yaml exec -T postgres printenv POSTGRES_USER).Trim()
$p0DemoDb = (docker compose --env-file .env -f infra/compose.yaml exec -T postgres printenv POSTGRES_DB).Trim()
$p0CountSql = 'SELECT (SELECT count(*) FROM expenses) AS expenses, (SELECT count(*) FROM expense_items) AS items, (SELECT count(*) FROM audit_logs) AS audits;'
docker compose --env-file .env -f infra/compose.yaml exec -T postgres psql -U $p0DbUser -d $p0DemoDb -v ON_ERROR_STOP=1 -c $p0CountSql
```

### 5.1 定义请求与断言助手

```powershell
$p0Base = 'http://localhost:5173/api'
$p0Origin = 'http://localhost:5173'

function Invoke-P0Api {
    param(
        [string]$Method = 'GET',
        [Parameter(Mandatory)][string]$Path,
        [Microsoft.PowerShell.Commands.WebRequestSession]$Session,
        [object]$Body,
        [string]$Key,
        [string]$Origin = $p0Origin,
        [switch]$OmitOrigin
    )
    $p0Headers = @{ Accept = 'application/json' }
    if ($Method -ne 'GET' -and -not $OmitOrigin) { $p0Headers.Origin = $Origin }
    if ($Key) { $p0Headers['Idempotency-Key'] = $Key }
    $p0Args = @{
        Uri = "$p0Base$Path"; Method = $Method; Headers = $p0Headers
        SkipHttpErrorCheck = $true; TimeoutSec = 15; ErrorAction = 'Stop'
    }
    if ($Session) { $p0Args.WebSession = $Session }
    if ($PSBoundParameters.ContainsKey('Body')) {
        $p0Args.ContentType = 'application/json; charset=utf-8'
        $p0Args.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10))
    }
    $p0Response = Invoke-WebRequest @p0Args
    $p0Data = if ($p0Response.Content) { $p0Response.Content | ConvertFrom-Json } else { $null }
    [pscustomobject]@{ Status = [int]$p0Response.StatusCode; Data = $p0Data }
}

function Assert-P0Status {
    param([object]$Response, [int]$Expected, [string]$Case)
    if ($Response.Status -ne $Expected) {
        throw "$Case expected $Expected, got $($Response.Status): $($Response.Data.message)"
    }
    Write-Host "PASS $Case (HTTP $Expected)"
}

$p0EmployeeA = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$p0EmployeeB = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$p0Finance = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
```

### 5.2 登录、创建、本人读取、重复和冲突

```powershell
$p0Anonymous = Invoke-P0Api -Path '/expenses'
Assert-P0Status $p0Anonymous 401 'API-01 anonymous'

$p0LoginA = Invoke-P0Api -Method POST -Path '/auth/demo-login' -Session $p0EmployeeA -Body @{ account_key = 'employee_a' }
Assert-P0Status $p0LoginA 200 'API-02 login A'
$p0LoginB = Invoke-P0Api -Method POST -Path '/auth/demo-login' -Session $p0EmployeeB -Body @{ account_key = 'employee_b' }
Assert-P0Status $p0LoginB 200 'API-03 login B'
$p0LoginF = Invoke-P0Api -Method POST -Path '/auth/demo-login' -Session $p0Finance -Body @{ account_key = 'finance' }
Assert-P0Status $p0LoginF 200 'API-04 login finance'

$p0Payload = @{
    title = 'P0-API-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
    purpose = '虚构客户拜访，P0接口验收'
    items = @(
        @{ category = 'transport'; incurred_on = '2026-11-20'; description = '虚构高铁票'; amount_fen = 28600 },
        @{ category = 'meal'; incurred_on = '2026-11-20'; description = '虚构工作餐'; amount_fen = 1450 }
    )
}
$p0Key = [guid]::NewGuid().ToString()
$p0Created = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload -Key $p0Key
Assert-P0Status $p0Created 201 'API-05 create'
$p0DraftId = $p0Created.Data.id
if (-not $p0DraftId -or $p0Created.Data.total_fen -ne 30050 -or
    $p0Created.Data.status -ne 'draft' -or $p0Created.Data.items.Count -ne 2 -or
    $p0Created.Data.applicant_id -ne $p0LoginA.Data.id) {
    throw 'API-05 invalid business result'
}
if ($p0Created.Data.PSObject.Properties.Name -contains 'request_hash' -or
    $p0Created.Data.PSObject.Properties.Name -contains 'idempotency_key') {
    throw 'API-05 leaked internal fields'
}
Write-Host "Draft ID: $p0DraftId"

$p0Own = Invoke-P0Api -Path "/expenses/$p0DraftId" -Session $p0EmployeeA
Assert-P0Status $p0Own 200 'API-06 own detail'

$p0Retry = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload -Key $p0Key
Assert-P0Status $p0Retry 201 'API-07 retry'
if ($p0Retry.Data.id -ne $p0DraftId) { throw 'API-07 duplicate draft created' }

$p0Changed = $p0Payload.Clone()
$p0Changed.title = 'P0-同键修改内容'
$p0Conflict = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Changed -Key $p0Key
Assert-P0Status $p0Conflict 409 'API-08 idempotency conflict'
```

当前接口对幂等重试也返回 201；本次按现有协议断言，不把“重试应该返回 200”当成既定要求。相同内容换新幂等键是新操作，不属于重复请求，可能合法创建另一张。

### 5.3 跨员工、财务私有草稿、Origin 与字段注入

```powershell
$p0Other = Invoke-P0Api -Path "/expenses/$p0DraftId" -Session $p0EmployeeB
Assert-P0Status $p0Other 404 'API-09 other employee detail'
$p0FinancePrivate = Invoke-P0Api -Path "/expenses/$p0DraftId" -Session $p0Finance
Assert-P0Status $p0FinancePrivate 404 'API-10 finance private draft'

foreach ($p0Reader in @($p0EmployeeB, $p0Finance)) {
    $p0List = Invoke-P0Api -Path '/expenses?limit=100&offset=0' -Session $p0Reader
    Assert-P0Status $p0List 200 'API-11 filtered list'
    if (@($p0List.Data.items | Where-Object { $_.id -eq $p0DraftId }).Count -ne 0) {
        throw 'API-11 private draft leaked through list'
    }
}

$p0NoKey = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload
Assert-P0Status $p0NoKey 400 'API-12 missing idempotency key'
$p0BadKey = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload -Key 'not-a-uuid'
Assert-P0Status $p0BadKey 400 'API-13 invalid idempotency key'
$p0BadOrigin = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload -Key ([guid]::NewGuid().ToString()) -Origin 'https://untrusted.example'
Assert-P0Status $p0BadOrigin 403 'API-14 untrusted origin'
$p0NoOrigin = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Payload -Key ([guid]::NewGuid().ToString()) -OmitOrigin
Assert-P0Status $p0NoOrigin 403 'API-15 missing origin'

foreach ($p0Field in @('tenant_id', 'applicant_id', 'status', 'total_fen')) {
    $p0Injected = $p0Payload.Clone()
    $p0Injected[$p0Field] = switch ($p0Field) {
        'status' { 'approved' }
        'total_fen' { 1 }
        default { [guid]::NewGuid().ToString() }
    }
    $p0Rejected = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0Injected -Key ([guid]::NewGuid().ToString())
    Assert-P0Status $p0Rejected 400 "API-16 reject $p0Field"
}
```

列表检查只针对本次创建的单号；不要断言演示环境所有列表永远只有固定条数。这里读取的是最新 100 条，在本地验收期间应包含刚创建的数据；更大数据量需分页检查。

### 5.4 金额、业务上限、错误结构与退出

```powershell
foreach ($p0Amount in @(0, -1, 1.5, '100', $true, 100000001)) {
    $p0BadMoney = $p0Payload | ConvertTo-Json -Depth 10 | ConvertFrom-Json -AsHashtable
    $p0BadMoney.items[0].amount_fen = $p0Amount
    $p0Rejected = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0BadMoney -Key ([guid]::NewGuid().ToString())
    Assert-P0Status $p0Rejected 400 "API-17 invalid amount $p0Amount"
    if (-not $p0Rejected.Data.code -or -not $p0Rejected.Data.message -or -not $p0Rejected.Data.requestId) {
        throw 'API-17 incomplete error response'
    }
}

$p0OverTotal = $p0Payload | ConvertTo-Json -Depth 10 | ConvertFrom-Json -AsHashtable
$p0OverTotal.items[0].amount_fen = 100000000
$p0OverTotal.items[1].amount_fen = 1
$p0Rejected = Invoke-P0Api -Method POST -Path '/expenses' -Session $p0EmployeeA -Body $p0OverTotal -Key ([guid]::NewGuid().ToString())
Assert-P0Status $p0Rejected 422 'API-18 total exceeds domain limit'

$p0BadQuery = Invoke-P0Api -Path '/expenses?limit=0' -Session $p0EmployeeA
Assert-P0Status $p0BadQuery 400 'API-19 invalid pagination'
$p0BadLogin = Invoke-P0Api -Method POST -Path '/auth/demo-login' -Session $p0EmployeeB -Body @{ account_key = 'employee_b'; role = 'finance' }
Assert-P0Status $p0BadLogin 400 'API-20 login role injection'
$p0Logout = Invoke-P0Api -Method POST -Path '/auth/logout' -Session $p0EmployeeA
Assert-P0Status $p0Logout 204 'API-21 logout'
$p0AfterLogout = Invoke-P0Api -Path '/me' -Session $p0EmployeeA
Assert-P0Status $p0AfterLogout 401 'API-22 logged out session'
```

关键区别：单项格式错误在 BFF DTO 阶段通常返回 400；单项都合法但总额超限由 Python 领域服务返回 422；直测 FastAPI schema 的现有 pytest 用例通常预期 422。404 对无记录和无权访问统一处理，以减少 ID 探测。

### 5.5 验证数据库副作用与审计

以下使用容器当前配置的用户名与库名，仅取非敏感变量。命令执行前确认当前 Compose 项目就是本地演示环境。

```powershell
$p0DbUser = (docker compose --env-file .env -f infra/compose.yaml exec -T postgres printenv POSTGRES_USER).Trim()
$p0DemoDb = (docker compose --env-file .env -f infra/compose.yaml exec -T postgres printenv POSTGRES_DB).Trim()
$p0AuditSql = @'
SELECT e.id, e.title, e.applicant_id, e.total_fen, e.status,
       COUNT(DISTINCT i.id) AS item_count,
       COUNT(DISTINCT a.id) AS create_audit_count
FROM expenses e
LEFT JOIN expense_items i ON i.expense_id = e.id
LEFT JOIN audit_logs a ON a.target_id = e.id AND a.action = 'expense.draft_created'
WHERE e.title LIKE 'P0-%'
GROUP BY e.id, e.title, e.applicant_id, e.total_fen, e.status
ORDER BY e.title;
'@
docker compose --env-file .env -f infra/compose.yaml exec -T postgres psql -U $p0DbUser -d $p0DemoDb -v ON_ERROR_STOP=1 -c $p0AuditSql
```

API-05 的单号应只有一张主单、两条明细、一个创建审计，总额 30050，状态 draft，申请人小林；LEFT JOIN 也会显示缺少明细或审计的主单，计数为 0 时判失败。

再次执行前面的计数查询：

```powershell
docker compose --env-file .env -f infra/compose.yaml exec -T postgres psql -U $p0DbUser -d $p0DemoDb -v ON_ERROR_STOP=1 -c $p0CountSql
```

相比第 5 节开始时，预期主单 +1、明细 +2、审计 +1；测试期间没有其他写入时，额外增长代表重复或被拒请求产生副作用，应判失败。

第 5 节单独执行一次时应只新增一张草稿；第 4 节会另外创建浏览器草稿。比较执行前后主单 / 审计数量，以确认被拒请求没有写入。已有 `test_create_totals_and_audit_are_atomic` 检查成功路径的审计；要证明中途失败全部回滚，还需第 9 节的故障注入用例。

## 6. PostgreSQL 专项测试

### 6.1 创建独立测试库

使用与第 3 节相同的 postgres 容器，创建独立数据库 `enterprise_agent_p0_test`。不要修改正式 `DATABASE_URL`，不要把测试 schema 建在生产数据库。

```powershell
$p0DbUser = (docker compose --env-file .env -f infra/compose.yaml exec -T postgres printenv POSTGRES_USER).Trim()
docker compose --env-file .env -f infra/compose.yaml exec -T postgres psql -U $p0DbUser -d postgres -tAc "SELECT datname FROM pg_database WHERE datname = 'enterprise_agent_p0_test'"
```

若上一步输出为空，运行以下命令；已存在时跳过创建，不删除重建：

```powershell
docker compose --env-file .env -f infra/compose.yaml exec -T postgres createdb -U $p0DbUser enterprise_agent_p0_test
```

预期创建成功，或查询确认数据库已经存在。role 必须有连接和创建测试 schema 的权限；本地默认 postgres 用户具备这些能力。

### 6.2 安全传入测试连接，运行现有专项

下面从当前后端配置读取连接信息，仅在子进程中把数据库名改为测试库，不打印密码、不把连接串写入仓库，也不更改 `.env`。如果当前 shell 有覆盖 `.env` 的 `DATABASE_URL`，先确认它指向本地容器；自建 PostgreSQL 用户应替换第 6.1 节的建库方式。

```powershell
@'
import os
import subprocess
import sys
from sqlalchemy.engine import make_url
from enterprise_agent.config import get_settings

url = make_url(get_settings().database_url)
if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1"}:
    raise SystemExit("P0 expects a local PostgreSQL URL; inspect configuration first")
env = os.environ.copy()
env["TEST_POSTGRES_URL"] = url.set(database="enterprise_agent_p0_test").render_as_string(hide_password=False)
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_postgres_integration.py", "-vv", "-rs"],
    env=env,
)
raise SystemExit(result.returncode)
'@ | uv run --locked --directory services/backend python -
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL专项失败，检查输出并记录阻塞' }
```

预期该文件的用例均 PASSED，不允许记录为 SKIPPED。P1 新增并发确认专项后为两项。原有 `test_postgres_migration_rules_and_idempotency` 实际验证：

1. 创建随机 `test_<uuid>` schema，并执行真实 Alembic 迁移。
2. `compare_metadata` 没有检测到 ORM 与迁移结构差异。
3. 种子可使用，合法创建后总额为 303 分。
4. 相同用户、相同键、相同内容的顺序重试返回同一单号。
5. 另一员工读取该草稿被拒（服务抛出 404）。
6. 最后清理本次随机 schema。

P1 的 `test_agent_parallel_confirmation_creates_one_expense` 另验证两个独立 PostgreSQL Session 并发确认只创建一张主单和一条审计。各种数据库约束的逐项触发、不同租户在 PostgreSQL 上完整隔离和财务事务中途失败回滚仍需补测；不能外推成这些能力已验收。

### 6.3 检查 schema 清理

```powershell
docker compose --env-file .env -f infra/compose.yaml exec -T postgres psql -U $p0DbUser -d enterprise_agent_p0_test -c "SELECT schema_name FROM information_schema.schemata WHERE schema_name ~ '^test_[0-9a-f]{32}$';"
```

没有其他并发测试时预期没有测试 schema。若残留先记录可能的中断 / 清理失败，确认归属；不要批量删除匹配到的 schema。

## 7. 核验 OpenAPI 与 BFF 类型生成

生成命令是磁盘文件生成，不要求启动三端，也不会完成业务授权验收。

```powershell
pnpm contracts:generate
if ($LASTEXITCODE -ne 0) { throw '协议生成失败' }
git diff --stat -- contracts/backend.openapi.json apps/bff/src/backend/backend.schema.ts
git diff -- contracts/backend.openapi.json apps/bff/src/backend/backend.schema.ts
pnpm typecheck
```

检查内容：创建请求引用 `CreateExpenseRequest`；成功响应引用 `ExpenseResponse`；409 等错误引用 `ErrorResponse`；`amount_fen` 为 integer；敏感内部字段不进入响应 schema；BFF 使用生成类型而非跨应用源码导入。已有后端测试会检查部分 schema 引用。

第一次生成出现差异不必然是失败：可能是旧快照或生成器版本差异。审查、修复并保留应提交的快照；类型检查必须通过。仅有“generated”文件头不能证明当前快照和代码一致。

验证生成稳定性，不依赖工作区是否干净：

```powershell
$p0ContractFiles = @('contracts/backend.openapi.json', 'apps/bff/src/backend/backend.schema.ts')
$p0ContractHashes = @{}
foreach ($p0File in $p0ContractFiles) { $p0ContractHashes[$p0File] = (Get-FileHash -LiteralPath $p0File -Algorithm SHA256).Hash }
pnpm contracts:generate
if ($LASTEXITCODE -ne 0) { throw '第二次协议生成失败' }
foreach ($p0File in $p0ContractFiles) {
    if ((Get-FileHash -LiteralPath $p0File -Algorithm SHA256).Hash -ne $p0ContractHashes[$p0File]) {
        throw "协议重复生成不稳定：$p0File"
    }
}
pnpm format:check
```

预期两次生成文件 hash 一致；生成后格式检查和 typecheck 通过。若生成器原始输出与 Prettier 不一致，统一生成流水线中的格式化步骤，再验证稳定性，不能靠每次手工格式化。

CI 中还需在干净 checkout 比较快照漂移：生成完成后执行 `git diff --exit-code -- contracts/backend.openapi.json apps/bff/src/backend/backend.schema.ts`。本地有已知待提交更新时，这个命令失败不等于生成器不稳定。

## 8. CI 与 Docker 锁定安装：修改及验证

### 8.1 CI 需要修改什么

在 `.github/workflows/ci.yml` 中：

1. `pnpm install --no-frozen-lockfile` 改成 `pnpm install --frozen-lockfile`。
2. `uv sync --directory services/backend` 改成 `uv sync --locked --directory services/backend`。
3. 增加 `pnpm format:check`。
4. 保留当前 PostgreSQL service 和 `TEST_POSTGRES_URL`；输出中专项必须 PASSED。
5. 生成协议后增加快照 diff 门禁，再执行 typecheck。
6. 在独立作业或合适的门禁中构建三份镜像；Node / Python 的普通 build 不能替代镜像构建。

核心命令应包含：

```yaml
- run: pnpm install --frozen-lockfile
- run: uv sync --locked --directory services/backend
- run: pnpm format:check
# 其余 lint / typecheck / test / build 按现有工作流保留
- run: pnpm contracts:generate
- run: git diff --exit-code -- contracts/backend.openapi.json apps/bff/src/backend/backend.schema.ts
- run: pnpm typecheck
```

验收：干净 checkout 的 CI 全绿；锁文件 / manifest 不一致会在安装阶段失败；业务 schema 改动而忘记提交协议快照时会在 diff 门禁失败。这两个反例在临时分支 / 隔离 worktree 验证，验证后保留或丢弃相应测试改动，不在当前工作区手工删除锁文件。

若提交到远端后才触发 CI，实际提交和推送按项目协作约定进行；本指南没有执行这些操作。

### 8.2 Docker 改造中的关键约束

当前 web / BFF Dockerfile 的 `npm install` 不读取 `pnpm-lock.yaml`；backend 的 `pip install .` 不读取 `uv.lock`。仅复制锁文件而仍使用这些命令，不算完成锁定安装。

| 应用         | 推荐修改方向                                                                                                                   | 验收重点                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| web / BFF    | 使用固定 pnpm 10.15.1，从仓库 workspace 安装，执行 `pnpm install --frozen-lockfile`，再按 filter 独立 build                    | root manifest、workspace 文件、相关 app manifest 和根锁文件在 context 内；不用 npm 重新解析 |
| backend      | 固定 uv 版本；复制 `pyproject.toml`、`uv.lock` 和项目源文件，执行 `uv sync --locked --no-dev` 或等效的受锁约束流程             | 启动使用已安装的虚拟环境，不在启动时隐式重新解析依赖；editable 安装路径在运行镜像中仍有效   |
| 构建 context | 推荐 web / BFF 的 Compose build context 改为仓库根 `..`，用 `dockerfile: apps/web/Dockerfile` / `apps/bff/Dockerfile` 指定文件 | Docker `COPY` 不能越过 context；不能在 app context 内 `COPY ../../pnpm-lock.yaml`           |
| 运行镜像     | web 仅静态产物；BFF 保留正确的 pnpm 依赖与相对链接布局，后端保留运行依赖；非 root 运行                                         | 不能只复制 app 的 `node_modules` 丢失根 `.pnpm` store，也不能把宿主机 `.venv` 打包进镜像    |
| context 排除 | 检查新的根 `.dockerignore`，排除 `.env`、`.git`、node_modules、.venv 等                                                        | 使用根 context 后，不能以为 app 下的普通 `.dockerignore` 会自动生效；不复制测试凭据         |

锁定依赖解析不等于镜像逐字节相同。基础镜像 / uv 安装器的版本或 digest、平台差异及供应链检查是后续生产门槛；本阶段至少固定工具版本并验证锁文件确实被使用。

### 8.3 构建和全容器验收

先停止第 3 节的宿主机三个 dev 进程，释放 5173、3000、8000；保留 postgres 容器与数据卷。

```powershell
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml build --no-cache backend bff web
docker compose --env-file .env -f infra/compose.yaml run --rm backend alembic upgrade head
docker compose --env-file .env -f infra/compose.yaml run --rm backend python -m enterprise_agent.db.seed
docker compose --env-file .env -f infra/compose.yaml up -d --wait
docker compose --env-file .env -f infra/compose.yaml ps
```

使用 `config --quiet` 验证结构，不把含插值密钥的完整 Compose 配置写到日志。首次无缓存构建验证真实安装路径；通过后日常可以使用普通缓存构建，不反复执行无必要的无缓存构建。

预期：构建日志使用 frozen / locked 安装；三份镜像构建成功；容器稳定运行；backend healthy；没有重启循环。web / BFF 没有 healthcheck 时 `--wait` 只保证它们运行，并不能证明 HTTP 就绪，需要再次运行第 3 节探针和第 4 / 5 节核心用例。

重点复测 UI-01、UI-02、UI-03、API-07 和 API-16；确认前端 nginx 代理、BFF 服务发现、Python 数据库连接与 pnpm 运行依赖都正常。

```powershell
docker compose --env-file .env -f infra/compose.yaml restart bff
docker compose --env-file .env -f infra/compose.yaml restart backend
```

按顺序分别等待 ready，复测会话失效和草稿持久化。不要同时重启两个服务后推断到底哪次重启影响了会话。不执行 `down -v`。

## 9. 用例清单：已有覆盖与待补自动化

下表“现有”仅指代码中有对应测试，不表示本轮已运行通过。

| ID          | 场景与预期                                                        | 当前覆盖 / 执行方式                                                                                            |
| ----------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| AUTH-01     | 匿名请求 401；退出后旧会话不可用                                  | 现有 BFF 测试；API-01 / 21 / 22                                                                                |
| AUTH-02     | 伪造 / 过期 / 错 scope JWT 被拒                                   | 现有 `test_missing_forged_expired_and_wrong_scope_tokens_are_rejected`，在隔离 pytest 运行，不手工暴露内部密钥 |
| AUTH-03     | 登录不能注入 role；POST 缺少 / 错误 Origin 被拒                   | 现有 BFF 部分覆盖；API-14 / 15 / 20                                                                            |
| AUTH-04     | 本人可读；同企业其他员工 / 财务不能读私人草稿                     | 现有 Python 测试 + UI-02 / 03 + API-09 / 10                                                                    |
| AUTH-05     | 不同企业财务无法读取 / 列出草稿                                   | 现有 SQLite `test_cross_tenant_finance_cannot_access_expense`；真实 PostgreSQL 版本待补                        |
| MONEY-01    | 两项 28600 + 1450 = 30050，申请人来自当前身份                     | UI-01 / API-05；现有 Python 合计用例使用 101 + 202 = 303                                                       |
| MONEY-02    | 0、负数、浮点、字符串、bool、超限单项被拒，无副作用               | 现有测试部分覆盖 + API-17；直接比较主单 / 审计计数的断言待加强                                                 |
| MONEY-03    | 单项合法、合计超限仍被领域服务拒绝                                | 现有 Python 用例 + API-18                                                                                      |
| INPUT-01    | 拒绝 tenant / applicant / status / total 等权威字段               | 现有 Python 测试覆盖部分字段；API-16 覆盖四项，tenant 注入自动化待补                                           |
| IDEM-01     | 同键同内容返回同一 ID，审计不新增                                 | 现有顺序重试测试 + API-07 / SQL 核对；审计重复断言待加强                                                       |
| IDEM-02     | 同键不同内容 409，原记录不改变                                    | 现有 Python 测试 + API-08；补强原记录内容与计数断言                                                            |
| IDEM-03     | 两个独立 DB Session 同时以同键创建，最终一张主单及一条创建审计    | P1 已新增真实 PostgreSQL Agent 并发确认测试；直接财务 API 并发场景可补，不能用同一 Session 模拟                |
| IDEM-04     | 相同用户的稳定键重试可恢复提交成功但响应丢失的结果                | P1 已覆盖财务提交后响应丢失的原确认重试；其他财务接口故障点可继续补充                                          |
| TX-01       | 主单 / 明细 flush 后、审计 commit 前注入异常，回滚后全部不新增    | 当前未覆盖失败路径；隔离测试中注入异常并关闭 / 回滚 Session，再用新 Session 验证                               |
| DB-01       | 迁移与 ORM 对齐、重复种子不制造重复、测试 schema 清理             | 现有专项 + SQLite seed 用例；第 6 节执行                                                                       |
| DB-02       | 绕过 HTTP 直接构造跨租户外键、非法金额 / 状态记录时 DB 拒绝并回滚 | 当前专项未逐项覆盖；仅在随机测试 schema 中补约束用例，不在演示库直接写非法数据                                 |
| CONTRACT-01 | schema 正确、两次生成稳定、BFF 编译通过、CI 能拦截漂移            | 现有 schema 引用测试 + 第 7 节；CI diff 门禁待改                                                               |
| BUILD-01    | 冻结安装不改锁文件；manifest 漂移导致安装失败                     | 第 2 节；CI / Docker 改造后在隔离分支验证反例                                                                  |
| BUILD-02    | 三镜像构建 / 启动成功，容器真实链路仍能创建和拦截越权             | 第 8 节；当前未完成本轮验收                                                                                    |
| PERSIST-01  | BFF 重启需重新登录、草稿保留；单独 Python 重启不清会话            | UI-05 / 06；自动化待补                                                                                         |

后续编写待补用例时复用正式 finance 服务、真实迁移和隔离数据库，不用替身财务服务来证明事务和幂等。跨租户测试身份只能在隔离测试库建立，不为验收增加可伪造租户的公开 API。

## 10. 常见失败及排查

| 现象                                 | 优先排查                                                                    | 记录分类                               |
| ------------------------------------ | --------------------------------------------------------------------------- | -------------------------------------- |
| Docker client 可用但 server 不可用   | Docker Desktop 未启动 / daemon 不可访问                                     | 环境阻塞                               |
| 55432 无法绑定                       | 实际端口占用；同步检查 POSTGRES_HOST_PORT 和 DATABASE_URL，不停止无关数据库 | 环境配置                               |
| ready 503                            | PostgreSQL healthy、连接配置、后端进程；日志脱敏查看                        | 依赖 / 配置或代码缺陷，依据证据分类    |
| 登录 503                             | 种子未运行或演示登录未显式启用                                              | 初始化 / 配置                          |
| 写接口全部 403                       | localhost / 127.0.0.1 混用，或缺少正确 Origin                               | 测试请求配置；不能直接关掉 OriginGuard |
| PowerShell 不认识 SkipHttpErrorCheck | 当前是 Windows PowerShell 5.1                                               | 换 PowerShell 7 运行第 5 节            |
| PG 用例 SKIPPED                      | TEST_POSTGRES_URL 未传给真正执行 pytest 的进程                              | 未验收，按第 6 节重跑                  |
| 创建 schema permission denied        | 连接角色没有测试库 schema 创建权限                                          | 测试环境权限阻塞                       |
| frozen / locked 报不一致             | manifest 与锁文件不匹配                                                     | 依赖一致性失败，审查后修复             |
| Docker COPY 找不到根锁文件           | app context 看不到根文件                                                    | Docker context 实施缺陷                |
| 镜像 build 成功但 BFF 起不来         | pnpm 链接 / store 未完整复制，dist 入口或运行依赖缺失                       | 运行镜像缺陷                           |
| 类型检查过但接口返回不对             | TS 类型不是运行时校验                                                       | 真实链路缺陷，记录实际响应并脱敏       |
| CI 生成协议后有 diff                 | 快照过期、生成版本 / 格式未统一                                             | 契约门禁正常拦截；核对后提交正确产物   |

## 11. 怎样记录到主计划

每次记录至少包括：日期、commit / 工作区变更、环境版本、用例 ID、执行命令、预期 / 实际、结果、证据位置、阻塞 / 修复与下一步。

模板（以下是占位，不是已执行结果）：

| 日期 | 用例 / 命令               | 预期               | 实际     | 状态   | 证据 / 阻塞  | 下一步           |
| ---- | ------------------------- | ------------------ | -------- | ------ | ------------ | ---------------- |
| 待填 | ENV-05 / `pnpm typecheck` | 退出码 0           | 待执行   | 待开始 | 待填         | 执行并记录       |
| 待填 | UI-02                     | 小陈读小林草稿 404 | 待执行   | 待开始 | 待填         | 浏览器验证       |
| 待填 | 第 6 节 PostgreSQL 专项   | PASSED，无 skip    | 待执行   | 待开始 | 待填         | 运行独立测试库   |
| 待填 | IDEM-03 / TX-01           | 并发幂等、失败回滚 | 待补用例 | 待开始 | 现有覆盖不足 | 实现隔离 PG 测试 |

将实际结果放到独立的 `docs/p0-results.md`（执行时再创建），主计划 P0 中链接该报告并勾选完成项。仅指南已编写时不勾选业务验收项。日志可保存在被忽略的 `work/p0/`，不要把 Cookie、JWT、`.env` 或带密码 URL 写入报告。

P0 完成标准：基础检查通过，真实三端草稿与权限通过，PostgreSQL 专项实际运行，关键并发 / 回滚测试补齐并通过，协议稳定且 CI 能发现漂移，CI / Docker 确实锁定安装且容器链路通过。若全容器或其他项受环境阻塞，逐项列明，P0 保持进行中或受阻，不写成“全部完成”。
