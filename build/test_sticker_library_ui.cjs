// Actual app.js handlers with a small DOM/API simulation. No browser or network.
const runTest = require('node:test');
const test = (name, action) => runTest(name, {timeout:3000}, action);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../app/ui/app.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const settled = () => new Promise(resolve => setImmediate(resolve));
const decode = text => String(text).replace(/&quot;/g, '"').replace(/&#39;/g, "'")
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

function fixture() {
  const personal = ['a', 'b'].map((letter, index) => ({id:'custom-'+letter.repeat(24),pack_id:'custom',
    title:index ? '第二张小鱼' : '第一张小鱼',label:'个人图',emotion:index ? 'shy' : 'happy',tags:['轻松'],
    description:'画面说明',use_when:'轻松接梗',avoid_when:'认真求助',attribution:'原作者'+index,enabled:true,
    url:'/api/stickers/custom/custom-'+letter.repeat(24)+'/image',format:'GIF',width:32,height:32,frames:2,size:400}));
  return {settings:{auto_match_enabled:true,cooldown_seconds:30,pack_selection:'all',auto_probability:35,scenario_policy:'balanced',custom_enabled:true},
    stickers:[{id:'happy',label:'原创图',pack_id:'original',url:'/assets/happy.png'},
      {id:'contrib-test',label:'审核投稿图',pack_id:'contributed',attribution:'社区作者',url:'/assets/community/image.gif'},...personal],packs:[]};
}

function harness(initial = fixture()) {
  const elements = new Map(), calls = [], fetches = [], toasts = [], downloads = [], urls = [], revoked = [];
  const intervals = new Map(), timeouts = new Map(), gates = [];
  let nextTimer = 1, nextURL = 1, allowConfirm = true, nextItem = 1;
  let data = plain(initial), auth = {configured:false,login:'',remembered:false};
  let job = {running:false,state:'idle',message:'尚未投稿',pr_url:'',fingerprint:''};
  class Element {
    constructor(tag='div') {
      this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.style={};
      this.listeners={};this.value='';this.checked=false;this.disabled=false;this.files=[];this._html='';this._text='';
      const classes=new Set();this.classList={add:(...xs)=>xs.forEach(x=>classes.add(x)),remove:(...xs)=>xs.forEach(x=>classes.delete(x)),
        contains:x=>classes.has(x),toggle(x,force){const on=force===undefined?!classes.has(x):force;on?classes.add(x):classes.delete(x);return on;}};
    }
    get isConnected(){for(let node=this;node;node=node.parentNode)if(node===document.body)return true;return false;}
    setAttribute(name,value){this.attrs[name]=String(value);if(name==='id'){this.id=String(value);elements.set(this.id,this);}
      if(name==='class')String(value).split(/\s+/).filter(Boolean).forEach(x=>this.classList.add(x));
      if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);
      if(name==='value')this.value=decode(value);if(name==='checked')this.checked=true;if(name==='disabled')this.disabled=true;
      if(['src','href','type','download'].includes(name))this[name]=decode(value);}
    getAttribute(name){return this.attrs[name]??null;} removeAttribute(name){delete this.attrs[name];}
    appendChild(child){child.parentNode=this;this.children.push(child);return child;}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this._unregister();this.parentNode=null;}
    _unregister(){this.children.forEach(x=>x._unregister());if(this.id&&elements.get(this.id)===this)elements.delete(this.id);}
    replaceChildren(...nodes){this.children.forEach(x=>x._unregister());this.children=[];this._html='';this._text='';nodes.forEach(x=>this.appendChild(x));}
    set innerHTML(html){this.replaceChildren();this._html=String(html);parse(this._html,this);}
    get innerHTML(){return this._html;}
    set textContent(value){this.replaceChildren();this._text=String(value);this._html=String(value);}
    get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
    addEventListener(type,handler){(this.listeners[type]??=[]).push(handler);}
    querySelectorAll(selector){const matches=[];function walk(node){for(const child of node.children){if(match(child,selector))matches.push(child);walk(child);}}walk(this);return matches;}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    focus(){document.activeElement=this;} scrollIntoView(){} select(){}
    async fire(type){if(type==='click'&&this.disabled)return;const event={type,target:this,currentTarget:this,preventDefault(){}};
      const handlers=[...(this.listeners[type]||[]),...(type==='click'&&this.onclick?[this.onclick]:[])];
      for(const handler of handlers)await handler(event);}
    click(){if(this.tagName==='A'){downloads.push({href:this.href,download:this.download});return;}return this.fire('click');}
  }
  function match(element,selector){return selector.split(',').some(part=>{part=part.trim();
    const attribute=part.match(/^\[([^\]]+)\]$/);if(attribute)return Object.hasOwn(element.attrs,attribute[1]);
    const tag=part.match(/^(input|select|textarea|button|a)(?::not\(\[disabled\]\))?$/i);
    return !!tag&&element.tagName===tag[1].toUpperCase()&&(!part.includes(':not')||!element.disabled);});}
  function parse(html,parent){const stack=[parent],voids=new Set(['input','img','br','hr','meta','link']);
    for(const token of html.matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g)){const text=token[0],node=stack.at(-1);
      if(text.startsWith('</')){if(stack.length>1)stack.pop();continue;}
      if(!text.startsWith('<')){if(node!==parent){node._html+=text;node._text+=decode(text);}continue;}
      const opening=text.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!opening)continue;
      const child=new Element(opening[1]);
      for(const attr of opening[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))child.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));
      node.appendChild(child);if(child.tagName==='OPTION'&&node.tagName==='SELECT'&&!node.value)node.value=child.value;
      if(!voids.has(opening[1].toLowerCase())&&!text.endsWith('/>'))stack.push(child);}}
  const document={hidden:false,activeElement:null,addEventListener(){},createElement:tag=>new Element(tag),
    getElementById:id=>elements.get(id)||null,querySelectorAll:selector=>document.body.querySelectorAll(selector)};
  document.body=new Element('body');document.activeElement=document.body;
  for(const id of ['modalBack','modalTitle','modalBody','modalOk','modalCancel']){const el=new Element(id.startsWith('modalO')||id==='modalCancel'?'button':'div');el.setAttribute('id',id);document.body.appendChild(el);}
  class FakeURL extends URL {static createObjectURL(value){const url='blob:sticker-test/'+nextURL++;urls.push({url,value});return url;}static revokeObjectURL(url){revoked.push(url);}}
  class FileReader {readAsDataURL(file){queueMicrotask(()=>{if(file.readError){this.onerror?.();return;}this.result='data:'+file.type+';base64,'+Buffer.from(file.content||'fake-raster-bytes').toString('base64');this.onload?.();});}}
  const get=id=>{const value=elements.get(id);assert.ok(value,`missing control ${id}`);return value;};
  function personal(){return data.stickers.filter(item=>item.pack_id==='custom');}
  function preview(body){const item=personal().find(x=>x.id===body.item_id);assert.ok(item,'preview needs selected personal item');
    const metadata=Object.fromEntries(['title','emotion','tags','description','use_when','avoid_when'].map(key=>[key,item[key]]));metadata.attribution=body.attribution;
    const fingerprint=crypto.createHash('sha256').update(JSON.stringify({item:item.id,metadata})).digest('hex');
    return {ok:true,fingerprint,metadata,license:'CC-BY-NC-4.0',size:512,files:['image.gif','metadata.json','LICENSE.txt'].map(name=>'assets/plugins/astrbot_plugin_dafeiyu_stickers/community/submissions/'+fingerprint+'/'+name)};}
  function route(url,options={}){const body=JSON.parse(options.body||'{}');
    if(url==='/api/stickers')return plain(data);
    if(url==='/api/stickers/settings'){data.settings={...data.settings,...body};return {ok:true};}
    if(url==='/api/stickers/custom/import'){const {image_base64,...metadata}=body;assert.equal(typeof image_base64,'string');
      const id='custom-'+String(nextItem++).repeat(24);const item={...metadata,id,pack_id:'custom',label:metadata.title,url:'/api/stickers/custom/'+id+'/image'};data.stickers.push(item);return {ok:true,item};}
    if(url==='/api/stickers/custom/update'){const item=personal().find(x=>x.id===body.id);assert.ok(item);Object.assign(item,body.changes);return {ok:true,item:plain(item)};}
    if(url==='/api/stickers/custom/delete'){data.stickers=data.stickers.filter(x=>x.id!==body.id);return {ok:true};}
    if(url==='/api/stickers/contributions/auth')return plain(auth);
    if(url==='/api/stickers/contributions/connect'){auth={configured:true,login:'test-contributor',remembered:body.remember};return plain(auth);}
    if(url==='/api/stickers/contributions/disconnect'){auth={configured:false,login:''};return plain(auth);}
    if(url==='/api/stickers/contributions/preview')return preview(body);
    if(url==='/api/stickers/contributions/submit'){job={running:true,state:'submitting',message:'提交中',pr_url:'',fingerprint:body.fingerprint};return {ok:true,...plain(job)};}
    if(url==='/api/stickers/contributions/status')return plain(job);
    throw new Error('Unexpected mocked API '+url);}
  async function deliver(kind,url,options){const ledger=kind==='fetch'?fetches:calls;ledger.push({url,options:plain(options||{})});
    const gateIndex=gates.findIndex(x=>x.kind===kind&&x.url===url),gate=gateIndex<0?null:gates.splice(gateIndex,1)[0];
    const value=kind==='fetch'?{ok:true,async blob(){return {type:'application/zip',size:20};}}:route(url,options);
    if(gate){gate.enteredResolve();const replacement=await gate.promise;return replacement===undefined?value:replacement;}return value;}
  const context=vm.createContext({document,window:{addEventListener(){}},localStorage:{getItem(){return null;}},console,URL:FakeURL,URLSearchParams,FileReader,
    setInterval(fn){const id=nextTimer++;intervals.set(id,fn);return id;},clearInterval:id=>intervals.delete(id),
    setTimeout(fn){const id=nextTimer++;timeouts.set(id,fn);return id;},clearTimeout:id=>timeouts.delete(id),
    confirm:()=>allowConfirm,fetch:(url,options)=>deliver('fetch',url,options)});
  vm.runInContext(source,context);
  context.mockAPI=(url,options)=>deliver('api',url,options);context.mockToast=value=>toasts.push(String(value));
  vm.runInContext('api=mockAPI;toast=mockToast;loadPlugins=async()=>{};pluginList=[{id:"astrbot_plugin_dafeiyu_stickers",enabled:true}];state=null;',context);
  return {context,calls,fetches,toasts,downloads,urls,revoked,intervals,timeouts,element:get,
    async open(){await context.openStickerLibrary();await settled();},
    click:id=>get(id).fire('click'),async input(id,value){get(id).value=value;await get(id).fire('input');},
    async check(id,value=true){get(id).checked=value;await get(id).fire('change');},
    async file(file){get('stickerCustomFile').files=file?[file]:[];await get('stickerCustomFile').fire('change');},
    card(action,id=personal()[0]?.id){const key='sticker'+action[0].toUpperCase()+action.slice(1);const button=get('stickerPersonalList').querySelectorAll('[data-sticker-'+action+']').find(x=>x.dataset[key]===id);assert.ok(button,`missing ${action} for ${id}`);return button;},
    personal:()=>plain(personal()),setConfirm:value=>{allowConfirm=value;},setAuth:value=>{auth=plain(value);},setJob:value=>{job={...job,...plain(value)};},
    requests:url=>calls.filter(x=>x.url===url),async tick(){for(const callback of [...intervals.values()])await callback();await settled();},
    runTimeouts(){for(const [id,callback]of[...timeouts]){timeouts.delete(id);callback();}},
    defer(url,kind='api'){let resolve,reject,enteredResolve;const promise=new Promise((a,b)=>{resolve=a;reject=b;});const entered=new Promise(a=>{enteredResolve=a;});gates.push({url,kind,promise,enteredResolve});return {entered,release:resolve,reject};}};
}

