"""Isolated HTTP access checks; no private storage, model or QQ transport."""
from contextlib import ExitStack
import unittest
import json
from unittest.mock import patch
from . import test_social_http as fixture
server=fixture.server


class GrowthHttpTests(unittest.TestCase):
    setUpClass=classmethod(fixture.SocialHttpTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.SocialHttpTests.tearDownClass.__func__)
    def request(self,*args,**kwargs):
        status,headers,raw=fixture.SocialHttpTests.request(self,*args,**kwargs)
        try:body=json.loads(raw)
        except ValueError:body={}
        return status,body

    def setUp(self):
        self.instance.preview=False
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.status=self.stack.enter_context(patch.object(server.companion_growth,'status',return_value={'ok':True,'counts':{'feedback':2}}))
        self.inspect=self.stack.enter_context(patch.object(server.companion_growth,'inspect',return_value={'ok':True,'entries':[]}))
        self.mutate=self.stack.enter_context(patch.object(server.companion_growth,'mutate',return_value={'ok':True,'message':'已暂停'}))
        self.stack.enter_context(patch.object(server.config_store,'load',side_effect=AssertionError('Private config must stay mocked')))
        self.stack.enter_context(patch.object(server.stickers,'status',return_value={}))
        self.stack.enter_context(patch.object(server.updater,'current_version',return_value='1.6.0'))

    def test_only_public_counts_on_get_private_actions_not_get(self):
        status,data=self.request('GET','/api/growth')
        self.assertEqual(status,200);self.assertEqual(data['counts']['feedback'],2)
        for path in ('/api/growth/inspect','/api/growth/mutate'):
            self.assertEqual(self.request('GET',path)[0],404)
        self.inspect.assert_not_called();self.mutate.assert_not_called()

    def test_private_actions_require_exact_origin_and_host(self):
        for path in ('/api/growth/inspect','/api/growth/mutate'):
            for headers in ({},{'Origin':'https://external.invalid'},{'Origin':self.origin,'Host':'localhost:1234'},
                            {'Origin':self.origin,'Sec-Fetch-Site':'cross-site'}):
                with self.subTest(path=path,headers=headers):
                    self.assertEqual(self.request('POST',path,{},headers=headers)[0],403)
        self.inspect.assert_not_called();self.mutate.assert_not_called()

    def test_valid_actions_forward_exact_body_once(self):
        headers={'Origin':self.origin}
        self.assertEqual(self.request('POST','/api/growth/inspect',{},headers=headers)[0],200)
        self.inspect.assert_called_once_with({})
        body={'id':'fake-entry','action':'pause','expected_revision':'r1','confirmed':True}
        self.assertEqual(self.request('POST','/api/growth/mutate',body,headers=headers)[0],200)
        self.mutate.assert_called_once_with(body)

    def test_unknown_nested_aliases_do_not_reach_business_functions(self):
        for path in ('/api/growth/x/inspect','/api/growth/mutate/mutate','/api/growth/delete'):
            self.assertEqual(self.request('POST',path,{},headers={'Origin':self.origin})[0],404)
        self.inspect.assert_not_called();self.mutate.assert_not_called()

    def test_stale_revision_is_rejected_without_retry(self):
        self.mutate.side_effect=ValueError('手记已改变，请刷新')
        self.assertEqual(self.request('POST','/api/growth/mutate',{'id':'x'},headers={'Origin':self.origin})[0],400)
        self.assertEqual(self.mutate.call_count,1)

    def test_preview_never_reads_private_entries_or_writes(self):
        self.instance.preview=True
        status,data=self.request('GET','/api/growth')
        self.assertEqual(status,200);self.assertFalse(data['available'])
        self.status.assert_not_called()
        for path in ('/api/growth/inspect','/api/growth/mutate'):
            self.assertEqual(self.request('POST',path,{},headers={'Origin':self.origin})[0],403)
        self.inspect.assert_not_called();self.mutate.assert_not_called()
