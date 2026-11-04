"""A native forget cannot report success while derived private text survives."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from backend import companion_memory as memory, companion_growth


class ForgetCascadeTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(memory,'_scope',return_value='default'))
        stack.enter_context(patch.object(memory,'_previews',{}))
        self.order=[];self.plan={'node_count':1,'archive_count':1}
        def request(method,path,body,**kwargs):
            if path.endswith('forget-preview'):return self.plan.copy()
            self.order.append('native');return {'ok':True,'forgotten':1}
        self.native=stack.enter_context(patch.object(memory.lovehouse,'_request',side_effect=request))
        self.purge=stack.enter_context(patch.object(companion_growth,'forget_library',create=True,side_effect=lambda library:self.order.append('derived')))
        preview=memory.mutate({'library_id':'default','id':'n1','action':'forget-preview'})
        self.body={'library_id':'default','id':'n1','action':'forget','preview_token':preview['preview_token'],'confirm':True}

    def test_confirmed_forget_clears_derived_before_native_once(self):
        result=memory.mutate(self.body)
        self.assertEqual(self.order,['derived','native']);self.assertTrue(result['ok'])
        self.purge.assert_called_once_with('default')

    def test_failed_derived_cleanup_aborts_original_forget(self):
        self.purge.side_effect=ValueError('派生清理尚未完成')
        with self.assertRaises(ValueError):memory.mutate(self.body)
        self.assertEqual(self.order,[])
        self.assertFalse(any(c.args[1].endswith('/forget') for c in self.native.call_args_list))

    def test_changed_scope_or_missing_confirmation_clears_nothing(self):
        self.plan['node_count']=2
        with self.assertRaises(ValueError):memory.mutate(self.body)
        self.purge.assert_not_called()
        self.body['confirm']=False
        with self.assertRaises(ValueError):memory.mutate(self.body)
        self.assertEqual(self.order,[])

    def test_native_failure_after_cleanup_is_not_retried(self):
        def request(method,path,body,**kwargs):
            if path.endswith('forget-preview'):return self.plan.copy()
            self.order.append('native');raise ValueError('原库没有确认完成')
        self.native.side_effect=request
        with self.assertRaises(ValueError):memory.mutate(self.body)
        self.assertEqual(self.order,['derived','native'])