async function ready(h,{auth=false}={}) {if(auth)h.setAuth({configured:true,login:'test-contributor'});await h.open();await h.card('share').fire('click');await h.click('stickerPrepareShare');await h.check('stickerShareRights');}

test('saved advanced values and personal/approved credits are kept; save sends only typed preferences',async()=>{
  const data=fixture();Object.assign(data.settings,{auto_probability:0,scenario_policy:'manual',custom_enabled:false,pack_selection:'custom'});
  const h=harness(data);await h.open();assert.equal(h.element('sticker-probability').value,'0');assert.equal(h.element('sticker-scenario').value,'manual');
  assert.equal(h.element('sticker-custom-enabled').checked,false);assert.match(h.element('modalBody').innerHTML,/社区作者/);
  await h.click('modalOk');const body=JSON.parse(h.requests('/api/stickers/settings')[0].options.body);
  assert.deepEqual(body,{auto_match_enabled:true,cooldown_seconds:30,pack_selection:'custom',auto_probability:0,scenario_policy:'manual',custom_enabled:false});
  assert.equal(h.element('modalBack').classList.contains('show'),false);
});

test('file validation, local preview and import send raster bytes plus allowed metadata without local filename',async()=>{
  const h=harness();await h.open();await h.click('stickerAddCustom');
  await h.file({type:'text/html',size:20,name:'secret.html'});await h.file({type:'image/png',size:9*1024*1024,name:'huge.png'});
  assert.equal(h.urls.length,0);assert.equal(h.requests('/api/stickers/custom/import').length,0);
  await h.file({type:'image/gif',size:64,name:'C:/private/personal-name.gif',content:'fake-gif'});
  h.element('stickerCustomTitle').value='新表情';h.element('stickerCustomEmotion').value='shy';h.element('stickerCustomTags').value='开心, 鼓励、元气，夸奖';
  h.element('stickerCustomAttribution').value='我的署名';await h.click('stickerSaveCustom');
  const body=JSON.parse(h.requests('/api/stickers/custom/import')[0].options.body);
  assert.deepEqual(Object.keys(body).sort(),['title','emotion','tags','description','use_when','avoid_when','attribution','enabled','image_base64'].sort());
  assert.deepEqual(body.tags,['开心','鼓励','元气','夸奖']);assert.equal(Buffer.from(body.image_base64,'base64').toString(),'fake-gif');
  assert.ok(!JSON.stringify(body).includes('private'));assert.equal(h.personal().length,3);assert.equal(h.revoked.length,1);
  assert.equal(h.element('stickerEditor').classList.contains('hidden'),true);assert.equal(h.element('stickerSaveCustom').disabled,false);
});

