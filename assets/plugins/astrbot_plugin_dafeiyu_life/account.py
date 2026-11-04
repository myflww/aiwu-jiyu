"""Reviewable own-account actions over documented NapCat interfaces.

This module contains no QQ login, cookies, arbitrary API dispatch, browser
automation or implementation copied from a protocol framework.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import stat

from .settings import IDS, LABELS, QZONE_URL, STATUS_ICONS, identity, normalize
from .state import Drafts

ACTION_LABELS = {"nickname": "修改机器人昵称", "signature": "修改机器人签名", "avatar": "修改机器人头像",
                 "status": "修改机器人自定义在线状态（会设为在线）", "qzone": "发布机器人 QQ 空间说说"}
VISIBILITY = {"private": (64, "仅自己可见"), "friends": (4, "好友可见"), "public": (1, "所有人可见")}


class Rejected(ValueError):
    """An authenticated protocol reply explicitly rejected the operation."""


class Unavailable(Rejected):
    """The connected implementation explicitly lacks the requested action."""


class BeforeDispatchBlocked(ValueError):
    """A local authorization check stopped the transport before sending."""


def failure(retcode, message=""):
    """Classify protocol metadata without returning remote error text."""
    missing = retcode == 1404 or bool(re.search(r"(?:API|action).{0,25}(?:not found|unsupported|not supported|不存在|不支持)", str(message)[:500], re.I))
    if missing:
        return Unavailable("当前连接服务没有提供这个真实接口，请使用QQ官方入口；没有报告操作成功。")
    return Rejected("NapCat返回失败回执，没有报告操作成功。请在机器人QQ核对后重新准备，不会重发这次操作。")


def checked_text(value, label, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
        raise ValueError(f"{label}须为{'0' if empty else '1'}至{maximum}字的文字")
    return value.strip()


def version_tuple(value):
    match = re.search(r"(?:^|[^0-9])(\d+)\.(\d+)\.(\d+)", str(value or "")[:100])
    return tuple(map(int, match.groups())) if match else None


class Accounts:
    def __init__(self, root, sticker_pack, config_provider):
        self.drafts = Drafts(root)
        self.pack = Path(sticker_pack)
        self.config_provider = config_provider

    def config(self, action):
        config = self.config_provider()
        owner, bot = identity(config)
        options = normalize(config)
        if config.get("enabled") is not True or not options["qzone_enabled" if action == "qzone" else "profile_enabled"]:
            raise ValueError("该机器人账号能力尚未开启，或授权已关闭；不会执行资料修改或发动态")
        return owner, bot

    @staticmethod
    async def self_info(call, expected_bot, *, detail=True):
        login = await call("get_login_info", {})
        if not isinstance(login, dict) or str(login.get("user_id") or "") != expected_bot:
            raise ValueError("NapCat 当前登录账号与配置的机器人小号不一致，已停止操作；不会修改主人账号")
        result = {"bot_qq": expected_bot, "nickname": str(login.get("nickname") or "")[:100], "signature": None}
        if detail:
            raw = await call("get_stranger_info", {"user_id": expected_bot, "no_cache": True})
            if not isinstance(raw, dict) or str(raw.get("user_id") or "") != expected_bot:
                raise ValueError("无法核对机器人自己的资料，已停止准备")
            result["nickname"] = str(raw.get("nickname") or result["nickname"])[:100]
            result["signature"] = str(raw.get("long_nick", raw.get("longNick", raw.get("personal_note", ""))) or "")[:500]
        return result

    @staticmethod
    async def runtime(call):
        raw = await call("get_version_info", {})
        if not isinstance(raw, dict):
            raise ValueError("无法读取 NapCat 实际版本，请检查连接服务")
        name = str(raw.get("app_name") or "")[:40]
        version = str(raw.get("app_version") or raw.get("napcat_version") or "")[:60]
        return {"app_name": name, "version": version,
                "qzone_supported": "napcat" in name.casefold() and (version_tuple(version) or (0,)) >= (4, 18, 33),
                "status_supported": "napcat" in name.casefold() and (version_tuple(version) or (0,)) >= (4, 18, 33)}

    def avatar(self, identifier):
        if identifier not in IDS:
            raise ValueError("请选择随附的六种大肥鱼头像之一；不接受任意文件路径或图片网址")
        for path in (self.pack, self.pack.parent, self.pack / "catalog.json", self.pack / (identifier + ".png")):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("头像素材不可使用链接")
        catalog_path = self.pack / "catalog.json"
        if catalog_path.stat().st_size > 64 * 1024 or catalog_path.stat().st_nlink != 1:
            raise ValueError("头像素材清单不可用")
        catalog = json.loads(catalog_path.read_text("utf-8"))
        rows = catalog.get("stickers", [])
        item = next((row for row in rows if isinstance(row, dict) and row.get("id") == identifier), None)
        if item is None or item.get("file") != identifier + ".png":
            raise ValueError("随附头像素材不存在")
        path = self.pack / item["file"]
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 24 <= info.st_size <= 5 * 1024 * 1024:
            raise ValueError("头像素材文件不可用")
        with path.open("rb") as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) != item.get("size") or hashlib.sha256(raw).hexdigest() != item.get("sha256") or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("头像素材校验失败，未向QQ发送图片")
        return raw, item["sha256"]

    async def profile(self, call):
        _, bot = self.config("nickname")
        result = await self.self_info(call, bot)
        return {"ok": True, **result, "runtime": await self.runtime(call), "target": "机器人小号", "tags_supported": False,
                "tags_message": "当前 NapCat 未提供已核验的个人资料标签修改接口，请在 QQ 官方客户端手动管理。"}

    async def prepare(self, body, call, *, origin="desktop"):
        if (not isinstance(body, dict) or set(body) != {"action", "payload"}
                or not isinstance(body.get("action"), str) or body["action"] not in ACTION_LABELS or not isinstance(body.get("payload"), dict)):
            raise ValueError("账号操作准备字段无效")
        action, payload = body["action"], body["payload"]
        owner, bot = self.config(action)
        fields = {"nickname": {"nickname"}, "signature": {"signature"}, "avatar": {"avatar_id"},
                  "status": {"wording", "icon_id"}, "qzone": {"content", "visibility"}}
        if set(payload) - fields[action]:
            raise ValueError("不能为账号操作传入其他目标或任意 API 参数")
        info = await self.self_info(call, bot, detail=action in {"nickname", "signature"})
        review = {"action": action, "label": ACTION_LABELS[action], "target_bot": bot, "before": "", "after": ""}
        if action == "nickname":
            if set(payload) != {"nickname"}:
                raise ValueError("请提供新昵称")
            payload = {"nickname": checked_text(payload["nickname"], "昵称", 40)}
            review.update(before=info["nickname"], after=payload["nickname"])
        elif action == "signature":
            if set(payload) != {"signature"}:
                raise ValueError("请提供新签名；空文字表示清除签名")
            payload = {"signature": checked_text(payload["signature"], "签名", 200, empty=True)}
            review.update(before=info["signature"], after=payload["signature"])
        elif action == "avatar":
            if set(payload) != {"avatar_id"}:
                raise ValueError("请选择大肥鱼头像")
            _, checksum = self.avatar(payload.get("avatar_id"))
            payload = {"avatar_id": payload["avatar_id"], "sha256": checksum}
            review.update(before="当前 QQ 头像（未下载）", after=LABELS[payload["avatar_id"]],
                          avatar_url="/assets/plugins/astrbot_plugin_dafeiyu_stickers/pack/" + payload["avatar_id"] + ".png")
        elif action == "status":
            wording = checked_text(payload.get("wording"), "在线状态", 40)
            if any(character in wording for character in "\r\n\t"):
                raise ValueError("自定义在线状态请使用单行文字")
            icon_id = payload.get("icon_id", "smile")
            if not isinstance(icon_id, str) or icon_id not in STATUS_ICONS:
                raise ValueError("请从微笑、奋斗、睡觉、咖啡四种已核对的基础图标中选择")
            runtime = await self.runtime(call)
            if not runtime["status_supported"]:
                raise Unavailable("当前连接服务未确认支持自定义在线状态；请在QQ官方客户端管理，尚未修改任何状态。")
            icon = STATUS_ICONS[icon_id]
            payload = {"wording": wording, "icon_id": icon_id, "face_id": icon["face_id"], "face_type": icon["face_type"]}
            review.update(before="当前在线状态（未读取）", after=wording, icon_id=icon_id, icon_label=icon["label"],
                          status_notice="会把机器人小号设为在线的自定义状态，图标与文案一起替换；这是在线状态，不是兴趣标签或个性签名。")
        else:
            runtime = await self.runtime(call)
            if not runtime["qzone_supported"]:
                raise Unavailable("当前连接服务未确认支持 QQ 空间发布接口；请使用 QQ 官方空间入口，未发布任何内容")
            visibility = payload.get("visibility", "private")
            if not isinstance(visibility, str) or visibility not in VISIBILITY:
                raise ValueError("请选择仅自己、好友或所有人可见")
            payload = {"content": checked_text(payload.get("content"), "动态正文", 800), "visibility": visibility}
            review.update(after=payload["content"], visibility=visibility, visibility_label=VISIBILITY[visibility][1], official_url=QZONE_URL)
        return self.drafts.create(owner, bot, origin, action, payload, review)

    async def confirm(self, body, call, *, natural_origin=None, kind=None):
        if natural_origin is None:
            if not isinstance(body, dict) or set(body) != {"draft_id", "confirmation_token", "confirmed"} or body.get("confirmed") is not True:
                raise ValueError("需要主人核对预览后明确确认这一次操作")
        owner, bot = identity(self.config_provider())
        # Claim is durable before a remote write. A timeout/crash never leads to
        # an automatic resend or replay, including from another process.
        row = self.drafts.claim(owner, bot, identifier=body.get("draft_id") if body else None,
                                token=body.get("confirmation_token") if body else None, origin=natural_origin, kind=kind)
        result = {"ok": True, "draft_id": row["id"], "action": row["action"], "target_bot": bot, "verified": False}
        dispatched = False
        try:
            current_owner, current_bot = self.config(row["action"])
            if current_owner != row["owner"] or current_bot != row["bot"]:
                raise ValueError("账号配置已经改变，请重新准备并确认")
            await self.self_info(call, bot, detail=False)
            if identity(self.config_provider()) != (owner, bot):
                raise ValueError("等待期间账号配置已经改变，操作已停止")
            self.config(row["action"])
            action, payload = row["action"], row["payload"]
            if action == "qzone":
                if not (await self.runtime(call))["qzone_supported"]:
                    raise Unavailable("当前服务未确认支持 QQ 空间发布，操作已停止")
                parameters = {"content": payload["content"], "ugc_right": VISIBILITY[payload["visibility"]][0]}
                api = "send_qzone_msg"
            elif action == "status":
                if not (await self.runtime(call))["status_supported"]:
                    raise Unavailable("当前服务未确认支持自定义在线状态")
                icon = STATUS_ICONS.get(payload.get("icon_id"))
                if not icon or any(icon[key] != payload.get(key) for key in ("face_id", "face_type")):
                    raise ValueError("预览后的在线状态图标配置发生变化，请重新准备")
                api, parameters = "set_diy_online_status", {"wording": payload["wording"], "face_id": icon["face_id"], "face_type": icon["face_type"]}
            elif action == "nickname":
                api, parameters = "set_qq_profile", {"nickname": payload["nickname"]}
            elif action == "signature":
                api, parameters = "set_self_longnick", {"longNick": payload["signature"]}
            else:
                raw, checksum = self.avatar(payload["avatar_id"])
                if checksum != payload["sha256"]:
                    raise ValueError("预览后的头像素材发生变化，请重新准备")
                api, parameters = "set_qq_avatar", {"file": "base64://" + base64.b64encode(raw).decode("ascii")}
            dispatched = True
            receipt = await call(api, parameters)
            if (isinstance(receipt, dict) and "result" in receipt
                    and not (type(receipt["result"]) in (int, str) and str(receipt["result"]) == "0")):
                raise Rejected("QQ资料接口返回了失败结果，没有报告修改成功。")
            if action == "qzone":
                if not isinstance(receipt, dict) or not isinstance(receipt.get("tid"), str) or not 1 <= len(receipt["tid"]) <= 200:
                    raise RuntimeError("发布后未收到可核对的说说标识")
                result.update(status="accepted", tid=receipt["tid"], official_url=QZONE_URL,
                              message="NapCat 已返回 QQ 空间说说标识。请在机器人小号空间核对；不会再次发布这条准备。")
            elif action == "status":
                if not isinstance(receipt, str):
                    raise RuntimeError("在线状态接口未返回已核验的字符串回执")
                result.update(status="accepted", message="NapCat 已返回自定义在线状态成功回执；小号已请求切换为在线的自定义状态，请在QQ核对图标和文案显示。")
            else:
                result.update(status="accepted", message="NapCat 已返回账号修改成功回执；显示或审核可能有延迟，请在机器人小号资料页核对。")
                if action in {"nickname", "signature"}:
                    try:
                        current = await self.self_info(call, bot)
                        verified = current[action] == payload[action]
                        if verified:
                            result.update(status="verified", verified=True, message="已收到成功回执，并重新读取确认机器人小号资料已更新。")
                    except Exception:
                        pass
        except BeforeDispatchBlocked:
            result.update(status="blocked", message="发送请求前授权已关闭或账号不一致，操作已停止；没有发送修改或发布请求。")
        except Unavailable:
            result.update(status="unavailable", message="当前连接服务没有提供这个真实接口；未报告修改或发布成功，请使用QQ官方客户端或QQ空间入口。", official_url=QZONE_URL if row["action"] == "qzone" else "https://im.qq.com/")
        except Rejected:
            result.update(status="rejected", message="NapCat或QQ返回了失败回执，没有报告操作成功。请在机器人小号核对后重新准备；不会重发这次操作。")
        except Exception:
            result.update(status="uncertain" if dispatched else "blocked",
                          message="这次操作已尝试，但结果尚未确认。请先到机器人 QQ 核对；为避免重复，不会重发这次准备。" if dispatched else
                          "执行前的账号、权限、素材或服务能力核对未通过；没有发送修改或发布请求，请检查后重新准备。")
        try:
            self.drafts.finish(row["id"], result)
            result["receipt_saved"] = True
        except Exception:
            # The durable claim already prevents replay. A failure to save the
            # final receipt must not turn an actual write into a retry request.
            result["receipt_saved"] = False
            result["message"] += " 本机最终回执未能保存，请先核对QQ，不要重复执行。"
        return result
