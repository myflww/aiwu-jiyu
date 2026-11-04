"""Five complete local cards, applied through AstrBot's native persona API.

No model calls, invented conversation history or silent overwrite on startup.
Only the fixed app persona's prompt is changed; tool lists are independent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid

from backend import astrbot_api, config_store, processes
from backend.paths import ASSETS, RUN_DIR, ASTRBOT_PORT

_lock = threading.RLock()
_IDS = {"natural", "classic", "gentle", "playful", "study"}
_BACKUP = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.json\Z")
_LIMIT = 128 * 1024


def _runtime_online():
    # A shared loopback port is not proof that this selected data home owns it.
    return config_store.is_installed() and processes.port_up(ASTRBOT_PORT)


def _read(path: Path, maximum=_LIMIT):
    roots = (ASSETS / "persona", RUN_DIR / "persona-backups")
    anchor = next((root for root in roots if path.is_relative_to(root)), None)
    if anchor is None:
        raise ValueError("人格文件超出固定目录。")
    relative = path.relative_to(anchor)
    if any(part in {".", ".."} for part in relative.parts):
        raise ValueError("人格文件路径无效。")
    current = anchor
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        details = current.lstat()
        if (stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400
                or getattr(current, "is_junction", lambda: False)()):
            raise ValueError("人格文件目录不能通过链接读取。")
    if anchor.resolve() not in path.resolve().parents:
        raise ValueError("人格文件超出固定目录。")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > maximum):
        raise ValueError("人格文件不可读取，请检查本机数据目录。")
    with path.open("rb") as stream:
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("人格文件超过可管理大小。")
    return value.decode("utf-8-sig")


def catalog():
    root = ASSETS / "persona"
    data = json.loads(_read(root / "cards.json"))
    if (data.get("schema_version") != 1 or not isinstance(data.get("cards"), list)
            or len(data["cards"]) != 5 or {r.get("id") for r in data["cards"]} != _IDS):
        raise ValueError("随附人格卡清单无效。")
    return data


def prompt(card_id="natural"):
    if not isinstance(card_id, str) or card_id not in _IDS:
        raise ValueError("请选择随附的五张人格卡。")
    root = ASSETS / "persona"
    data = catalog()
    card = next(r for r in data["cards"] if r["id"] == card_id)
    if card.get("file") != f"cards/{card_id}.md" or data.get("common_file") != "common-v150.md":
        raise ValueError("人格卡路径无效。")
    common = _read(root / data["common_file"])
    body = _read(root / card["file"])
    return common.strip() + "\n\n" + body.strip()


def _revision(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _directory():
    path = RUN_DIR / "persona-backups"
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("人格备份目录不能是链接。")
    return path


def backups():
    directory = _directory()
    rows = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), reverse=True)[:20]:
            if not _BACKUP.fullmatch(path.name):
                continue
            try:
                record = json.loads(_read(path))
                if record.get("persona_id") != config_store.PERSONA_ID:
                    continue
                rows.append({"id": path.name, "created_at": record.get("created_at"),
                             "card_before": record.get("card_before", "custom")})
            except (OSError, ValueError, TypeError):
                continue
    return rows


def _find_persona(rows, persona_id):
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise ValueError("运行人格目录格式不受支持，请在 AstrBot 后台检查。")
    row = next((r for r in rows if r.get("persona_id") == persona_id), None)
    if row is not None and (not isinstance(row.get("system_prompt"), str)
                            or len(row["system_prompt"].encode("utf-8")) > 64 * 1024):
        raise ValueError("当前人格较大或格式不受支持，请在 AstrBot 后台管理。")
    return row


def _current(token):
    return _find_persona(astrbot_api.list_personas(token), config_store.PERSONA_ID)


def _current_and_baseline(token):
    # Read one native snapshot, never SQL or the fallback personality template.
    rows = astrbot_api.list_personas(token)
    current = _find_persona(rows, config_store.PERSONA_ID)
    return current, _find_persona(rows, "default") if current is None else None


def _creation_fields(baseline):
    if baseline is None:
        return {"begin_dialogs": [], "tools": [], "skills": [], "custom_error_message": ""}
    fields = {}
    for name in ("tools", "skills"):
        value = baseline.get(name)
        if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
            raise ValueError("默认人格工具或技能格式异常，未创建可能丢失权限的新人格。")
        # None is the native all-active mode; [] is explicitly no grants.
        fields[name] = value
    dialogs = baseline.get("begin_dialogs")
    if dialogs is not None and (not isinstance(dialogs, list) or len(dialogs) % 2):
        raise ValueError("默认人格开场格式异常，请先在 AstrBot 后台检查。")
    fields["begin_dialogs"] = dialogs or []
    message = baseline.get("custom_error_message")
    if message is not None and not isinstance(message, str):
        raise ValueError("默认人格报错回复格式异常，未修改人格。")
    # This matches the native PersonaService normalization on creation.
    fields["custom_error_message"] = message.strip() or None if message is not None else None
    try:
        encoded = json.dumps(fields, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > _LIMIT:
            raise ValueError()
        return json.loads(encoded)  # Detached lists; never mutate the baseline.
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("默认人格配置超过可管理范围或格式异常，未修改人格。") from None


def _baseline_revision(baseline):
    if baseline is None:
        return None
    fields = {"system_prompt": baseline["system_prompt"], **_creation_fields(baseline)}
    return _revision(json.dumps(fields, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _card_for(text):
    digest = _revision(text)
    return next((key for key in _IDS if _revision(prompt(key)) == digest), None)


def status():
    data = catalog()
    value = {"ok": True, "cards": data["cards"], "default": data["default"],
             "active_card_id": None, "active_name": "尚未读取运行人格", "revision": None,
             "runtime_online": _runtime_online(), "can_apply": False,
             "backups": backups(), "notice": "切换只改变人格提示词；聊天、记忆和权限独立保留。"}
    if not value["runtime_online"]:
        value["notice"] = "对话服务未启动。可以先预览，启动后再应用人格卡。"
        return value
    try:
        row = _current(astrbot_api.dashboard_token())
        value["can_apply"] = True
        if row is None:
            value["active_name"] = "尚未创建大肥鱼人格"
        else:
            selected = _card_for(row["system_prompt"])
            value.update(active_card_id=selected, revision=_revision(row["system_prompt"]),
                         active_name=next((r["name"] for r in data["cards"] if r["id"] == selected), "自定义／旧版人格"),
                         tool_mode="all_active" if row.get("tools") is None else "selected",
                         selected_tool_count=None if row.get("tools") is None else len(row.get("tools", [])))
    except Exception:
        value["notice"] = "暂时无法读取 AstrBot 人格，请先检查本地后台登录和服务状态。"
    return value


def preview(body):
    if not isinstance(body, dict) or set(body) != {"card_id"}:
        raise ValueError("人格预览参数无效。")
    text = prompt(body["card_id"])
    card = next(r for r in catalog()["cards"] if r["id"] == body["card_id"])
    return {"ok": True, "card": card, "prompt": text, "sha256": _revision(text)}


def _backup(row, before):
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    existing = [p for p in directory.glob("*.json") if _BACKUP.fullmatch(p.name)]
    source_id = row.get("persona_id", config_store.PERSONA_ID)
    for candidate in existing[:20]:
        try:
            previous = json.loads(_read(candidate))
            if (previous.get("persona_id") == config_store.PERSONA_ID
                    and previous.get("source_persona_id", config_store.PERSONA_ID) == source_id
                    and previous.get("system_prompt") == row["system_prompt"]
                    and previous.get("sha256") == _revision(row["system_prompt"])):
                return candidate.name
        except (OSError, ValueError, TypeError):
            continue
    if len(existing) >= 20:
        raise ValueError("已保留20份人格备份，请先整理备份目录再切换；没有删除旧备份。")
    identifier = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json"
    record = {"persona_id": config_store.PERSONA_ID, "system_prompt": row["system_prompt"],
              "created_at": time.time(), "card_before": before or "custom",
              "sha256": _revision(row["system_prompt"])}
    if source_id == "default":
        record["source_persona_id"] = "default"
    payload = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".persona-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    try:
        os.replace(temporary, directory / identifier)
    finally:
        temporary.unlink(missing_ok=True)
    return identifier


def _replace(text, expected, card_id):
    if not _runtime_online():
        raise ValueError("请先启动对话服务，再应用人格。")
    token = astrbot_api.dashboard_token()
    row, baseline = _current_and_baseline(token)
    revision = _revision(row["system_prompt"]) if row else None
    if expected != revision:
        raise ValueError("运行人格已变化，请刷新后重新预览。")
    if row and row["system_prompt"] == text:
        return {"ok": True, "changed": False, "active_card_id": card_id, "revision": revision}
    inherited = _creation_fields(baseline) if row is None else None
    baseline_revision = _baseline_revision(baseline)
    original = row if row is not None else baseline
    backup_id = _backup(original, _card_for(original["system_prompt"])) if original else None
    # Recheck just before the native write. The native API has no transactional
    # ETag; an external dashboard write at exactly the same time remains last-win.
    latest, latest_baseline = _current_and_baseline(token)
    if (_revision(latest["system_prompt"]) if latest else None) != revision:
        raise ValueError("人格在备份期间被其他窗口修改，已停止应用。")
    if row is None and _baseline_revision(latest_baseline) != baseline_revision:
        raise ValueError("默认人格在备份期间被其他窗口修改，已停止创建；请刷新后重试。")
    if row:
        result = astrbot_api._request("/api/v1/personas/by-id",
            {"persona_id": config_store.PERSONA_ID, "system_prompt": text}, token, method="PUT")
    else:
        result = astrbot_api._request("/api/v1/personas", {
            "persona_id": config_store.PERSONA_ID, "system_prompt": text,
            **inherited}, token)
    if result.get("status") != "ok":
        raise ValueError("AstrBot 未确认人格修改，请刷新检查。")
    confirmed = _current(token)
    if not confirmed or confirmed["system_prompt"] != text:
        raise ValueError("人格修改结果尚未确认；原人格备份已保留，请刷新检查。")
    if row is None and baseline is not None and _creation_fields(confirmed) != inherited:
        raise ValueError("新人格的工具或技能继承尚未确认；原提示词备份已保留，请在后台检查。")
    config = config_store.load()
    config["persona"] = {"card_id": card_id, "prompt_revision": _revision(text)}
    config_store.save(config)
    # Rhythm follows the same selected card without restarting the QQ service.
    from backend import companion
    companion.sync_config(config)
    return {"ok": True, "changed": True, "active_card_id": card_id,
            "revision": _revision(text), "backup_id": backup_id,
            "notice": "人格已更新；聊天和记忆保留，旧会话里的措辞不会被删除。"}


def apply(body):
    if not isinstance(body, dict) or set(body) != {"card_id", "expected_revision"}:
        raise ValueError("人格应用参数无效。")
    text = prompt(body["card_id"])
    with _lock:
        return _replace(text, body["expected_revision"], body["card_id"])


def restore(body):
    if (not isinstance(body, dict) or set(body) != {"backup_id", "expected_revision", "confirmed"}
            or body.get("confirmed") is not True
            or not isinstance(body.get("backup_id"), str) or not _BACKUP.fullmatch(body["backup_id"])):
        raise ValueError("请明确选择并确认要恢复的原人格。")
    with _lock:
        value = json.loads(_read(_directory() / body["backup_id"]))
        text = value.get("system_prompt")
        if (value.get("persona_id") != config_store.PERSONA_ID or not isinstance(text, str)
                or len(text.encode("utf-8")) > 64 * 1024 or _revision(text) != value.get("sha256")):
            raise ValueError("人格备份校验失败。")
        return _replace(text, body["expected_revision"], _card_for(text))


def grant_bundled_tools(names):
    """An explicit capability enable may add only its own bounded tool names.

    None already means all active tools in AstrBot; [] means no tools, not all.
    No card switch invokes this permission operation by itself.
    """
    allowed = {"send_dafeiyu_sticker", "dafeiyu_web_search", "dafeiyu_read_qq_profile",
               "dafeiyu_prepare_nickname", "dafeiyu_prepare_signature",
               "dafeiyu_prepare_avatar", "dafeiyu_prepare_qzone", "dafeiyu_prepare_status"}
    if not isinstance(names, list) or not all(isinstance(n, str) and n in allowed for n in names):
        raise ValueError("不能向人格添加不受支持的工具。")
    if not names:
        return {"ok": True, "changed": False}
    if not _runtime_online():
        return {"ok": True, "changed": False, "pending": True,
                "notice": "能力开关已保存；启动服务后再同步人格工具。"}
    with _lock:
        token = astrbot_api.dashboard_token()
        row = _current(token)
        if not row or row.get("tools") is None:
            return {"ok": True, "changed": False}
        tools = row.get("tools")
        if not isinstance(tools, list) or not all(isinstance(n, str) for n in tools):
            raise ValueError("当前人格工具清单格式异常，请在后台检查。")
        merged = list(dict.fromkeys([*tools, *names]))
        if merged == tools:
            return {"ok": True, "changed": False}
        result = astrbot_api._request("/api/v1/personas/by-id",
            {"persona_id": config_store.PERSONA_ID, "tools": merged}, token, method="PUT")
        if result.get("status") != "ok":
            raise ValueError("能力已保存，但人格工具同步未确认，请检查本地后台。")
        return {"ok": True, "changed": True}