test('a file-read failure does not upload or strand the save control',async()=>{
  const h=harness();await h.open();await h.click('stickerAddCustom');await h.file({type:'image/png',size:32,readError:true});
  h.element('stickerCustomTitle').value='不能读取的图片';await h.click('stickerSaveCustom');
  assert.equal(h.requests('/api/stickers/custom/import').length,0);assert.equal(h.element('stickerSaveCustom').disabled,false);
  assert.ok(h.toasts.some(message=>message.includes('图片读取失败')));
});

test('a late import completion does not erase a new editor draft or its selected preview',async()=>{
  const h=harness();await h.open();await h.click('stickerAddCustom');await h.file({type:'image/png',size:32,content:'old-file'});
  await h.input('stickerCustomTitle','正在保存的旧素材');const gate=h.defer('/api/stickers/custom/import'),pending=h.click('stickerSaveCustom');await gate.entered;
  await h.click('stickerAddCustom');await h.file({type:'image/gif',size:32,content:'new-draft'});await h.input('stickerCustomTitle','新的草稿');
  const url=h.element('stickerCustomPreview').src;gate.release();await pending;
  assert.equal(h.element('stickerCustomTitle').value,'新的草稿');assert.equal(h.element('stickerEditor').classList.contains('hidden'),false);
  assert.equal(h.element('stickerCustomPreview').src,url);assert.ok(!h.revoked.includes(url));
  assert.equal(Buffer.from(JSON.parse(h.requests('/api/stickers/custom/import')[0].options.body).image_base64,'base64').toString(),'old-file');
});

