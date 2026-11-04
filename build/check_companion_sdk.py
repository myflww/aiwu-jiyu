"""Run with the installed AstrBot SDK interpreter, isolated root, no real I/O."""
from pathlib import Path
import asyncio,importlib.util,json,os,sys,tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
TEMP=tempfile.TemporaryDirectory(prefix='aiwu-companion-sdk-')
os.environ['ASTRBOT_ROOT']=TEMP.name
os.chdir(TEMP.name)
from astrbot.api.event import MessageChain
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata,star_registry
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import ConversationV2
from astrbot.core.agent.message import AssistantMessageSegment
from astrbot.core.utils.session_lock import session_lock_manager
from astrbot.core.provider.entities import LLMResponse,TokenUsage
plugin=ROOT/'assets/plugins/astrbot_plugin_dafeiyu_companion'
spec=importlib.util.spec_from_file_location('_aiwu_companion_sdk',plugin/'__init__.py',submodule_search_locations=[str(plugin)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from importlib import import_module
module=import_module(spec.name+'.reply_pipeline')
companion_main=import_module(spec.name+'.main')
UMO='sdk-test:FriendMessage:123456789'
async def run():
 usage_plugin=companion_main.DafeiyuCompanion.__new__(companion_main.DafeiyuCompanion)
 usage_plugin._state={'usage_event':'real-sdk-usage','usage_reported':False,'usage':{'date':'2026-11-04','input_tokens':0,'output_tokens':0,'reported_calls':0,'unreported_calls':1}}
 usage_plugin._save=lambda:True
 sdk_response=LLMResponse(role='assistant',completion_text='ok',usage=TokenUsage(input_other=10,input_cached=5,output=3))
 usage_plugin._usage_finish('real-sdk-usage',sdk_response)
 assert usage_plugin._state['usage']['input_tokens']==15 and usage_plugin._state['usage']['output_tokens']==3
 assert usage_plugin._state['usage']['reported_calls']==1 and usage_plugin._state['usage']['unreported_calls']==0
 conv=ConversationV2(conversation_id='cid',platform_id='sdk-test',user_id=UMO,
  content=[{'role':'user','content':'明天有面试'}],created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
 class DB:
  async def get_conversation_by_id(self,*,cid):return conv
  async def update_conversation(self,*,cid,content,**kwargs):conv.content=content
 manager=ConversationManager(DB());manager.session_conversations[UMO]='cid'
 adapter=SimpleNamespace(meta=lambda:SimpleNamespace(id='sdk-test',name='aiocqhttp'),send_by_session=AsyncMock())
 context=SimpleNamespace(conversation_manager=manager,platform_manager=SimpleNamespace(platform_insts=[adapter]),get_config=lambda **kw:{})
 context.send_message=lambda *args:Context.send_message(context,*args)
 context.get_registered_star=lambda name:Context.get_registered_star(context,name)
 love=ROOT/'assets/plugins/astrbot_plugin_love_house'
 love_spec=importlib.util.spec_from_file_location('_aiwu_memory_sdk',love/'__init__.py',submodule_search_locations=[str(love)])
 love_package=importlib.util.module_from_spec(love_spec);sys.modules[love_spec.name]=love_package;love_spec.loader.exec_module(love_package)
 love_main=import_module(love_spec.name+'.main')
 Bridge=import_module(love_spec.name+'.bridge').Bridge
 source={'source_id':'source_sdk','runtime_agent_id':'fish_sdk','adapter':'astrbot','platforms':['qq'],'enabled':True,'revoked':False}
 client=SimpleNamespace(status=lambda:dict(source),read_context=lambda *args,**kw:{'memory_context':{'material_role':'evidence_not_instructions','records':[{'layer':'commitment','value':'明天面试'}]}})
 bridge=Bridge(Path(TEMP.name)/'journal',client,runtime_agent_id='fish_sdk',allowed_umos=[UMO])
 memory=love_main.LoveHousePlugin.__new__(love_main.LoveHousePlugin)
 memory.context=context;memory.config={'enabled':True};memory.router=SimpleNamespace(for_umo=lambda umo:bridge if umo==UMO else None)
 metadata=context.get_registered_star('love_house')
 if metadata is None:
  metadata=StarMetadata(name='love_house');star_registry.append(metadata)
 metadata.star_cls=memory;metadata.activated=True
 try:
  pipe=module.ReplyPipeline(context,MessageChain,AsyncMock(return_value=True))
  notes,status=await pipe.context_notes(UMO,'out1','query');assert '明天面试' in notes and 'love_house_memory_material' in notes
  async with module.session_guard(UMO):
   result=await pipe.deliver(UMO,'cid','out1','希望你的面试顺利。',{},AsyncMock(return_value=True))
  assert result['accepted'] and result['history']=='saved' and result['memory']=='durably_staged',result
  adapter.send_by_session.assert_awaited_once()
  AssistantMessageSegment.model_validate(conv.content[-1])
  assert len(conv.content)==2 and conv.content[-1]['role']=='assistant'
  with bridge.db() as db:
   row=db.execute('SELECT kind,user,text FROM records').fetchone()
   assert row['kind']=='output' and row['user'] is None and row['text']=='希望你的面试顺利。'
  memory.config['record_companion_outputs']=False
  assert await memory.companion_recall(UMO,'out2','query')==''
  assert await memory.companion_record(UMO,'out2','no recording',receipt='adapter_accepted') is False
  memory.config['record_companion_outputs']=True
  source['enabled']=False
  assert await memory.companion_recall(UMO,'out3','query')==''
  source['enabled']=True
  hold=asyncio.Event();entered=asyncio.Event();release=asyncio.Event()
  async def ordinary():
   async with session_lock_manager.acquire_lock(UMO):hold.set();await release.wait()
  async def proactive():
   async with module.session_guard(UMO):entered.set()
  old=asyncio.create_task(ordinary());await hold.wait();new=asyncio.create_task(proactive());await asyncio.sleep(0)
  assert not entered.is_set();release.set();await asyncio.gather(old,new);assert entered.is_set()
  print(json.dumps({'real_sdk_shared_send':True,'real_conversation_manager':True,'assistant_only_valid_schema':True,'shared_session_lock':True,'real_memory_bridge_output_and_gates':True,'real_TokenUsage_fields':True,'real_QQ_or_model_calls':False}))
 finally:star_registry.remove(metadata)
try:
 asyncio.run(run())
finally:
 os.chdir(ROOT)
 assert Path(TEMP.name).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
 TEMP.cleanup()
