// Permission and late-response flows in isolation. No browser, QQ or network.
const runTest=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=(name,fn)=>runTest(name,{timeout:4000},fn);
const source=fs.readFileSync(path.join(__dirname,'../app/ui/conversation.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');

function harness(){
 const nodes=new Map(),calls=[],toasts=[],gates=[];let generation=0,saveModal=null;
 let personas={revision:'rev-one',can_apply:true,active_name:'原人格',cards:[{id:'calm',name:'安静小鱼',subtitle:'安静陪伴',author:'myflww',license:'非商业',tags:['轻松'],art:'/assets/plugins/astrbot_plugin_dafeiyu_stickers/pack/happy.png',source_urls:['https://github.com/myflww/aiwu-jiyu']}],backups:[{id:'backup-one',created_at:1700000000,card_before:'原人格'}]};
 let prompt='完整人格与示例',turns={collection_seconds:10,collection_max_wait_seconds:30,collection_max_messages:12,collection_max_chars:6000,message_collection_enabled:true,natural_bubbles_enabled:true,group_reply_mode:'addressed',group_wake_words:'小鱼',group_cooldown_seconds:90,group_hourly_limit:6,group_participation_probability:3,plugin_installed:true,plugin_enabled:true};
 let life={settings:{search_enabled:false,search_provider:'bing_rss',searxng_url:'',profile_enabled:false,qzone_enabled:false},capabilities:{},owner_configured:true,bot_configured:true,avatars:[{id:'happy',label:'开心'}]};
 let searchResults=[{url:'https://example.com/source',title:'来源',snippet:'实际搜索摘要'}],review={action:'nickname',label:'确认修改',target_bot:'robot-test',before:'旧昵称',after:'新昵称'};
 function simple(node,selector){if(selector.startsWith('#'))return node.id===selector.slice(1);if(selector.startsWith('.'))return node.classList.contains(selector.slice(1));const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return Object.hasOwn(node.attrs,attr[1])&&(attr[2]===undefined||node.attrs[attr[1]]===attr[2]);return node.tagName===selector.toUpperCase();}
 const matches=(node,selector)=>selector.split(',').some(value=>{const parts=value.trim().split(/\s+/);if(!simple(node,parts.pop()))return false;for(let i=parts.length-1,ancestor=node.parentNode;i>=0;i--){while(ancestor&&!simple(ancestor,parts[i]))ancestor=ancestor.parentNode;if(!ancestor)return false;ancestor=ancestor.parentNode;}return true;});
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.listeners={};this.checked=false;this.disabled=false;this.value='';this._html='';this._text='';this._id='';const classes=new Set();this.classList={add:(...v)=>v.forEach(x=>classes.add(x)),remove:(...v)=>v.forEach(x=>classes.delete(x)),contains:v=>classes.has(v),toggle(v,on){on=on??!classes.has(v);on?classes.add(v):classes.delete(v);return on;}};}
  set id(v){if(nodes.get(this._id)===this)nodes.delete(this._id);this._id=String(v);if(v)nodes.set(this._id,this);}get id(){return this._id;}
  set className(v){String(v).split(/\s+/).filter(Boolean).forEach(x=>this.classList.add(x));}
  setAttribute(k,v){v=String(v);this.attrs[k]=v;if(k==='id')this.id=v;if(k==='class')this.className=v;if(k==='value')this.value=v;if(k==='checked')this.checked=true;if(k==='disabled')this.disabled=true;if(['type','src','href'].includes(k))this[k]=v;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=v;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  after(child){const parent=this.parentNode;if(!parent)return;child.parentNode=parent;parent.children.splice(parent.children.indexOf(this)+1,0,child);}
  unregister(){this.children.forEach(x=>x.unregister());if(nodes.get(this.id)===this)nodes.delete(this.id);this.parentNode=null;}
  set innerHTML(v){this.children.forEach(x=>x.unregister());this.children=[];this._text='';this._html=String(v);parse(this._html,this);}get innerHTML(){return this._html;}
  set textContent(v){this.children.forEach(x=>x.unregister());this.children=[];this._text=String(v);this._html=escape(v);}get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  querySelectorAll(selector){const found=[];const walk=node=>{for(const child of node.children){if(matches(child,selector))found.push(child);walk(child);}};walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(type,fn,options={}){(this.listeners[type]??=[]).push({fn,once:!!options.once,capture:!!options.capture});}
  get isConnected(){for(let node=this;node;node=node.parentNode)if(node===document.body)return true;return false;}
  async fire(type){if(type==='click'&&this.disabled)return;const event={type,target:this,currentTarget:this,preventDefault(){}};const entries=[...(this.listeners[type]||[])].sort((a,b)=>Number(b.capture)-Number(a.capture));for(const entry of entries.filter(x=>x.capture)){if(entry.once)this.listeners[type]=this.listeners[type].filter(x=>x!==entry);await entry.fn(event);}if(type==='click'&&this.onclick)await this.onclick(event);if(type==='change'&&this.onchange)await this.onchange(event);for(const entry of entries.filter(x=>!x.capture)){if(entry.once)this.listeners[type]=this.listeners[type].filter(x=>x!==entry);await entry.fn(event);}await settle();}
 }
 function parse(html,parent){const stack=[parent],voids=new Set(['input','img','br','hr']);for(const token of html.matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g)){const value=token[0],node=stack.at(-1);if(value.startsWith('</')){if(stack.length>1)stack.pop();continue;}if(!value.startsWith('<')){if(node!==parent){node._text+=decode(value);if(node.tagName==='TEXTAREA')node.value+=decode(value);}continue;}const open=value.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!open)continue;const child=new Element(open[1]);for(const attr of open[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))child.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));node.appendChild(child);if(child.tagName==='OPTION'&&node.tagName==='SELECT'&&(!node.value||Object.hasOwn(child.attrs,'selected')))node.value=child.value;if(!voids.has(open[1])&&!value.endsWith('/>'))stack.push(child);}}
 const document={body:new Element('body'),getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element(tag),querySelectorAll:selector=>document.body.querySelectorAll(selector)};
 const add=(id,parent=document.body,tag='div')=>{const node=new Element(tag);node.id=id;parent.appendChild(node);return node;};
 const settings=add('view-settings'),identity=add('settingsIdentity',settings),nav=add('nav',settings);nav.className='settings-section-nav';
 const whitelist=add('view-whitelist'),summary=add('summary',whitelist);summary.className='permission-summary';
 for(const id of ['modalTitle','modalBody','modalOk','modalCancel'])add(id,document.body,id.startsWith('modal')&&['modalOk','modalCancel'].includes(id)?'button':'div');
 function route(url,body){
  if(url==='/api/personas')return clone(personas);
  if(url==='/api/personas/preview')return {prompt};
  if(url==='/api/personas/apply'||url==='/api/personas/restore')return {ok:true};
  if(url==='/api/companion'){if(Object.keys(body).length)turns={...turns,...body};return clone(turns);}
  if(url==='/api/life')return clone(life);
  if(url==='/api/life/settings'){life.settings={...life.settings,...body};return {ok:true,persona_tools:{notice:'能力已保存'}};}
  if(url==='/api/life/search')return {results:clone(searchResults)};
  if(url==='/api/life/profile')return {nickname:'小鱼',signature:'日常陪伴'};
  if(url==='/api/life/prepare')return {draft_id:'draft-one',confirmation_token:'token-one',review:clone(review)};
  if(url==='/api/life/confirm')return {status:'verified'};
  if(url==='/api/life/cancel')return {ok:true};
  throw new Error('Unexpected fixture API '+url);
 }
 async function api(url,options={}){const body=JSON.parse(options.body||'{}');calls.push({url,body:clone(body),method:options.method||'GET'});const result=route(url,body),index=gates.findIndex(g=>g.url===url);if(index>=0){const gate=gates.splice(index,1)[0];gate.enter();const replacement=await gate.promise;return replacement===undefined?result:replacement;}return result;}
 const get=id=>{assert.ok(nodes.has(id),'missing '+id);return nodes.get(id);};
 const shell={api,esc:escape,toast:message=>toasts.push(String(message)),showView(){},getModalGeneration:()=>generation,refreshPlugins(){},
  openModal(title,html,save){++generation;get('modalTitle').textContent=title;get('modalBody').innerHTML=html;get('modalOk').disabled=false;get('modalOk').classList.remove('hidden');get('modalCancel').textContent='取消';saveModal=save;},
  closeModal(){++generation;saveModal=null;get('modalBody').innerHTML='';}};
 get('modalCancel').addEventListener('click',()=>shell.closeModal());
 const window={},context=vm.createContext({window,document,console,Date});vm.runInContext(source,context);window.AiwuConversation.init(shell);
 return {api:window.AiwuConversation,shell,calls,toasts,get,requests:url=>calls.filter(call=>call.url===url),save:()=>saveModal?.(),title:()=>get('modalTitle').textContent,
  click:id=>get(id).fire('click'),button:(selector,id='personaCardList')=>{const node=get(id).querySelector(selector);assert.ok(node,'missing '+selector);return node;},
  setPersonas:value=>{personas={...personas,...clone(value)};},setPrompt:value=>{prompt=value;},setSearch:value=>{searchResults=clone(value);},setReview:value=>{review={...review,...clone(value)};},
  defer(url){let enter,release,reject;const entered=new Promise(resolve=>{enter=resolve;}),promise=new Promise((yes,no)=>{release=yes;reject=no;});gates.push({url,enter,promise});return {entered,release,reject};}};
}