test('edit invalidates a share preview; toggle sends only enabled and delete requires confirmation',async()=>{
  const h=harness();await ready(h);assert.equal(h.element('stickerExportShare').disabled,false);
  const id=h.personal()[0].id;await h.card('edit',id).fire('click');h.element('stickerCustomDescription').value='修改画面说明';await h.click('stickerSaveCustom');
  assert.equal(h.element('stickerExportShare').disabled,true);assert.equal(h.element('stickerShareRights').checked,false);
  await h.card('toggle',id).fire('click');let body=JSON.parse(h.requests('/api/stickers/custom/update').at(-1).options.body);
  assert.deepEqual(body,{id,changes:{enabled:false}});
  h.setConfirm(false);await h.card('delete',id).fire('click');assert.equal(h.requests('/api/stickers/custom/delete').length,0);
  h.setConfirm(true);await h.card('delete',id).fire('click');assert.equal(h.requests('/api/stickers/custom/delete').length,1);
  assert.equal(h.personal().length,1);assert.equal(h.element('stickerSharePanel').classList.contains('hidden'),true);
});

test('saving an open metadata editor does not undo a later explicit disable',async()=>{
  const h=harness();await h.open();const id=h.personal()[0].id;await h.card('edit',id).fire('click');
  await h.card('toggle',id).fire('click');assert.equal(h.personal().find(row=>row.id===id).enabled,false);
  await h.input('stickerCustomDescription','编辑说明不改变素材开关');await h.click('stickerSaveCustom');
  assert.equal(h.personal().find(row=>row.id===id).enabled,false,'metadata save must preserve the current explicit opt-out');
  const body=JSON.parse(h.requests('/api/stickers/custom/update').at(-1).options.body);
  assert.ok(!Object.hasOwn(body.changes,'enabled'),'metadata-only update must not replay an old toggle value');
});

