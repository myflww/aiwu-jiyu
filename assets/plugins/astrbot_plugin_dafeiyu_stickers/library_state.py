"""Local library preferences, receipts and explicit relevance feedback.

Only image IDs, hashed session scopes, named scenes and counters are stored.
No message text, QQ identifier, credentials, inference or network access.
SQLite transactions coordinate the desktop and active AstrBot processes.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import sqlite3
import stat
import time

SCENES = {"general", "casual", "support", "task", "greeting"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
MAX_ROWS = 2000
DIAGNOSTIC_REASONS = {
    "candidate_offered": "已向本次主模型提供候选",
    "probability_skipped": "本轮自动配图概率未命中",
    "scene_suppressed": "严肃、低落倾诉或任务场景优先文字",
    "model_declined": "模型明确选择不配图",
    "marker_missing_no_match": "模型未选图，本地也没有充分的日常互动证据",
    "invalid_marker": "模型选择不在本轮允许候选中",
    "selected_model": "主模型已选择本轮候选，等待实际发送",
    "selected_fallback": "按双方明确的日常互动证据选图，等待实际发送",
    "repeat_avoided": "避免重复上一张图片，本轮保留文字",
    "cooldown": "自动配图仍在冷却中",
    "disabled": "自动配图已关闭或仅允许明确请求",
    "tool_round": "工具回合不额外自动配图",
    "already_image": "回复已有图片，不再追加",
    "asset_invalid": "图片校验失败，未发送图片",
    "packet_not_sent": "图片未进入实际发送，已释放预留",
    "adapter_accepted": "图片包已获发送适配器接受，不代表对方已读",
    "delivery_unknown": "图片发送结果未知，保留冷却且不补发",
}


def suppressed_text(text):
    return bool(re.search(r"严肃|认真(?:点|回答|说)|不要开玩笑|去世|葬礼|自杀|自残|急救|确诊|手术|病危|抑郁|崩溃|失恋|伤心|难过|痛苦|难受|焦虑|法律意见|医疗建议|报错|Traceback|```|https?://|(?:帮我|请|麻烦).{0,20}(?:修复|排查|调试|编写|运行|安装|部署|计算|翻译|制作)", text, re.I))


def scope(origin=""):
    if not isinstance(origin, str) or len(origin) > 512:
        raise ValueError("会话范围无效")
    return hashlib.sha256(origin.encode("utf-8")).hexdigest() if origin else "all"


class LibraryState:
    def __init__(self, root: Path):
        self.root = Path(root)

    @contextmanager
    def connection(self, *, write=False):
        if not self.root.is_absolute():
            raise ValueError("图库状态目录无效")
        for folder in (self.root.parent, self.root):
            if folder.exists() and (folder.is_symlink() or getattr(folder.stat(), "st_file_attributes", 0) & 0x400):
                raise ValueError("图库状态目录不可为链接")
        path = self.root / "library.sqlite3"
        for suffix in ("", "-journal", "-wal", "-shm"):
            item = Path(str(path) + suffix)
            if item.exists() or item.is_symlink():
                info = item.lstat()
                if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 8 * 1024 * 1024):
                    raise ValueError("图库状态文件不可用")
        if not path.exists() and not write:
            yield None
            return
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=2)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("CREATE TABLE IF NOT EXISTS favorite (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS used (id TEXT, scope TEXT, at REAL, count INTEGER, PRIMARY KEY(id,scope))")
            db.execute("CREATE TABLE IF NOT EXISTS rejected (id TEXT, scope TEXT, scene TEXT, count INTEGER, at REAL, PRIMARY KEY(id,scope,scene))")
            db.execute("CREATE TABLE IF NOT EXISTS diagnostic (reason TEXT PRIMARY KEY, count INTEGER, at REAL)")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def valid(identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ValueError("表情标识无效")

    def annotate(self, items: list[dict], *, origin="", scene="general") -> list[dict]:
        if scene not in SCENES:
            raise ValueError("配图场景无效")
        key = scope(origin)
        with self.connection() as db:
            favorites = dict(db.execute("SELECT id,enabled FROM favorite")) if db else {}
            uses = {row[0]: row[1:] for row in db.execute("SELECT id,MAX(at),SUM(count) FROM used GROUP BY id")} if db else {}
            rejects = {row[0]: row[1] for row in db.execute(
                "SELECT id,SUM(count) FROM rejected WHERE scope IN (?, 'all') AND scene IN (?, 'general') GROUP BY id", (key, scene))} if db else {}
        return [{**item, "favorite": bool(favorites.get(item["id"], False)),
                 "last_used": uses.get(item["id"], (None, 0))[0], "use_count": uses.get(item["id"], (None, 0))[1],
                 "scene_rejections": int(rejects.get(item["id"], 0))} for item in items]

    def set_favorite(self, identifier, enabled):
        self.valid(identifier)
        if type(enabled) is not bool:
            raise ValueError("收藏开关必须是布尔值")
        with self.connection(write=True) as db:
            existing = db.execute("SELECT enabled FROM favorite WHERE id=?", (identifier,)).fetchone()
            if enabled and not (existing and existing[0]) and db.execute("SELECT COUNT(*) FROM favorite WHERE enabled=1").fetchone()[0] >= 512:
                raise ValueError("收藏最多512张，请先取消部分收藏")
            db.execute("INSERT INTO favorite VALUES (?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled", (identifier, int(enabled)))
        return {"id": identifier, "favorite": enabled}

    def record_used(self, identifier, *, origin="", now=None):
        self.valid(identifier)
        with self.connection(write=True) as db:
            db.execute("INSERT INTO used VALUES (?,?,?,1) ON CONFLICT(id,scope) DO UPDATE SET at=excluded.at,count=MIN(count+1,1000000)",
                       (identifier, scope(origin), time.time() if now is None else now))
            db.execute("DELETE FROM used WHERE rowid IN (SELECT rowid FROM used ORDER BY at DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))

    def reject(self, identifier, *, origin="", scene="general"):
        self.valid(identifier)
        if scene not in SCENES:
            raise ValueError("配图场景无效")
        with self.connection(write=True) as db:
            db.execute("INSERT INTO rejected VALUES (?,?,?,1,?) ON CONFLICT(id,scope,scene) DO UPDATE SET count=MIN(count+1,20),at=excluded.at",
                       (identifier, scope(origin), scene, time.time()))
            db.execute("DELETE FROM rejected WHERE rowid IN (SELECT rowid FROM rejected ORDER BY at DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
        return {"id": identifier, "scene": scene, "message": "已记下：自动配图会降低这张图在所选场景的优先级。明确点图仍可发送。"}

    def clear_feedback(self, identifier, *, origin="", scene="general"):
        self.valid(identifier)
        if scene not in SCENES:
            raise ValueError("配图场景无效")
        with self.connection(write=True) as db:
            db.execute("DELETE FROM rejected WHERE id=? AND scope=? AND scene=?", (identifier, scope(origin), scene))
        return {"id": identifier, "scene": scene, "message": "已恢复该场景的自动选图偏好。"}

    def record_decision(self, reason):
        if reason not in DIAGNOSTIC_REASONS:
            raise ValueError("未知的配图诊断原因")
        with self.connection(write=True) as db:
            db.execute("INSERT INTO diagnostic VALUES (?,1,?) ON CONFLICT(reason) DO UPDATE SET count=MIN(count+1,1000000),at=excluded.at", (reason, time.time()))

    def diagnostics(self):
        with self.connection() as db:
            rows = list(db.execute("SELECT reason,count,at FROM diagnostic LIMIT 100")) if db else []
        rows = [row for row in rows if row[0] in DIAGNOSTIC_REASONS and type(row[1]) is int and type(row[2]) in (int, float)]
        latest = max(rows, key=lambda row: row[2]) if rows else None
        return {"available": bool(rows), "last_at": latest[2] if latest else None,
                "last_reason": latest[0] if latest else None,
                "last_message": DIAGNOSTIC_REASONS[latest[0]] if latest else "尚无本机配图决策记录",
                "counts": {row[0]: max(0, min(1000000, row[1])) for row in rows},
                "note": "仅记录配图原因与次数，不保存QQ号、聊天正文或模型密钥；适配器接受不等于已读。"}


def lexical_rank(items, text: str, *, avoid="", matches_only=False):
    """Explainable shortlist, not a learned semantic model or image reader.

    Explicit rejection divides lexical relevance by 1 + count. Favorite is a
    tie breaker; a favorite never defeats an explicit rejection or safety gate.
    These are product rules, not paper-derived optimal numeric parameters.
    """
    text = text.casefold()
    def score(item):
        terms = list(dict.fromkeys([item.get("title") or item.get("label", ""),
                                  *item.get("aliases", []), *item.get("tags", [])]))
        return sum(1 for term in terms if isinstance(term, str) and len(term) >= 2 and term.casefold() in text)
    def key(item):
        count = item.get("scene_rejections", 0)
        return (-(score(item) / (1 + count)), count, item["id"] == avoid, not item.get("favorite", False))
    if matches_only:
        items = [item for item in items if score(item) > 0]
    return sorted(items, key=key)
