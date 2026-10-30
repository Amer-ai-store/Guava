'use strict';
const labels={overview:'نظرة عامة',control:'المراقبة والسيطرة',sales:'المبيعات',purchases:'المشتريات',accounting:'المحاسبة',checks:'الشيكات',inventory:'المخزون',users:'المستخدمون',operations:'التقارير التشغيلية',financial:'التقارير المالية',investigation:'التحقيق المالي',schema:'المخطط والعلاقات'};
const errors={agent_sql_not_verified:'لم يثبت اتصال SQL بواسطة وكيل الخادم',sql_not_configured:'اتصال SQL غير مهيأ',sql_driver_missing:'برنامج تشغيل SQL غير مثبت',sql_connection_or_query_failed:'تعذر الاتصال أو تنفيذ الاستعلام',read_only_principal_required:'الحساب يملك صلاحيات تتجاوز القراءة؛ أُوقفت القراءة',tls_configuration_required:'يلزم اتصال مشفر بشهادة موثوقة',wrong_server:'الخادم لا يطابق JAWAFFASVR',dashboard_unreachable:'تعذر الاتصال بخدمة اللوحة',wrong_database:'قاعدة البيانات لا تطابق Jawafa_DB',invalid_mapping:'ربط الأعمدة غير صالح'};
const $=id=>document.getElementById(id);
// GitHub Pages hosts the central UI; its short-lived API session stays in memory.
const portalApi=document.body?.dataset.apiOrigin;
const portalClient=portalApi==='https://trustsoft-control.zxzx-mohammad91.workers.dev'&&globalThis.location?.origin==='https://amer-ai-store.github.io';
const invalidPortal=Boolean(portalApi)&&!portalClient;
let portalSession='',portalGeneration=0;
async function acceptPortalLogin(response){
 if(!portalClient)return;
 const data=await response.json();
 if(typeof data.session!=='string'||data.session.length>4096||data.session.length<32)throw Error('portal_session_missing');
 portalSession=data.session;
}