test('connection clears typed token immediately and disconnection keeps final button state disabled',async()=>{
  const h=harness();await ready(h);h.element('stickerGithubToken').value='test_token_only_for_fake_transport';
  const gate=h.defer('/api/stickers/contributions/connect'),pending=h.click('stickerGithubConnect');await gate.entered;
  assert.equal(h.element('stickerGithubToken').value,'');assert.equal(h.element('stickerGithubConnect').disabled,true);gate.release();await pending;
  assert.equal(h.element('stickerSubmitShare').disabled,false);assert.equal(h.element('stickerGithubDisconnect').disabled,false);
  await h.click('stickerGithubDisconnect');assert.equal(h.element('stickerSubmitShare').disabled,true);
  assert.equal(h.element('stickerGithubDisconnect').disabled,true,'withBusy must not re-enable a disconnected account button');
  assert.deepEqual(Object.keys(JSON.parse(h.requests('/api/stickers/contributions/connect')[0].options.body)).sort(),['remember','token']);
});

test('export requires preview and rights; downloaded package uses captured public payload without credentials',async()=>{
  const h=harness();await h.open();assert.equal(h.element('stickerExportShare').disabled,true);await h.card('share').fire('click');
  await h.click('stickerPrepareShare');assert.equal(h.element('stickerExportShare').disabled,true);await h.check('stickerShareRights');
  await h.click('stickerExportShare');assert.equal(h.fetches.length,1);const body=JSON.parse(h.fetches[0].options.body);
  assert.deepEqual(Object.keys(body).sort(),['attribution','fingerprint','item_id','license','rights_confirmed']);
  assert.equal(body.license,'CC-BY-NC-4.0');assert.equal(body.rights_confirmed,true);assert.equal(h.downloads.length,1);assert.match(h.downloads[0].download,/^AiwuJiyu-sticker-[a-f0-9]{12}\.zip$/);
  h.runTimeouts();assert.ok(h.revoked.includes(h.downloads[0].href));
});

