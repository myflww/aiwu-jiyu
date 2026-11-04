"""Proactive shared pipeline and explicit preference control; no external sends."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

from .test_companion import main,OWNER,BOT,UMO
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from backend import companion,companion_memory as memory
from importlib import import_module
pipeline=import_module(main.__package__+'.reply_pipeline')
pref=import_module(main.__package__+'.preferences')

class Chain:
 def __init__(self):self.chain=[]
 def message(self,text):self.chain.append(SimpleNamespace(text=text));return self

class PreferenceTests(unittest.TestCase):
 def test_presets_do_not_enable_capabilities_and_legacy_is_custom(self):
  for name,data in pref.PROFILES.items():
   self.assertFalse(set(data['values']) & set(main.normalize({}) and ('proactive_enabled','activity_enabled','emotion_enabled','whale_style_enabled','natural_bubbles_enabled')))
   options=main.normalize(data['values'])
   self.assertEqual(pref.matched_profile(options),name)
   self.assertFalse(options['enabled'])
  self.assertEqual(pref.matched_profile(main.normalize({'interval_minutes':30,'daily_limit':3})),'custom')
 def test_explicit_feedback_does_not_match_quoted_or_hypothetical_sentences(self):
  self.assertEqual(pref.feedback_mode('今天忙，少找我。'),'quiet')
  self.assertEqual(pref.feedback_mode('今天可以多陪我聊聊'),'more')
  for text in ('你说今天忙，少找我','如果今天忙少找我怎么办','他今天忙，少找我','最近没回你'):
   self.assertIsNone(pref.feedback_mode(text))
 def test_expiry_identity_clear_and_more_never_raise_budget(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'preferences.json';now=1000000
   pref.write_preference(path,OWNER,BOT,'quiet',duration_hours=2,now=now)
   data=pref.read_preference(path,OWNER,BOT,now=now+10)
   self.assertEqual(pref.effective_options(main.normalize({}),data)['trigger_probability'],0)
   self.assertFalse(pref.read_preference(path,OWNER,'000000000',now=now+10))
   self.assertFalse(pref.read_preference(path,OWNER,BOT,now=now+7200))
   pref.write_preference(path,OWNER,BOT,'more',now=now)
   more=pref.effective_options(main.normalize({'daily_limit':1}),pref.read_preference(path,OWNER,BOT,now=now))
   self.assertEqual(more['daily_limit'],1)
   pref.write_preference(path,OWNER,BOT,'normal',now=now)
   self.assertFalse(pref.read_preference(path,OWNER,BOT,now=now))
 def test_backend_profile_keeps_switches_and_temporary_reads_are_detached(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(companion,'ASTRBOT',Path(temp)),patch.object(companion.config_store,'load',return_value={'owner_qq':OWNER,'bot_qq':BOT,'companion':{}}),patch.object(companion,'configure',return_value={}) as save:
   companion.apply_profile('quiet');payload=save.call_args.args[0]
   self.assertNotIn('proactive_enabled',payload)
   result=companion.set_temporary({'mode':'quiet','duration_hours':1})
   self.assertTrue(result['temporary_preference']['active'])
   self.assertEqual(result['effective_settings']['trigger_probability'],0)
   self.assertNotIn(OWNER,json.dumps(result['temporary_preference']))
 def test_desktop_today_uses_configured_timezone_midnight_and_rejects_mixed_duration(self):
  now=datetime(2026,11,3,22,15,tzinfo=timezone(timedelta(hours=8)))
  config={'owner_qq':OWNER,'bot_qq':BOT,'companion':{'timezone':'Asia/Shanghai'}}
  with tempfile.TemporaryDirectory() as temp,patch.object(companion,'ASTRBOT',Path(temp)),patch.object(companion.config_store,'load',return_value=config),patch.object(companion._policy,'schedule_now',return_value=now),patch.object(companion._preferences.time,'time',return_value=now.timestamp()):
   result=companion.set_temporary({'mode':'quiet','until_end_of_day':True})
   midnight=now.replace(hour=0,minute=0)+timedelta(days=1)
   self.assertEqual(result['temporary_preference']['expires_at'],midnight.timestamp())
   for body in ({'mode':'quiet','until_end_of_day':True,'duration_hours':12},{'mode':'quiet','until_end_of_day':1},{'mode':'quiet','until_end_of_day':None}):
    with self.assertRaises(ValueError):companion.set_temporary(body)

class PipelineTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.history=[{'role':'user','content':'明天面试'}]
  self.manager=SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value='cid'),get_conversation=AsyncMock(side_effect=lambda *a:SimpleNamespace(history=json.dumps(self.history))))
  async def update(*args,**kw):self.history=kw['history']
  self.manager.update_conversation=AsyncMock(side_effect=update)
  self.sticker=SimpleNamespace(proactive_context=AsyncMock(return_value='sticker candidates'),prepare_proactive=AsyncMock(return_value={'text':'祝你一切顺利。','component':object(),'reservation':'r'}),acknowledge_proactive=AsyncMock(),revalidate_proactive=AsyncMock(return_value=object()))
  self.memory=SimpleNamespace(companion_recall=AsyncMock(return_value='real memory evidence'),companion_record=AsyncMock(return_value=True))
  self.instances={'love_house':self.memory,'astrbot_plugin_dafeiyu_stickers':self.sticker}
  self.context=SimpleNamespace(conversation_manager=self.manager,send_message=AsyncMock(return_value=True),get_registered_star=lambda name:SimpleNamespace(activated=True,star_cls=self.instances[name]) if name in self.instances else None)
  self.allowed=AsyncMock(return_value=True)
  self.pipe=pipeline.ReplyPipeline(self.context,Chain,self.allowed)
 async def test_real_memory_and_candidates_retrieved_no_extra_model_or_fake_event(self):
  notes,status=await self.pipe.context_notes(UMO,'out1','最近待关心的事')
  self.assertIn('real memory',notes);self.assertIn('sticker candidates',notes)
  self.assertEqual(status,{'memory':'ready','stickers':'ready'})
  self.memory.companion_recall.assert_awaited_once_with(UMO,'out1','最近待关心的事')
 async def test_delivered_reply_saved_assistant_only_and_durably_staged_with_true_receipt(self):
  result=await self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=True))
  self.assertTrue(result['accepted']);self.assertEqual(result['history'],'saved')
  self.assertEqual(len(self.history),2);self.assertEqual(self.history[-1],{'role':'assistant','content':'祝你一切顺利。'})
  self.memory.companion_record.assert_awaited_once_with(UMO,'out1','祝你一切顺利。',receipt='adapter_accepted')
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','sent')
 async def test_cancel_before_send_releases_image_and_writes_no_history_or_memory(self):
  result=await self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=False))
  self.assertFalse(result['accepted']);self.context.send_message.assert_not_awaited()
  self.manager.update_conversation.assert_not_awaited();self.memory.companion_record.assert_not_awaited()
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','not_sent')
 async def test_ambiguous_adapter_no_retry_no_false_history(self):
  self.context.send_message.side_effect=RuntimeError('private-secret-error')
  result=await self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=True))
  self.assertFalse(result['accepted']);self.context.send_message.assert_awaited_once()
  self.manager.update_conversation.assert_not_awaited();self.memory.companion_record.assert_not_awaited()
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','failed_unknown')
  self.assertNotIn('secret',json.dumps(result))
 async def test_cancel_after_first_bubble_records_only_accepted_text_and_does_not_ack_image(self):
  text=('愿意听你慢慢说今天的事情，也可以一起想想明天的小计划。'*6)
  self.sticker.prepare_proactive.return_value={'text':text,'component':object(),'reservation':'r'}
  guard=AsyncMock(side_effect=[True,False])
  with patch.object(pipeline.BubbleReplies,'_selected',return_value=True):
   result=await self.pipe.deliver(UMO,'cid','out1',text,{'natural_bubbles_enabled':True},guard)
  self.assertTrue(result['accepted']);self.assertEqual(result['sent_bubbles'],1)
  self.assertLess(len(self.history[-1]['content']),len(text))
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','not_sent')
 async def test_disabled_plugins_never_read_memory_or_prepare_images(self):
  self.allowed.return_value=False
  notes,_=await self.pipe.context_notes(UMO,'out1','q');self.assertEqual(notes,'')
  await self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=True))
  self.memory.companion_recall.assert_not_awaited();self.sticker.prepare_proactive.assert_not_awaited()
 async def test_history_failure_is_visible_without_retrying_an_accepted_send(self):
  self.manager.update_conversation.side_effect=OSError('private-disk-error')
  result=await self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=True))
  self.assertTrue(result['accepted']);self.assertEqual(result['history'],'unavailable')
  self.assertEqual(result['memory'],'durably_staged');self.context.send_message.assert_awaited_once()
  self.assertNotIn('private',json.dumps(result))
 async def test_missing_required_host_lock_stops_before_body(self):
  import builtins
  original=builtins.__import__
  def importing(name,*args,**kwargs):
   if name=='astrbot.core.utils.session_lock':raise ImportError('unsupported SDK')
   return original(name,*args,**kwargs)
  with patch.object(builtins,'__import__',side_effect=importing):
   with self.assertRaises(RuntimeError):
    async with pipeline.session_guard(UMO,required=True):self.fail('Must not send without host lock')
 async def test_worker_cancel_after_accepted_prefix_persists_before_propagating(self):
  text='今天可以慢慢聊，不急着把一切都做完。'*12
  self.sticker.prepare_proactive.return_value={'text':text,'component':object(),'reservation':'r'}
  first=asyncio.Event();pause=asyncio.Event();callback=Mock()
  async def sent(*args):first.set();return True
  self.context.send_message.side_effect=sent
  async def sleeping(seconds):pause.set();await asyncio.Event().wait()
  with patch.object(pipeline.BubbleReplies,'_selected',return_value=True),patch.object(pipeline.asyncio,'sleep',side_effect=sleeping):
   task=asyncio.create_task(self.pipe.deliver(UMO,'cid','out1',text,{'natural_bubbles_enabled':True},AsyncMock(return_value=True),on_result=callback))
   await pause.wait();task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(self.context.send_message.await_count,1)
  self.assertTrue(self.history[-1]['content']);self.assertLess(len(self.history[-1]['content']),len(text))
  self.memory.companion_record.assert_awaited_once()
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','not_sent')
  self.assertEqual(callback.call_args.args[0]['history'],'saved')
 async def test_second_cancel_during_history_bookkeeping_does_not_erase_prefix(self):
  started=asyncio.Event();release=asyncio.Event();callback=Mock()
  async def update(*args,**kwargs):started.set();await release.wait();self.history=kwargs['history']
  self.manager.update_conversation.side_effect=update
  task=asyncio.create_task(self.pipe.deliver(UMO,'cid','out1','hello',{},AsyncMock(return_value=True),on_result=callback))
  await started.wait();task.cancel();await asyncio.sleep(0)
  task.cancel();release.set()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(self.history[-1]['content'],'祝你一切顺利。')
  self.assertTrue(callback.call_args.args[0]['cancelled'])
 async def test_final_image_revalidated_after_earlier_text_and_can_be_revoked(self):
  text='我们可以慢慢说说今天想做的事情，一点一点来就很好。'*8
  self.sticker.prepare_proactive.return_value={'text':text,'component':object(),'reservation':'r'}
  self.sticker.revalidate_proactive.return_value=None
  with patch.object(pipeline.BubbleReplies,'_selected',return_value=True),patch.object(pipeline.BubbleReplies,'_delay',return_value=0):
   result=await self.pipe.deliver(UMO,'cid','out1',text,{'natural_bubbles_enabled':True},AsyncMock(return_value=True))
  self.assertTrue(result['accepted']);self.sticker.revalidate_proactive.assert_awaited_once()
  self.assertTrue(all(len(call.args[1].chain)==1 for call in self.context.send_message.call_args_list))
  self.sticker.acknowledge_proactive.assert_awaited_once_with(UMO,'r','not_sent')
 async def test_disabled_or_failed_sticker_cannot_leak_protocol_markers_to_qq_or_history(self):
  self.allowed.return_value=False
  result=await self.pipe.deliver(UMO,'cid','out1','hello[大肥鱼表情:happy]',{},AsyncMock(return_value=True))
  self.assertEqual(result['text'],'hello');self.assertEqual(self.history[-1]['content'],'hello')
  self.context.send_message.reset_mock()
  result=await self.pipe.deliver(UMO,'cid','out2','[sticker:happy]',{},AsyncMock(return_value=True))
  self.assertTrue(result['invalid']);self.context.send_message.assert_not_awaited()
  self.assertEqual(pipeline.visible_text('代码 `[sticker:happy]` 链接 https://example.test/[sticker:happy] 和 [sticker:sad]'),
                   '代码 `[sticker:happy]` 链接 https://example.test/[sticker:happy] 和 ')

class MemoryControlTests(unittest.TestCase):
 def setUp(self):
  self.lib=patch.object(memory.lovehouse,'libraries',return_value={'libraries':[{'id':'default','is_demo':False},{'id':'demo','is_demo':True}]});self.lib.start();self.addCleanup(self.lib.stop)
  self.requests=patch.object(memory.lovehouse,'_request');self.api=self.requests.start();self.addCleanup(self.requests.stop)
  self.reads=patch.object(memory,'_native_get',side_effect=lambda path,lib:self.api('GET',path,library_id=lib));self.reads.start();self.addCleanup(self.reads.stop)
  self.revision='a'*64
 def test_catalog_and_read_are_scoped_to_one_actual_library(self):
  self.api.return_value={'meta':{'library_id':'default'},'nodes':[]}
  self.assertEqual(memory.catalog({'library_id':'default'})['entries'],[])
  self.api.assert_called_with('GET','/api/graph',library_id='default')
  self.api.return_value={'id':'other'}
  with self.assertRaises(ValueError):memory.read({'library_id':'default','id':'x'})
 def test_new_commitment_is_an_explicit_native_memory_not_a_local_transcript(self):
  memory.add({'library_id':'default','kind':'commitment','text':'周五关心面试情况'})
  method,route,data=self.api.call_args.args
  self.assertEqual(route,'/api/add');self.assertEqual(data['kind'],'commitment')
  self.assertTrue(data['meta']['confirmed'])
  with self.assertRaises(ValueError):memory.add({'library_id':'demo','text':'bad'})
 def test_correct_requires_fresh_revision_and_uses_lineage_preserving_native_action(self):
  self.api.return_value={'saved':True}
  with patch.object(memory,'_graph',return_value=([],self.revision)):
   memory.mutate({'library_id':'default','id':'n1','action':'correct','text':'真实更正','expected_revision':self.revision})
  self.api.assert_called_with('POST','/api/memories/n1/correct',{'text':'真实更正'},library_id='default')
  with patch.object(memory,'_graph',return_value=([],'b'*64)):
   with self.assertRaises(ValueError):memory.mutate({'library_id':'default','id':'n1','action':'enabled','enabled':False,'expected_revision':self.revision})
 def test_disabled_entries_remain_findable_and_important_state_comes_from_native_metadata(self):
  nodes=[{'id':'old','created':1,'text':'旧话题','label':'旧回忆','meta':{}},
         {'id':'new','created':2,'text':'面试','label':'待跟进','kind':'commitment','meta':{'memory_disabled':True,'important':True,'source':'user_confirmed','confirmed':True,'speaker':'user'}}]
  self.api.return_value={'nodes':nodes,'meta':{'library_id':'default'}}
  listing=memory.catalog({'library_id':'default','limit':1})
  self.assertEqual(listing['entries'][0]['id'],'new');self.assertTrue(listing['entries'][0]['important']);self.assertFalse(listing['entries'][0]['enabled'])
  next_page=memory.catalog({'library_id':'default','limit':1,'cursor':listing['next_cursor']})
  self.assertEqual(next_page['entries'][0]['id'],'old')
  disabled=memory.catalog({'library_id':'default','view':'disabled'})
  self.assertEqual([r['id'] for r in disabled['entries']],['new'])
  with self.assertRaises(ValueError):memory.catalog({'library_id':'default','view':'active','cursor':listing['next_cursor']})
  self.api.side_effect=[{'nodes':nodes,'meta':{}},nodes[1]]
  entry=memory.read({'library_id':'default','id':'new','revision':listing['revision']})
  self.assertEqual(entry['text'],'面试');self.assertFalse(entry['record']['enabled'])
 def test_forget_requires_matching_preview_confirm_and_unchanged_native_scope(self):
  plan={'node_count':2,'archive_count':1,'node_ids':['n1','n2'],'note':'includes backups'}
  self.api.return_value=plan
  preview=memory.mutate({'library_id':'default','id':'n1','action':'forget-preview'})
  args={'library_id':'default','id':'n1','action':'forget','preview_token':preview['preview_token']}
  with self.assertRaises(ValueError):memory.mutate(args)
  with self.assertRaises(ValueError):memory.mutate({**args,'id':'n2','confirm':True})
  self.api.return_value={**plan,'node_count':3}
  with self.assertRaises(ValueError):memory.mutate({**args,'confirm':True})
  self.api.side_effect=[plan,{'forgotten':True}]
  self.assertTrue(memory.mutate({**args,'confirm':True})['forgotten'])
  with self.assertRaises(ValueError):memory.mutate({**args,'confirm':True})
 def test_invalid_identifiers_and_fields_do_not_reach_service(self):
  for body in ({'library_id':'../other'},{'library_id':'default','limit':False},{'library_id':'default','unknown':'x'}):
   with self.assertRaises(ValueError):memory.catalog(body)
  self.api.assert_not_called()
 def test_partial_body_is_marked_incomplete_and_stop_restore_uses_native_controls(self):
  self.api.return_value={'id':'n1','text':'x'*8001,'meta':{'memory_disabled':True}}
  entry=memory.read({'library_id':'default','id':'n1'})
  self.assertFalse(entry['complete']);self.assertEqual(len(entry['text']),8000)
  with patch.object(memory,'_graph',return_value=([],self.revision)):
   memory.mutate({'library_id':'default','id':'n1','action':'enabled','enabled':True,'expected_revision':self.revision})
  self.api.assert_called_with('POST','/api/memories/n1/enabled',{'enabled':True},library_id='default')
 def test_view_decay_does_not_make_an_open_editor_stale_but_actual_edits_do(self):
  node={'id':'n1','created':1,'label':'待关心','text':'面试','kind':'commitment','effective_salience':.8,'meta':{'important':True}}
  self.api.return_value={'nodes':[node]}
  first=memory.catalog({'library_id':'default'})['revision']
  node['effective_salience']=.7999;node['state']='dormant'
  self.assertEqual(memory.catalog({'library_id':'default'})['revision'],first)
  node['text']='面试已经结束'
  self.assertNotEqual(memory.catalog({'library_id':'default'})['revision'],first)
 def test_large_native_read_rejected_before_body_decode(self):
  self.reads.stop()
  response=SimpleNamespace(status=200,getheader=lambda key:str(9*1024*1024),read=Mock(side_effect=AssertionError('must not read oversized response')))
  connection=SimpleNamespace(request=Mock(),getresponse=lambda:response,close=Mock())
  with patch.object(memory.lovehouse,'_port',return_value=12345),patch.object(memory.lovehouse,'_health',return_value=True),patch.object(memory.lovehouse,'_cookie',return_value='isolated-test-cookie'),patch.object(memory.http.client,'HTTPConnection',return_value=connection):
   with self.assertRaises(ValueError):memory._native_get('/api/graph','default')
  response.read.assert_not_called();connection.close.assert_called_once()

if __name__=='__main__':unittest.main()
