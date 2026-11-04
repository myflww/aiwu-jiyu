"""Life feature contracts with synthetic identities and mocked transports only.

No real QQ/account write, model invocation, web search or installed profile.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_life"
sys.path.insert(0, str(ROOT / "app"))
from backend import life

shared = life._shared()
policy, search, account, state = (shared[name] for name in ("settings", "search", "account", "state"))


class Transport:
    def __init__(self):
        self.bot = "987654321"
        self.nickname, self.signature = "小鱼", "吃白饭，偶尔干活"
        self.version = "4.18.42"
        self.calls = []
        self.break_write = False
        self.qzone_receipt = {"tid": "test-tid", "time": 1700000000}
        self.status_receipt = ""

    async def __call__(self, api, payload):
        self.calls.append((api, payload))
        if api == "get_login_info":
            return {"user_id": self.bot, "nickname": self.nickname}
        if api == "get_stranger_info":
            return {"user_id": self.bot, "nickname": self.nickname, "long_nick": self.signature, "phoneNum": "never-expose"}
        if api == "get_version_info":
            return {"app_name": "NapCat.Onebot", "app_version": self.version}
        if self.break_write:
            raise TimeoutError("private-transport-error")
        if api == "set_qq_profile":
            self.nickname = payload["nickname"]
            return {"result": 0}
        if api == "set_self_longnick":
            self.signature = payload["longNick"]
            return None
        if api == "set_qq_avatar":
            return None
        if api == "set_diy_online_status":
            return self.status_receipt
        if api == "send_qzone_msg":
            return self.qzone_receipt
        raise AssertionError("Unexpected API " + api)

    def writes(self):
        return [(api, payload) for api, payload in self.calls if api.startswith("set_") or api == "send_qzone_msg"]


class SearchTests(unittest.TestCase):
    def test_defaults_are_off_and_config_is_typed_public_https_only(self):
        self.assertFalse(any(policy.DEFAULTS[k] for k in ("search_enabled", "profile_enabled", "qzone_enabled")))
        self.assertEqual(policy.DEFAULTS["search_provider"], "bing_rss")
        for value in ("http://example.org", "https://127.0.0.1", "https://10.0.0.1/", "https://localhost", "https://u:p@example.org", "https://example.org?q=secret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.validate({"searxng_url": value})
        for body in ({"profile_enabled": 1}, {"search_provider": []}, {"owner_qq": "other"}, {"searxng_url": None}):
            with self.assertRaises(ValueError): policy.validate(body)
        self.assertEqual(policy.validate({"searxng_url": "https://search.example.org/base/"})["searxng_url"], "https://search.example.org/base/search")

    def test_bing_requires_real_rss_and_returns_bounded_untrusted_sources(self):
        raw = b'<rss><channel><item><title>&lt;b&gt;Python&lt;/b&gt;</title><link>https://docs.python.org/</link><description>&lt;script&gt;bad()&lt;/script&gt;Read documentation</description></item><item><title>duplicate</title><link>https://docs.python.org/</link></item><item><link>javascript:alert(1)</link></item><item><link>https://127.0.0.1/</link></item></channel></rss>'
        seen = []
        def reader(request):
            seen.append(request)
            return raw, "text/xml"
        result = search.perform("Python docs", {"search_enabled": True}, reader=reader)
        self.assertEqual(result["results"], [{"title": "Python", "url": "https://docs.python.org/", "snippet": "Read documentation"}])
        self.assertTrue(result["untrusted_content"])
        self.assertEqual(parse_qs(seen[0].full_url.split("?", 1)[1]), {"format": ["rss"], "q": ["Python docs"]})
        self.assertNotIn("Authorization", seen[0].headers)
        for content, mime in ((b'<html>login</html>', "text/html"), (b'<html/>', "text/xml"), (b'<!DOCTYPE rss [<!ENTITY x "x">]><rss/>', "text/xml"), (b'x' * (search.MAX_BYTES + 1), "text/xml")):
            with self.subTest(mime=mime), self.assertRaises(ValueError):
                search.perform("Python docs", {"search_enabled": True}, reader=lambda req: (content, mime))

    def test_searxng_posts_only_keywords_and_cannot_leak_remote_errors(self):
        seen = []
        def reader(request):
            seen.append(request)
            return json.dumps({"results": [{"title": "t", "url": "https://example.org/", "content": "snippet"}]}).encode(), "application/json"
        options = {"search_enabled": True, "search_provider": "searxng", "searxng_url": "https://search.example.org/"}
        result = search.perform("公开技术词", options, reader=reader)
        self.assertEqual(result["provider"], "searxng")
        self.assertEqual(seen[0].method, "POST")
        self.assertEqual(parse_qs(seen[0].data.decode())["q"], ["公开技术词"])
        self.assertNotIn("?", seen[0].full_url)
        with self.assertRaisesRegex(ValueError, "没有编造") as error:
            search.perform("公开技术词", options, reader=Mock(side_effect=OSError("secret-key-or-raw-html")))
        self.assertNotIn("secret-key-or-raw-html", str(error.exception))

    def test_invalid_or_private_query_and_disabled_search_never_reach_transport(self):
        reader = Mock()
        for query in ("", "a" * 201, "QQ:123456789", "sk-" + "x" * 30, "C:\\private\\x", "a\nb"):
            with self.assertRaises(ValueError): search.perform(query, {"search_enabled": True}, reader=reader)
        with self.assertRaises(ValueError): search.perform("public", {}, reader=reader)
        reader.assert_not_called()

    def test_private_dns_and_redirects_fail_before_forwarding(self):
        with patch.object(search.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), patch.object(search, "build_opener") as opener:
            with self.assertRaises(ValueError): search._read(types.SimpleNamespace(full_url="https://search.example.org/"))
            opener.assert_not_called()
        with self.assertRaises(ValueError): search._NoRedirect().redirect_request(None, None, 302, "", {}, "https://outside.example/")


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-life-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = self.root / "stickers/pack"
        self.pack.mkdir(parents=True)
        data = io.BytesIO(); Image.new("RGB", (32, 32), "blue").save(data, "PNG")
        self.image = data.getvalue()
        (self.pack / "happy.png").write_bytes(self.image)
        (self.pack / "catalog.json").write_text(json.dumps({"stickers": [{"id": "happy", "file": "happy.png", "size": len(self.image), "sha256": hashlib.sha256(self.image).hexdigest()}]}), "utf-8")
        self.config = {**policy.DEFAULTS, "enabled": True, "owner_qq": "123456789", "bot_qq": "987654321", "profile_enabled": True, "qzone_enabled": True}
        self.service = account.Accounts(self.root / "data/life", self.pack, lambda: dict(self.config))
        self.transport = Transport()

    def prepare(self, action="nickname", payload=None, origin="test:FriendMessage:123456789"):
        return asyncio.run(self.service.prepare({"action": action, "payload": payload or {"nickname": "大肥鱼"}}, self.transport, origin=origin))

    def confirm(self, draft):
        return asyncio.run(self.service.confirm({"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": True}, self.transport))

    def test_preparation_is_review_only_and_does_not_expose_other_profile_fields(self):
        draft = self.prepare()
        self.assertEqual(draft["review"]["before"], "小鱼")
        self.assertEqual(draft["review"]["after"], "大肥鱼")
        self.assertEqual(draft["review"]["target_bot"], self.config["bot_qq"])
        self.assertEqual(self.transport.writes(), [])
        self.assertNotIn("never-expose", json.dumps(draft))
        self.assertAlmostEqual(draft["expires_at"] - state.time.time(), 600, delta=2)

    def test_nickname_changes_only_one_field_and_replay_never_writes_twice(self):
        draft = self.prepare()
        result = self.confirm(draft)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["verified"])
        self.assertEqual(self.transport.writes(), [("set_qq_profile", {"nickname": "大肥鱼"})])
        with self.assertRaises(ValueError): self.confirm(draft)
        self.assertEqual(len(self.transport.writes()), 1)
        recent = self.service.drafts.recent("123456789", "987654321")
        self.assertNotIn(draft["confirmation_token"], json.dumps(recent))
        self.assertNotIn("大肥鱼", json.dumps(recent))

    def test_token_payload_confirmation_and_cross_owner_cannot_be_forged(self):
        draft = self.prepare()
        for body in ({"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": "true"},
                     {"draft_id": draft["draft_id"], "confirmation_token": "wrong-token" * 4, "confirmed": True},
                     {"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": True, "payload": {"nickname": "tamper"}}):
            with self.assertRaises(ValueError): asyncio.run(self.service.confirm(body, self.transport))
        self.config["owner_qq"] = "333333333"
        with self.assertRaises(ValueError): self.confirm(draft)
        self.assertEqual(self.transport.writes(), [])

    def test_revoked_permission_and_changed_login_are_blocked_before_write(self):
        draft = self.prepare()
        self.config["profile_enabled"] = False
        self.assertEqual(self.confirm(draft)["status"], "blocked")
        self.config["profile_enabled"] = True
        draft = self.prepare()
        self.transport.bot = self.config["owner_qq"]
        self.assertEqual(self.confirm(draft)["status"], "blocked")
        self.assertEqual(self.transport.writes(), [])

    def test_signature_and_avatar_use_the_documented_fixed_action_payload(self):
        draft = self.prepare("signature", {"signature": ""})
        self.assertEqual(self.confirm(draft)["status"], "verified")
        self.assertEqual(self.transport.writes()[0], ("set_self_longnick", {"longNick": ""}))
        draft = self.prepare("avatar", {"avatar_id": "happy"})
        self.assertIn("avatar_url", draft["review"])
        self.assertEqual(self.confirm(draft)["status"], "accepted")
        self.assertEqual(self.transport.writes()[1][0], "set_qq_avatar")
        self.assertTrue(self.transport.writes()[1][1]["file"].startswith("base64://"))
        for value in ("../../private", "https://example.org/x.png", "C:\\secret.png"):
            with self.assertRaises(ValueError): self.prepare("avatar", {"avatar_id": value})

    def test_avatar_tamper_after_review_never_reaches_qq(self):
        draft = self.prepare("avatar", {"avatar_id": "happy"})
        (self.pack / "happy.png").write_bytes(b"changed")
        self.assertEqual(self.confirm(draft)["status"], "blocked")
        self.assertEqual(self.transport.writes(), [])

    def test_online_status_uses_reviewed_qsid_allowlist_and_empty_string_success(self):
        self.assertEqual({key: value["face_id"] for key, value in policy.STATUS_ICONS.items()},
                         {"smile": "14", "focus": "30", "rest": "8", "coffee": "60"})
        draft = self.prepare("status", {"wording": "正在学习"})
        review = draft["review"]
        self.assertIn("会设为在线", review["label"])
        self.assertIn("不是兴趣标签", review["status_notice"])
        self.assertEqual((review["icon_id"], review["icon_label"], review["after"]), ("smile", "微笑", "正在学习"))
        self.assertEqual(self.transport.writes(), [])
        result = self.confirm(draft)
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["verified"])
        self.assertEqual(self.transport.writes(), [("set_diy_online_status", {"face_id": "14", "face_type": "1", "wording": "正在学习"})])
        with self.assertRaises(ValueError): self.confirm(draft)

    def test_online_status_rejects_arbitrary_ids_multiline_and_wrong_service(self):
        for payload in ({"wording": "test", "icon_id": "123"}, {"wording": "test", "icon_id": []},
                        {"wording": "test", "face_id": "123"}, {"wording": "line1\nline2"},
                        {"wording": "x" * 41}, {"wording": "test", "icon_id": "https://example.org/"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError): self.prepare("status", payload)
        self.transport.version = "4.0.0"
        with self.assertRaises(account.Unavailable): self.prepare("status", {"wording": "test", "icon_id": "focus"})
        self.assertEqual(self.transport.writes(), [])

    def test_online_status_unavailable_or_bad_receipt_never_reports_success(self):
        draft = self.prepare("status", {"wording": "休息一下", "icon_id": "rest"})
        self.transport.version = "4.0.0"
        self.assertEqual(self.confirm(draft)["status"], "unavailable")
        self.assertEqual(self.transport.writes(), [])
        self.transport.version = "4.18.42"
        draft = self.prepare("status", {"wording": "咖啡时间", "icon_id": "coffee"})
        self.transport.status_receipt = None
        self.assertEqual(self.confirm(draft)["status"], "uncertain")
        with self.assertRaises(ValueError): self.confirm(draft)
        self.assertEqual(len(self.transport.writes()), 1)

    def test_qzone_real_tid_and_explicit_visibility_and_no_unverified_old_service(self):
        draft = self.prepare("qzone", {"content": "公开测试草稿"})
        self.assertEqual(draft["review"]["visibility"], "private")
        result = self.confirm(draft)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["tid"], "test-tid")
        self.assertEqual(self.transport.writes(), [("send_qzone_msg", {"content": "公开测试草稿", "ugc_right": 64})])
        self.transport.version = "4.0.0"
        with self.assertRaisesRegex(ValueError, "未确认支持"): self.prepare("qzone", {"content": "不应发布", "visibility": "public"})

    def test_ambiguous_write_and_missing_tid_are_uncertain_without_retry(self):
        for failure in ("timeout", "missing_tid"):
            with self.subTest(failure=failure):
                draft = self.prepare("qzone", {"content": "只发一次"})
                self.transport.break_write = failure == "timeout"
                self.transport.qzone_receipt = {}
                result = self.confirm(draft)
                self.assertEqual(result["status"], "uncertain")
                self.assertNotIn("private-transport-error", json.dumps(result))
                with self.assertRaises(ValueError): self.confirm(draft)
        self.assertEqual(len(self.transport.writes()), 2)

    def test_kernel_rejection_and_missing_api_are_never_success_receipts(self):
        for receipt in ({"result": 1001}, {"result": False}):
            draft = self.prepare()
            async def rejected_call(api, payload):
                if api == "set_qq_profile":
                    return receipt
                return await self.transport(api, payload)
            body = {"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": True}
            result = asyncio.run(self.service.confirm(body, rejected_call))
            self.assertEqual(result["status"], "rejected")
            self.assertFalse(result["verified"])
        draft = self.prepare("qzone", {"content": "test"})
        async def unavailable(api, payload):
            if api == "send_qzone_msg": raise account.failure(1404, "Action not found: private debug content")
            return await self.transport(api, payload)
        body = {"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": True}
        result = asyncio.run(self.service.confirm(body, unavailable))
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("private debug", json.dumps(result))
        self.assertEqual(result["official_url"], "https://qzone.qq.com/")

    def test_receipt_storage_failure_cannot_trigger_replay_of_an_actual_write(self):
        draft = self.prepare()
        with patch.object(self.service.drafts, "finish", side_effect=OSError("synthetic write failure")):
            result = self.confirm(draft)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["receipt_saved"])
        self.assertIn("不要重复", result["message"])
        with self.assertRaises(ValueError): self.confirm(draft)
        self.assertEqual(len(self.transport.writes()), 1)

    def test_two_process_style_confirmations_claim_only_once(self):
        draft = self.prepare()
        other = account.Accounts(self.root / "data/life", self.pack, lambda: dict(self.config))
        body = {"draft_id": draft["draft_id"], "confirmation_token": draft["confirmation_token"], "confirmed": True}
        async def scenario():
            return await asyncio.gather(self.service.confirm(body, self.transport), other.confirm(body, self.transport), return_exceptions=True)
        result = asyncio.run(scenario())
        self.assertEqual(sum(isinstance(row, ValueError) for row in result), 1)
        self.assertEqual(len(self.transport.writes()), 1)

    def test_natural_confirmation_requires_same_origin_unique_pending_and_matching_kind(self):
        origin = "qq-test:FriendMessage:123456789"
        draft = self.prepare(origin=origin)
        with self.assertRaises(ValueError): asyncio.run(self.service.confirm(None, self.transport, natural_origin="wrong", kind="profile"))
        with self.assertRaises(ValueError): asyncio.run(self.service.confirm(None, self.transport, natural_origin=origin, kind="qzone"))
        second = self.prepare("signature", {"signature": "test"}, origin)
        with self.assertRaisesRegex(ValueError, "唯一"): asyncio.run(self.service.confirm(None, self.transport, natural_origin=origin, kind="profile"))
        self.service.drafts.cancel("123456789", "987654321", second["draft_id"])
        result = asyncio.run(self.service.confirm(None, self.transport, natural_origin=origin, kind="profile"))
        self.assertEqual(result["draft_id"], draft["draft_id"])
        self.assertEqual(len(self.transport.writes()), 1)

    def test_expired_and_cancelled_drafts_cannot_execute_and_search_budget_is_shared(self):
        draft = self.prepare()
        with patch.object(state.time, "time", return_value=draft["expires_at"] + 1):
            with self.assertRaises(ValueError): self.confirm(draft)
        draft = self.prepare()
        self.service.drafts.cancel("123456789", "987654321", draft["draft_id"])
        with self.assertRaises(ValueError): self.confirm(draft)
        self.service.drafts.reserve_search("123456789", "987654321", now=1700000000)
        with self.assertRaises(ValueError): state.Drafts(self.root / "data/life").reserve_search("123456789", "987654321", now=1700000001)
        self.assertEqual(self.transport.writes(), [])


class BackendTests(unittest.TestCase):
    def test_config_preserves_other_settings_and_status_does_not_use_network(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-life-backend-") as directory:
            config = {"owner_qq": "123456789", "bot_qq": "987654321", "model": {"api_key_enc": "private-existing"}, "pc_access": {"desktop_enabled": False}}
            with patch.object(life, "ASTRBOT", Path(directory) / "astrbot"), patch.object(life.config_store, "load", return_value=config), patch.object(life.config_store, "save") as save, patch.object(life, "_master_allowed", return_value=True), patch.object(life.napcat_api, "_call") as remote:
                result = life.configure({"search_enabled": True})
                self.assertTrue(result["settings"]["search_enabled"])
                self.assertEqual(config["model"]["api_key_enc"], "private-existing")
                self.assertFalse(config["pc_access"]["desktop_enabled"])
                self.assertEqual(life.authorized_tools(), ["dafeiyu_web_search"])
                save.assert_called_once()
                remote.assert_not_called()
                self.assertNotIn("private-existing", json.dumps(result))
                self.assertEqual([row["id"] for row in result["icons"]], ["smile", "focus", "rest", "coffee"])
                self.assertFalse(result["capabilities"]["status"]["available"])

    def test_unsupported_prepare_exposes_unavailable_without_private_profile_or_write(self):
        service = Mock()
        service.prepare = AsyncMock(side_effect=account.Unavailable("当前连接服务未确认支持自定义在线状态"))
        with patch.object(life, "_accounts", return_value=service):
            result = life.prepare({"action": "status", "payload": {"wording": "test"}})
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["ok"])
        self.assertEqual(result["official_url"], "https://im.qq.com/")

    def test_transport_requires_both_webui_and_onebot_success_receipts(self):
        with patch.object(life, "_runtime_config", return_value={"enabled": True, "profile_enabled": True, "qzone_enabled": True}):
            for value in ({"code": 1, "message": "secret"}, {"code": 0, "data": {"status": "failed", "retcode": 1}}, {"code": 0, "data": {"status": "ok", "retcode": 1}}):
                with patch.object(life.napcat_api, "_call", return_value=value):
                    with self.assertRaises(account.Rejected): asyncio.run(life._call("set_self_longnick", {"longNick": "test"}))
            with patch.object(life.napcat_api, "_call", return_value={"code": 0, "data": {"status": "ok", "retcode": 0, "data": {"tid": "real-id"}}}):
                self.assertEqual(asyncio.run(life._call("send_qzone_msg", {"content": "test"})), {"tid": "real-id"})


if __name__ == "__main__":
    unittest.main()