test('an in-flight preview cannot restore old author or mix another selected image into its response',async()=>{
  for(const change of ['author','selection']){
    const h=harness();await h.open();await h.card('share').fire('click');
    const gate=h.defer('/api/stickers/contributions/preview'),pending=h.click('stickerPrepareShare');await gate.entered;
    if(change==='author')await h.input('stickerShareAttribution','新的公开作者');else await h.card('share',h.personal()[1].id).fire('click');
    gate.release();await pending;await h.check('stickerShareRights');
    assert.equal(h.element('stickerExportShare').disabled,true,'stale response must not be considered a current preview');
    assert.equal(h.element('stickerSubmitShare').disabled,true);assert.equal(h.element('stickerSharePreview').innerHTML,'');
    await h.click('stickerPrepareShare');assert.equal(h.element('stickerShareRights').checked,false);
  }
});

test('editing selected metadata while preview is in flight invalidates that response',async()=>{
  const h=harness();await h.open();await h.card('share').fire('click');
  const gate=h.defer('/api/stickers/contributions/preview'),pending=h.click('stickerPrepareShare');await gate.entered;
  await h.card('edit').fire('click');h.element('stickerCustomUse').value='新的适用场景';await h.click('stickerSaveCustom');
  gate.release();await pending;await h.check('stickerShareRights');
  assert.equal(h.element('stickerExportShare').disabled,true);assert.equal(h.element('stickerSharePreview').innerHTML,'');
});

test('a failed refreshed preview cannot leave the earlier public consent usable',async()=>{
  const h=harness();await ready(h);const gate=h.defer('/api/stickers/contributions/preview'),pending=h.click('stickerPrepareShare');await gate.entered;
  gate.reject(new Error('公开署名无效'));await pending;
  assert.equal(h.element('stickerSharePreview').innerHTML,'');assert.equal(h.element('stickerShareRights').checked,false);
  assert.equal(h.element('stickerExportShare').disabled,true);assert.ok(h.toasts.includes('公开署名无效'));
});

test('export remains disabled and releases object URLs if consent/preview changes while packaging',async()=>{
  const h=harness();await ready(h);const gate=h.defer('/api/stickers/contributions/export','fetch');const pending=h.click('stickerExportShare');await gate.entered;
  await h.input('stickerShareAttribution','变更后的作者');gate.release();await pending;
  assert.equal(h.element('stickerExportShare').disabled,true,'withBusy final state follows current consent');
  h.runTimeouts();assert.ok(h.urls.every(item=>h.revoked.includes(item.url)),'no unrevoked export blob after a discarded response');
});

test('async contribution polls once per interval, blocks duplicates and accepts only a verified PR URL',async()=>{
  const h=harness();await ready(h,{auth:true});await h.click('stickerSubmitShare');
  assert.equal(h.requests('/api/stickers/contributions/submit').length,1);assert.equal(h.intervals.size,1);assert.equal(h.element('stickerSubmitShare').disabled,true);
  await h.click('stickerSubmitShare');assert.equal(h.requests('/api/stickers/contributions/submit').length,1);
  h.setJob({running:true,message:'处理中',pr_url:'https://evil.example/pull/9'});await h.tick();assert.equal(h.element('stickerShareResult').classList.contains('hidden'),true);
  h.setJob({running:false,message:'已提交',pr_url:'https://github.com/myflww/aiwu-jiyu/pull/9'});await h.tick();
  assert.equal(h.intervals.size,0);assert.equal(h.element('stickerShareResult').href,'https://github.com/myflww/aiwu-jiyu/pull/9');
});

