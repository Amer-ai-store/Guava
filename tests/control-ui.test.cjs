const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.hidden=false;this.disabled=false;this.checked=true;this.value='';this.textContent='';}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=[...nodes];}
 setAttribute(k,v){this[k]=v;}
 set innerHTML(_){throw Error('Unsafe HTML assignment');}
}
function setup(dedicated=false,file=false){
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const calls=[];
 let admin=false,fail=false;
 const state={agent_configured:true,online:false,last_seen:null,jobs:[],backups:{state:'not_configured',files:[]},audit:[]};
 const fetch=async(path,options={})=>{
  calls.push({path,options});
  if(path==='/api/snapshot')return {status:200,ok:true,json:async()=>({state:'disconnected',data:null,error:'sql_not_configured',failures:1,events:[]})};
  if(path==='/api/admin/login'){admin=true;return {status:200,ok:true,json:async()=>({ok:true})};}
  if(path==='/api/control/state'){
   if(fail)throw Error('test link outage');
   return {status:admin?200:403,ok:admin,json:async()=>state};
  }
  if(path==='/api/control/jobs')return {status:202,ok:true,json:async()=>({id:'test-job'})};
  throw Error('Unexpected route '+path);
 };
 const context=vm.createContext({document:{body:{dataset:{startSection:dedicated?'control':''}},getElementById:get,createElement:tag=>new Element(tag),
  querySelectorAll:selector=>selector.startsWith('#control-actions')?get('control-actions').children:get('nav').children},
  fetch,AbortSignal,location:{protocol:file?'file:':'http:'},setTimeout:()=>1,clearTimeout:()=>{},console});
 vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
 return {get,calls,context,state,setFail:()=>{fail=true;}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('control panel works with unavailable SQL; admin action uses fixed JSON and CSRF header',async()=>{
 const app=setup();await settle();
 app.get('nav').children.find(b=>b.dataset.section==='control').onclick();
 await settle();
 assert.equal(app.get('control-panel').hidden,false);
 assert.equal(app.get('admin-login').hidden,false);
 assert.ok(app.get('control-actions').children.every(b=>b.disabled));
 app.get('admin-token').value='test-only-admin-secret';
 await app.get('admin-login').onsubmit({preventDefault(){}});
 assert.equal(app.get('admin-token').value,'');
 assert.ok(app.get('control-actions').children.every(b=>!b.disabled));
 app.get('control-actions').children.find(b=>b.dataset.action==='sql_probe').onclick();
 await settle();
 const request=app.calls.find(c=>c.path==='/api/control/jobs');
 assert.deepEqual(JSON.parse(request.options.body),{action:'sql_probe'});
 assert.equal(request.options.headers['X-TrustSoft-Action'],'1');
 assert.match(app.get('control-message').textContent,/لا يعني نجاح الاتصال/);
});
test('control transport outage disables operations instead of displaying success',async()=>{
 const app=setup();await settle();
 app.get('nav').children.find(b=>b.dataset.section==='control').onclick();await settle();
 app.get('admin-token').value='test-only-admin-secret';
 await app.get('admin-login').onsubmit({preventDefault(){}});
 app.setFail();
 await vm.runInContext('refreshControl()',app.context);
 assert.ok(app.get('control-actions').children.every(b=>b.disabled));
 assert.match(app.get('control-message').textContent,/النتائج السابقة قديمة/);
});
test('dedicated control page uses admin login directly and exposes only operations navigation',async()=>{
 const app=setup(true);await settle();
 assert.equal(app.get('nav').children.length,1);
 assert.equal(app.get('nav').children[0].dataset.section,'control');
 app.get('token').value='test-only-admin-secret';
 await app.get('login').onsubmit({preventDefault(){}});
 assert.ok(app.calls.some(call=>call.path==='/api/admin/login'));
 assert.ok(!app.calls.some(call=>call.path==='/api/login'));
 assert.equal(app.get('control-panel').hidden,false);
 assert.match(app.get('status').textContent,/بانتظار أول اتصال/);
 assert.match(app.get('alert').textContent,/إن كان مثبتًا/);
 app.state.last_seen=1690000000;
 await vm.runInContext('refreshControl()',app.context);
 assert.equal(app.get('status').textContent,'قناة الإدارة غير متصلة');
});

test('downloaded HTML gives web hosting guidance and sends no API or credential requests',async()=>{
 const app=setup(true,true);await settle();
 assert.equal(app.get('browser-message').hidden,false);
 assert.match(app.get('browser-message').textContent,/HTTPS/);
 assert.equal(app.get('login-submit').disabled,true);
 assert.equal(app.calls.length,0);
});