test('persona preview never applies automatically; apply and restore keep the shown revision and require restore consent',async()=>{
 const h=harness();await h.api.loadPersonas();await h.button('[data-persona-preview]').fire('click');assert.equal(h.requests('/api/personas/apply').length,0);
 await h.save();assert.deepEqual(h.requests('/api/personas/apply')[0].body,{card_id:'calm',expected_revision:'rev-one'});
 await h.button('[data-persona-restore]','personaBackupList').fire('click');await assert.rejects(()=>h.save(),/确认/);assert.equal(h.requests('/api/personas/restore').length,0);
 h.get('personaRestoreConfirm').checked=true;await h.save();assert.deepEqual(h.requests('/api/personas/restore')[0].body,{backup_id:'backup-one',expected_revision:'rev-one',confirmed:true});
});

test('a late persona preview cannot replace a different modal or apply anything',async()=>{
 const h=harness();await h.api.loadPersonas();const gate=h.defer('/api/personas/preview'),click=h.button('[data-persona-preview]').fire('click');await gate.entered;
 h.shell.openModal('另一个窗口','<p>正在操作别的内容</p>',null);gate.release();await click;assert.equal(h.title(),'另一个窗口');assert.equal(h.requests('/api/personas/apply').length,0);
});

test('life opening is read-only and saving passes only explicit capability settings without searching or sending',async()=>{
 const h=harness();await h.api.openLife();assert.deepEqual(h.calls.map(x=>x.url),['/api/life']);
 h.get('lifeSearchEnabled').checked=true;h.get('lifeProfileEnabled').checked=true;h.get('lifeSearchProvider').value='searxng';h.get('lifeEndpoint').value='https://search.example.com/search';
 const gate=h.defer('/api/life/settings'),saving=h.click('lifeSave');await gate.entered;h.get('lifeEndpoint').value='https://new.example.com/search';gate.release();await saving;
 assert.deepEqual(h.requests('/api/life/settings')[0].body,{search_enabled:true,search_provider:'searxng',searxng_url:'https://search.example.com/search',profile_enabled:true,qzone_enabled:false});
 assert.equal(h.get('lifeEndpoint').value,'https://new.example.com/search');assert.equal(h.requests('/api/life/search').length,0);assert.equal(h.requests('/api/life/prepare').length,0);assert.equal(h.requests('/api/life/confirm').length,0);
});

