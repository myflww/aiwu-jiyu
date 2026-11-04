"""Search, favorites and explicit local-only sticker suitability previews."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import sqlite3

from backend import custom_stickers, stickers
from backend.paths import ASSETS


@lru_cache(maxsize=1)
def _policy():
    path = ASSETS / "plugins" / stickers.PLUGIN_ID / "library_state.py"
    spec = importlib.util.spec_from_file_location("_aiwu_sticker_library_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def store():
    return _policy().LibraryState(custom_stickers.data_root().parent)


def _all():
    return stickers.status(settings=stickers.load_settings(), custom_items=custom_stickers.list_items())


def _find(identifier):
    _policy().LibraryState.valid(identifier)
    item = next((row for row in _all()["stickers"] if row["id"] == identifier), None)
    if item is None:
        raise ValueError("这张表情已不存在，请刷新图库")
    return item


def browse(body=None):
    body = {} if body is None else body
    if not isinstance(body, dict) or set(body) - {"query", "favorites_only", "sort"}:
        raise ValueError("图库筛选字段无效")
    query, ordering = body.get("query", ""), body.get("sort", "default")
    if (not isinstance(query, str) or len(query) > 100 or ordering not in {"default", "recent", "title"}
            or type(body.get("favorites_only", False)) is not bool):
        raise ValueError("图库搜索或排序无效")
    data = _all()
    try:
        items = store().annotate(data["stickers"])
    except (OSError, ValueError, sqlite3.Error):
        items = [{**row, "favorite": False, "last_used": None, "use_count": 0, "scene_rejections": 0} for row in data["stickers"]]
        data["library_warning"] = "收藏与使用记录暂不可读，图片仍可浏览。请检查数据目录后重试。"
    if query.strip():
        value = query.strip().casefold()
        items = [row for row in items if value in " ".join(str(row.get(key) or "") for key in
                  ("title", "label", "emotion", "tags", "aliases", "description", "use_when", "attribution")).casefold()]
    if body.get("favorites_only", False):
        items = [row for row in items if row["favorite"]]
    if ordering == "recent":
        items.sort(key=lambda row: -(row["last_used"] or 0))
    if ordering == "title":
        items.sort(key=lambda row: (row.get("title") or row.get("label") or "").casefold())
    return {"ok": True, **data, "stickers": items, "count": len(items), "total_count": data["count"]}


def set_favorite(body):
    if not isinstance(body, dict) or set(body) != {"id", "favorite"}:
        raise ValueError("收藏字段无效")
    _find(body["id"])
    return {"ok": True, **store().set_favorite(body["id"], body["favorite"])}


def feedback(body):
    if not isinstance(body, dict) or set(body) - {"id", "scene", "origin", "reason"}:
        raise ValueError("选图反馈字段无效")
    _find(body.get("id"))
    reason = body.get("reason", "not_suitable")
    if reason not in {"not_suitable", "clear"}:
        raise ValueError("请选择不合适或清除反馈")
    method = store().reject if reason == "not_suitable" else store().clear_feedback
    return {"ok": True, **method(body["id"], scene=body.get("scene", "general"), origin=body.get("origin", ""))}


def preview(body):
    if (not isinstance(body, dict) or set(body) - {"text", "scene"}
            or not isinstance(body.get("text"), str) or not 1 <= len(body["text"].strip()) <= 800):
        raise ValueError("请填写1至800字的试选场景")
    # Load shared regexes from a non-runtime module, not the AstrBot entrypoint.
    policy = _policy()
    scene = body.get("scene", "general")
    if scene not in policy.SCENES:
        raise ValueError("配图场景无效")
    data = _all()
    settings = data["settings"]
    items = [row for row in data["stickers"] if row.get("enabled", True)]
    selection = settings["pack_selection"]
    items = [row for row in items if selection == "all" or row.get("pack_id") == selection
             or selection == "community" and row.get("pack_id") in {"community", "contributed", "materials"}]
    if not settings["custom_enabled"]:
        items = [row for row in items if row.get("pack_id") != "custom"]
    blocked = scene in {"support", "task"} or policy.suppressed_text(body["text"])
    items = policy.lexical_rank(store().annotate(items, scene=scene), body["text"], matches_only=True)[:8] if not blocked else []
    return {"ok": True, "mode": "local_candidates", "model_called": False, "message_sent": False,
            "scene": scene, "candidates": items,
            "message": "此场景优先认真回复，自动配图建议不发图。" if blocked else
                       "当前已启用图库没有文字标签命中的候选。可以换用已有标签关键词；本地试选不识图，也不判断同义语义。" if not items else
                       "这里只列出文字标签命中的本地候选，并按你的反馈排序；不识图、不调用模型、不发送QQ，实际聊天由当前模型决定是否选图。"}
