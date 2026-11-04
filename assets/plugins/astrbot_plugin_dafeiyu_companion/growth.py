"""Local, scoped expression learning. No model, transport, transcript or authority.

Explicit low-impact feedback is an enum. A model reflection remains a hypothesis;
only its bounded, code-defined expression trial can take effect before review.
Every reflection use requires the same event's current, live memory evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time

_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_QQ = re.compile(r"[1-9][0-9]{4,11}\Z")
_LOCK = threading.RLock()
_NORMAL = {"length": "normal", "questions": "normal", "humor": "normal", "wait": "normal", "stickers": "normal"}
_LABELS = {("length", "short"): "说得简短一点", ("length", "detailed"): "解释详细一点",
           ("length", "normal"): "恢复自然篇幅", ("questions", "fewer"): "减少反问",
           ("questions", "normal"): "可以自然提问", ("humor", "less"): "少开玩笑",
           ("humor", "normal"): "可以自然开玩笑", ("wait", "more"): "多留一点说话空间",
           ("wait", "normal"): "恢复自然等待", ("stickers", "less"): "减少自动表情",
           ("stickers", "normal"): "恢复自然表情频率"}
BEHAVIORS = {"shorter": ("length", "short"), "listen_first": ("wait", "more"),
             "less_teasing": ("humor", "less"), "fewer_questions": ("questions", "fewer"),
             "fewer_stickers": ("stickers", "less")}
_PHRASES = {
    "length": {"short": ("简短点", "简短一点", "说短点", "说简短点", "少说一点", "别说这么长"),
               "detailed": ("详细一点", "说详细点", "讲详细点", "解释详细一点"),
               "normal": ("恢复正常篇幅", "恢复自然篇幅", "不用刻意简短", "不用总是简短")},
    "questions": {"fewer": ("少反问", "少反问点", "少反问一点", "不要总问", "别总问", "别总反问", "不要老反问"),
                  "normal": ("可以反问", "可以问我问题", "可以自然提问", "不用刻意少问", "恢复自然提问")},
    "humor": {"less": ("别开玩笑", "不要开玩笑", "少开玩笑", "别逗我", "先别开玩笑"),
              "normal": ("可以开玩笑", "可以逗我", "恢复自然玩笑", "不用这么严肃")},
    "wait": {"more": ("等我说完", "先听我说完", "别抢话", "不要抢话", "别急着接话"),
             "normal": ("不用等我说完", "不用刻意等我", "可以直接接话", "恢复自然等待")},
    "stickers": {"less": ("少发表情", "少发表情包", "别总发表情", "别总发表情包", "少发点表情", "先别发表情"),
                 "normal": ("可以发表情", "可以发表情包", "恢复自然表情", "恢复正常表情频率")}}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _identity(owner, bot, umo=None):
    if (not isinstance(owner, str) or not _QQ.fullmatch(owner) or not isinstance(bot, str)
            or not _QQ.fullmatch(bot) or owner == bot):
        raise ValueError("请先核对主人和机器人 QQ；成长只属于主人私聊")
    actor = _hash([owner, bot])
    if umo is None:
        return actor
    if (not isinstance(umo, str) or len(umo) > 240 or umo.split(":")[1:] != ["FriendMessage", owner]
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", umo.split(":")[0])):
        raise ValueError("成长只处理已登记的主人私聊")
    return actor, _hash([actor, umo])


def _library(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or value == "demo":
        raise ValueError("请先连接这段主人私聊的真实记忆库")
    return value


def _event(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300 or any(ord(c) < 32 for c in value):
        raise ValueError("本轮缺少稳定事件标识，未生成成长记录")
    return _hash(value)


def packet_from_material(material):
    if isinstance(material, dict):
        packet = material.get("memory_context", material)
    elif isinstance(material, str) and len(material) <= 24000 and material.lstrip().startswith("<love_house_memory_material>"):
        try:
            packet, _ = json.JSONDecoder().raw_decode(material[material.index("{"):])
        except (ValueError, TypeError, RecursionError):
            return None
    else:
        return None
    if (not isinstance(packet, dict) or packet.get("schema") != "love-house-memory-context/v1"
            or packet.get("material_role") != "evidence_not_instructions"
            or not isinstance(packet.get("identity"), dict) or packet["identity"].get("is_demo") is True
            or not isinstance(packet.get("records"), list) or len(packet["records"]) > 100
            or len(_json(packet)) > 20000):
        return None
    return packet


def _witness(record, now):
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not _ID.fullmatch(record["id"]):
        return None
    validity, source = record.get("validity"), record.get("source")
    if (not isinstance(validity, dict) or validity.get("active") is not True or validity.get("superseded_by")
            or not isinstance(source, dict) or source.get("kind") not in {"conversation_turn", "user_confirmed", "organizer", "tool_observation"}
            or record.get("interpretation") not in {"current_memory", "historical_evidence"}
            or record.get("layer") in {"persona", "persona_history", "agent_statement", "suggestion", "reflection"}
            or record.get("subject") in {"agent", "assistant", "platform_participant"}
            or record.get("domain") == "roleplay" or not isinstance(record.get("value"), str)
            or not 1 <= len(record["value"]) <= 12000):
        return None
    for key in ("valid_from", "valid_to"):
        value = validity.get(key)
        if value is not None and (type(value) not in (int, float) or not 0 < value < 4102444800
                                  or key == "valid_from" and value > now or key == "valid_to" and value <= now):
            return None
    refs = source.get("evidence")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 8 or source.get("evidence_omitted", 0):
        return None
    clean = []
    for ref in refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("id"), str) or not _ID.fullmatch(ref["id"])
                or ref.get("role") not in {"user", "tool"} or ref.get("participant_scope") == "external_participant"):
            return None
        clean.append({"id": ref["id"], "role": ref["role"], "at": ref.get("source_event_at")})
    return {"id": record["id"], "value_hash": _hash(record["value"]), "kind": source["kind"],
            "refs": clean, "user_evidence": all(r["role"] == "user" for r in clean)}


def witness_is_current(witness, nodes, library_id, *, now=None):
    """Review uses one already bounded native graph. No text is returned/stored."""
    now = time.time() if now is None else now
    def active(node):
        if not isinstance(node, dict) or not isinstance(node.get("meta", {}), dict):
            return False
        meta = node.get("meta", {})
        if (meta.get("library_id") not in (None, library_id) or meta.get("memory_disabled") or meta.get("forgotten")
                or meta.get("demo") or node.get("superseded_by") or node.get("recoverable") is False):
            return False
        for key in ("valid_from", "valid_to"):
            value = node.get(key)
            if value is not None and (type(value) not in (int, float) or not 0 < value < 4102444800
                                      or key == "valid_from" and value > now or key == "valid_to" and value <= now):
                return False
        return True
    node = nodes.get(witness["id"])
    if not active(node) or node.get("meta", {}).get("source", "local") != witness["kind"]:
        return False
    values = [node.get("text")]
    atom = node.get("meta", {}).get("memory_atom")
    if (isinstance(atom, dict) and atom.get("schema") == "love-house-memory-atom/v1"
            and isinstance(atom.get("value"), str) and atom["value"] in str(node.get("text", ""))
            and (node.get("updated") is None or type(atom.get("recorded_at")) in (int, float)
                 and atom["recorded_at"] >= node["updated"])):
        values.append(atom["value"])
    if not any(isinstance(value, str) and _hash(value) == witness["value_hash"] for value in values):
        return False
    return all(active(nodes.get(ref["id"])) for ref in witness["refs"])


class Growth:
    def __init__(self, root, *, clock=None):
        self.root, self.clock, self._bound, self._seen_events = Path(root), clock or time.time, {}, {}

    @contextmanager
    def _db(self, *, write=False):
        if not self.root.is_absolute():
            raise ValueError("成长手记目录无效")
        for path in (self.root, *self.root.parents):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("成长手记不能使用链接目录")
        path = self.root / "growth.sqlite3"
        for suffix in ("", "-journal", "-wal", "-shm"):
            member = Path(str(path) + suffix)
            if member.exists() or member.is_symlink():
                info = member.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400
                        or info.st_size > 2 * 1024 * 1024):
                    raise ValueError("成长手记文件不可读取")
        if not path.exists() and not write:
            yield None
            return
        if write:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(path if write else path.as_uri() + "?mode=ro", uri=not write, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            if write:
                db.execute("PRAGMA secure_delete=ON")
                db.execute("CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY,actor TEXT NOT NULL,scope TEXT NOT NULL,library TEXT NOT NULL,kind TEXT NOT NULL,dimension TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,data TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS generations (library TEXT PRIMARY KEY,generation TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS reflection_budget (actor TEXT NOT NULL,event TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(actor,event))")
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
                path.chmod(0o600)
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _capacity(db):
        db.execute("DELETE FROM entries WHERE id IN (SELECT id FROM entries WHERE status IN ('paused','superseded') ORDER BY updated DESC LIMIT -1 OFFSET 60)")
        if db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] >= 128:
            raise ValueError("成长手记已达本地容量上限；现有记录保留，未覆盖")

    def clear_event(self, owner, bot, umo, event_id=None):
        _, scope = _identity(owner, bot, umo)
        with _LOCK:
            current = self._bound.get(scope)
            if event_id is None or current and current["event"] == _event(event_id):
                self._bound.pop(scope, None)

    @staticmethod
    def _db_generation(db, library):
        if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generations'").fetchone():
            return "0"
        row = db.execute("SELECT generation FROM generations WHERE library=?", (library,)).fetchone()
        return row[0] if row else "0"

    def _generation(self, library):
        with self._db() as db:
            return self._db_generation(db, library)

    def bind_evidence(self, owner, bot, umo, event_id, material, *, origin="message"):
        actor, scope = _identity(owner, bot, umo)
        self.clear_event(owner, bot, umo)
        if origin not in {"message", "proactive"}:
            raise ValueError("成长证据执行来源无效")
        event, packet, now = _event(event_id), packet_from_material(material), self.clock()
        if packet is None:
            return {"ok": False, "library_id": None, "record_ids": []}
        library = _library(packet["identity"].get("library_id"))
        generation = self._generation(library)
        records = {row["id"]: row for item in packet["records"] if (row := _witness(item, now)) is not None}
        with _LOCK:
            seen = self._seen_events.get(scope)
            if seen and seen["event"] == event and seen["library"] == library and seen["generation"] != generation:
                # A tool-loop may repeat the old host memory cache after a
                # desktop forget. It is still the same old received event.
                return {"ok": False, "library_id": None, "record_ids": []}
            self._seen_events[scope] = {"event": event, "library": library, "generation": generation}
            self._bound[scope] = {"actor": actor, "event": event, "library": library, "records": records,
                                  "expires": now + 600, "origin": origin, "generation": generation}
            for key in list(self._bound):
                if self._bound[key]["expires"] <= now:
                    del self._bound[key]
            if len(self._bound) > 32:
                del self._bound[next(iter(self._bound))]
            if len(self._seen_events) > 32:
                del self._seen_events[next(iter(self._seen_events))]
        return {"ok": True, "library_id": library, "record_ids": list(records)}

    def observe_feedback(self, owner, bot, umo, event_id, text, library_id):
        actor, scope = _identity(owner, bot, umo)
        library, event = _library(library_id), _event(event_id)
        if (not isinstance(text, str) or not 1 <= len(text) <= 600
                or any(marker in text for marker in ('"', "'", "“", "”", "「", "」", "```", ">", "他说", "她说", "如果", "比如", "转述"))):
            return {"ok": True, "learned": [], "reason": "not_explicit_feedback"}
        normalized = re.sub(r"\s+", "", text)
        prefix = r"^(?:(?:小鱼|大肥鱼)[，,]?)?(?:请|麻烦)?(?:你)?(?:以后|接下来)?(?:请)?"
        suffix = r"(?:吧|呀|哦|啊|好吗|可以吗|好不好)?[？?]?$"
        found = {}
        for clause in re.split(r"[，,。！!；;\n]+", normalized):
            clause = re.sub(prefix, "", clause)
            clause = re.sub(suffix, "", clause)
            for dimension, values in _PHRASES.items():
                for value, phrases in values.items():
                    if clause in phrases:
                        found[dimension] = (value, clause)
        if not found:
            return {"ok": True, "learned": [], "reason": "not_explicit_feedback"}
        now, learned = self.clock(), []
        with self._db(write=True) as db:
            for dimension, (value, quote) in found.items():
                identifier = "fb_" + _hash([actor, scope, library, event, dimension])[:32]
                data = {"value": value, "source_quote": quote, "source_hash": _hash(text), "event": event}
                existing = db.execute("SELECT data FROM entries WHERE id=?", (identifier,)).fetchone()
                if existing:
                    previous = json.loads(existing["data"])
                    if any(previous.get(key) != data[key] for key in ("value", "source_hash", "event")):
                        raise ValueError("同一真实事件出现不同反馈，未改写旧证据")
                    continue
                self._capacity(db)
                db.execute("UPDATE entries SET status='superseded',updated=? WHERE actor=? AND scope=? AND library=? AND kind='feedback' AND dimension=? AND status='active'", (now, actor, scope, library, dimension))
                db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, actor, scope, library, "feedback", dimension, "active", now, now, _json(data)))
                learned.append({"dimension": dimension, "value": value, "label": _LABELS[(dimension, value)]})
        return {"ok": True, "learned": learned, "reason": "explicit_user_feedback"}

    def preferences(self, owner, bot, umo, library_id):
        actor, scope = _identity(owner, bot, umo)
        library = _library(library_id)
        return self._preferences(actor, scope, library)[0]

    def _preferences(self, actor, scope, library):
        result, explicit = dict(_NORMAL), set()
        with self._db() as db:
            if db is not None:
                for row in db.execute("SELECT dimension,data FROM entries WHERE actor=? AND scope=? AND library=? AND kind='feedback' AND status='active' ORDER BY created", (actor, scope, library)):
                    value = json.loads(row["data"]).get("value")
                    if (row["dimension"], value) in _LABELS:
                        result[row["dimension"]] = value
                        explicit.add(row["dimension"])
        return result, explicit

    def propose(self, owner, bot, umo, event_id, evidence_ids, hypothesis, next_step, behavior=""):
        actor, scope = _identity(owner, bot, umo)
        event, now = _event(event_id), self.clock()
        with _LOCK:
            bound = deepcopy(self._bound.get(scope))
        if (not bound or bound["event"] != event or bound["expires"] <= now or bound["origin"] != "message"
                or self._generation(bound["library"]) != bound["generation"]):
            raise ValueError("本轮没有可核验的主人私聊证据，未生成推测")
        if (not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 5
                or any(not isinstance(i, str) or i not in bound["records"] for i in evidence_ids)
                or len(set(evidence_ids)) != len(evidence_ids)):
            raise ValueError("反思只能引用本轮实际召回的1至5条有效证据")
        for value in (hypothesis, next_step):
            if (not isinstance(value, str) or not value.strip() or len(value) > 300 or any(ord(c) < 32 for c in value)
                    or re.search(r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+|<analysis>|```", value, re.I)):
                raise ValueError("请只保留简短可展示的推测与改进摘要，不保存密钥或隐藏推理")
        if behavior not in ("", *BEHAVIORS):
            raise ValueError("语气试行只能选择已支持的低影响表达方式")
        evidence = [bound["records"][i] for i in evidence_ids]
        dimension, _ = BEHAVIORS.get(behavior, (None, None))
        # Any explicit choice in this dimension wins, including an explicit
        # return to 'normal'. The direct preference path needs no model guess.
        explicit = self._explicit_dimensions(actor, scope, bound["library"])
        trial = bool(behavior and all(w["user_evidence"] for w in evidence) and dimension not in explicit)
        data = {"hypothesis": hypothesis.strip(), "next_step": next_step.strip(), "evidence": evidence,
                "behavior": behavior, "trial_until": now + 3600 if trial else None, "event": event}
        with self._db(write=True) as db:
            if self._db_generation(db, bound["library"]) != bound["generation"]:
                raise ValueError("记忆库在本轮经历过忘记操作，请发送新消息后再取当前证据")
            rows = db.execute("SELECT created,event FROM reflection_budget WHERE actor=? ORDER BY created DESC", (actor,)).fetchall()
            if any(row["event"] == event for row in rows):
                raise ValueError("本条真实输入已经生成过反思，不重复提案")
            if rows and now - rows[0]["created"] < 600:
                raise ValueError("反思提案至少间隔10分钟；没有额外调用模型")
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            if sum(time.strftime("%Y-%m-%d", time.gmtime(row["created"])) == day for row in rows) >= 3:
                raise ValueError("今天已保留3条反思提案，先用已有证据改进表达")
            self._capacity(db)
            identifier = "rf_" + secrets.token_hex(16)
            db.execute("INSERT INTO reflection_budget VALUES (?,?,?)", (actor, event, now))
            db.execute("DELETE FROM reflection_budget WHERE created<?", (now - 172800,))
            db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", (identifier, actor, scope, bound["library"], "reflection", dimension or "", "pending", now, now, _json(data)))
        return {"ok": True, "id": identifier, "status": "pending", "epistemic_status": "model_hypothesis",
                "behavior_trial": behavior if trial else None, "trial_until": data["trial_until"],
                "message": "已保留有来源的推测；它不是事实或权限。自由文本计划仅供查看，不会执行。"}

    def _explicit_dimensions(self, actor, scope, library):
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT dimension FROM entries WHERE actor=? AND scope=? AND library=? AND kind='feedback' AND status='active'", (actor, scope, library))} if db is not None else set()

    def forget_library(self, library_id):
        """Explicit forget companion step. No native library or other path write.

        Text is securely deleted, then the WAL is checkpointed. A busy/failed
        checkpoint raises; the caller must not label partial cleanup success.
        Hash-only budgets remain so forgetting cannot replay the same proposal.
        """
        library, now = _library(library_id), self.clock()
        with self._db(write=True) as db:
            deleted = db.execute("DELETE FROM entries WHERE library=? AND kind='reflection'", (library,)).rowcount
            cleared = 0
            for row in db.execute("SELECT id,data FROM entries WHERE library=? AND kind='feedback'", (library,)).fetchall():
                data = json.loads(row["data"])
                if data.get("source_quote"):
                    data["source_quote"] = ""
                    db.execute("UPDATE entries SET data=?,updated=? WHERE id=?", (_json(data), now, row["id"]))
                    cleared += 1
            db.execute("INSERT INTO generations VALUES (?,?) ON CONFLICT(library) DO UPDATE SET generation=excluded.generation", (library, secrets.token_hex(16)))
            db.commit()
            checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0] != 0:
                raise ValueError("成长派生正文已清理，但本地检查点仍被占用；未将清理报告为全部完成，请稍后重试")
        with _LOCK:
            for scope in list(self._bound):
                if self._bound[scope]["library"] == library:
                    del self._bound[scope]
        return {"ok": True, "reflection_deleted": deleted, "feedback_quotes_cleared": cleared,
                "message": "已清理本库成长推测、改进摘要和反馈原句；纯表达枚举与来源哈希保留，其他库未更改。"}

    @staticmethod
    def _revision(row):
        return _hash(dict(row))

    def entries(self, owner, bot, library_id):
        actor, library, result = _identity(owner, bot), _library(library_id), []
        with self._db() as db:
            if db is None:
                return []
            for row in db.execute("SELECT * FROM entries WHERE actor=? AND library=? ORDER BY created DESC,id LIMIT 128", (actor, library)):
                data = json.loads(row["data"])
                item = {"id": row["id"], "revision": self._revision(row), "kind": row["kind"], "status": row["status"],
                        "created_at": row["created"], "updated_at": row["updated"], "evidence_ids": [], "effectiveness": "unverified"}
                if row["kind"] == "feedback":
                    item.update(dimension=row["dimension"], value=data["value"], source_quote=data["source_quote"],
                                label=_LABELS.get((row["dimension"], data["value"]), "已表达的偏好"),
                                source="explicit_user_feedback", epistemic_status="explicit_expression_preference")
                else:
                    item.update(label="有来源的反思推测", hypothesis=data["hypothesis"], next_step=data["next_step"],
                                evidence_ids=[w["id"] for w in data["evidence"]], behavior=data["behavior"],
                                trial_until=data.get("trial_until"), source="model_hypothesis", epistemic_status="model_hypothesis")
                result.append(item)
        return result

    def change(self, owner, bot, library_id, identifier, action, expected_revision, *, confirmed=False, verify=None):
        actor, library = _identity(owner, bot), _library(library_id)
        if (confirmed is not True or not isinstance(identifier, str) or not re.fullmatch(r"(?:fb|rf)_[a-f0-9]{32}", identifier)
                or action not in {"approve", "pause", "revoke", "resume"}
                or not isinstance(expected_revision, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_revision)):
            raise ValueError("请先查看当前成长记录，再明确确认这次更改")
        with self._db() as db:
            row = db.execute("SELECT * FROM entries WHERE id=? AND actor=? AND library=?", (identifier, actor, library)).fetchone() if db else None
            if row is None or self._revision(row) != expected_revision:
                raise ValueError("成长记录已变化或不属于当前账号与记忆库，请刷新")
            row = dict(row)
        data, now = json.loads(row["data"]), self.clock()
        if action == "approve":
            if row["kind"] != "reflection" or row["status"] != "pending" or not callable(verify) or verify(library, data["evidence"]) is not True:
                raise ValueError("原证据已改变、停用或暂不可核验；没有把推测确认为事实")
            status = "approved"
        elif action in {"pause", "revoke"}:
            if row["status"] not in {"active", "approved", "pending"}:
                raise ValueError("这条记录当前未在使用，不能重复暂停")
            data["resume_status"] = row["status"]
            status = "paused"
        else:
            if row["status"] != "paused":
                raise ValueError("只可恢复已暂停的记录；已被新反馈替代的旧偏好不会复活")
            status = data.get("resume_status")
            if status not in {"active", "pending", "approved"}:
                raise ValueError("这条记录的原状态无法核验")
            if row["kind"] == "reflection" and status == "approved" and (not callable(verify) or verify(library, data["evidence"]) is not True):
                raise ValueError("原证据暂不可核验，未恢复使用")
        with self._db(write=True) as db:
            fresh = db.execute("SELECT * FROM entries WHERE id=? AND actor=? AND library=?", (identifier, actor, library)).fetchone()
            if fresh is None or self._revision(fresh) != expected_revision:
                raise ValueError("成长记录在核验时已变化，请刷新")
            if action == "resume" and row["kind"] == "feedback" and db.execute("SELECT 1 FROM entries WHERE actor=? AND scope=? AND library=? AND kind='feedback' AND dimension=? AND status='active' AND id<>?", (actor, row["scope"], library, row["dimension"], identifier)).fetchone():
                raise ValueError("已有更新的明确偏好，旧记录不会覆盖它")
            db.execute("UPDATE entries SET status=?,updated=?,data=? WHERE id=?", (status, now, _json(data), identifier))
        return {"ok": True, "id": identifier, "status": status,
                "message": "已更新使用状态；反思仍是有来源的推测，不是事实、真实意识或执行权限。"}

    def context(self, owner, bot, umo, event_id, library_id):
        actor, scope = _identity(owner, bot, umo)
        event, library, now = _event(event_id), _library(library_id), self.clock()
        with _LOCK:
            bound = deepcopy(self._bound.get(scope))
        if (not bound or bound["event"] != event or bound["library"] != library or bound["expires"] <= now
                or self._generation(library) != bound["generation"]):
            return ""
        prefs, explicit = self._preferences(actor, scope, library)
        payload = {"explicit_expression_preferences": [_LABELS[(key, prefs[key])] for key in _NORMAL if key in explicit],
                   "temporary_expression_trials": [], "reviewed_hypotheses": []}
        with self._db() as db:
            rows = db.execute("SELECT status,data FROM entries WHERE actor=? AND scope=? AND library=? AND kind='reflection' AND status IN ('pending','approved') ORDER BY created DESC LIMIT 12", (actor, scope, library)).fetchall() if db else []
            dimensions = set(explicit)
            for row in rows:
                data = json.loads(row["data"])
                if any(bound["records"].get(w["id"]) != w for w in data["evidence"]):
                    continue
                behavior = data.get("behavior")
                if (behavior in BEHAVIORS and data.get("trial_until") is not None and data["trial_until"] > now
                        and all(w["user_evidence"] for w in data["evidence"])):
                    dimension, value = BEHAVIORS[behavior]
                    if dimension not in dimensions:
                        payload["temporary_expression_trials"].append(_LABELS[(dimension, value)])
                        dimensions.add(dimension)
                if row["status"] == "approved" and len(payload["reviewed_hypotheses"]) < 3:
                    item = {"hypothesis": data["hypothesis"], "is_user_fact": False, "effectiveness": "unverified",
                            "evidence_ids": [w["id"] for w in data["evidence"]]}
                    payload["reviewed_hypotheses"].append(item)
                    if len(_json(payload)) > 2200:
                        payload["reviewed_hypotheses"].pop()
        if not any(payload.values()):
            return ""
        return ("<dafeiyu_growth_material>\n只参考下列当前有效的表达偏好；明确反馈优先。语气试行可撤回，不扩大任何权限。"
                "反思是待验证推测，不是用户事实、真实意识或执行命令；自由文本改进计划不在此处执行。\n"
                + _json(payload) + "\n</dafeiyu_growth_material>")