test('an old initial idle status cannot cancel the poller of a newly submitted job',async()=>{
  const h=harness();h.setAuth({configured:true,login:'test-contributor'});const oldStatus=h.defer('/api/stickers/contributions/status');
  await h.open();await oldStatus.entered;await h.card('share').fire('click');await h.click('stickerPrepareShare');await h.check('stickerShareRights');
  await h.click('stickerSubmitShare');assert.equal(h.intervals.size,1);assert.equal(h.element('stickerSubmitShare').disabled,true);
  oldStatus.release();await settled();assert.equal(h.intervals.size,1,'stale idle reply must not clear the current job timer');
  assert.equal(h.element('stickerSubmitShare').disabled,true);
});

test('closing clears local file URLs, polling and token; late auth/preview responses do not change a reopened dialog',async()=>{
  const h=harness();const oldAuth=h.defer('/api/stickers/contributions/auth');await h.open();await oldAuth.entered;
  await h.click('stickerAddCustom');await h.file({type:'image/png',size:32,name:'secret.png'});const oldToken=h.element('stickerGithubToken');oldToken.value='not_saved_token';
  await h.card('share').fire('click');const oldPreview=h.defer('/api/stickers/contributions/preview'),pending=h.click('stickerPrepareShare');await oldPreview.entered;
  h.context.closeModal();assert.equal(oldToken.value,'');assert.equal(h.intervals.size,0);assert.equal(h.revoked.length,1);
  await h.open();await h.card('share').fire('click');
  oldAuth.release({configured:true,login:'old-session'});oldPreview.release();await pending;await settled();
  assert.doesNotMatch(h.element('stickerGithubStatus').textContent,/old-session/);assert.equal(h.element('stickerSharePreview').innerHTML,'');
  assert.equal(h.element('stickerSubmitShare').disabled,true);assert.equal(h.element('stickerExportShare').disabled,true);
});

test('closing during submit/status leaves no new poller and no updates in another modal',async()=>{
  const h=harness();await ready(h,{auth:true});const gate=h.defer('/api/stickers/contributions/submit'),pending=h.click('stickerSubmitShare');await gate.entered;
  h.context.closeModal();h.context.openModal('另一个窗口','<p id="other-dialog">别的设置</p>',null);gate.release();await pending;
  assert.equal(h.intervals.size,0);assert.equal(h.element('modalTitle').textContent,'另一个窗口');assert.equal(h.toasts.some(x=>/null|undefined/.test(x)),false);
  const h2=harness();await ready(h2,{auth:true});await h2.click('stickerSubmitShare');const statusGate=h2.defer('/api/stickers/contributions/status'),tick=h2.tick();await statusGate.entered;
  const token=h2.element('stickerGithubToken');token.value='unsaved';h2.context.closeModal();statusGate.release();await tick;
  assert.equal(token.value,'');assert.equal(h2.intervals.size,0);assert.equal(h2.element('modalBack').classList.contains('show'),false);
});

test('loading responses and public metadata cannot replace another modal or inject markup',async()=>{
  const h=harness();const gate=h.defer('/api/stickers'),pending=h.context.openStickerLibrary();await gate.entered;
  h.context.openModal('其他设置','<p>保留当前窗口</p>',null);gate.release();await pending;assert.equal(h.element('modalTitle').textContent,'其他设置');
  const data=fixture();data.stickers.find(row=>row.pack_id==='custom').title='<img src=x onerror=alert(1)>';
  const safe=harness(data);await safe.open();assert.match(safe.element('stickerPersonalList').innerHTML,/&lt;img/);
  await safe.card('share').fire('click');await safe.input('stickerShareAttribution','<script>author</script>');await safe.click('stickerPrepareShare');
  assert.match(safe.element('stickerSharePreview').innerHTML,/&lt;script&gt;author/);assert.doesNotMatch(safe.element('stickerSharePreview').innerHTML,/<script>/);
});
