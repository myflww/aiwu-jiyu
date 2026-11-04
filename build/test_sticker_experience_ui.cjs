// New sticker experience handlers in isolation: no browser, real files or network.
const runTest=require('node:test');const test=(name,fn)=>runTest(name,{timeout:4000},fn);
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/ui/sticker-experience.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const image=(id='happy',extra={})=>({id,title:id==='happy'?'开心小鱼':id,description:'一张可爱的图',use_when:'分享好消息',attribution:'测试作者',favorite:false,url:'/assets/plugins/stickers/happy.png',...extra});

function harness({autoMount=true}={}){
 const nodes=new Map(),calls=[],toasts=[],gates=[],reads=[],heldReaders=[],catalogCalls=[];let generation=1,items=[image()],candidates=[image()],catalogError=null;
 let material={version:'bundled',available:false,message:'随附素材'},checkResult={version:'bundled',latest_version:'v-next',available:true,fingerprint:'a'.repeat(64),changes:{added:2,changed:1,removed:1}};
 let applyResult={version:'v-next',available:false,message:'已应用'},catalogChange=async()=>{};
 const match=(node,selector)=>selector.split(',').some(selector=>{selector=selector.trim();const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return Object.hasOwn(node.attrs,attr[1])&&(attr[2]===undefined||node.attrs[attr[1]]===attr[2]);return node.tagName===selector.toUpperCase();});
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.listeners={};this.style={};this.checked=false;this.disabled=false;this.files=[];this._value='';this._id='';this._html='';this._text='';}
  set id(value){if(this._id&&nodes.get(this._id)===this)nodes.delete(this._id);this._id=String(value);if(value)nodes.set(this._id,this);}get id(){return this._id;}
  set value(value){this._value=String(value);if(this.type==='file'&&value==='')this.files=[];}get value(){return this._value;}
  setAttribute(name,value){value=String(value);this.attrs[name]=value;if(name==='id')this.id=value;if(name==='type')this.type=value;if(name==='value')this.value=value;if(name==='checked')this.checked=true;if(name==='disabled')this.disabled=true;if(name==='src')this.src=value;if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=value;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  unregister(){this.children.forEach(child=>child.unregister());if(this.id&&nodes.get(this.id)===this)nodes.delete(this.id);}
  remove(){this.unregister();if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
  set innerHTML(value){this.children.forEach(child=>child.unregister());this.children=[];this._text='';this._html=String(value);parse(this._html,this);}get innerHTML(){return this._html;}
  set textContent(value){this.children.forEach(child=>child.unregister());this.children=[];this._text=String(value);this._html=escape(value);}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  querySelectorAll(selector){const found=[];const walk=node=>{for(const child of node.children){if(match(child,selector))found.push(child);walk(child);}};walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(key,fn){(this.listeners[key]??=[]).push(fn);}
  get isConnected(){for(let node=this;node;node=node.parentNode)if(node===document.body)return true;return false;}
  async fire(type,extra={}){if(type==='click'&&this.disabled)return;const event={type,target:this,currentTarget:this,preventDefault(){},...extra};for(const fn of [...(this.listeners[type]||[]),...(type==='click'&&this.onclick?[this.onclick]:[])])await fn(event);await settle();}
 }
 function parse(html,parent){const stack=[parent],voids=new Set(['input','img','br','hr']);for(const token of html.matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g)){const text=token[0],node=stack.at(-1);if(text.startsWith('</')){if(stack.length>1)stack.pop();continue;}if(!text.startsWith('<')){if(node!==parent){node._text+=decode(text);if(node.tagName==='TEXTAREA')node.value+=decode(text);}continue;}const open=text.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!open)continue;const child=new Element(open[1]);for(const attr of open[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))child.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));node.appendChild(child);if(child.tagName==='OPTION'&&node.tagName==='SELECT'&&(!node.value||Object.hasOwn(child.attrs,'selected')))node.value=child.value;if(!voids.has(open[1])&&!text.endsWith('/>'))stack.push(child);}}
 const document={body:new Element('body'),getElementById:id=>nodes.get(id)||null};
 class FileReader{
  readAsDataURL(file){reads.push(file.name);const complete=()=>{if(file.readError){this.onerror?.();return;}this.result='data:'+(file.type||'application/octet-stream')+';base64,'+Buffer.from(file.content||'fake-image').toString('base64');this.onload?.();};if(file.hold)heldReaders.push(complete);else queueMicrotask(complete);}
 }
 function route(url,body){
  if(url.startsWith('/api/stickers/library?')){const params=new URL('http://local'+url).searchParams;const list=items.filter(item=>(!params.get('query')||String(item.title).includes(params.get('query')))&&(params.get('favorites_only')!=='true'||item.favorite));return {stickers:clone(list),count:list.length,total_count:items.length};}
  if(url==='/api/stickers/library/favorite'){const row=items.find(item=>item.id===body.id);if(row)row.favorite=body.favorite;return {ok:true};}
  if(url==='/api/stickers/library/preview')return {candidates:clone(candidates),message:'本地候选，不保证实际配图'};
  if(url==='/api/stickers/library/feedback')return {ok:true};
  if(url==='/api/stickers/custom/batch')return {imported:body.files.length,reused:0,invalid:0,results:body.files.map(file=>({name:file.name,status:'imported'}))};
  if(url==='/api/stickers/custom/zip')return {imported:1,reused:1,invalid:1,results:[{name:'one.png',status:'imported'},{name:'two.gif',status:'reused'},{name:'not-image',status:'invalid',message:'格式不支持'}]};
  if(url==='/api/stickers/materials')return clone(material);
  if(url==='/api/stickers/materials/check')return clone(checkResult);
  if(url==='/api/stickers/materials/apply')return clone(applyResult);
  throw new Error('Unexpected fixture API '+url);
 }
 async function api(url,options={}){const body=JSON.parse(options.body||'{}');calls.push({url,body:clone(body),method:options.method||'GET'});const value=route(url,body),at=gates.findIndex(gate=>gate.url===url);if(at>=0){const gate=gates.splice(at,1)[0];gate.enter();const replacement=await gate.promise;return replacement===undefined?value:replacement;}return value;}
 const shell={api,esc:escape,toast:value=>toasts.push(String(value)),getModalGeneration:()=>generation,onCatalogChange:async()=>{catalogCalls.push(true);if(catalogError)throw catalogError;return catalogChange();}};
 const window={};const context=vm.createContext({window,document,FileReader,URL,encodeURIComponent,console});vm.runInContext(source,context);
 function mount(){const host=new Element('div');host.id='stickerExperienceMount';document.body.appendChild(host);window.AiwuStickerExperience.mount(shell);return host;}
 if(autoMount)mount();
 const get=id=>{const value=nodes.get(id);assert.ok(value,'missing control '+id);return value;};
 return {calls,toasts,reads,catalogCalls,get,context,shell,mount,
  async ready(){await settle();},async click(id){await get(id).fire('click');},
  async input(id,value,event='input'){get(id).value=value;await get(id).fire(event);},
  async files(id,files){get(id).files=files;get(id)._value=files.length?'selected':'';await get(id).fire('change');},
  button(selector,id){const value=get(id).querySelectorAll(selector)[0];assert.ok(value,'missing '+selector);return value;},
  setItems:value=>{items=clone(value);},setCandidates:value=>{candidates=clone(value);},setMaterial:value=>{material=clone(value);},setCheck:value=>{checkResult=clone(value);},setApply:value=>{applyResult=clone(value);},setCatalogChange:fn=>{catalogChange=fn;},setCatalogError:value=>{catalogError=value;},
  close(){generation++;get('stickerExperienceMount').remove();},async reopen(){generation++;mount();await settle();},
  requests:url=>calls.filter(call=>call.url===url),prefix:url=>calls.filter(call=>call.url.startsWith(url)),
  defer(url){let release,reject,enter;const entered=new Promise(resolve=>{enter=resolve;}),promise=new Promise((yes,no)=>{release=yes;reject=no;});gates.push({url,promise,enter});return {entered,release,reject};},
  releaseFiles(){for(const release of heldReaders.splice(0))release();},
 };
}
const file=(name,size=32,extra={})=>({name,size,type:'image/png',content:'test-raster',...extra});
async function preview(h,scene='general'){await h.input('stickerTryText','分享好消息');await h.input('stickerTryScene',scene);await h.click('stickerTryRun');}

