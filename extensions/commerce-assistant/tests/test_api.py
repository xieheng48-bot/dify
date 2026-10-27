from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest

from commerce_app.api import create_app
from commerce_app.config import Config
from commerce_app import dify


NOW = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
ALICE = {"Authorization": "Bearer demo-alice"}
BOB = {"Authorization": "Bearer demo-bob"}
TOOLS = {"Authorization": "Bearer demo-dify-tools"}


@pytest.fixture
def setup(tmp_path):
    clock = [NOW]
    config = Config(now=lambda: clock[0])
    path = str(tmp_path / "commerce.db")
    app = create_app(path, config)
    return TestClient(app), path, config, clock


def proposal(client):
    response = client.post("/api/returns/proposals", headers=ALICE,
                           json={"order_id": "A1001", "reason": "申请退货"})
    assert response.status_code == 200
    value = response.json()
    assert "confirmation_token" not in value
    preview = client.get("/api/human/proposals/" + value["proposal_id"], headers=ALICE).json()
    return {"proposal_id": preview["proposal_id"], "digest": preview["digest"],
            "confirmation_token": preview["confirmation_token"],
            "confirmed": True, "idempotency_key": "test-request-001"}


def test_no_auth_and_tool_key_cannot_be_user(setup):
    client, *_ = setup
    for headers in ({}, TOOLS, {"Authorization": "Bearer invalid"}):
        response = client.get("/api/orders/A1001", headers=headers)
        assert response.status_code == 401
    assert client.get("/api/audit", headers=TOOLS).status_code == 401


def test_user_cannot_cross_order_boundary_and_denial_audited(setup):
    client, *_ = setup
    assert client.get("/api/orders/A1001", headers=BOB).status_code == 404
    assert client.post("/api/returns/check", headers=BOB, json={"order_id": "A1001"}).status_code == 404
    events = client.get("/api/audit", headers=BOB).json()["items"]
    assert events[0]["outcome"] == "denied"
    assert client.get("/api/audit", headers=ALICE).json()["items"] == []


def test_effective_policy_and_eligibility(setup):
    client, *_ = setup
    data = client.post("/api/returns/check", headers=ALICE, json={"order_id": "A1001"}).json()
    assert data["eligible"] and data["elapsed_days"] == 2
    assert data["citation"]["policy_id"] == "P-E2"
    data = client.post("/api/returns/check", headers=ALICE, json={"order_id": "A1002"}).json()
    assert not data["eligible"] and data["elapsed_days"] == 40
    assert client.post("/api/returns/proposals", headers=ALICE,
                       json={"order_id": "A1002", "reason": "申请退货"}).status_code == 409


def test_dify_schema_contains_only_public_tool_and_schema_is_valid(setup):
    client, *_ = setup
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/tools/policies/search"}
    assert schema["servers"] == [{"url": "http://127.0.0.1:8011"}]
    assert schema["paths"]["/tools/policies/search"]["post"]["security"]
    assert "/api/human/confirm" in client.get("/api/openapi.json").json()["paths"]
    assert client.post("/tools/policies/search", headers=ALICE,
                       json={"category": "electronics", "as_of": "2026-10-25", "question": "退货几天"}).status_code == 401


def test_public_tool_filters_historical_policy_and_category(setup):
    client, *_ = setup
    old = client.post("/tools/policies/search", headers=TOOLS,
                      json={"category": "electronics", "as_of": "2026-09-01", "question": "退货几天"}).json()
    assert old["citations"][0]["policy_id"] == "P-E1"
    current = client.post("/tools/policies/search", headers=TOOLS,
                          json={"category": "apparel", "as_of": "2026-10-25", "question": "退货几天"}).json()
    assert current["citations"][0]["policy_id"] == "P-A1"
    assert "order" not in current


@pytest.mark.parametrize("sql", [
    "DELETE FROM policies WHERE id='P-E2'",
    "UPDATE policies SET effective_to='2026-10-01' WHERE id='P-E2'",
    "UPDATE policies SET effective_to='2099-12-31' WHERE id='P-E1'",
])
def test_missing_expired_or_overlapping_policy_fails_closed(setup, sql):
    client, path, *_ = setup
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
    response = client.post("/api/returns/check", headers=ALICE, json={"order_id": "A1001"})
    assert response.status_code == 409
    assert response.json()["error"] == "policy_unavailable"


