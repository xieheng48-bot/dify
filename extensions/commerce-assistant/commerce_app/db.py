from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import sqlite3

from .config import Config


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, category TEXT NOT NULL,
  product TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  delivered_on TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, version INTEGER NOT NULL,
  effective_from TEXT NOT NULL, effective_to TEXT NOT NULL,
  return_days INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, order_id TEXT NOT NULL,
  reason TEXT NOT NULL, digest TEXT NOT NULL, confirmation_token TEXT NOT NULL,
  expires_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL, order_id TEXT NOT NULL, reason TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
  user_id TEXT NOT NULL, key TEXT NOT NULL, request_digest TEXT NOT NULL,
  response_json TEXT NOT NULL, PRIMARY KEY (user_id, key)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_return_ticket
ON tickets(order_id) WHERE status='awaiting_staff_review';
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
  action TEXT NOT NULL, resource_id TEXT NOT NULL,
  outcome TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str, config: Config):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if not conn.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone():
                self._seed(conn, config)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _seed(conn: sqlite3.Connection, config: Config):
        today = config.now().date()
        day = lambda n: (today + timedelta(days=n)).isoformat()
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", [
            ("A1001", "alice", "electronics", "蓝牙耳机", 39900, day(-2), "delivered", 1),
            ("A1002", "alice", "apparel", "运动外套", 29900, day(-40), "delivered", 1),
            ("B2001", "bob", "electronics", "机械键盘", 49900, day(-1), "delivered", 1),
        ])
        conn.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?)", [
            ("P-E1", "electronics", 1, "2000-01-01", day(-31), 14,
             "数码商品售后政策 v1", "历史版本：签收后 14 天内可申请退货，后由 v2 替代。"),
            ("P-E2", "electronics", 2, day(-30), "2099-12-31", 7,
             "数码商品售后政策 v2", "签收后 7 天内可申请退货；商品及配件需完整，人工审核后处理。"),
            ("P-A1", "apparel", 1, "2000-01-01", "2099-12-31", 30,
             "服饰商品售后政策 v1", "签收后 30 天内可申请退货，吊牌及包装需完整，人工审核后处理。"),
        ])
        conn.execute("INSERT INTO meta VALUES ('seeded', ?)", (today.isoformat(),))