test('a newer search and favorite filter discard older browse snapshots',async()=>{
 const h=harness();await h.ready();h.setItems([image('a',{title:'旧词'}),image('b',{title:'新词',favorite:true})]);
 await h.input('stickerSearchQuery','旧词');const oldURL='/api/stickers/library?query='+encodeURIComponent('旧词')+'&favorites_only=false&sort=default';const gate=h.defer(oldURL);const pending=h.click('stickerSearchRun');await gate.entered;
 await h.input('stickerSearchQuery','新词');h.get('stickerFavoritesOnly').checked=true;await h.get('stickerFavoritesOnly').fire('change');gate.release();await pending;assert.match(h.get('stickerSearchResults').innerHTML,/新词/);assert.doesNotMatch(h.get('stickerSearchResults').innerHTML,/旧词/);
});

test('favorites invalidate an earlier GET before saving and refreshing authoritative catalog state',async()=>{
 const h=harness();await h.ready();const url='/api/stickers/library?query=&favorites_only=false&sort=default',gate=h.defer(url);const pending=h.click('stickerSearchRun');await gate.entered;
 await h.button('[data-library-favorite]','stickerSearchResults').fire('click');gate.release();await pending;assert.deepEqual(h.requests('/api/stickers/library/favorite')[0].body,{id:'happy',favorite:true});assert.match(h.get('stickerSearchResults').innerHTML,/取消收藏/);
});