test('account changes prepare a draft first, require the review checkbox, and cancel the exact unconfirmed draft',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeText').value='新昵称';await h.click('lifePrepare');assert.equal(h.requests('/api/life/confirm').length,0);
 await assert.rejects(()=>h.save(),/核对/);assert.equal(h.requests('/api/life/confirm').length,0);h.get('lifeConfirmed').checked=true;await h.save();
 assert.deepEqual(h.requests('/api/life/confirm')[0].body,{draft_id:'draft-one',confirmation_token:'token-one',confirmed:true});
 await h.api.openLife();h.get('lifeText').value='另一份草稿';await h.click('lifePrepare');await h.click('modalCancel');assert.deepEqual(h.requests('/api/life/cancel')[0].body,{draft_id:'draft-one'});assert.equal(h.requests('/api/life/confirm').length,1);
});

test('custom online status prepares only wording and the selected icon, then requires explicit review confirmation',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeAction').value='status';await h.get('lifeAction').fire('change');h.get('lifeText').value='  认真干活  ';h.get('lifeStatusIcon').value='focus';
 h.setReview({action:'status',label:'确认在线状态',before:'休息',after:'认真干活 · 奋斗'});await h.click('lifePrepare');
 assert.deepEqual(h.requests('/api/life/prepare')[0].body,{action:'status',payload:{wording:'认真干活',icon_id:'focus'}});assert.equal(h.requests('/api/life/confirm').length,0);
 await assert.rejects(()=>h.save(),/核对/);h.get('lifeConfirmed').checked=true;await h.save();assert.deepEqual(h.requests('/api/life/confirm')[0].body,{draft_id:'draft-one',confirmation_token:'token-one',confirmed:true});
});

