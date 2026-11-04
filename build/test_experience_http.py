"""Real HTTP routing boundaries with all v1.4.0 services mocked.

No installed profile, model, QQ, image import, backup, recovery or update action
is touched. Only the real local handler and request parser execute.
"""
from __future__ import annotations

from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from backend import server


class ExperienceHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = server.LocalServer(('127.0.0.1', 0), server.Handler)
        cls.instance.preview = False
        cls.instance.daemon_threads = True
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = 'http://127.0.0.1:' + str(cls.instance.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.instance.shutdown()
        cls.instance.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.instance.preview = False
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.mocks = {}
        targets = {
            'health': (server.health, 'snapshot'), 'tasks': (server.tasks, 'snapshot'),
            'backups': (server.recovery, 'list_backups'), 'backup': (server.recovery, 'create_backup'),
            'restore': (server.recovery, 'restore'), 'library': (server.sticker_library, 'browse'),
            'favorite': (server.sticker_library, 'set_favorite'), 'preview': (server.sticker_library, 'preview'),
            'feedback': (server.sticker_library, 'feedback'), 'material_status': (server.sticker_materials, 'status'),
            'material_check': (server.sticker_materials, 'check'), 'material_apply': (server.sticker_materials, 'apply'),
            'catalog': (server.companion_memory, 'catalog'), 'read': (server.companion_memory, 'read'),
            'add': (server.companion_memory, 'add'), 'mutate': (server.companion_memory, 'mutate'),
            'profile': (server.companion, 'apply_profile'), 'temporary': (server.companion, 'set_temporary'),
            'batch': (server.custom_stickers, 'import_batch'), 'zip': (server.custom_stickers, 'import_zip'),
            'single': (server.custom_stickers, 'import_item'),
        }
        for name, (module, function) in targets.items():
            self.mocks[name] = stack.enter_context(patch.object(module, function, return_value={'ok': True, 'mock_service': name}))
        # Preview fixtures may read package-only inventory, never actual profile.
        stack.enter_context(patch.object(server.stickers, 'status', return_value={'settings': {}, 'stickers': []}))
        stack.enter_context(patch.object(server.updater, 'current_version', return_value='1.4.0'))
        self.updater = Mock()
        self.updater.download_rollback.return_value = {'ok': True, 'mock_service': 'rollback'}
        self.get_updater = stack.enter_context(patch.object(server.updater, 'get_updater', return_value=self.updater))

    def request(self, method, path, body=None, *, headers=None, raw=None, missing_host=False):
        encoded = raw if raw is not None else None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        connection = http.client.HTTPConnection(*self.instance.server_address, timeout=4)
        try:
            if missing_host:
                connection.putrequest(method, path, skip_host=True)
                for key, value in (headers or {}).items(): connection.putheader(key, value)
                if encoded is not None: connection.putheader('Content-Length', str(len(encoded)))
                connection.endheaders(encoded)
            else:
                connection.request(method, path, body=encoded, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, body, **kwargs):
        return self.request('POST', path, body, headers={'Origin': self.origin, 'Content-Type': 'application/json'}, **kwargs)

    def test_read_surfaces_use_only_the_requested_mock_and_are_uncached_json(self):
        cases = [('/api/health?reason=memory', 'health', ('memory',)), ('/api/tasks', 'tasks', ()),
                 ('/api/recovery', 'backups', ()), ('/api/stickers/materials', 'material_status', ())]
        for path, name, args in cases:
            with self.subTest(path=path):
                status, headers, raw = self.request('GET', path)
                self.assertEqual(status, 200, raw)
                self.assertIn('application/json', headers['Content-Type'])
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
                self.assertEqual(json.loads(raw)['mock_service'], name)
                self.mocks[name].assert_called_once_with(*args)
        status, _, raw = self.request('GET', '/api/stickers/library?query=%E5%BC%80%E5%BF%83&favorites_only=true&sort=recent')
        self.assertEqual(status, 200, raw)
        self.mocks['library'].assert_called_once_with({'query': '开心', 'favorites_only': True, 'sort': 'recent'})
        self.updater.download_rollback.assert_not_called()
        self.assertFalse(any(self.mocks[n].called for n in ('backup', 'restore', 'material_apply', 'add', 'mutate')))

    def test_each_write_family_requires_exact_origin_and_host_before_any_service_runs(self):
        paths = ['/api/memories/catalog', '/api/memories/read', '/api/memories/add', '/api/memories/mutate',
                 '/api/companion/profile', '/api/companion/temporary', '/api/recovery/backup', '/api/recovery/restore',
                 '/api/recovery/rollback', '/api/stickers/library/favorite', '/api/stickers/library/preview',
                 '/api/stickers/library/feedback', '/api/stickers/materials/check', '/api/stickers/materials/apply',
                 '/api/stickers/custom/batch', '/api/stickers/custom/zip']
        for path in paths:
            for headers in ({}, {'Origin': 'https://outside.example'}, {'Origin': self.origin, 'Host': 'outside.example'},
                            {'Origin': self.origin, 'Sec-Fetch-Site': 'cross-site'}):
                with self.subTest(path=path, headers=headers):
                    status, _, _ = self.request('POST', path, {}, headers=headers)
                    self.assertEqual(status, 403)
            status, _, _ = self.request('POST', path, {}, headers={'Origin': self.origin}, missing_host=True)
            self.assertEqual(status, 403)
        self.assertTrue(all(not mock.called for mock in self.mocks.values()))
        self.get_updater.assert_not_called()

    def test_allowed_memory_and_preference_requests_preserve_the_explicit_body(self):
        body = {'library_id': 'test-library', 'id': 'memory-one', 'action': 'correct',
                'expected_revision': 'read-revision', 'text': '经用户确认的更正'}
        for route in ('catalog', 'read', 'add', 'mutate'):
            status, _, raw = self.post('/api/memories/' + route, body)
            self.assertEqual(status, 200, raw)
            self.mocks[route].assert_called_once_with(body)
        status, _, _ = self.post('/api/companion/profile', {'profile': 'natural'})
        self.assertEqual(status, 200)
        self.mocks['profile'].assert_called_once_with('natural')
        temporary = {'mode': 'quiet', 'duration_hours': 12}
        status, _, _ = self.post('/api/companion/temporary', temporary)
        self.assertEqual(status, 200)
        self.mocks['temporary'].assert_called_once_with(temporary)

    def test_recovery_and_rollback_are_separate_and_rollback_needs_literal_confirmation(self):
        status, _, _ = self.post('/api/recovery/backup', {})
        self.assertEqual(status, 200)
        self.mocks['backup'].assert_called_once_with()
        restore = {'backup_id': 'a' * 32, 'confirmed': True}
        status, _, _ = self.post('/api/recovery/restore', restore)
        self.assertEqual(status, 200)
        self.mocks['restore'].assert_called_once_with(restore)
        for body in ({'backup_id': 'a' * 32}, {'backup_id': 'a' * 32, 'confirmed': 'true'}, {'backup_id': 'a' * 32, 'confirmed': False}):
            status, _, _ = self.post('/api/recovery/rollback', body)
            self.assertEqual(status, 400)
        self.updater.download_rollback.assert_not_called()
        status, _, _ = self.post('/api/recovery/rollback', restore)
        self.assertEqual(status, 200)
        self.updater.download_rollback.assert_called_once_with('a' * 32, confirmed=True)

    def test_library_and_material_actions_forward_only_user_selected_payload(self):
        for route, name, body in [('favorite', 'favorite', {'id': 'happy', 'favorite': True}),
                                  ('preview', 'preview', {'text': '分享好消息', 'scene': 'greeting'}),
                                  ('feedback', 'feedback', {'id': 'happy', 'scene': 'greeting', 'reason': 'clear'})]:
            status, _, raw = self.post('/api/stickers/library/' + route, body)
            self.assertEqual(status, 200, raw)
            self.mocks[name].assert_called_once_with(body)
        status, _, _ = self.post('/api/stickers/materials/check', {})
        self.assertEqual(status, 200)
        self.mocks['material_check'].assert_called_once_with()
        fingerprint = {'fingerprint': 'f' * 64}
        status, _, _ = self.post('/api/stickers/materials/apply', fingerprint)
        self.assertEqual(status, 200)
        self.mocks['material_apply'].assert_called_once_with(fingerprint)
        self.assertFalse(self.mocks['batch'].called or self.mocks['single'].called or self.mocks['zip'].called)

    def test_preview_never_reads_profiles_or_performs_new_actions(self):
        self.instance.preview = True
        for path in ('/api/health?reason=memory', '/api/tasks', '/api/recovery', '/api/stickers/library?query=test', '/api/stickers/materials'):
            status, headers, raw = self.request('GET', path)
            self.assertEqual(status, 200, raw)
            self.assertIn('application/json', headers['Content-Type'])
        for path in ('/api/memories/read', '/api/companion/profile', '/api/recovery/backup', '/api/recovery/restore',
                     '/api/recovery/rollback', '/api/stickers/library/feedback', '/api/stickers/materials/check',
                     '/api/stickers/materials/apply', '/api/stickers/custom/batch', '/api/stickers/custom/zip'):
            status, _, _ = self.post(path, {'confirmed': True})
            self.assertEqual(status, 403)
        self.assertTrue(all(not mock.called for mock in self.mocks.values()))
        self.get_updater.assert_not_called()

    def test_upload_family_size_limits_reject_content_length_before_read_or_parse(self):
        for path, mib in (('/api/stickers/custom/import', 12), ('/api/stickers/custom/batch', 36), ('/api/stickers/custom/zip', 28)):
            status, _, _ = self.request('POST', path, raw=b'{', headers={
                'Origin': self.origin, 'Content-Type': 'application/json', 'Content-Length': str(mib * 1024 * 1024 + 1)})
            self.assertEqual(status, 413)
        status, _, _ = self.request('POST', '/api/recovery/restore', raw=b'[1,2]', headers={
            'Origin': self.origin, 'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertTrue(all(not self.mocks[n].called for n in ('single', 'batch', 'zip', 'restore')))

    def test_unknown_new_api_routes_are_json_404_instead_of_the_app_shell(self):
        for prefix in ('/api/health/', '/api/tasks/', '/api/memories/', '/api/recovery/', '/api/companion/', '/api/stickers/library/', '/api/stickers/materials/'):
            for method in ('GET', 'POST'):
                with self.subTest(prefix=prefix, method=method):
                    status, headers, raw = self.request(method, prefix + 'not-a-route', {} if method == 'POST' else None,
                        headers={'Origin': self.origin, 'Content-Type': 'application/json'})
                    self.assertEqual(status, 404, raw[:100])
                    self.assertIn('application/json', headers['Content-Type'])

    def test_nested_route_names_cannot_alias_a_privileged_leaf_action(self):
        for path in ('/api/recovery/nested/backup', '/api/stickers/materials/nested/check',
                     '/api/memories/nested/catalog', '/api/stickers/library/nested/favorite'):
            with self.subTest(path=path):
                status, _, _ = self.post(path, {})
                self.assertEqual(status, 404)
        self.assertTrue(all(not mock.called for mock in self.mocks.values()))


if __name__ == '__main__': unittest.main()