// Do not depend on AbortSignal.timeout: older Android/WebView versions omit it.
async function apiRequest(path,options={},timeout=15000){
 if(invalidPortal)throw Error('unapproved_portal_origin');
 const generation=portalGeneration;
 const controller=typeof AbortController==='function'?new AbortController():null;
 const deadline=controller?setTimeout(()=>controller.abort(),timeout):null;
 try{
  let target=path,request=options;
  if(portalClient){
   if(!/^\/api\/(login|admin\/login|snapshot|health|control\/(state|jobs))$/.test(path))throw Error('unapproved_portal_route');
   target=portalApi+path;
   request={...options,credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer',headers:{...options.headers,'X-TrustSoft-Client':'central',...(portalSession?{Authorization:'Bearer '+portalSession}:{})}};
  }
  const response=await fetch(target,controller?{...request,signal:controller.signal}:request);
  if(portalClient&&response.status===401)portalSession='';
  const payload=await response.json();
  if(portalClient&&generation!==portalGeneration)throw Error('portal_session_closed');
  return {status:response.status,ok:response.ok,json:async()=>payload};
 }finally{if(deadline!==null)clearTimeout(deadline);}
}
const localFile=globalThis.location?.protocol==='file:';
if(localFile){
 $('browser-message').hidden=false;
 $('browser-message').textContent='هذه ملفات المشروع وليست موقعًا يعمل. افتح رابط HTTPS المنشور للوحة في متصفح الهاتف. فتح ZIP أو HTML من التنزيلات لا يشغّل الخادم.';
 $('login-submit').disabled=true;
}

const dedicatedControl=document.body?.dataset.startSection==='control';
const adminEntry=dedicatedControl||(portalClient&&document.body?.dataset.portalAdmin==='true');
let selected=dedicatedControl||document.body?.dataset.defaultSection==='control'?'control':'overview',snapshot=null,busy=false,timer;
function el(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
for(const [key,label] of Object.entries(labels)){if(dedicatedControl&&key!=='control')continue;const b=el('button',label);b.onclick=()=>{selected=key;render();};b.dataset.section=key;$('nav').append(b);}
function table(rows){if(!rows?.length)return el('div','لا توجد نتائج مثبتة لهذا العرض.');const wrap=document.createElement('div'),t=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody'),keys=[...new Set(rows.flatMap(Object.keys))],tr=document.createElement('tr');keys.forEach(k=>tr.append(el('th',k)));head.append(tr);rows.forEach(row=>{const r=document.createElement('tr');keys.forEach(k=>r.append(el('td',typeof row[k]==='object'?JSON.stringify(row[k]):String(row[k]??'—'))));body.append(r);});t.append(head,body);wrap.className='table-scroll';wrap.append(t);return wrap;}
function render(){if(!snapshot)return;
 $('title').textContent=labels[selected];document.querySelectorAll('nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.section===selected)));
 const s=snapshot,d=s.data,ok=s.state==='connected'&&!s.stale;
 $('status').textContent=ok?'متصل • استعلام ناجح':'غير متصل / بيانات غير حديثة';$('status').dataset.ok=String(ok);
 $('alert').hidden=ok;$('alert').textContent=(errors[s.error]||s.error||'بانتظار التحقق')+' — لا توجد بيانات حية مؤكدة. أي بيانات سابقة أدناه قديمة.';
 $('updated').textContent='آخر قراءة ناجحة: '+(s.last_success?new Date(s.last_success*1000).toLocaleString('ar'):'لم تحدث');
 $('cards').replaceChildren();for(const [label,value] of [['حالة القراءة',ok?'ناجحة':'غير مثبتة'],['الجداول والعروض المرئية',d&&d.schema_state!=='not_discovered'?new Set(d.catalog.map(c=>c.schema_name+'.'+c.object_name)).size:'—'],['العلاقات المعلنة',d&&d.schema_state!=='not_discovered'?d.relations.length:'—'],['إخفاقات متتالية',s.failures]]){const c=el('div','');c.className='card';c.append(el('span',label),el('strong',String(value)));$('cards').append(c);}
 $('control-panel').hidden=selected!=='control';$('data-panel').hidden=selected==='control';if(selected==='control'){$('cards').hidden=!controlState;$('alert').hidden=true;$('status').textContent='قناة الإدارة • جارٍ التحقق';refreshControl();return renderEvents();}$('cards').hidden=false;
 $('panel-title').textContent=labels[selected];$('scope').textContent='';$('content').replaceChildren();
 if(!d){$('content').append(el('div','لم يتم جلب بيانات من TrustSoft. لا تُعرض بيانات تجريبية.'));return renderEvents();}
 if(selected==='overview'){$('content').append(table(Object.entries(d.sections).map(([k,v])=>({القسم:labels[k]||k,الحالة:v.state,صفوف_العينة:v.rows.length}))));}
 else if(selected==='schema'){$('scope').textContent=(d.schema_state==='sample'?'عينة محدودة من المخطط؛ ':d.schema_state==='not_discovered'?'لم يُكتشف المخطط بعد؛ ':'')+'المخطط المرئي للحساب فقط؛ غياب علاقة معلنة لا يثبت غياب علاقة أعمال.';$('content').append(table(d.catalog),el('h3','العلاقات المعلنة'),table(d.relations));}
 else if(selected==='investigation'){$('scope').textContent='نطاق عينة فقط. فحص السندات المفقودة غير منفذ حتى إثبات اكتمال التسلسل والإلغاءات.';$('content').append(table(d.investigation.findings));}
 else{const section=d.sections[selected];$('scope').textContent=section?.state==='sample'?'عينة بحد أقصى 200 صف • '+section.scope:'لم يُعتمد ربط هذا القسم بجداول TrustSoft بعد.';$('content').append(table(section?.rows));}
 renderEvents();
}
function renderEvents(){$('events').replaceChildren(table((snapshot.events||[]).slice().reverse().map(e=>({الحدث:e.type,الوقت:new Date(e.utc_epoch*1000).toISOString()}))));}
async function refresh(){
 if(localFile||busy)return false;busy=true;clearTimeout(timer);$('refresh').disabled=true;
 try{
  const r=await apiRequest(dedicatedControl?'/api/control/state':'/api/snapshot');
  if(r.status===401||(dedicatedControl&&r.status===403)){$('login').hidden=false;$('workspace').hidden=true;return false;}
  if(!r.ok)throw Error();
  const data=await r.json();$('login').hidden=true;$('workspace').hidden=false;
  if(dedicatedControl){
   $('control-panel').hidden=false;$('data-panel').hidden=true;$('title').textContent=labels.control;
   $('updated').textContent='آخر تحديث لحالة الإدارة: '+new Date().toLocaleString('ar');
   renderControlState(data);
   $('events').replaceChildren(table([{القناة:data.online?'متصلة':'غير متصلة',آخر_اتصال:data.last_seen?new Date(data.last_seen*1000).toISOString():'لم يحدث'}]));
  }else{snapshot=data;render();}
  return true;
 }catch{
  controlAvailable=false;controlButtons(false);
  if(snapshot){snapshot={...snapshot,state:'disconnected',stale:true,error:'dashboard_unreachable'};render();}
  $('status').textContent='انقطع الاتصال بخدمة اللوحة';$('status').dataset.ok='false';$('alert').hidden=false;
  $('alert').textContent='تعذر تحديث الحالة. البيانات المعروضة قديمة. ستعاد المحاولة تلقائيًا.';return false;
 }finally{busy=false;$('refresh').disabled=false;if($('auto').checked)timer=setTimeout(refresh,30000);}
}

$('login').onsubmit=async e=>{
 e.preventDefault();if(localFile)return;
 $('login-submit').disabled=true;
 try{
  if(portalClient)portalSession='';
  const r=await apiRequest(adminEntry?'/api/admin/login':'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('token').value})});
  $('token').value='';
  if(!r.ok){
   const failure=await r.json();
   $('login-error').textContent=failure.error==='admin_not_configured'?'خدمة إدارة الموقع لم تُهيأ بعد؛ يلزم إعداد مضيف الويب.':'لم يُقبل مفتاح الدخول. تحقق من المفتاح الخاص بهذا الموقع.';
   return;
  }
  await acceptPortalLogin(r);
  $('login-error').textContent='';
  if(!await refresh())$('login-error').textContent='لم تثبت جلسة الدخول. استخدم رابط HTTPS للموقع واسمح بملفات ارتباطه، ثم أعد المحاولة.';
 }catch{
  $('token').value='';$('login-error').textContent='تعذر الوصول لخدمة الويب. تحقق من رابط الموقع والإنترنت، ثم أعد المحاولة.';
 }finally{$('login-submit').disabled=false;}
};

$('refresh').onclick=refresh;$('auto').onchange=()=>{clearTimeout(timer);if($('auto').checked)refresh();};

const actionLabels={reconnect:'إعادة محاولة الاتصال',server_probe:'فحص اتصال الخادم',sql_probe:'فحص قاعدة البيانات',export_probe:'فحص ومراقبة التصدير',backup_probe:'فحص النسخ الاحتياطية'};
let controlBusy=false,controlState=null,controlAvailable=false;
for(const [action,label] of Object.entries(actionLabels)){
 const b=el('button',label);b.type='button';b.disabled=true;b.dataset.action=action;
 b.onclick=()=>submitAction(action);$('control-actions').append(b);
}
function controlButtons(enabled){document.querySelectorAll('#control-actions button').forEach(b=>b.disabled=!enabled);}
function renderControlState(state){
  controlState=state;controlAvailable=true;$('admin-login').hidden=true;
  controlButtons(controlState.agent_configured);
  if(selected==='control'){
   $('cards').hidden=false;$('cards').replaceChildren();
   for(const [label,value] of [['قناة الخادم',controlState.online?'متصلة':'غير متصلة'],['طلبات تنتظر',controlState.jobs.filter(j=>j.status==='queued').length],['فحوص جارية',controlState.jobs.filter(j=>j.status==='running').length],[controlState.backups.source==='agent_inventory'?'نسخ مرصودة من الوكيل':'نسخ على مضيف اللوحة',controlState.backups.files.length]]){
    const card=el('div','');card.className='card';card.append(el('span',label),el('strong',String(value)));$('cards').append(card);
   }
   $('status').textContent=controlState.online?'قناة الإدارة متصلة':(controlState.last_seen?'قناة الإدارة غير متصلة':'بانتظار أول اتصال من الخادم');$('status').dataset.ok=String(controlState.online);
   $('alert').hidden=controlState.online;$('alert').textContent=controlState.last_seen?
    'لا توجد قناة إدارة حية للخادم حاليًا. الطلبات لا تعني نجاح اتصال SQL. الوكيل يعيد المحاولة أثناء تشغيله وتوفر الشبكة.':
    'لم تسجل اللوحة أي اتصال من JAWAFFASVR حتى الآن. يلزم تشغيل الوكيل على الخادم أو فحص مهمته إن كان مثبتًا. تنفيذ طلب إعادة الاتصال يحتاج اتصال الوكيل باللوحة.';
  }
  $('control-message').textContent=!controlState.agent_configured?'قناة الوكيل غير مهيأة بعد. ثبّت الوكيل واضبط اعتماداته.':
   (controlState.online?'الوكيل متصل؛ يمكن إرسال الفحوص.':(controlState.last_seen?
    'الوكيل غير متصل؛ الطلبات تنتظر حتى 120 ثانية. يعيد الوكيل المحاولة أثناء تشغيله وتوفر الشبكة.':
    'بانتظار أول اتصال من الوكيل؛ شغله على JAWAFFASVR أو افحص مهمته إن كان مثبتًا. الطلبات تنتهي بعد 120 ثانية إن لم يستلمها.'))+
   ' آخر اتصال: '+(controlState.last_seen?new Date(controlState.last_seen*1000).toLocaleString('ar'):'لم يحدث');
  const exportLabels={insufficient_evidence:'لا تكفي العينات للحكم',change_observed:'تغيرت أعداد الملفات أو أحجامها بين عينتين',unchanged_samples:'لم تتغير العينتان؛ هذا لا يثبت تعطل التصدير'};
  const progress=controlState.export_monitor||{state:'insufficient_evidence',samples:[]};
  $('export-monitor').replaceChildren(el('p',exportLabels[progress.state]||progress.state),table(progress.samples));
  $('control-jobs').replaceChildren();
  for(const job of controlState.jobs){
   const block=el('details','');block.append(el('summary',(actionLabels[job.action]||job.action)+' • '+job.status+' • '+new Date(job.created*1000).toLocaleString('ar')));
   block.append(el('pre',JSON.stringify(job.output||{state:job.status},null,2)));$('control-jobs').append(block);
  }
  if(!controlState.jobs.length)$('control-jobs').append(el('p','لا توجد طلبات تشغيل بعد.'));
  $('backups-title').textContent=controlState.backups.source==='agent_inventory'?'فهرس النسخ الوارد من الوكيل':'النسخ الموجودة على مضيف اللوحة';
  $('control-backups').replaceChildren(el('p','حالة الجرد: '+controlState.backups.state+(controlState.backups.stale?' • آخر فهرس قديم أو لم يُتحقق بعد':'')),table(controlState.backups.files));
  $('control-audit').replaceChildren(table(controlState.audit.map(e=>({الوقت:new Date(e.at*1000).toISOString(),الحدث:e.event,الطلب:e.job_id}))));
}
async function refreshControl(){
 if(localFile)return;
 if(controlBusy)return;controlBusy=true;controlAvailable=false;
 try{
  const r=await apiRequest('/api/control/state',{},12000);
  if(r.status===401||r.status===403){$('cards').hidden=true;$('status').textContent='يلزم دخول الإدارة';$('status').dataset.ok='false';$('admin-login').hidden=false;controlButtons(false);$('control-message').textContent='سجل دخول الإدارة؛ مفتاح المشاهدة لا يمنح تنفيذ الأوامر.';return;}
  if(!r.ok)throw Error();
  renderControlState(await r.json());return true;
 }catch{controlButtons(false);if(selected==='control'){$('status').textContent='قناة الإدارة غير متاحة';$('status').dataset.ok='false';}$('control-message').textContent='تعذر الوصول لقناة الإدارة. النتائج السابقة قديمة؛ ستعاد المحاولة مع تحديث اللوحة.';}
 finally{controlBusy=false;}
}
async function submitAction(action){
 if(localFile)return;
 controlButtons(false);
 try{
  const r=await apiRequest('/api/control/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-TrustSoft-Action':'1'},body:JSON.stringify({action})});
  const result=await r.json();if(!r.ok)throw Error(result.error||'request_failed');
  await refreshControl();$('control-message').textContent='تم إدراج الطلب '+result.id+'؛ انتظار نتيجة الوكيل، وهذا لا يعني نجاح الاتصال بعد.';
 }catch(e){$('control-message').textContent='لم يُقبل الطلب: '+e.message;}
 finally{controlButtons(controlAvailable&&Boolean(controlState?.agent_configured));}
}
$('admin-login').onsubmit=async e=>{
 e.preventDefault();if(localFile)return;
 try{
  const r=await apiRequest('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('admin-token').value})});
  $('admin-token').value='';if(!r.ok)throw Error();await acceptPortalLogin(r);await refreshControl();
 }catch{$('control-message').textContent='تعذر دخول الإدارة؛ تحقق من المفتاح وإعداد الخدمة.';}
};

globalThis.addEventListener?.('online',()=>{if(!localFile)refresh();});
function clearPortalState(){
 portalGeneration++;portalSession='';snapshot=null;controlState=null;controlAvailable=false;controlButtons(false);clearTimeout(timer);
 $('auto').checked=false;$('workspace').hidden=true;$('login').hidden=false;$('token').value='';$('admin-token').value='';
 for(const id of ['content','events','cards','control-jobs','control-backups','control-audit','export-monitor'])$(id).replaceChildren();
 $('status').textContent='انتهت جلسة TrustSoft';$('status').dataset.ok='false';
}
globalThis.addEventListener?.('pagehide',()=>{if(portalClient)clearPortalState();});
const portalExit=document.getElementById('portal-exit');
if(portalExit){
 portalExit.hidden=!portalClient;
 portalExit.onclick=clearPortalState;
}
document.addEventListener?.('visibilitychange',()=>{
 if(document.visibilityState==='visible'&&$('auto').checked&&!localFile)refresh();
});
refresh();
