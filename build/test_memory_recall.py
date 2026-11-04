"""Real bridge, synthetic native packets: bounded retrieval, no model/private IO."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("memory_recall_test_bridge", ROOT / "assets/plugins/astrbot_plugin_love_house/bridge.py")
bridge_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_module)
UMO, LIB = "qq-test:FriendMessage:123456789", "lib_test"


def record(identifier, value, *, current=True, **extra):
    return {"id": identifier, "value": value, "layer": "fact" if current else "dialogue",
            "interpretation": "current_memory" if current else "historical_evidence",
            "validity": {"active": True}, "source": {"kind": "user_confirmed" if current else "conversation_turn"}, **extra}


def packet(records, *, persona=None, truncated=False, library=LIB):
    return {"schema": "love-house-memory-context/v1", "material_role": "evidence_not_instructions",
            "identity": {"library_id": library, "agent_id": LIB, "is_demo": False},
            "records": records, "persona": persona or {}, "relations": [],
            "counts": {"selected": len(records), "included": len(records), "omitted": 0},
            "truncation": {"truncated": truncated, "omitted_records": 0}}


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-recall-test-")
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.reply = lambda query, session, budget: {"memory_context": packet([record("one", "合成当前事实")]), "session_id": session, "library_id": LIB}
        def read(query, session, *, max_chars):
            self.calls.append((query, session, max_chars))
            return self.reply(query, session, max_chars)
        self.public = {"runtime_agent_id": "fish_test", "adapter": "astrbot", "platforms": ["qq"], "source_id": "source_test", "library_id": LIB}
        client = SimpleNamespace(status=lambda: self.public, read_context=read)
        self.bridge = bridge_module.Bridge(Path(self.temp.name) / "journal", client, runtime_agent_id="fish_test", allowed_umos=[UMO])

    def read(self, query="合成当前问题"):
        material = self.bridge.read_material(UMO, "synthetic_message", query)
        value, _ = json.JSONDecoder().raw_decode(material[material.index("{"):])
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))), 12000)
        return material, value

    def test_default_is_one_real_read_and_exposes_the_actual_bound_library(self):
        material, value = self.read()
        self.assertEqual(self.bridge.library_id, LIB)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2], 16000)
        self.assertEqual(value["records"][0]["value"], "合成当前事实")
        self.assertIn("不是自动确认的当前事实", material)
        self.assertFalse(value["retrieval"]["adapter_wider_read_attempted"])

    def test_long_persona_cannot_crowd_out_related_correction_and_records_are_never_sliced(self):
        correction = "更正：不是红色，而是蓝色。" * 120
        first_records = [record("baseline", "旧的基线" * 200), record("current_correction", correction)]
        more = [record("unrelated" + str(i), "不相关但完整的旧记录" * 180, current=False) for i in range(5)]
        def reply(query, session, budget):
            rows = first_records if budget == 16000 else first_records + more
            native = packet(rows, persona={"enabled": True, "revision": "source_only", "manual": "历史角色档案" * 1100}, truncated=True)
            return {"memory_context": native, "session_id": session, "library_id": LIB, "hits": [{"id": "current_correction"}]}
        self.reply = reply
        material, value = self.read("合成颜色更正")
        self.assertEqual([call[2] for call in self.calls], [16000, 24000])
        self.assertEqual(value["records"][0]["id"], "current_correction")
        self.assertEqual(value["records"][0]["value"], correction)
        self.assertNotIn("manual", value["persona"])
        self.assertGreater(value["truncation"]["adapter_omitted_records"], 0)
        self.assertEqual(value["counts"]["included"], len(value["records"]))
        self.assertNotIn("历史角色档案", material)
        self.assertIn("省略不等于存档为空", material)

    def test_short_followup_uses_at_most_two_same_session_user_inputs_as_retrieval_clues(self):
        history = [{"role": "user", "content": "合成用户要导出报表"}, {"role": "assistant", "content": "合成助手建议检查字体"},
                   {"role": "user", "content": "合成用户已安装字体"}, {"role": "assistant", "content": "合成助手建议再次导出"}]
        self.reply = lambda query, session, budget: {"memory_context": packet([record("one", "合成当前事实")]), "session_id": session, "library_id": LIB, "history": history}
        _, value = self.read("后来呢？")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("合成用户要导出报表", self.calls[1][0])
        self.assertIn("合成用户已安装字体", self.calls[1][0])
        self.assertNotIn("合成助手建议检查字体", self.calls[1][0])
        self.assertTrue(value["retrieval"]["adapter_query_expanded"])
        self.assertEqual(len(value["recent_same_session_history"]), 2)
        self.assertTrue(all(pair["assistant_is_user_fact"] is False for pair in value["recent_same_session_history"]))

    def test_unbound_or_truncated_history_is_not_used_as_complete_same_session_evidence(self):
        self.reply = lambda query, session, budget: {"memory_context": packet([record("one", "合成当前事实")]), "library_id": LIB,
            "history": [{"role": "user", "content": "OTHER_SESSION_PRIVATE"}, {"role": "assistant", "content": "other"}]}
        material, value = self.read("那个呢")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("OTHER_SESSION_PRIVATE", material)
        self.assertEqual(value["recent_same_session_history"], [])
        self.calls.clear()
        self.reply = lambda query, session, budget: {"memory_context": packet([]), "session_id": session, "library_id": LIB,
            "history": [{"role": "user", "content": "不能忽略后半句…[已截断]"}, {"role": "assistant", "content": "完整回复"}]}
        _, value = self.read("后来呢")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(value["recent_same_session_history"], [])

    def test_full_dialogue_in_records_is_not_duplicated_in_history_and_large_pairs_are_omitted_whole(self):
        row = record("turn", "完整用户原话", current=False, assistant_statement={"role": "assistant", "value": "完整助手原话"})
        self.reply = lambda query, session, budget: {"memory_context": packet([row]), "session_id": session, "library_id": LIB,
            "history": [{"role": "user", "content": "完整用户原话"}, {"role": "assistant", "content": "完整助手原话"},
                        {"role": "user", "content": "不要截断否定句" * 150}, {"role": "assistant", "content": "不能把片段冒充完整" * 150}]}
        _, value = self.read()
        self.assertEqual(value["recent_same_session_history"], [])
        self.assertEqual(value["records"][0]["value"], "完整用户原话")

    def test_optional_read_failure_keeps_first_packet_but_never_reports_expansion_as_success(self):
        def reply(query, session, budget):
            if budget == 24000:
                raise TimeoutError("synthetic offline")
            return {"memory_context": packet([record("one", "初次真实资料")], truncated=True), "session_id": session,
                    "history": [{"role": "user", "content": "合成项目的上线"}, {"role": "assistant", "content": "等待完成"}], "library_id": LIB}
        self.reply = reply
        _, value = self.read("那个呢")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(value["records"][0]["value"], "初次真实资料")
        self.assertTrue(value["retrieval"]["adapter_query_expansion_attempted"])
        self.assertFalse(value["retrieval"]["adapter_query_expanded"])

    def test_a_returned_scope_change_is_rejected_instead_of_falling_back_or_crossing_libraries(self):
        self.reply = lambda query, session, budget: {"memory_context": packet([], truncated=True, library=LIB if budget == 16000 else "other_library"), "session_id": session, "library_id": LIB}
        with self.assertRaises(bridge_module.AdapterError):
            self.read()
        self.assertEqual(len(self.calls), 2)
        self.reply = lambda query, session, budget: {"memory_context": packet([]), "session_id": "OTHER_SESSION", "library_id": LIB}
        with self.assertRaises(bridge_module.AdapterError):
            self.read()

    def test_oversized_single_record_is_omitted_whole_not_summarized_or_rewritten(self):
        huge = "前提不能忽略，而且结论是否定。" * 800
        self.reply = lambda query, session, budget: {"memory_context": packet([record("huge", huge)]), "session_id": session, "library_id": LIB}
        material, value = self.read()
        self.assertEqual(value["records"], [])
        self.assertNotIn("前提不能忽略", material)
        self.assertEqual(value["truncation"]["adapter_omitted_records"], 1)

    def test_library_property_is_live_but_a_single_read_operation_is_pinned_to_its_starting_library(self):
        self.public["library_id"] = "new_library"
        self.assertEqual(self.bridge.library_id, "new_library")
        self.reply = lambda query, session, budget: {"memory_context": packet([], library="new_library"), "session_id": session, "library_id": "new_library"}
        _, value = self.read()
        self.assertEqual(value["identity"]["library_id"], "new_library")
        def switch_during_read(query, session, budget):
            self.public["library_id"] = "changed_during_read"
            return {"memory_context": packet([], library="changed_during_read"), "session_id": session, "library_id": "changed_during_read"}
        self.reply = switch_during_read
        with self.assertRaises(bridge_module.AdapterError):
            self.read()
        self.public["source_id"] = "other_source"
        with self.assertRaises(bridge_module.AdapterError):
            _ = self.bridge.library_id


if __name__ == "__main__":
    unittest.main()