def test_human_confirmation_idempotency_and_ticket_access(setup):
    client, path, *_ = setup
    payload = proposal(client)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert client.post("/api/human/confirm", headers=TOOLS, json=payload).status_code == 401
    assert client.post("/api/human/confirm", headers=BOB, json=payload).status_code == 404
    first = client.post("/api/human/confirm", headers=ALICE, json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "awaiting_staff_review"
    assert client.post("/api/human/confirm", headers=ALICE, json=payload).json() == first.json()
    different = {**payload, "confirmed": False}
    assert client.post("/api/human/confirm", headers=ALICE, json=different).json()["error"] == "idempotency_conflict"
    assert client.post("/api/human/confirm", headers=ALICE, json={**payload, "idempotency_key": "second-request"}).json()["error"] == "proposal_consumed"
    ticket_id = first.json()["ticket_id"]
    assert client.get("/api/tickets/" + ticket_id, headers=ALICE).status_code == 200
    assert client.get("/api/tickets/" + ticket_id, headers=BOB).status_code == 404
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit WHERE action='ticket.create'").fetchone()[0] == 1


@pytest.mark.parametrize("changed,code", [
    ({"confirmed": False}, "confirmation_required"),
    ({"confirmation_token": "invalid"}, "invalid_confirmation"),
    ({"digest": "0" * 64}, "approval_mismatch"),
])
def test_confirmation_cannot_be_forged_or_implicit(setup, changed, code):
    client, *_ = setup
    payload = {**proposal(client), **changed}
    assert client.post("/api/human/confirm", headers=ALICE, json=payload).json()["error"] == code


def test_unicode_wrong_confirmation_returns_forbidden(setup):
    client, *_ = setup
    payload = {**proposal(client), "confirmation_token": "确认凭据"}
    response = client.post("/api/human/confirm", headers=ALICE, json=payload)
    assert response.status_code == 403
    assert response.json()["error"] == "invalid_confirmation"


def test_unicode_wrong_tool_token_returns_unauthorized(setup):
    client, *_ = setup
    # HTTP 字段值用原始 UTF-8 bytes 发送，覆盖比较函数遇到 Unicode 的路径。
    response = client.post("/tools/policies/search",
                           headers=[(b"Authorization", "Bearer 错误工具密钥".encode("utf-8"))],
                           json={"category": "electronics", "as_of": "2026-10-25", "question": "退货几天"})
    assert response.status_code == 401


def test_dify_base_url_config_drives_schema(setup):
    _, path, config, _ = setup
    custom = TestClient(create_app(path, replace(config, dify_tool_base_url="http://host.docker.internal:8011")))
    assert custom.get("/openapi.json").json()["servers"] == [{"url": "http://host.docker.internal:8011"}]


@pytest.mark.parametrize("url", ["file:///tmp/tool", "localhost:8011", "https://", "http://a b:8011"])
def test_invalid_dify_base_url_is_rejected(url):
    with pytest.raises(ValueError, match="DIFY_TOOL_BASE_URL"):
        Config(dify_tool_base_url=url)


@pytest.mark.parametrize("sql", [
    "UPDATE orders SET version=version+1 WHERE id='A1001'",
    "UPDATE policies SET return_days=8 WHERE id='P-E2'",
])
def test_stale_approval_rejected(setup, sql):
    client, path, *_ = setup
    payload = proposal(client)
    with sqlite3.connect(path) as conn:
        conn.execute(sql)
    assert client.post("/api/human/confirm", headers=ALICE, json=payload).json()["error"] == "stale_approval"


def test_expired_approval(setup):
    client, _, _, clock = setup
    payload = proposal(client)
    clock[0] += timedelta(minutes=16)
    assert client.post("/api/human/confirm", headers=ALICE, json=payload).json()["error"] == "approval_expired"


def test_two_proposals_cannot_create_two_active_tickets_for_one_order(setup):
    client, path, *_ = setup
    first = proposal(client)
    second = {**proposal(client), "idempotency_key": "another-request-002"}
    assert client.post("/api/human/confirm", headers=ALICE, json=first).status_code == 200
    response = client.post("/api/human/confirm", headers=ALICE, json=second)
    assert response.status_code == 409
    assert response.json()["error"] == "active_ticket_exists"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_sqlite_reopen_keeps_ticket_and_idempotency(setup):
    client, path, config, clock = setup
    payload = proposal(client)
    result = client.post("/api/human/confirm", headers=ALICE, json=payload).json()
    clock[0] += timedelta(days=10)
    restarted = TestClient(create_app(path, config))
    assert restarted.get("/api/tickets/" + result["ticket_id"], headers=ALICE).status_code == 200
    assert restarted.post("/api/human/confirm", headers=ALICE, json=payload).json() == result
    assert restarted.get("/api/orders/A1001", headers=ALICE).json()["delivered_on"] == "2026-10-25"


def test_offline_assistant_truthfully_labels_no_llm(setup):
    client, *_ = setup
    data = client.post("/api/assistant", headers=ALICE,
                       json={"order_id": "A1001", "question": "可以退货吗"}).json()
    assert data["mode"] == "offline_rules"
    assert "未创建" in data["note"]


@pytest.mark.parametrize("failure,expected", [
    (httpx.ReadTimeout("timeout"), "dify_timeout"),
    (httpx.Response(401, request=httpx.Request("POST", "http://test/v1/chat-messages")), "dify_error"),
    (httpx.Response(200, json={"answer": ""}, request=httpx.Request("POST", "http://test/v1/chat-messages")), "dify_error"),
])
def test_dify_failures_are_not_reported_as_success(setup, monkeypatch, failure, expected):
    _, path, config, _ = setup
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            if isinstance(failure, Exception):
                raise failure
            return failure
    monkeypatch.setattr(dify.httpx, "AsyncClient", FakeClient)
    client = TestClient(create_app(path, replace(config, dify_api_base="http://test/v1", dify_api_key="test-key")))
    response = client.post("/api/assistant", headers=ALICE,
                           json={"order_id": "A1001", "question": "可以退货吗"})
    assert response.status_code in (502, 504)
    assert response.json()["error"] == expected


def test_dashboard_and_injection_shaped_order_id(setup):
    client, *_ = setup
    assert "人工确认" in client.get("/").text
    response = client.post("/api/returns/check", headers=ALICE, json={"order_id": "' OR 1=1 --"})
    assert response.status_code == 404