test('labels and metadata are escaped and remote/javascript image locations are not emitted as image sources',async()=>{
 const h=harness({autoMount:false}),evil='<img src=x onerror=bad()>';h.setItems([image('unsafe',{title:evil,description:evil,attribution:evil,url:'https://outside.example/private'}),image('js',{url:'javascript:bad()'}),image('safe',{url:'/api/stickers/custom/custom-aaa/image'})]);h.mount();await h.ready();const html=h.get('stickerSearchResults').innerHTML;assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src=x|src="https:|src="javascript:/);assert.match(html,/src="\/api\/stickers\/custom\/custom-aaa\/image"/);
});

test('changing preview text invalidates pending candidates and clears the old results',async()=>{
 const h=harness();await h.ready();await h.input('stickerTryText','原场景');const gate=h.defer('/api/stickers/library/preview');const pending=h.click('stickerTryRun');await gate.entered;await h.input('stickerTryText','新场景');gate.release();await pending;assert.equal(h.get('stickerTryResults').innerHTML,'');assert.match(h.get('stickerTryNotice').textContent,/场景已改变/);
});

test('changing the select scene discards old candidates even when the change event is the observed event',async()=>{
 const h=harness();await h.ready();await h.input('stickerTryText','原场景');const gate=h.defer('/api/stickers/library/preview');const pending=h.click('stickerTryRun');await gate.entered;await h.input('stickerTryScene','support','change');gate.release();await pending;assert.equal(h.get('stickerTryResults').innerHTML,'');assert.match(h.get('stickerTryNotice').textContent,/场景已改变/);
});

test('reject and clear feedback carry exactly the scene that produced the candidates',async()=>{
 const h=harness();await h.ready();await preview(h,'greeting');await h.button('[data-try-reject]','stickerTryResults').fire('click');await h.button('[data-try-clear]','stickerTryResults').fire('click');assert.deepEqual(h.requests('/api/stickers/library/feedback').map(call=>call.body),[{id:'happy',scene:'greeting',reason:'not_suitable'},{id:'happy',scene:'greeting',reason:'clear'}]);
});

test('an old feedback response cannot replace the status of a newly changed preview scene',async()=>{
 const h=harness();await h.ready();await preview(h,'greeting');const gate=h.defer('/api/stickers/library/feedback');const pending=h.button('[data-try-reject]','stickerTryResults').fire('click');await gate.entered;await h.input('stickerTryText','新的认真求助');gate.release();await pending;assert.match(h.get('stickerTryNotice').textContent,/场景已改变/);
});

test('count, per-file, total and ZIP limits reject before file reading or HTTP import calls',async()=>{
 const h=harness();await h.ready();for(const invalid of [Array.from({length:21},(_,i)=>file(i+'.png')), [file('large.png',8*1024*1024+1)],Array.from({length:4},(_,i)=>file(i+'.png',7*1024*1024))]){await h.files('stickerBatchFiles',invalid);await h.click('stickerBatchImport');}
 await h.files('stickerBatchZip',[file('large.zip',20*1024*1024+1,{type:'application/zip'})]);await h.click('stickerBatchImport');assert.equal(h.reads.length,0);assert.equal(h.prefix('/api/stickers/custom/').length,0);assert.equal(h.toasts.length,4);assert.equal(h.get('stickerBatchImport').disabled,false);
});

