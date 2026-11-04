"""Synthetic evidence only: no private libraries, model or QQ transport."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("growth_test_policy", ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion/growth.py")
growth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(growth)
OWNER, BOT, UMO, LIB = "123456789", "987654321", "qq-test:FriendMessage:123456789", "lib_test"


def record(identifier="fact_one", value="合成用户希望学习摄影", **changes):
    item = {"id": identifier, "layer": "fact", "subject": "user", "value": value,
            "interpretation": "current_memory", "domain": "real", "epistemic_status": "user_confirmed_statement",
            "validity": {"active": True, "superseded_by": None, "valid_from": None, "valid_to": None},
            "source": {"kind": "user_confirmed", "evidence": [{"id": identifier, "role": "user", "source_event_at": 1000}], "evidence_omitted": 0}}
    item.update(changes)
    return item


def packet(records=None, library=LIB):
    return {"schema": "love-house-memory-context/v1", "material_role": "evidence_not_instructions",
            "identity": {"library_id": library, "is_demo": False}, "records": records if records is not None else [record()]}


def native_node(identifier="fact_one", text="合成用户希望学习摄影", **changes):
    item = {"id": identifier, "text": text, "meta": {"source": "user_confirmed", "library_id": LIB}}
    item.update(changes)
    return item


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-growth-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "growth"
        self.now = 1762220000.0
        self.store = growth.Growth(self.root, clock=lambda: self.now)

    def bind(self, event="event1", records=None, **kwargs):
        return self.store.bind_evidence(OWNER, BOT, UMO, event, packet(records), **kwargs)

    def propose(self, event="event1", behavior="shorter", ids=None):
        return self.store.propose(OWNER, BOT, UMO, event, ids or ["fact_one"], "合成推测：也许分清重点更合适", "DISPLAY_ONLY_FREE_PLAN", behavior)

    def entries(self):
        return self.store.entries(OWNER, BOT, LIB)

    def change(self, item, action, **kwargs):
        return self.store.change(OWNER, BOT, LIB, item["id"], action, item["revision"], confirmed=True, **kwargs)

    def test_read_only_and_unrecognized_feedback_do_not_create_a_database(self):
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB), growth._NORMAL)
        self.assertEqual(self.store.observe_feedback(OWNER, BOT, UMO, "event1", "今天吃了合成晚饭", LIB)["learned"], [])
        self.assertFalse(self.root.exists())

    def test_explicit_feedback_is_enum_only_latest_opposite_wins_and_retries_are_idempotent(self):
        text = "小鱼，请你以后少反问，别开玩笑，等我说完，简短点，少发表情"
        result = self.store.observe_feedback(OWNER, BOT, UMO, "event1", text, LIB)
        self.assertEqual(len(result["learned"]), 5)
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB), {"length": "short", "questions": "fewer", "humor": "less", "wait": "more", "stickers": "less"})
        self.store.observe_feedback(OWNER, BOT, UMO, "event1", text, LIB)
        self.assertEqual(len(self.entries()), 5)
        self.now += 1
        self.store.observe_feedback(OWNER, BOT, UMO, "event2", "可以反问，可以开玩笑，不用等我说完，详细一点，可以发表情", LIB)
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB), {"length": "detailed", "questions": "normal", "humor": "normal", "wait": "normal", "stickers": "normal"})
        with self.store._db() as db:
            stored = "\n".join(row[0] for row in db.execute("SELECT data FROM entries"))
        self.assertNotIn(text, stored)
        self.assertTrue(all(len(e.get("source_quote", "")) < 30 for e in self.entries()))

    def test_quotations_conditionals_questions_and_double_negatives_are_not_guessed(self):
        for index, text in enumerate(("他说少反问", "比如少发表情", "如果你简短点会怎样", "“别开玩笑”是什么意思", "你是不是应该少反问", "不要少反问", "老师让我简短点")):
            self.assertEqual(self.store.observe_feedback(OWNER, BOT, UMO, str(index), text, LIB)["learned"], [], text)
        self.assertFalse(self.root.exists())

    def test_owner_bot_library_and_private_origin_remain_isolated(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "e1", "少反问", LIB)
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, "lib_other")["questions"], "normal")
        self.assertEqual(self.store.preferences(OWNER, "876543210", UMO, LIB)["questions"], "normal")
        self.assertEqual(self.store.preferences(OWNER, BOT, "other:FriendMessage:" + OWNER, LIB)["questions"], "normal")
        self.assertEqual(self.store.entries("234567890", BOT, LIB), [])
        for wrong in ("qq-test:GroupMessage:" + OWNER, "qq-test:FriendMessage:234567890", "qq-test:FriendMessage:" + OWNER + ":extra"):
            with self.assertRaises(ValueError):
                self.store.observe_feedback(OWNER, BOT, wrong, "e2", "少反问", LIB)

    def test_no_current_material_or_new_event_can_borrow_previous_evidence(self):
        self.bind()
        self.propose()
        self.assertIn("说得简短一点", self.store.context(OWNER, BOT, UMO, "event1", LIB))
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "different", LIB), "")
        self.assertEqual(self.store.bind_evidence(OWNER, BOT, UMO, "different", "ordinary user text")["ok"], False)
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "event1", LIB), "")
        with self.assertRaises(ValueError):
            self.propose()

    def test_inactive_assistant_roleplay_and_cross_participant_material_cannot_seed_reflections(self):
        bad = [record("off", validity={"active": False}), record("assistant", subject="agent"),
               record("roleplay", domain="roleplay"), record("persona", layer="persona_history"),
               record("peer", source={"kind": "conversation_turn", "evidence": [{"id": "peer", "role": "user", "participant_scope": "external_participant"}]}),
               record("expired", validity={"active": True, "valid_to": self.now - 1})]
        self.assertEqual(self.bind(records=bad)["record_ids"], [])
        with self.assertRaises(ValueError):
            self.propose(ids=["off"])

    def test_model_trial_uses_only_code_mapping_and_never_promotes_text_to_a_fact_or_command(self):
        self.bind()
        proposal = self.propose()
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["behavior_trial"], "shorter")
        context = self.store.context(OWNER, BOT, UMO, "event1", LIB)
        self.assertIn("说得简短一点", context)
        self.assertNotIn("DISPLAY_ONLY_FREE_PLAN", context)
        self.assertNotIn("合成推测", context)
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB)["length"], "normal")
        self.now += 3601
        self.bind("event2")
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "event2", LIB), "")

    def test_explicit_opposite_feedback_overrides_an_unexpired_model_trial(self):
        self.bind()
        self.propose(behavior="fewer_questions")
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback2", "可以反问", LIB)
        context = self.store.context(OWNER, BOT, UMO, "event1", LIB)
        self.assertIn("可以自然提问", context)
        self.assertNotIn("减少反问", context)

    def test_changed_or_missing_evidence_disables_trial_and_reviewed_hypothesis(self):
        self.bind()
        self.propose()
        item = self.entries()[0]
        self.change(item, "approve", verify=lambda *_: True)
        context = self.store.context(OWNER, BOT, UMO, "event1", LIB)
        self.assertIn("合成推测", context)
        self.assertNotIn("DISPLAY_ONLY_FREE_PLAN", context)
        self.bind("event2", [record(value="已经更正的合成事实")])
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "event2", LIB), "")
        self.bind("event3", [])
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "event3", LIB), "")

    def test_review_checks_revision_and_scope_and_pause_resume_never_unpauses_a_newer_choice(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback1", "少反问", LIB)
        item = self.entries()[0]
        self.change(item, "pause")
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB)["questions"], "normal")
        with self.assertRaises(ValueError):
            self.change(item, "resume")
        paused = self.entries()[0]
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback2", "可以反问", LIB)
        with self.assertRaises(ValueError):
            self.change(paused, "resume")
        with self.assertRaises(ValueError):
            self.store.change("234567890", BOT, LIB, paused["id"], "resume", paused["revision"], confirmed=True)

    def test_approval_needs_current_native_evidence_and_preserves_pending_state_on_rejection(self):
        self.bind()
        self.propose()
        item = self.entries()[0]
        with self.assertRaises(ValueError):
            self.change(item, "approve", verify=lambda *_: False)
        self.assertEqual(self.entries()[0]["status"], "pending")
        with self.assertRaises(ValueError):
            self.store.change(OWNER, BOT, LIB, item["id"], "approve", item["revision"], confirmed=False, verify=lambda *_: True)
        self.assertEqual(self.change(item, "approve", verify=lambda *_: True)["status"], "approved")
        self.assertEqual(self.entries()[0]["epistemic_status"], "model_hypothesis")

    def test_native_review_rejects_changed_disabled_expired_and_other_library_nodes(self):
        witness = growth._witness(record(), self.now)
        self.assertTrue(growth.witness_is_current(witness, {"fact_one": native_node()}, LIB, now=self.now))
        for node in (native_node(text="different"), native_node(meta={"source": "user_confirmed", "memory_disabled": True}),
                     native_node(superseded_by="new_id"), native_node(valid_to=self.now - 1),
                     native_node(meta={"source": "user_confirmed", "library_id": "lib_other"})):
            self.assertFalse(growth.witness_is_current(witness, {"fact_one": node}, LIB, now=self.now))

    def test_proactive_evidence_is_not_a_received_user_event_or_reflection_permission(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback", "简短点", LIB)
        self.bind("actual_proactive_output", origin="proactive")
        self.assertIn("说得简短一点", self.store.context(OWNER, BOT, UMO, "actual_proactive_output", LIB))
        with self.assertRaises(ValueError):
            self.propose("actual_proactive_output")

    def test_durable_rate_limits_and_single_real_input_do_not_spawn_extra_model_work(self):
        self.bind("e1")
        self.propose("e1")
        self.bind("e2")
        with self.assertRaisesRegex(ValueError, "10分钟"):
            self.propose("e2")
        for event in ("e2", "e3"):
            self.now += 601
            self.bind(event)
            self.propose(event)
        self.now += 601
        self.bind("e4")
        with self.assertRaisesRegex(ValueError, "3条"):
            self.propose("e4")
        self.assertEqual(len(self.entries()), 3)

    def test_hardlinked_database_is_refused_without_touching_the_other_file(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback1", "少反问", LIB)
        target = self.root / "growth.sqlite3"
        other = Path(self.temp.name) / "other.sqlite3"
        os.link(target, other)
        before = other.read_bytes()
        with self.assertRaises(ValueError):
            self.entries()
        self.assertEqual(other.read_bytes(), before)

    def test_forget_purges_only_this_library_derived_text_and_preserves_enum_hash_and_other_libraries(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback1", "少反问", LIB)
        self.bind()
        self.propose()
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback_other", "简短点", "lib_other")
        before = self.store.preferences(OWNER, BOT, UMO, LIB)
        result = self.store.forget_library(LIB)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reflection_deleted"], 1)
        self.assertEqual(result["feedback_quotes_cleared"], 1)
        self.assertEqual(self.store.preferences(OWNER, BOT, UMO, LIB), before)
        self.assertEqual(self.entries()[0]["source_quote"], "")
        self.assertEqual(self.store.entries(OWNER, BOT, "lib_other")[0]["source_quote"], "简短点")
        with self.store._db() as db:
            data = json.loads(db.execute("SELECT data FROM entries WHERE library=?", (LIB,)).fetchone()[0])
        self.assertEqual(data["source_hash"], growth._hash("少反问"))
        self.assertNotIn(b"DISPLAY_ONLY_FREE_PLAN", (self.root / "growth.sqlite3").read_bytes())
        self.assertTrue(self.store.forget_library(LIB)["ok"])

    def test_forget_from_another_process_invalidates_inflight_and_cached_same_event_material(self):
        self.bind()
        self.propose()
        other_process = growth.Growth(self.root, clock=lambda: self.now)
        other_process.forget_library(LIB)
        self.assertEqual(self.store.context(OWNER, BOT, UMO, "event1", LIB), "")
        self.assertFalse(self.bind("event1")["ok"])
        with self.assertRaises(ValueError):
            self.propose("event1")
        self.assertTrue(self.bind("new_real_input")["ok"])
        # Retrying the original input after a restart cannot reset its durable
        # proposal budget merely by clearing private derived text.
        self.now += 601
        other_process.bind_evidence(OWNER, BOT, UMO, "event1", packet())
        with self.assertRaisesRegex(ValueError, "已经生成过"):
            other_process.propose(OWNER, BOT, UMO, "event1", ["fact_one"], "合成推测", "合成摘要")

    def test_forget_checkpoint_busy_is_never_reported_as_complete_and_retry_is_safe(self):
        self.bind()
        self.propose()
        reader = sqlite3.connect(self.root / "growth.sqlite3")
        self.addCleanup(reader.close)
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM entries").fetchall()
        with self.assertRaisesRegex(ValueError, "检查点"):
            self.store.forget_library(LIB)
        reader.rollback()
        reader.close()
        self.assertTrue(self.store.forget_library(LIB)["ok"])
        self.assertEqual(self.entries(), [])

    def test_old_feedback_retry_after_forget_never_restores_the_cleared_quote(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback", "少反问", LIB)
        self.store.forget_library(LIB)
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback", "少反问", LIB)
        self.assertEqual(self.entries()[0]["source_quote"], "")


sys.path.insert(0, str(ROOT / "app"))
from backend import companion_growth as backend_growth


class GrowthBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-growth-backend-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {"owner_qq": OWNER, "bot_qq": BOT, "companion": {"growth_enabled": True}}
        self.patches = [patch.object(backend_growth, "ASTRBOT", self.root / "astrbot"),
                        patch.object(backend_growth.config_store, "load", side_effect=lambda: deepcopy(self.config)),
                        patch.object(backend_growth.companion, "_plugin_enabled", return_value=True)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        source_dir = self.root / "source"
        source_dir.mkdir()
        source = {"schema": "love-house-agent-source/v1", "source_id": "source_test", "connection_id": "connection_test",
                  "library_id": LIB, "runtime_agent_id": "fish_test", "adapter": "astrbot", "platforms": ["qq"],
                  "base_url": "http://127.0.0.1:8768", "token": "synthetic-not-a-real-token", "enabled": True,
                  "revoked": False, "outbox_dir": str(source_dir / "source_test")}
        self.source_path = source_dir / "source.json"
        self.source_path.write_text(json.dumps(source), encoding="utf-8")
        directory = backend_growth.ASTRBOT / "data/config"
        directory.mkdir(parents=True)
        self.plugin_path = directory / "astrbot_plugin_love_house_config.json"
        self.binding = {"runtime_agent_id": "fish_test", "source_config_path": str(self.source_path), "allowed_umos": [UMO], "platform": "qq"}
        self.plugin_path.write_text(json.dumps({"enabled": True, "bindings": [self.binding]}), encoding="utf-8")
        self.store = backend_growth._journal()

    def test_get_has_only_flags_and_counts_and_never_reads_the_memory_graph(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback", "少反问", LIB)
        with patch.object(backend_growth.companion_memory, "_graph", side_effect=AssertionError("no native read")):
            public = backend_growth.status()
            self.assertEqual(public["counts"]["feedback"], 1)
            self.assertTrue(public["available"])
            encoded = json.dumps(public, ensure_ascii=False)
            for private in ("少反问", LIB, OWNER, BOT, str(self.source_path), "source_quote", "hypothesis"):
                self.assertNotIn(private, encoded)
            self.assertEqual(len(backend_growth.inspect({})["entries"]), 1)

    def test_inspection_rejects_arbitrary_account_library_or_path_and_mutation_requires_confirm(self):
        for body in ({"library_id": "other"}, {"owner_qq": "other"}, {"path": "C:/private"}):
            with self.assertRaises(ValueError):
                backend_growth.inspect(body)
        with self.assertRaises(ValueError):
            backend_growth.mutate({"id": "x", "action": "approve", "expected_revision": "x", "confirmed": False})

    def test_approval_reads_one_bounded_native_graph_then_revalidates_binding_without_a_write_to_native(self):
        self.store.bind_evidence(OWNER, BOT, UMO, "event", packet())
        self.store.propose(OWNER, BOT, UMO, "event", ["fact_one"], "PRIVATE_HYPOTHESIS", "DISPLAY_ONLY_PLAN")
        item = backend_growth.inspect({})["entries"][0]
        with patch.object(backend_growth.companion_memory, "_graph", return_value=([native_node()], "rev")) as read:
            result = backend_growth.mutate({"id": item["id"], "action": "approve", "expected_revision": item["revision"], "confirmed": True})
        read.assert_called_once_with(LIB)
        self.assertEqual(result["status"], "approved")
        self.assertNotIn("PRIVATE_HYPOTHESIS", json.dumps(backend_growth.status()))

    def test_conflicting_sources_fail_closed_and_disabling_learning_keeps_existing_entries(self):
        self.store.observe_feedback(OWNER, BOT, UMO, "feedback", "少反问", LIB)
        self.config["companion"]["growth_enabled"] = False
        self.assertFalse(backend_growth.status()["available"])
        self.assertEqual(len(backend_growth.inspect({})["entries"]), 1)
        self.plugin_path.write_text(json.dumps({"enabled": True, "bindings": [self.binding, self.binding]}), encoding="utf-8")
        self.assertFalse(backend_growth.status()["library_bound"])
        with self.assertRaises(ValueError):
            backend_growth.inspect({})


if __name__ == "__main__":
    unittest.main()
