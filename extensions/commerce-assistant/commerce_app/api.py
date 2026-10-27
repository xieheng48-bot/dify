from datetime import date
import os
from pathlib import Path
import secrets
from typing import Literal

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .config import Config
from .db import Database
from .dify import chat
from .service import BusinessError, CommerceService


class SearchRequest(BaseModel):
    category: Literal["electronics", "apparel"]
    as_of: date = Field(description="需要查询的政策日期；核验订单时应使用签收日期")
    question: str = Field(min_length=1, max_length=1000)


class OrderRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=80)


class ProposalRequest(OrderRequest):
    reason: str = Field(min_length=2, max_length=500)


class ConfirmRequest(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=80)
    digest: str = Field(min_length=64, max_length=64)
    confirmation_token: str = Field(min_length=1, max_length=100)
    confirmed: bool
    idempotency_key: str = Field(min_length=8, max_length=100)


class ChatRequest(OrderRequest):
    question: str = Field(min_length=1, max_length=1000)


def create_app(db_path: str | None = None, config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    if config.dify_tool_token in config.tokens:
        raise ValueError("工具密钥不得复用用户凭据")
    service = CommerceService(Database(db_path or os.getenv("COMMERCE_DB", "commerce.db"), config), config)
    app = FastAPI(title="电商售后工具服务", version="0.1.0")
    app.state.service = service
    bearer = HTTPBearer(auto_error=False)

    def user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        identity = config.tokens.get(credentials.credentials) if credentials else None
        if identity is None:
            raise BusinessError(401, "unauthorized", "请使用有效的用户 Bearer token")
        return identity

    def tool_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not credentials or not secrets.compare_digest(
            credentials.credentials.encode("utf-8"), config.dify_tool_token.encode("utf-8")
        ):
            raise BusinessError(401, "unauthorized", "Dify 工具密钥无效")

    @app.exception_handler(BusinessError)
    async def handle_error(request, exc: BusinessError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message}, headers=headers)

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "mode": "dify_chatflow" if config.dify_api_base else "offline_rules"}

    @app.post("/tools/policies/search", operation_id="search_effective_policy",
              summary="按品类及日期检索公开演示政策", dependencies=[Depends(tool_auth)])
    def policy_search(body: SearchRequest):
        """只读知识工具，不识别用户，也不访问任何订单或工单。"""
        return service.public_policies(body.category, body.as_of, body.question)

    @app.get("/api/orders/{order_id}", tags=["用户业务"])
    def get_order(order_id: str, identity: str = Depends(user)):
        return service.order(identity, order_id)

    @app.post("/api/returns/check", tags=["用户业务"])
    def check(body: OrderRequest, identity: str = Depends(user)):
        return service.eligibility(identity, body.order_id)

    @app.post("/api/returns/proposals", tags=["用户业务"])
    def propose(body: ProposalRequest, identity: str = Depends(user)):
        return service.propose(identity, body.order_id, body.reason)

    @app.get("/api/human/proposals/{proposal_id}", tags=["人工确认"])
    def preview(proposal_id: str, identity: str = Depends(user)):
        return service.human_preview(identity, proposal_id)

    @app.post("/api/human/confirm", tags=["人工确认"])
    def confirm(body: ConfirmRequest, identity: str = Depends(user)):
        return service.confirm(identity, body.model_dump())

    @app.get("/api/tickets/{ticket_id}", tags=["用户业务"])
    def get_ticket(ticket_id: str, identity: str = Depends(user)):
        return service.ticket(identity, ticket_id)

    @app.get("/api/audit", tags=["用户业务"])
    def audit(identity: str = Depends(user)):
        return {"items": service.audits(identity)}

    @app.post("/api/assistant", tags=["解释助手"])
    async def assistant(body: ChatRequest, identity: str = Depends(user)):
        facts = service.eligibility(identity, body.order_id)
        return await chat(config, identity, body.question, facts)

    def full_schema():
        return get_openapi(title=app.title, version=app.version, routes=app.routes)

    @app.get("/api/openapi.json", include_in_schema=False)
    def user_openapi():
        return full_schema()

    @app.get("/api/docs", include_in_schema=False)
    def user_docs():
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="用户 API 文档")

    def tool_schema():
        return get_openapi(title="Dify 公开政策工具", version=app.version,
                           routes=[route for route in app.routes if route.path.startswith("/tools/")],
                           servers=[{"url": config.dify_tool_base_url}])

    app.openapi = tool_schema
    return app


app = create_app()