test('file reading failures release both inputs and the import action, allowing a successful retry',async()=>{
 const h=harness();await h.ready();await h.files('stickerBatchFiles',[file('broken.png',32,{readError:true})]);await h.click('stickerBatchImport');assert.equal(h.requests('/api/stickers/custom/batch').length,0);assert.equal(h.get('stickerBatchFiles').disabled,false);assert.equal(h.get('stickerBatchZip').disabled,false);assert.equal(h.get('stickerBatchImport').disabled,false);
 await h.files('stickerBatchFiles',[file('valid.png')]);await h.click('stickerBatchImport');assert.equal(h.requests('/api/stickers/custom/batch').length,1);assert.equal(h.catalogCalls.length,1);assert.match(h.get('stickerBatchReport').innerHTML,/导入 1/);
});

test('batch defaults are typed and image import is local only, while ZIP reports escaped per-file outcomes',async()=>{
 const h=harness();await h.ready();await h.input('stickerBatchTags','开心，庆祝');await h.input('stickerBatchAuthor','作者');await h.input('stickerBatchUse','分享好消息');await h.input('stickerBatchAvoid','认真倾诉');await h.files('stickerBatchFiles',[file('one.png'),file('two.gif',64,{type:'image/gif'})]);await h.click('stickerBatchImport');const body=h.requests('/api/stickers/custom/batch')[0].body;assert.deepEqual(body.defaults,{tags:['开心','庆祝'],attribution:'作者',use_when:'分享好消息',avoid_when:'认真倾诉',emotion:'daily',enabled:true});assert.equal(body.files.length,2);assert.equal(typeof body.files[0].image_base64,'string');assert.equal(h.prefix('/api/stickers/contributions/').length,0);
 await h.files('stickerBatchZip',[file('pack.zip',128,{type:'application/zip'})]);await h.click('stickerBatchImport');assert.equal(h.requests('/api/stickers/custom/zip').length,1);assert.match(h.get('stickerBatchReport').innerHTML,/复用 1.*未导入 1/);
});

test('closing during file reading prevents upload and old callbacks cannot change a reopened library',async()=>{
 const h=harness();await h.ready();await h.files('stickerBatchFiles',[file('pending.png',32,{hold:true})]);const pending=h.click('stickerBatchImport');await settle();h.close();await h.reopen();h.releaseFiles();await pending;assert.equal(h.requests('/api/stickers/custom/batch').length,0);assert.equal(h.get('stickerBatchFiles').disabled,false);assert.equal(h.get('stickerBatchReport').innerHTML,'');
});

test('a late initial material state cannot replace a newly checked update and apply sends only its preview fingerprint',async()=>{
 const h=harness({autoMount:false}),gate=h.defer('/api/stickers/materials');h.mount();await gate.entered;await h.click('stickerMaterialsCheck');gate.release();await settle();assert.equal(h.get('stickerMaterialsApply').disabled,false);assert.match(h.get('stickerMaterialsChanges').textContent,/新增 2/);await h.click('stickerMaterialsApply');assert.deepEqual(h.requests('/api/stickers/materials/apply')[0].body,{fingerprint:'a'.repeat(64)});assert.equal(h.catalogCalls.length,1);assert.equal(h.prefix('/api/stickers/custom/').length,0);assert.equal(h.prefix('/api/stickers/library/favorite').length,0);
});

test('no available update or missing fingerprint keeps apply disabled; successful application returns it to disabled',async()=>{
 const h=harness();await h.ready();assert.equal(h.get('stickerMaterialsApply').disabled,true);await h.click('stickerMaterialsApply');assert.equal(h.requests('/api/stickers/materials/apply').length,0);
 h.setCheck({available:false,fingerprint:'b'.repeat(64),message:'当前素材已是最新版'});await h.click('stickerMaterialsCheck');assert.equal(h.get('stickerMaterialsApply').disabled,true);
 h.setCheck({available:true,message:'没有完成预览'});await h.click('stickerMaterialsCheck');assert.equal(h.get('stickerMaterialsApply').disabled,true);
 h.setCheck({available:true,fingerprint:'a'.repeat(64),changes:{added:1}});await h.click('stickerMaterialsCheck');await h.click('stickerMaterialsApply');assert.equal(h.get('stickerMaterialsApply').disabled,true);
});

test('a late material apply response cannot repaint or refresh the catalog in a reopened modal',async()=>{
 const h=harness();await h.ready();await h.click('stickerMaterialsCheck');const gate=h.defer('/api/stickers/materials/apply');const pending=h.click('stickerMaterialsApply');await gate.entered;h.close();await h.reopen();gate.release();await pending;assert.equal(h.catalogCalls.length,0);assert.equal(h.get('stickerMaterialsApply').disabled,true);assert.doesNotMatch(h.get('stickerMaterialsNotice').textContent,/已应用/);
});
