// Companion settings with fake controls/API; never writes user preferences.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/ui/app.js'), 'utf8');

const legacy = {schedule_mode:'fixed',interval_minutes:30,min_interval_minutes:60,max_interval_minutes:180,
  idle_minutes:0,daily_limit:3,quiet_start:23,quiet_end:8,timezone:'local',trigger_probability:100,
  unanswered_decay_percent:0,max_unanswered:0,session_expiry_days:7,natural_bubbles_frequency:'occasional',
  proactive_enabled:true,whale_style_enabled:false,natural_bubbles_enabled:true,emotion_enabled:false,activity_enabled:false};
const ids = {schedule_mode:'companion-mode',interval_minutes:'companion-interval',min_interval_minutes:'companion-min-interval',
  max_interval_minutes:'companion-max-interval',idle_minutes:'companion-idle',daily_limit:'companion-limit',
  quiet_start:'companion-quiet-start',quiet_end:'companion-quiet-end',timezone:'companion-timezone',
  trigger_probability:'companion-probability',unanswered_decay_percent:'companion-decay',max_unanswered:'companion-unanswered',
  session_expiry_days:'companion-expiry',natural_bubbles_frequency:'companion-bubbles-frequency'};

function harness() {
  const elements = new Map(), calls = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {value:'',checked:false,textContent:'',innerHTML:'',disabled:false,
        classList:{contains:v=>classes.has(v),toggle(v,on){on?classes.add(v):classes.delete(v);}},
        focus(){this.focused=true;},addEventListener(){}});
    }
    return elements.get(id);
  }
  const context = vm.createContext({document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
    localStorage:{getItem(){return null;}},console,setTimeout,clearTimeout,setInterval,clearInterval,URLSearchParams});
  vm.runInContext(source, context);
  context.recordApi=async(url,opts)=>{calls.push({url,opts});return context.fixture;};
  context.recordModal=(title,html,onOk)=>{context.modal={title,html,onOk};};
  vm.runInContext('api=recordApi;openModal=recordModal;toast=()=>{};loadPlugins=async()=>{};',context);
  return {context,element,calls,
    seed(data){Object.entries(ids).forEach(([key,id])=>element(id).value=String(data[key]));
      ['proactive_enabled','whale_style_enabled','natural_bubbles_enabled','emotion_enabled','activity_enabled'].forEach(key=>element('companion-'+key).checked=data[key]===true);},
    read(data=legacy){return JSON.parse(JSON.stringify(context.readCompanionPreferences(data)));}};
}

test('saved fixed choices and zero-valued advanced settings survive a complete typed payload',()=>{
  const h=harness();h.seed({...legacy,daily_limit:0});const body=h.read();
  assert.equal(body.schedule_mode,'fixed');assert.equal(body.interval_minutes,30);assert.equal(body.daily_limit,0);
  assert.equal(body.idle_minutes,0);assert.equal(body.max_unanswered,0);assert.equal(body.unanswered_decay_percent,0);
  Object.entries(body).filter(([key])=>!key.endsWith('_enabled')&&!['schedule_mode','timezone','natural_bubbles_frequency'].includes(key)).forEach(([,value])=>assert.ok(Number.isInteger(value)));
  assert.equal(Object.values(body).filter(v=>typeof v==='boolean').length,5);
});

test('mode changes hide and disable irrelevant inputs; reference fills only its declared values',()=>{
  const h=harness();h.seed({...legacy,trigger_probability:37,unanswered_decay_percent:20,max_unanswered:2});
  h.context.updateCompanionScheduleFields();assert.equal(h.element('companion-interval').disabled,false);
  assert.equal(h.element('companion-min-interval').disabled,true);
  const preset={schedule_mode:'random',min_interval_minutes:60,max_interval_minutes:180,idle_minutes:40,daily_limit:4,quiet_start:23,quiet_end:8,timezone:'local'};
  h.context.fillCompanionReference({presets:{dsh_reference:preset}});
  assert.equal(h.element('companion-mode').value,'random');assert.equal(h.element('companion-interval').disabled,true);
  assert.equal(h.element('companion-fixed-fields').classList.contains('hidden'),true);
  assert.equal(h.element('companion-min-interval').disabled,false);
  assert.equal(h.element('companion-probability').value,'37');assert.equal(h.element('companion-decay').value,'20');
  assert.equal(h.element('companion-unanswered').value,'2');assert.equal(h.element('companion-proactive_enabled').checked,true);
  assert.equal(h.calls.length,0);assert.match(h.element('companionPresetNote').textContent,/尚未保存/);
});

test('invalid active ranges, nonintegers and unsupported enums are rejected before a save',()=>{
  const h=harness();
  for(const [id,value] of [['companion-probability','1.5'],['companion-expiry','-1'],['companion-limit','21'],['companion-mode','cron'],['companion-timezone','invalid']]){
    h.seed(legacy);h.element(id).value=value;assert.throws(()=>h.read());
  }
  h.seed({...legacy,schedule_mode:'random',min_interval_minutes:100,max_interval_minutes:50});
  assert.throws(()=>h.read(),/最长间隔不能小于/);assert.equal(h.calls.length,0);
});

test('runtime explains opportunity estimates, zero budgets and adapter-level receipts accurately',()=>{
  const h=harness();h.context.renderCompanionRuntime({...legacy,daily_limit:0,runtime:{message:'等待主人回应',daily_count:0,daily_limit:0,
    schedule_mode:'random',timezone:'UTC',effective_probability:12.5,unanswered_count:3,paused_until_reply:true,
    initiated_count:2,last_receipt:'adapter_accepted',last_trigger_source:'background_idle',last_result:'probability_skipped'}});
  assert.equal(h.element('companionDailyCount').textContent,'0 / 0');
  assert.equal(h.element('companionEffectiveProbability').textContent,'12.5%');
  assert.match(h.element('companionUnanswered').textContent,/等待主人回应/);
  assert.match(h.element('companionLastReceipt').textContent,/不代表主人已读/);
  assert.equal(h.element('companionTriggerSource').textContent,'本机后台自主机会');
  assert.equal(h.element('companionNextTrigger').textContent,'—');
});

test('modal keeps all five switches, collapsed advanced settings, and a scrollable settings body',async()=>{
  const h=harness();h.context.fixture=legacy;await h.context.openCompanionModal();
  const html=h.context.modal.html;
  assert.match(html,/companion-settings-sheet/);
  assert.match(html,/<details class="companion-advanced">/);
  assert.match(html,/下一次机会预估/);
  assert.match(html,/id="companion-interval" min="5"/);
  assert.match(html,/id="companion-limit" min="0" max="20"/);
  assert.equal((html.match(/type="checkbox"/g)||[]).length,5);
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/companion');
});
