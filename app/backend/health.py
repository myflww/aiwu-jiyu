"""Read-only readiness evidence. A configured switch is never a delivery test."""
from __future__ import annotations

import time

from backend import companion, config_store, lovehouse, napcat_api, plugins, processes, stickers

REASONS = {"all", "proactive", "stickers", "memory", "computer"}


def snapshot(reason: str = "all") -> dict:
    if reason not in REASONS:
        raise ValueError("请选择全部、主动聊天、表情、记忆或电脑操作体检")
    checks = []

    def add(identifier, label, status, message, evidence=None, actions=None, scope=()):
        if reason == "all" or not scope or reason in scope:
            checks.append(dict(id=identifier, label=label, status=status, message=message,
                               evidence=evidence or {}, actions=actions or []))

    def action(code, label, target):
        return {"code": code, "label": label, "target": target}

    config = config_store.load()
    if not isinstance(config, dict):
        config = {}
    try:
        service = processes.status()
    except Exception:
        service = {}
    service = service if isinstance(service, dict) else {}
    def listening(name):
        row = service.get(name)
        return isinstance(row, dict) and row.get("up") is True
    astrbot, napcat, bridge = listening("astrbot"), listening("napcat"), listening("onebot")
    add("astrbot", "对话服务", "ok" if astrbot else "blocked",
        "对话端口正在监听；这不单独证明模型可用。" if astrbot else "对话服务未运行，先在首页一键开启。",
        {"listening": astrbot}, [] if astrbot else [action("open_overview", "前往一键开启", "overview")])
    add("qq_service", "QQ 连接服务", "ok" if napcat else "blocked",
        "QQ 连接服务已启动，继续检查账号登录。" if napcat else "QQ 连接服务未启动。",
        {"listening": napcat}, [] if napcat else [action("open_overview", "前往一键开启", "overview")])
    logged_in = None
    if napcat:
        try:
            logged_in = napcat_api.login_status().get("isLogin") is True
        except Exception:
            pass
    add("qq_login", "QQ 账号登录", "ok" if logged_in is True else "blocked" if logged_in is False else "unknown",
        "QQ 已登录；消息是否送达仍以发送回执为准。" if logged_in is True else
        "QQ 尚未登录，请在首页扫码或尝试已授权账号。" if logged_in is False else "尚未确认 QQ 登录，请先启动连接服务。",
        {"logged_in": logged_in}, [] if logged_in else [action("open_overview", "检查 QQ 登录", "overview")])
    add("onebot", "对话与 QQ 通道", "ok" if bridge else "blocked", "消息通道正在监听。" if bridge else
        "消息通道尚未监听；开启服务后仍未恢复，可在诊断日志查看连接错误。", {"listening": bridge},
        [] if bridge else [action("open_logs", "查看诊断日志", "logs")])
    try:
        key_present = bool(config_store.api_key(config))
    except Exception:
        key_present = False
    model = config.get("model")
    model_name = isinstance(model, dict) and bool(model.get("model"))
    add("model", "聊天模型", "ok" if key_present and model_name else "blocked",
        "已配置可读取的模型密钥；本次体检未调用模型，余额、限流及服务连通性仍待实际请求确认。" if key_present and model_name
        else "模型或可读取的 API Key 尚未配置，请到设置补充。",
        {"model_configured": model_name, "key_readable": key_present, "model_request_tested": False},
        [action("open_settings", "检查模型设置", "settings")])
    try:
        inventory = {item["id"]: item for item in plugins.list_plugins()}
    except Exception:
        inventory = {}
    names = [("proactive", companion.PLUGIN_NAME, "主动陪伴"), ("stickers", stickers.PLUGIN_ID, "表情插件"),
             ("memory", "astrbot_plugin_love_house", "长期记忆插件"), ("computer", "astrbot_plugin_pc_operator", "电脑操作插件")]
    for scope, identifier, label in names:
        row = inventory.get(identifier, {})
        configured = row.get("installed") is True and row.get("enabled") is True
        pending = row.get("pending_change") is True
        add(identifier, label, "attention" if pending else "ok" if configured else "blocked",
            "配置更改尚待服务应用。" if pending else "插件文件与启用配置已就绪；不代表已完成一次真实操作。" if configured
            else "插件未安装或开关未启用，可在插件百宝箱检查。",
            {"installed": row.get("installed") is True, "enabled": row.get("enabled") is True,
             "pending_change": pending, "real_operation_tested": False},
            [action("open_plugins", "查看插件百宝箱", "plugins")], (scope,))
    try:
        c = companion.status(config)
        runtime = c.get("runtime") or {}
        worker = runtime.get("worker_running") is True
        code = runtime.get("code")
        good = c.get("proactive_enabled") is True and worker
        status = "ok" if good and code in {"waiting_interval", "ready", "sent", "generating"} else "attention" if good else "blocked"
        add("proactive_worker", "主动聊天实际状态", status,
            str(runtime.get("message") or "尚未收到主动聊天任务的心跳。")[:300],
            {"enabled": c.get("proactive_enabled") is True, "worker_running": worker,
             "session_known": runtime.get("session_known") is True, "code": str(code or "not_running")[:48],
             "last_receipt": runtime.get("last_receipt") if runtime.get("last_receipt") in {"not_sent", "adapter_accepted", "delivery_uncertain"} else None},
            [action("open_companion", "查看陪伴设置", "plugins")], ("proactive",))
    except Exception:
        add("proactive_worker", "主动聊天实际状态", "unknown", "暂时无法读取陪伴任务状态。", scope=("proactive",))
    try:
        prefs = stickers.load_settings()
        automatic = prefs.get("auto_match_enabled") is True and prefs.get("scenario_policy") != "manual"
        add("sticker_rule", "自动配图规则", "ok" if automatic else "attention",
            "自动配图已允许，仍受语境、概率、间隔和可用素材限制；不合适时保留文字回复。" if automatic else
            "当前未允许自动配图；明确点图与自动配图是不同入口。",
            {"automatic_allowed": automatic, "selection_is_guaranteed": False},
            [action("open_stickers", "查看配图与素材", "plugins")], ("stickers",))
    except Exception:
        add("sticker_rule", "自动配图规则", "unknown", "暂时无法读取自动配图设置。", scope=("stickers",))
    try:
        info, memory = lovehouse.detect(), lovehouse.plugin_status()
        running = info.get("running") is True
        bindings = memory.get("bindings", 0)
        bindings = bindings if type(bindings) is int and 0 <= bindings <= 100 else 0
        add("memory_service", "爱之屋记忆连接", "ok" if running and memory.get("enabled") is True and bindings else "blocked",
            "爱之屋在线且存在已登记的聊天连接；本次没有读取或写入回忆。" if running and memory.get("enabled") is True and bindings
            else "记忆需要爱之屋在线、记忆插件启用，并登记目标 QQ 会话。",
            {"service_running": running, "enabled": memory.get("enabled") is True, "registered_sources": bindings,
             "recall_or_write_tested": False}, [action("open_memory", "检查记忆小屋", "lovehouse")], ("memory", "proactive"))
    except Exception:
        add("memory_service", "爱之屋记忆连接", "unknown", "暂时无法读取本机爱之屋状态。",
            actions=[action("open_memory", "检查记忆小屋", "lovehouse")], scope=("memory", "proactive"))
    pc = config.get("pc_access")
    pc = pc if isinstance(pc, dict) else {}
    add("computer_grants", "电脑操作授权", "ok" if pc.get("enabled") is True else "attention",
        "工作目录操作已授权；桌面查看与输入仍由独立开关控制。" if pc.get("enabled") is True else "电脑操作权限关闭，可按需在插件页授权。",
        {"workspace_enabled": pc.get("enabled") is True, "desktop_enabled": pc.get("desktop_enabled") is True},
        [action("open_computer", "查看电脑操作权限", "plugins")], ("computer",))
    overall = "blocked" if any(c["status"] == "blocked" for c in checks) else "attention" if any(c["status"] != "ok" for c in checks) else "ready"
    return {"ok": True, "checked_at": time.time(), "reason": reason, "overall": overall, "checks": checks,
            "summary": "发现需要处理的连接或设置，请按下方指引检查。" if overall == "blocked" else
            "基础配置已就绪，部分能力仍需实际聊天确认。", "readonly": True,
            "model_test_performed": False, "qq_test_performed": False}
