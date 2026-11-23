"""验证 HTTP 输入输出、幂等写入及跨用户和跨租户权限。"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from enterprise_agent.db.models import AuditLog, Tenant, User

from conftest import headers, token


def test_create_totals_and_audit_are_atomic(client, db, payload):
    """创建成功后主单、费用合计和审计记录均已提交。"""
    before = db.scalar(select(func.count()).select_from(AuditLog))
    response = client.post("/api/v1/expenses", json=payload, headers=headers())
    assert response.status_code == 201
    body = response.json()
    assert body["total_fen"] == 303
    assert body["status"] == "draft"
    assert len(body["items"]) == 2
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before + 1


@pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True])
def test_invalid_money_is_rejected(client, payload, amount):
    """HTTP schema 拒绝非正数、浮点数、字符串及 bool 金额。"""
    payload["items"][0]["amount_fen"] = amount
    response = client.post("/api/v1/expenses", json=payload, headers=headers())
    assert response.status_code == 422
    assert response.json()["code"] == "HTTP_422"
    assert response.json()["details"]


@pytest.mark.parametrize(
    "field,value",
    [("total_fen", 1), ("status", "approved"), ("applicant_id", str(uuid4()))],
)
def test_client_cannot_assign_authoritative_fields(client, payload, field, value):
    """请求 schema 拒绝客户端自行指定总额、状态和申请人。"""
    payload[field] = value
    assert client.post("/api/v1/expenses", json=payload, headers=headers()).status_code == 422


def test_sum_limit_is_checked(client, payload):
    """即使每项金额有效，财务服务也会检查整张申请的总额上限。"""
    for item in payload["items"]:
        item["amount_fen"] = 100_000_000
    assert client.post("/api/v1/expenses", json=payload, headers=headers()).status_code == 422


def test_retry_is_idempotent_and_changed_payload_conflicts(client, payload):
    """相同幂等键和内容复用结果，相同键不同内容返回冲突。"""
    auth = headers()
    first = client.post("/api/v1/expenses", json=payload, headers=auth)
    retry = client.post("/api/v1/expenses", json=payload, headers=auth)
    assert first.json()["id"] == retry.json()["id"]
    payload["title"] = "修改内容"
    assert client.post("/api/v1/expenses", json=payload, headers=auth).status_code == 409


def test_other_employee_and_finance_cannot_read_private_draft(client, payload):
    """员工本人能读取草稿；其他员工和财务不能读取私人草稿。"""
    expense_id = client.post("/api/v1/expenses", json=payload, headers=headers()).json()["id"]
    for account in ["employee_b", "finance"]:
        response = client.get(f"/api/v1/expenses/{expense_id}", headers=headers(account))
        assert response.status_code == 404
    assert client.get(f"/api/v1/expenses/{expense_id}", headers=headers()).status_code == 200


def test_finance_sees_submitted_fixture_only(client):
    """响应 schema 返回初始化的已提交样例，不暴露其他员工草稿。"""
    response = client.get("/api/v1/expenses", headers=headers("finance"))
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["status"] == "pending"


def test_cross_tenant_finance_cannot_access_expense(client, db, payload):
    """即使角色同为财务，不同企业之间仍按 tenant_id 隔离。"""
    tenant = Tenant(id=uuid4(), name="另一个租户")
    db.add(tenant)
    db.flush()
    stranger = User(id=uuid4(), tenant_id=tenant.id, name="外部财务", role="finance")
    db.add(stranger)
    db.commit()
    expense_id = client.post("/api/v1/expenses", json=payload, headers=headers()).json()["id"]
    auth = {"Authorization": f"Bearer {token(str(stranger.id))}"}
    response = client.get(f"/api/v1/expenses/{expense_id}", headers=auth)
    assert response.status_code == 404
    assert client.get("/api/v1/expenses", headers=auth).json()["items"] == []


def test_missing_forged_expired_and_wrong_scope_tokens_are_rejected(client):
    """缺少、伪造、过期或 scope 不匹配的令牌不能读取业务数据。"""
    subject = "20000000-0000-4000-8000-000000000001"
    assert client.get("/api/v1/expenses").status_code == 401
    for bearer_token in [token(subject, secret="wrong-secret" * 4), token(subject, expired=True)]:
        response = client.get(
            "/api/v1/expenses", headers={"Authorization": f"Bearer {bearer_token}"}
        )
        assert response.status_code == 401
    demo = {"Authorization": f"Bearer {token('demo-bootstrap', scope='demo')}"}
    assert client.get("/api/v1/expenses", headers=demo).status_code == 403


def test_seed_is_repeatable(db):
    """重复运行种子函数不重复生成审计或业务记录。"""
    from enterprise_agent.db.seed import seed

    before = db.scalar(select(func.count()).select_from(AuditLog))
    seed(db)
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_expense_list_query_is_validated(client, query):
    """分页 Query schema 限定页大小和偏移量，并返回统一的参数错误结构。"""
    response = client.get(f"/api/v1/expenses?{query}", headers=headers())
    assert response.status_code == 422
    assert response.json()["code"] == "HTTP_422"
    assert response.json()["details"]


def test_openapi_declares_request_response_and_error_schemas(client):
    """OpenAPI 将创建请求、成功响应和常见错误映射到独立 Pydantic schema。"""
    openapi = client.get("/openapi.json").json()
    operation = openapi["paths"]["/api/v1/expenses"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/CreateExpenseRequest"
    )
    assert operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ExpenseResponse"
    )
    assert operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ErrorResponse"
    )


def test_expense_response_does_not_expose_internal_fields(client, payload):
    """公开响应只包含声明的字段，不泄漏幂等键或内部请求摘要。"""
    response = client.post("/api/v1/expenses", json=payload, headers=headers())
    assert response.status_code == 201
    assert "request_hash" not in response.json()
    assert "idempotency_key" not in response.json()
