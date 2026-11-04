"""User-directed view and controls over the existing LoveHouse memory library.

No second transcript database, model call, automatic deletion or cross-library
fallback. The native source preserves correction lineage and forget semantics.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import secrets
import threading
import time
from urllib.parse import quote

from backend import lovehouse

_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_lock = threading.RLock()
_previews = {}


def _native_get(path, library, *, retry=True):
    """Bound the native read before JSON decoding; same local management auth."""
    if path != "/api/graph" and not path.startswith("/api/node?id="):
        raise ValueError("未知的记忆读取入口")
    port = lovehouse._port()
    if not lovehouse._health(port, timeout=1.5):
        raise ValueError("爱之屋没有在运行，请先启动爱之屋")
    cookie = lovehouse._cookie(port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    limit = 8 * 1024 * 1024
    try:
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}",
            "Origin": f"http://127.0.0.1:{port}", "Cookie": cookie, "X-NKB-Library": library})
        response = connection.getresponse()
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > limit):
            raise ValueError("此记忆库较大，请在爱之屋原窗口管理")
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("此记忆库较大，请在爱之屋原窗口管理")
        if response.status == 401 and retry:
            lovehouse._cookie(port, force=True)
            return _native_get(path, library, retry=False)
        if response.status != 200:
            raise ValueError("暂时无法读取原记忆条目，请在爱之屋检查服务")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("记忆服务返回格式不正确")
        return result
    finally:
        connection.close()


def _body(body, allowed):
    if not isinstance(body, dict) or set(body) - set(allowed):
        raise ValueError("不支持的记忆操作字段")
    return body


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("请选择有效的记忆库或记忆条目")
    return value


def _scope(body, *, mutate=False):
    library = _identifier(body.get("library_id"))
    rows = lovehouse.libraries().get("libraries", [])
    selected = next((r for r in rows if r.get("id") == library), None)
    if selected is None or mutate and selected.get("is_demo"):
        raise ValueError("请选择本机自己的记忆库；不能更改示例库")
    return library


def _graph(library):
    data = _native_get("/api/graph", library)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("爱之屋没有返回可管理的记忆目录")
    nodes = data["nodes"]
    if len(nodes) > 5000 or len(json.dumps(data, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
        raise ValueError("此库较大，请在爱之屋原窗口管理，桌面没有截断冒充完整目录")
    if data.get("meta", {}).get("library_id") not in (None, library):
        raise ValueError("记忆服务返回了不同的库，已停止读取")
    nodes = [node for node in nodes if isinstance(node, dict) and _ID.fullmatch(str(node.get("id", "")))]
    # Native graph projection includes continuously decaying effective_salience
    # and visual state. Those are not user edits and must not invalidate an
    # open editor every second. Hash stored evidence/metadata fields only.
    fields = {"id", "label", "text", "kind", "tags", "salience", "impact", "meta", "event_at", "created",
              "updated", "reinforced", "decay_at", "superseded_by", "valid_from", "valid_to", "important"}
    stable = [{k: value for k, value in node.items() if k in fields} for node in sorted(nodes, key=lambda n:n["id"])]
    revision = _fingerprint(stable)
    return nodes, revision


def _revision(library, expected):
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("请先读取当前记忆目录再操作")
    _, latest = _graph(library)
    if latest != expected:
        raise ValueError("记忆目录已变化，请刷新后重新检查")


def _entry(node):
    meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
    kind = str(node.get("kind") or "experience")
    return {"id": node["id"], "title": str(node.get("label") or "未命名记忆")[:500], "kind": kind,
        "layer": kind if kind in {"fact", "preference", "commitment", "persona"} else "experience",
        "recorded_at": node.get("created"), "source_time": node.get("event_at"),
        "enabled": meta.get("memory_disabled") is not True,
        "important": meta.get("important") is True,
        "source": str(meta.get("source") or "unknown")[:100], "confirmed": meta.get("confirmed") is True,
        "speaker": str(meta.get("speaker") or "unknown")[:50],
        "superseded": bool(node.get("superseded_by")),
        "interpretation": "management_record", "epistemic_status": "user_confirmed_statement" if meta.get("confirmed") is True and meta.get("speaker") == "user" else "source_evidence",
        "value_chars": len(str(node.get("text") or ""))}


def catalog(body):
    _body(body, {"library_id", "cursor", "limit", "view", "query"})
    library = _scope(body)
    limit, view, query = body.get("limit", 20), body.get("view", "all"), body.get("query", "")
    if type(limit) is not int or not 1 <= limit <= 50 or view not in {"all", "active", "disabled"}:
        raise ValueError("每页记忆数量须为1至50，请选择全部、使用中或已停用")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("搜索词须不超过200字")
    nodes, revision = _graph(library)
    nodes.sort(key=lambda row: (row.get("created") or 0, row["id"]), reverse=True)
    selected = [n for n in nodes if (view == "all" or (_entry(n)["enabled"] == (view == "active")))
                and (not query or query.casefold() in (str(n.get("label", "")) + "\n" + str(n.get("text", ""))).casefold())]
    cursor, offset = body.get("cursor"), 0
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if (not isinstance(value, list) or len(value) != 5 or value[:4] != [library, revision, view, query]
                    or type(value[4]) is not int or not 0 <= value[4] <= len(selected)):
                raise ValueError()
            offset = value[4]
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("目录或筛选已变化，请从第一页重新读取") from None
    entries = [_entry(node) for node in selected[offset:offset + limit]]
    next_offset = offset + len(entries)
    next_cursor = (base64.urlsafe_b64encode(json.dumps([library, revision, view, query, next_offset],ensure_ascii=False).encode()).decode().rstrip("=")
                   if next_offset < len(selected) else None)
    return {"schema": "aiwu-memory-management/v1", "identity": {"library_id": library}, "revision": revision,
            "entries": entries, "total": len(selected), "returned": len(entries), "next_cursor": next_cursor,
            "complete": next_cursor is None, "offset": offset, "read_only": True, "view": view,
            "note": "本机管理目录含停用条目；使用中仅指未手动停用，不保证每轮都会被召回。"}


def read(body):
    _body(body, {"library_id", "id", "revision"})
    library, identifier = _scope(body), _identifier(body.get("id"))
    if body.get("revision"):
        _revision(library, body["revision"])
    node = _native_get("/api/node?id=" + quote(identifier, safe=""), library)
    if not isinstance(node, dict) or node.get("id") != identifier:
        raise ValueError("原记忆条目暂不可读取")
    record = _entry(node)
    record["display"] = {"title": record["title"]}
    meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
    record["source_ids"] = [x for x in meta.get("members", []) if isinstance(x, str) and _ID.fullmatch(x)][:30]
    record["corrects"] = meta.get("corrects") if isinstance(meta.get("corrects"), str) and _ID.fullmatch(meta["corrects"]) else None
    text = str(node.get("text") or "")
    return {"identity": {"library_id": library}, "record": record, "text": text[:8000],
            "complete": len(text) <= 8000, "total_chars": len(text), "revision": body.get("revision")}


def _fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def mutate(body):
    _body(body, {"library_id", "id", "action", "text", "label", "enabled", "important", "expected_revision", "preview_token", "confirm"})
    library, identifier = _scope(body, mutate=True), _identifier(body.get("id"))
    action = body.get("action")
    route = "/api/memories/" + quote(identifier, safe="") + "/"
    if action == "forget-preview":
        plan = lovehouse._request("POST", route + "forget-preview", {}, library_id=library)
        token, now = secrets.token_urlsafe(24), time.time()
        with _lock:
            for key in list(_previews):
                if _previews[key][3] <= now:
                    del _previews[key]
            if len(_previews) >= 64:
                raise ValueError("待确认的忘记操作较多，请稍后重新预览")
            _previews[token] = (library, identifier, _fingerprint(plan), now + 300)
        return {"preview": plan, "preview_token": token, "expires_at": now + 300, "read_only": True}
    if action == "forget":
        if body.get("confirm") is not True:
            raise ValueError("请先预览忘记范围并明确确认")
        token = body.get("preview_token")
        if not isinstance(token, str):
            raise ValueError("请重新预览忘记范围")
        with _lock:
            saved = _previews.get(token)
        if not saved or saved[:2] != (library, identifier) or saved[3] <= time.time():
            raise ValueError("预览已失效或属于其他条目，请重新预览")
        plan = lovehouse._request("POST", route + "forget-preview", {}, library_id=library)
        if _fingerprint(plan) != saved[2]:
            raise ValueError("忘记范围已经变化，请重新检查预览")
        # A forgotten experience must not survive as a separate short model
        # hypothesis. Clear derived private text first; failure aborts the
        # original native forget, rather than reporting a partial success.
        from backend import companion_growth
        companion_growth.forget_library(library)
        result = lovehouse._request("POST", route + "forget", {"confirm": True}, library_id=library)
        with _lock:
            _previews.pop(token, None)
        return result
    _revision(library, body.get("expected_revision"))
    if action == "correct":
        text = body.get("text")
        label = body.get("label")
        if not isinstance(text, str) or not text.strip() or len(text) > 40000:
            raise ValueError("更正内容须为1至40000字")
        if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 100):
            raise ValueError("标题须为1至100字")
        args = {"text": text}
        if label is not None:
            args["label"] = label
        return lovehouse._request("POST", route + "correct", args, library_id=library)
    if action == "enabled":
        if type(body.get("enabled")) is not bool:
            raise ValueError("记忆使用状态须为开关")
        return lovehouse._request("POST", route + "enabled", {"enabled": body["enabled"]}, library_id=library)
    if action == "important":
        if type(body.get("important")) is not bool:
            raise ValueError("重要标记须为开关")
        node = _native_get("/api/node?id=" + quote(identifier, safe=""), library)
        if node.get("id") != identifier or not isinstance(node.get("meta", {}), dict):
            raise ValueError("记忆原条目不可读取，请刷新")
        metadata = {**node.get("meta", {}), "important": body["important"]}
        return lovehouse._request("POST", "/api/update", {"id": identifier, "meta": metadata,
            "salience": .9 if body["important"] else .6}, library_id=library)
    raise ValueError("请选择更正、停用或预览忘记")


def add(body):
    _body(body, {"library_id", "text", "kind", "important"})
    library = _scope(body, mutate=True)
    text, kind, important = body.get("text"), body.get("kind", "fact"), body.get("important", True)
    if not isinstance(text, str) or not text.strip() or len(text) > 40000:
        raise ValueError("请填写1至40000字的真实偏好、事实或约定")
    if kind not in {"fact", "preference", "commitment"} or type(important) is not bool:
        raise ValueError("请选择偏好、事实或待跟进约定")
    # Native /api/add supports commitment nodes; /api/ai/remember only accepts
    # fact/preference/episode. Do not silently turn a commitment into a fact.
    return lovehouse._request("POST", "/api/add", {"label": text.strip().splitlines()[0][:80],
        "text": text.strip(), "kind": kind, "tags": ["用户确认", "爱屋及鱼"], "salience": .8 if important else .6,
        "meta": {"source": "user_confirmed", "confirmed": True, "speaker": "user", "important": important}}, library_id=library)
