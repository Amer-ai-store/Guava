'use strict';
const labels={overview:'نظرة عامة',control:'المراقبة والسيطرة',sales:'المبيعات',purchases:'المشتريات',accounting:'المحاسبة',checks:'الشيكات',inventory:'المخزون',users:'المستخدمون',operations:'التقارير التشغيلية',financial:'التقارير المالية',investigation:'التحقيق المالي',schema:'المخطط والعلاقات'};
const errors={agent_sql_not_verified:'لم يثبت اتصال SQL بواسطة وكيل الخادم',sql_not_configured:'اتصال SQL غير مهيأ',sql_driver_missing:'برنامج تشغيل SQL غير مثبت',sql_connection_or_query_failed:'تعذر الاتصال أو تنفيذ الاستعلام',read_only_principal_required:'الحساب يملك صلاحيات تتجاوز القراءة؛ أُوقفت القراءة',tls_configuration_required:'يلزم اتصال مشفر بشهادة موثوقة',wrong_server:'الخادم لا يطابق JAWAFFASVR',dashboard_unreachable:'تعذر الاتصال بخدمة اللوحة',wrong_database:'قاعدة البيانات لا تطابق Jawafa_DB',invalid_mapping:'ربط الأعمدة غير صالح'};
const $=id=>document.getElementById(id);