test('editing while prepare is pending cancels the old draft and preserves the new input without opening review',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeText').value='旧昵称';const gate=h.defer('/api/life/prepare'),preparing=h.click('lifePrepare');await gate.entered;
 h.get('lifeText').value='刚修改的新昵称';await h.get('lifeText').fire('input');gate.release();await preparing;
 assert.equal(h.title(),'QQ 生活与联网搜索');assert.equal(h.get('lifeText').value,'刚修改的新昵称');assert.equal(h.get('modalBody').querySelector('#lifeConfirmed'),null);
 assert.deepEqual(h.requests('/api/life/cancel').map(call=>call.body),[{draft_id:'draft-one'}]);assert.equal(h.requests('/api/life/confirm').length,0);assert.match(h.get('lifeNotice').textContent,/内容已改变/);
});

test('unavailable prepare responses without a real draft never expose a confirmation action',async()=>{
 for(const response of [{ok:false,status:'unavailable',message:'连接服务不支持状态修改'},{status:'unavailable',message:'当前无法准备预览'}]){
  const h=harness();await h.api.openLife();h.get('lifeText').value='新昵称';const gate=h.defer('/api/life/prepare'),preparing=h.click('lifePrepare');await gate.entered;gate.release(response);await preparing;
  assert.equal(h.title(),'QQ 生活与联网搜索');assert.equal(h.get('modalBody').querySelector('#lifeConfirmed'),null);assert.equal(h.get('modalOk').classList.contains('hidden'),true);
  assert.equal(h.save(),undefined);assert.equal(h.requests('/api/life/confirm').length,0);assert.equal(h.requests('/api/life/cancel').length,0);assert.ok(h.toasts.includes(response.message));
 }
});

test('untrusted persona, search and review text is escaped; active URLs must pass the allowed protocol',async()=>{
 const h=harness(),bad='<img src=x onerror=alert(1)>';h.setPrompt('</textarea>'+bad);await h.api.loadPersonas();await h.button('[data-persona-preview]').fire('click');
 assert.match(h.get('modalBody').innerHTML,/&lt;\/textarea&gt;&lt;img/);assert.doesNotMatch(h.get('modalBody').innerHTML,/<img src=x/);
 await h.api.openLife();h.setSearch([{url:'javascript:alert(1)',title:bad,snippet:bad},{url:'https://example.com/?x=1&y=2',title:bad,snippet:bad}]);h.get('lifeQuery').value='关键词';await h.click('lifeSearchRun');
 assert.doesNotMatch(h.get('lifeSearchResults').innerHTML,/javascript:|<img src=x/);assert.match(h.get('lifeSearchResults').innerHTML,/&lt;img/);
 h.setReview({before:bad,after:bad,target_bot:bad,visibility_label:bad});await h.click('lifePrepare');assert.doesNotMatch(h.get('modalBody').innerHTML,/<img src=x/);assert.match(h.get('modalBody').innerHTML,/&lt;img/);
});

test('edits during a rules GET or save survive the stale response instead of silently restoring old input',async()=>{
 const h=harness();await h.api.loadTurns();const gate=h.defer('/api/companion'),loading=h.api.loadTurns();await gate.entered;
 h.get('turnWaitSeconds').value='23';await h.get('turnWaitSeconds').fire('input');gate.release();await loading;assert.equal(h.get('turnWaitSeconds').value,'23');
 const save=h.defer('/api/companion'),saving=h.click('turnSave');await save.entered;h.get('turnWaitSeconds').value='27';await h.get('turnWaitSeconds').fire('input');save.release();await saving;
 assert.equal(h.get('turnWaitSeconds').value,'27');assert.equal(h.requests('/api/companion').at(-1).body.collection_seconds,23);assert.match(h.get('turnNotice').textContent,/尚未保存/);
});

test('a returned search is not shown under a subsequently edited query, and a late life GET does not steal another modal',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeQuery').value='旧关键词';const gate=h.defer('/api/life/search'),search=h.click('lifeSearchRun');await gate.entered;h.get('lifeQuery').value='新关键词';gate.release();await search;
 assert.match(h.get('lifeSearchResults').textContent,/关键词已改变/);assert.equal(h.get('lifeSearchResults').querySelectorAll('a').length,0);
 const life=h.defer('/api/life'),opening=h.api.openLife();await life.entered;h.shell.openModal('其他设置','<p>保留此窗口</p>',null);life.release();await opening;assert.equal(h.title(),'其他设置');
});
