"""业务规则独立于路由和大模型；所有写操作由服务端决定。"""
from datetime import date, datetime, timedelta
import hashlib
import json
import secrets
from uuid import uuid4

from .config import Config
from .db import Database


class BusinessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def serialize(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(serialize(value).encode()).hexdigest()


class CommerceService:
    def __init__(self, db: Database, config: Config):
        self.db, self.config = db, config

    def _audit(self, conn, user: str, action: str, resource: str, outcome: str):
        conn.execute("INSERT INTO audit(user_id,action,resource_id,outcome,created_at) VALUES (?,?,?,?,?)",
                     (user, action, resource, outcome, self.config.now().isoformat()))

    def _order(self, conn, user: str, order_id: str) -> dict:
        row = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user)).fetchone()
        if row is None:
            # 同样的错误不暴露订单是否属于其他用户；拒绝记录独立提交。
            raise BusinessError(404, "order_not_found", "订单不存在或不属于当前用户")
        return dict(row)

    def _eligibility(self, conn, user: str, order_id: str) -> dict:
        order = self._order(conn, user, order_id)
        policies = conn.execute(
            "SELECT * FROM policies WHERE category=? AND effective_from<=? AND effective_to>=?",
            (order["category"], order["delivered_on"], order["delivered_on"]),
        ).fetchall()
        if len(policies) != 1:
            raise BusinessError(409, "policy_unavailable", "适用政策缺失或有重叠，请转人工核验")
        policy = dict(policies[0])
        elapsed = (self.config.now().date() - date.fromisoformat(order["delivered_on"])).days
        eligible = order["status"] == "delivered" and 0 <= elapsed <= policy["return_days"]
        return {
            "order": order, "policy": policy, "elapsed_days": elapsed,
            "eligible": eligible,
            "reason": "可提交退货申请，最终由人工审核" if eligible else "订单状态或签收天数不符合退货申请条件",
            "citation": {"policy_id": policy["id"], "version": policy["version"],
                         "title": policy["title"], "effective_from": policy["effective_from"],
                         "effective_to": policy["effective_to"], "text": policy["content"]},
        }

    def order(self, user: str, order_id: str) -> dict:
        with self.db.connect() as conn:
            try:
                result = self._order(conn, user, order_id)
            except BusinessError:
                self._audit(conn, user, "order.read", order_id, "denied")
                # 先提交拒绝日志，再抛出错误，避免回滚掉审计。
                conn.commit()
                raise
            self._audit(conn, user, "order.read", order_id, "allowed")
            return result

    def eligibility(self, user: str, order_id: str) -> dict:
        with self.db.connect() as conn:
            return self._eligibility(conn, user, order_id)

    def public_policies(self, category: str, as_of: date, question: str) -> dict:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM policies WHERE category=? AND effective_from<=? AND effective_to>=?",
                (category, as_of.isoformat(), as_of.isoformat()),
            ).fetchall()
        if len(rows) != 1:
            raise BusinessError(409, "policy_unavailable", "适用政策缺失或有重叠，请转人工核验")
        policy = dict(rows[0])
        return {"retrieval_mode": "category_date_filter_and_lexical_demo", "query": question,
                "category": category, "as_of": as_of.isoformat(),
                "matched_keywords": [w for w in ("退货", "天", "包装", "配件", "签收", "审核")
                                     if w in question and w in policy["content"]],
                "citations": [{"policy_id": policy["id"], "version": policy["version"],
                               "title": policy["title"], "text": policy["content"],
                               "effective_from": policy["effective_from"], "effective_to": policy["effective_to"]}],
                "note": "公开演示政策；仅按品类/日期过滤及关键词匹配，不是向量 RAG。"}

    def propose(self, user: str, order_id: str, reason: str) -> dict:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            facts = self._eligibility(conn, user, order_id)
            if not facts["eligible"]:
                raise BusinessError(409, "return_ineligible", facts["reason"])
            proposal_id = "P-" + uuid4().hex[:16]
            snapshot = self._snapshot(facts, reason)
            expires_at = (self.config.now() + timedelta(minutes=15)).isoformat()
            conn.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)", (
                proposal_id, user, order_id, reason, digest(snapshot), secrets.token_urlsafe(24),
                expires_at, serialize(snapshot), "pending"))
            self._audit(conn, user, "return.propose", proposal_id, "pending")
            return {"proposal_id": proposal_id, "status": "pending_human_confirmation",
                    "expires_at": expires_at, "order_id": order_id, "reason": reason,
                    "citation": facts["citation"], "message": "尚未创建工单，请在人工界面确认。"}

    @staticmethod
    def _snapshot(facts: dict, reason: str) -> dict:
        return {"action": "create_return_ticket", "order": facts["order"],
                "policy": facts["policy"], "reason": reason}

    def human_preview(self, user: str, proposal_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id=? AND user_id=?", (proposal_id, user)).fetchone()
            if row is None:
                raise BusinessError(404, "proposal_not_found", "申请不存在")
            return {"proposal_id": row["id"], "status": row["status"],
                    "snapshot": json.loads(row["snapshot_json"]), "digest": row["digest"],
                    "confirmation_token": row["confirmation_token"], "expires_at": row["expires_at"]}

    def confirm(self, user: str, payload: dict) -> dict:
        request_digest = digest(payload)
        with self.db.connect() as conn:
            # 写事务把核验、幂等查重、创建工单和审计收束到同一提交点。
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM idempotency WHERE user_id=? AND key=?",
                                    (user, payload["idempotency_key"])).fetchone()
            if previous:
                if previous["request_digest"] != request_digest:
                    raise BusinessError(409, "idempotency_conflict", "同一幂等键不能用于不同请求")
                return json.loads(previous["response_json"])
            row = conn.execute("SELECT * FROM proposals WHERE id=? AND user_id=?",
                               (payload["proposal_id"], user)).fetchone()
            if row is None:
                raise BusinessError(404, "proposal_not_found", "申请不存在")
            if payload["confirmed"] is not True:
                raise BusinessError(400, "confirmation_required", "请明确确认本次工单内容")
            if not secrets.compare_digest(row["confirmation_token"].encode("utf-8"),
                                          payload["confirmation_token"].encode("utf-8")):
                raise BusinessError(403, "invalid_confirmation", "人工确认凭据无效")
            if row["digest"] != payload["digest"]:
                raise BusinessError(409, "approval_mismatch", "确认内容与申请不一致")
            if self.config.now() >= datetime.fromisoformat(row["expires_at"]):
                raise BusinessError(409, "approval_expired", "确认已过期，请重新生成申请")
            if row["status"] != "pending":
                raise BusinessError(409, "proposal_consumed", "申请已处理，请查询原工单")
            facts = self._eligibility(conn, user, row["order_id"])
            if not facts["eligible"] or digest(self._snapshot(facts, row["reason"])) != row["digest"]:
                raise BusinessError(409, "stale_approval", "订单或政策已变化，请重新生成申请")
            if conn.execute("SELECT 1 FROM tickets WHERE order_id=? AND status='awaiting_staff_review'",
                            (row["order_id"],)).fetchone():
                raise BusinessError(409, "active_ticket_exists", "该订单已有待审核退货工单，请查询原工单")
            result = {"ticket_id": "T-" + uuid4().hex[:16], "order_id": row["order_id"],
                      "status": "awaiting_staff_review", "created_at": self.config.now().isoformat(),
                      "message": "退货工单已创建，等待客服审核；没有执行退款。"}
            conn.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)", (
                result["ticket_id"], row["id"], user, row["order_id"], row["reason"],
                result["status"], result["created_at"]))
            conn.execute("UPDATE proposals SET status='confirmed' WHERE id=?", (row["id"],))
            conn.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (
                user, payload["idempotency_key"], request_digest, serialize(result)))
            self._audit(conn, user, "ticket.create", result["ticket_id"], "created")
            return result

    def ticket(self, user: str, ticket_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id=? AND user_id=?", (ticket_id, user)).fetchone()
            if row is None:
                raise BusinessError(404, "ticket_not_found", "工单不存在或不属于当前用户")
            return dict(row)

    def audits(self, user: str) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT action,resource_id,outcome,created_at FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 50", (user,))]