// Do not depend on AbortSignal.timeout: older Android/WebView versions omit it.
async function apiRequest(path,options={},timeout=15000){
 const controller=typeof AbortController==='function'?new AbortController():null;
 const deadline=controller?setTimeout(()=>controller.abort(),timeout):null;
 try{
  const response=await fetch(path,controller?{...options,signal:controller.signal}:options);
  const payload=await response.json();
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
let selected=dedicatedControl?'control':'overview',snapshot=null,busy=false,timer;
function el(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
for(const [key,label] of Object.entries(labels)){if(dedicatedControl&&key!=='control')continue;const b=el('button',label);b.onclick=()=>{selected=key;render();};b.dataset.section=key;$('nav').append(b);}
function table(rows){if(!rows?.length)return el('div','لا توجد نتائج مثبتة لهذا العرض.');const wrap=document.createElement('div'),t=document.createElement('table'),head=document.createElement('thead'),body=document.createElement('tbody'),keys=[...new Set(rows.flatMap(Object.keys))],tr=document.createElement('tr');keys.forEach(k=>tr.append(el('th',k)));head.append(tr);rows.forEach(row=>{const r=document.createElement('tr');keys.forEach(k=>r.append(el('td',typeof row[k]==='object'?JSON.stringify(row[k]):String(row[k]??'—'))));body.append(r);});t.append(head,body);wrap.className='table-scroll';wrap.append(t);return wrap;}
function render(){if(!snapshot)return;
 $('title').textContent=labels[selected];document.querySelectorAll('nav button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.section===selected)));
 const s=snapshot,d=s.data,ok=s.state==='connected'&&!s.stale;
 $('status').textContent=ok?'متصل • استعلام ناجح':'غير متصل / بيانات غير حديثة';$('status').dataset.ok=String(ok);
 $('alert').hidden=ok;$('alert').textContent=(errors[s.error]||s.error||'بانتظار التحقق')+' — لا توجد بيانات حية مؤكدة. أي بيانات سابقة أدناه قديمة.';
 $('updated').textContent='آخر قراءة ناجحة: '+(s.last_success?new Date(s.last_success*1000).toLocaleString('ar'):'لم تحدث');
 $('cards').replaceChildren();for(const [label,value] of [['حالة القراءة',ok?'ناجحة':'غير مثبتة'],['الجداول والعروض المرئية',d?new Set(d.catalog.map(c=>c.schema_name+'.'+c.object_name)).size:'—'],['العلاقات المعلنة',d?d.relations.length:'—'],['إخفاقات متتالية',s.failures]]){const c=el('div','');c.className='card';c.append(el('span',label),el('strong',String(value)));$('cards').append(c);}
 $('control-panel').hidden=selected!=='control';$('data-panel').hidden=selected==='control';if(selected==='control'){$('cards').hidden=!controlState;$('alert').hidden=true;$('status').textContent='قناة الإدارة • جارٍ التحقق';refreshControl();return renderEvents();}$('cards').hidden=false;
 $('panel-title').textContent=labels[selected];$('scope').textContent='';$('content').replaceChildren();
 if(!d){$('content').append(el('div','لم يتم جلب بيانات من TrustSoft. لا تُعرض بيانات تجريبية.'));return renderEvents();}
 if(selected==='overview'){$('content').append(table(Object.entries(d.sections).map(([k,v])=>({القسم:labels[k]||k,الحالة:v.state,صفوف_العينة:v.rows.length}))));}
 else if(selected==='schema'){$('scope').textContent='المخطط المرئي للحساب فقط؛ غياب علاقة معلنة لا يثبت غياب علاقة أعمال.';$('content').append(table(d.catalog),el('h3','العلاقات المعلنة'),table(d.relations));}
 else if(selected==='investigation'){$('scope').textContent='نطاق عينة فقط. فحص السندات المفقودة غير منفذ حتى إثبات اكتمال التسلسل والإلغاءات.';$('content').append(table(d.investigation.findings));}
 else{const section=d.sections[selected];$('scope').textContent=section?.state==='sample'?'عينة بحد أقصى 200 صف • '+section.scope:'لم يُعتمد ربط هذا القسم بجداول TrustSoft بعد.';$('content').append(table(section?.rows));}
 renderEvents();
}
function renderEvents(){$('events').replaceChildren(table((snapshot.events||[]).slice().reverse().map(e=>({الحدث:e.type,الوقت:new Date(e.utc_epoch*1000).toISOString()}))));}
async function refresh(){if(localFile||busy)return false;busy=true;clearTimeout(timer);$('refresh').disabled=true;try{const r=await apiRequest('/api/snapshot');if(r.status===401){$('login').hidden=false;$('workspace').hidden=true;return false;}if(!r.ok)throw Error();snapshot=await r.json();$('login').hidden=true;$('workspace').hidden=false;render();return true;}catch{if(snapshot){snapshot={...snapshot,state:'disconnected',stale:true,error:'dashboard_unreachable'};render();}$('status').textContent='انقطع الاتصال بخدمة اللوحة';$('status').dataset.ok='false';$('alert').hidden=false;$('alert').textContent='تعذر تحديث الحالة. البيانات المعروضة قديمة. ستعاد المحاولة تلقائيًا.';return false;}finally{busy=false;$('refresh').disabled=false;if($('auto').checked)timer=setTimeout(refresh,30000);}}
$('login').onsubmit=async e=>{
 e.preventDefault();if(localFile)return;
 $('login-submit').disabled=true;
 try{
  const r=await apiRequest(dedicatedControl?'/api/admin/login':'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('token').value})});
  $('token').value='';
  if(!r.ok){
   const failure=await r.json();
   $('login-error').textContent=failure.error==='admin_not_configured'?'خدمة إدارة الموقع لم تُهيأ بعد؛ يلزم إعداد مضيف الويب.':'لم يُقبل مفتاح الدخول. تحقق من المفتاح الخاص بهذا الموقع.';
   return;
  }
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
async function refreshControl(){
 if(localFile)return;
 if(controlBusy)return;controlBusy=true;controlAvailable=false;
 try{
  const r=await apiRequest('/api/control/state',{},12000);
  if(r.status===401||r.status===403){$('cards').hidden=true;$('status').textContent='يلزم دخول الإدارة';$('status').dataset.ok='false';$('admin-login').hidden=false;controlButtons(false);$('control-message').textContent='سجل دخول الإدارة؛ مفتاح المشاهدة لا يمنح تنفيذ الأوامر.';return;}
  if(!r.ok)throw Error();
  controlState=await r.json();controlAvailable=true;$('admin-login').hidden=true;
  controlButtons(controlState.agent_configured);
  if(selected==='control'){
   $('cards').hidden=false;$('cards').replaceChildren();
   for(const [label,value] of [['قناة الخادم',controlState.online?'متصلة':'غير متصلة'],['طلبات تنتظر',controlState.jobs.filter(j=>j.status==='queued').length],['فحوص جارية',controlState.jobs.filter(j=>j.status==='running').length],['نسخ على مضيف اللوحة',controlState.backups.files.length]]){
    const card=el('div','');card.className='card';card.append(el('span',label),el('strong',String(value)));$('cards').append(card);
   }
   $('status').textContent=controlState.online?'قناة الإدارة متصلة':'قناة الإدارة غير متصلة';$('status').dataset.ok=String(controlState.online);
   $('alert').hidden=controlState.online;$('alert').textContent='لا توجد قناة إدارة حية للخادم حاليًا. الطلبات لا تعني نجاح اتصال SQL. الوكيل سيعيد المحاولة عند عودة الشبكة.';
  }
  $('control-message').textContent=!controlState.agent_configured?'قناة الوكيل غير مهيأة بعد. ثبّت الوكيل واضبط اعتماداته.':
   (controlState.online?'الوكيل متصل؛ يمكن إرسال الفحوص.':'الوكيل غير متصل؛ الطلبات تنتظر حتى 120 ثانية، مع إعادة اتصال تلقائية من الوكيل.')+
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
  $('control-backups').replaceChildren(el('p','حالة الجرد: '+controlState.backups.state),table(controlState.backups.files));
  $('control-audit').replaceChildren(table(controlState.audit.map(e=>({الوقت:new Date(e.at*1000).toISOString(),الحدث:e.event,الطلب:e.job_id}))));
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
  $('admin-token').value='';if(!r.ok)throw Error();await refreshControl();
 }catch{$('control-message').textContent='تعذر دخول الإدارة؛ تحقق من المفتاح وإعداد الخدمة.';}
};

globalThis.addEventListener?.('online',()=>{if(!localFile)refresh();});
document.addEventListener?.('visibilitychange',()=>{
 if(document.visibilityState==='visible'&&$('auto').checked&&!localFile)refresh();
});
refresh();
