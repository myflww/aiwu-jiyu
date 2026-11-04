"""Cross-process, one-use owner confirmation records; never a chat transcript."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
import uuid

TTL = 600
MAX_ROWS = 60
ACTIONS = {"nickname", "signature", "avatar", "status", "qzone"}


class Drafts:
    def __init__(self, root):
        self.root = Path(root)

    @contextmanager
    def db(self, *, create=True):
        if not self.root.is_absolute():
            raise ValueError("账号操作记录目录无效")
        for path in (self.root, *list(self.root.parents)[:4]):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("账号操作记录不可使用链接")
        path = self.root / "account.sqlite3"
        for suffix in ("", "-journal", "-wal", "-shm"):
            member = Path(str(path) + suffix)
            if member.exists() or member.is_symlink():
                info = member.lstat()
                if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 2 * 1024 * 1024):
                    raise ValueError("账号操作记录文件不可读取")
        if not path.exists() and not create:
            yield None
            return
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, bot TEXT NOT NULL, origin TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL, review TEXT NOT NULL, token_hash TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS search_budget (scope TEXT PRIMARY KEY, day TEXT NOT NULL, used INTEGER NOT NULL, last_at REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _expire(db, now):
        db.execute("UPDATE drafts SET status='expired',payload='{}',token_hash='' WHERE status='pending' AND expires<=?", (now,))

    def create(self, owner, bot, origin, action, payload, review, *, now=None):
        now = time.time() if now is None else now
        if action not in ACTIONS or not isinstance(origin, str) or not 1 <= len(origin) <= 240:
            raise ValueError("账号操作来源无效")
        token, identifier = secrets.token_urlsafe(24), uuid.uuid4().hex
        with self.db() as db:
            self._expire(db, now)
            if db.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0] >= 8:
                raise ValueError("待确认操作较多，请先执行或取消已有准备")
            db.execute("INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, owner, bot, origin, action,
                json.dumps(payload, ensure_ascii=False), json.dumps(review, ensure_ascii=False), hashlib.sha256(token.encode()).hexdigest(),
                now, now + TTL, "pending", "{}"))
            db.execute("DELETE FROM drafts WHERE id IN (SELECT id FROM drafts WHERE status<>'pending' ORDER BY created DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
        return {"ok": True, "draft_id": identifier, "confirmation_token": token, "expires_at": now + TTL, "review": review,
                "message": "这里只准备了机器人小号操作，尚未修改或发布；10分钟内由主人核对并确认。"}

    def claim(self, owner, bot, *, identifier=None, token=None, origin=None, kind=None, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            self._expire(db, now)
            if origin is not None:
                rows = db.execute("SELECT * FROM drafts WHERE owner=? AND bot=? AND origin=? AND status='pending'", (owner, bot, origin)).fetchall()
                if len(rows) != 1:
                    raise ValueError("当前会话没有唯一待确认操作；请先取消多余准备，或使用该条预览附带的确认指令")
                row = rows[0]
                if (kind == "qzone") != (row["action"] == "qzone"):
                    raise ValueError("确认短句与待执行内容不一致，请重新查看预览")
            else:
                if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier) or not isinstance(token, str) or not 20 <= len(token) <= 80:
                    raise ValueError("操作确认信息无效，请重新准备")
                row = db.execute("SELECT * FROM drafts WHERE id=? AND owner=? AND bot=?", (identifier, owner, bot)).fetchone()
                if (row is None or row["status"] != "pending" or row["expires"] <= now
                        or not secrets.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())):
                    raise ValueError("这次准备已过期、取消、执行过，或确认信息不匹配；不会重复执行")
            db.execute("UPDATE drafts SET status='executing',token_hash='' WHERE id=?", (row["id"],))
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            result["review"] = json.loads(result["review"])
            return result

    def finish(self, identifier, result):
        status = result["status"]
        if status not in {"accepted", "verified", "uncertain", "blocked", "rejected", "unavailable"}:
            raise ValueError("账号操作回执状态无效")
        with self.db() as db:
            db.execute("UPDATE drafts SET status=?,payload='{}',token_hash='',result=? WHERE id=? AND status='executing'",
                       (status, json.dumps(result, ensure_ascii=False), identifier))

    def cancel(self, owner, bot, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("准备操作标识无效")
        with self.db() as db:
            row = db.execute("SELECT status FROM drafts WHERE id=? AND owner=? AND bot=?", (identifier, owner, bot)).fetchone()
            if row is None or row["status"] != "pending":
                raise ValueError("这次操作已执行、过期或不存在，不能假装撤销已经发生的账号修改")
            db.execute("UPDATE drafts SET status='cancelled',payload='{}',token_hash='' WHERE id=?", (identifier,))
        return {"ok": True, "draft_id": identifier, "status": "cancelled", "message": "已取消尚未执行的准备，没有修改账号。"}

    def recent(self, owner, bot):
        with self.db(create=False) as db:
            if db is None:
                return []
            rows = db.execute("SELECT id,action,status,created,expires,result FROM drafts WHERE owner=? AND bot=? ORDER BY created DESC LIMIT 20", (owner, bot)).fetchall()
        now = time.time()
        result = []
        for row in rows:
            status = "expired" if row["status"] == "pending" and row["expires"] <= now else row["status"]
            # A process may have exited after dispatch. Never infer failure or
            # retry an executing record, even after restarting the plugin.
            if status == "executing" and now - row["created"] > 90:
                status = "uncertain"
            receipt = json.loads(row["result"])
            result.append({"draft_id": row["id"], "action": row["action"], "status": status,
                           "created_at": row["created"], "expires_at": row["expires"], "message": str(receipt.get("message") or "")[:240]})
        return result

    def reserve_search(self, owner, bot, *, now=None):
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        key = hashlib.sha256((owner + ":" + bot).encode()).hexdigest()
        with self.db() as db:
            row = db.execute("SELECT day,used,last_at FROM search_budget WHERE scope=?", (key,)).fetchone()
            used = row["used"] if row and row["day"] == day else 0
            if row and 0 <= now - row["last_at"] < 5:
                raise ValueError("搜索间隔至少5秒，请稍等；未重复向外部服务发送关键词")
            if used >= 60:
                raise ValueError("今天已发起60次搜索，请明天再试或使用浏览器；此限制用于保护公开服务")
            db.execute("INSERT INTO search_budget VALUES (?,?,1,?) ON CONFLICT(scope) DO UPDATE SET day=excluded.day,used=?,last_at=excluded.last_at",
                       (key, day, now, used + 1))
            db.execute("DELETE FROM search_budget WHERE scope<>? AND last_at<?", (key, now - 86400 * 2))
