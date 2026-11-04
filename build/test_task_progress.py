"""Factual progress and privacy from mocked tools; never operates the desktop."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

if __package__:
    from .test_pc_desktop import pc, Event
else:
    from test_pc_desktop import pc, Event
from backend import tasks, config_store


class TaskProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-progress-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.config = {"enabled": True, "desktop_enabled": True, "private_only": True,
                       "owner_qq": "123456789", "workspace": str(self.workspace), "members": {}}
        self.plugin = pc.PcOperator(None, self.config)
        p = patch.object(config_store, "load", return_value={"pc_access": {"workspace": str(self.workspace)}})
        p.start(); self.addCleanup(p.stop)

    def test_file_operations_publish_real_saved_artifact_without_contents_or_sender(self):
        secret = "not-for-the-journal"
        result = asyncio.run(self.plugin.pc_write_file(Event(), "result.txt", secret))
        self.assertIn("已写入", result)
        rows = tasks.snapshot()["recent"]
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["artifact"], "result.txt")
        self.assertNotIn(secret, json.dumps(rows))
        self.assertNotIn("123456789", json.dumps(rows))
        read = asyncio.run(self.plugin.pc_read_file(Event(), "result.txt"))
        self.assertEqual(read, secret)
        self.assertEqual(tasks.snapshot()["recent"][0]["action"], "read")

    def test_running_command_is_visible_before_exit_and_failure_is_not_reported_as_complete(self):
        started, release = threading.Event(), threading.Event()
        def execute(*args, **kwargs):
            started.set()
            release.wait(3)
            return types.SimpleNamespace(returncode=7, stdout="private-shell-output", stderr="")
        async def scenario():
            with patch.object(pc.subprocess, "run", side_effect=execute):
                operation = asyncio.create_task(self.plugin.pc_run_command(Event(), "private-shell-command"))
                await asyncio.to_thread(started.wait, 2)
                state = tasks.snapshot()
                self.assertEqual(len(state["active"]), 1)
                self.assertEqual(state["active"][0]["status"], "running")
                release.set()
                await operation
        asyncio.run(scenario())
        row = tasks.snapshot()["recent"][0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["exit_code"], 7)
        self.assertFalse(tasks.snapshot()["active"])
        encoded = (self.workspace / ".dafeiyu/tasks.json").read_text("utf-8")
        self.assertNotIn("private-shell-command", encoded)
        self.assertNotIn("private-shell-output", encoded)

    def test_denied_caller_cannot_generate_progress_or_invoke_desktop(self):
        with patch.object(pc.desktop_native, "get_desktop") as native:
            result = json.loads(asyncio.run(self.plugin.pc_desktop_click(Event(sender="55555555"), 1, 1, window_id=1)))
        self.assertFalse(result["ok"])
        native.assert_not_called()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_revoked_queued_desktop_permission_is_rechecked_before_execution(self):
        async def scenario():
            await self.plugin._desktop_actions.acquire()
            try:
                operation = asyncio.create_task(self.plugin.pc_desktop_click(Event(), 1, 1, window_id=1))
                await asyncio.sleep(0)
                self.config["desktop_enabled"] = False
            finally:
                self.plugin._desktop_actions.release()
            return await operation
        with patch.object(pc.desktop_native, "get_desktop") as native:
            result = json.loads(asyncio.run(scenario()))
        self.assertFalse(result["ok"])
        native.assert_not_called()
        self.assertFalse(tasks.snapshot()["recent"])

    def test_stale_running_is_unconfirmed_not_fabricated_failed_and_raw_summaries_are_not_trusted(self):
        identifier = pc.task_journal.begin(self.workspace, "desktop_type_text", "owner")
        file = self.workspace / ".dafeiyu/tasks.json"
        state = json.loads(file.read_text("utf-8"))
        state["tasks"][0].update(started_at=time.time() - 1000, summary="private-user-text", artifact="../../private")
        file.write_text(json.dumps(state), "utf-8")
        row = tasks.snapshot()["recent"][0]
        self.assertEqual(row["id"], identifier)
        self.assertEqual(row["status"], "unconfirmed")
        self.assertNotIn("artifact", row)
        self.assertNotIn("private-user-text", json.dumps(row))

    def test_retention_and_unknown_actions_are_bounded(self):
        for _ in range(65):
            identifier = pc.task_journal.begin(self.workspace, "read", "owner")
            pc.task_journal.finish(self.workspace, identifier, success=True)
        self.assertEqual(len(tasks.snapshot(limit=60)["recent"]), 60)
        self.assertEqual(pc.task_journal.begin(self.workspace, "unknown-action"), "")
        with self.assertRaises(ValueError): tasks.snapshot(limit=0)


if __name__ == "__main__": unittest.main()
