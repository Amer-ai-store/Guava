/* Free serverless control plane. D1 persists state across Worker restarts.
   Only fixed, read-only Windows probes are dispatched. No production SQL is run here. */
const ACTIONS=['server_probe','sql_probe','export_probe','backup_probe','reconnect'];
const SECTIONS=['sales','purchases','accounting','checks','inventory','users','operations','financial','vouchers','payments','audit'];
const MAX_BODY=512*1024, encoder=new TextEncoder();
const now=()=>Date.now()/1000;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const random=()=>[...crypto.getRandomValues(new Uint8Array(16))].map(v=>v.toString(16).padStart(2,'0')).join('');
const commonHeaders={
 'Cache-Control':'no-store','Strict-Transport-Security':'max-age=31536000','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
};
function json(status,data,cookie){
 const headers=new Headers({...commonHeaders,'Content-Type':'application/json; charset=utf-8'});
 if(cookie)headers.set('Set-Cookie',cookie);
 return new Response(JSON.stringify(data),{status,headers});
}
class Failure extends Error{constructor(status,code){super(code);this.status=status;}}
async function digest(value){return new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value)));}
async function equal(a,b){
 const [left,right]=await Promise.all([digest(a),digest(b)]);
 let difference=0;for(let i=0;i<left.length;i++)difference|=left[i]^right[i];
 return difference===0;
}
function configured(env){
 const keys=[env.TRUSTSOFT_API_TOKEN,env.TRUSTSOFT_ADMIN_TOKEN,env.TRUSTSOFT_AGENT_TOKEN,env.SESSION_SIGNING_KEY];
 return keys.every(k=>typeof k==='string'&&k.length>=32)&&new Set(keys).size===keys.length;
}
function b64(bytes){return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');}
function un64(value){const raw=atob(value.replaceAll('-','+').replaceAll('_','/'));return Uint8Array.from(raw,c=>c.charCodeAt(0));}
async function signing(env){
 return crypto.subtle.importKey('raw',encoder.encode(env.SESSION_SIGNING_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
}
async function version(role,env){return b64(await digest(role==='admin'?env.TRUSTSOFT_ADMIN_TOKEN:env.TRUSTSOFT_API_TOKEN));}
async function issueCookie(role,env){
 const ver=await version(role,env);
 const body=b64(encoder.encode(JSON.stringify({role,exp:now()+3600,nonce:random(),ver})));
 const signature=await crypto.subtle.sign('HMAC',await signing(env),encoder.encode(body));
 return (role==='admin'?'ts_admin=':'ts=')+body+'.'+b64(new Uint8Array(signature))+'; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=3600';
}
async function authorized(request,env,admin=false){
 if(!configured(env))return false;
 for(const part of (request.headers.get('Cookie')||'').split(';')){
  const item=part.trim();if(!item.startsWith('ts=')&&!item.startsWith('ts_admin='))continue;
  try{
   const value=item.slice(item.indexOf('=')+1);if(value.length>2048)continue;
   const [body,signature,...extra]=value.split('.');if(!body||!signature||extra.length)continue;
   if(!await crypto.subtle.verify('HMAC',await signing(env),un64(signature),encoder.encode(body)))continue;
   const data=JSON.parse(new TextDecoder().decode(un64(body)));
   if(data.exp>now()&&['reader','admin'].includes(data.role)&&(!admin||data.role==='admin')&&typeof data.ver==='string'&&await equal(data.ver,await version(data.role,env)))return true;
  }catch{}
 }
 return false;
}
function sameOrigin(request){
 const site=request.headers.get('Sec-Fetch-Site');
 if(site&&!['same-origin','none'].includes(site))throw new Failure(403,'cross_site_request');
 const origin=request.headers.get('Origin');
 if(origin&&origin!==new URL(request.url).origin)throw new Failure(403,'cross_site_request');
}
async function body(request,max=4096){
 const length=Number(request.headers.get('Content-Length')||0);
 if(length>max)throw new Failure(413,'body_too_large');
 if(!request.body)throw new Failure(400,'invalid_json');
 const reader=request.body.getReader(),chunks=[];let bytes=0;
 while(true){
  const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;
  if(bytes>max){await reader.cancel();throw new Failure(413,'body_too_large');}
  chunks.push(part.value);
 }
 const raw=new Uint8Array(bytes);let offset=0;
 for(const part of chunks){raw.set(part,offset);offset+=part.length;}
 let result;try{result=JSON.parse(new TextDecoder().decode(raw));}catch{throw new Failure(400,'invalid_json');}
 if(!object(result))throw new Failure(400,'invalid_json');
 return result;
}
const prepare=(db,sql,args=[])=>db.prepare(sql).bind(...args);
const first=(db,sql,args=[])=>prepare(db,sql,args).first();
async function rows(db,sql,args=[]){return (await prepare(db,sql,args).all()).results;}
const run=(db,sql,args=[])=>prepare(db,sql,args).run();
async function meta(db,key){const r=await first(db,'SELECT value FROM metadata WHERE key=?',[key]);return r?JSON.parse(r.value):null;}
async function broker(db){
 let id=await meta(db,'broker_id');
 if(!id){await run(db,'INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)',['broker_id',JSON.stringify(random())]);id=await meta(db,'broker_id');}
 return id;
}
async function expire(db){
 await run(db,"UPDATE jobs SET status='expired' WHERE status IN ('queued','running') AND expires<?",[now()]);
}
async function enqueue(db,action,automatic=false){
 if(!ACTIONS.includes(action))throw new Failure(409,'action_not_allowed');
 await expire(db);
 const current=await first(db,"SELECT id FROM jobs WHERE action=? AND status IN ('queued','running')",[action]);
 if(current)return {id:current.id,deduplicated:true};
 const last=await first(db,'SELECT created FROM jobs WHERE action=? ORDER BY created DESC LIMIT 1',[action]);
 if(last&&now()-last.created<15)throw new Failure(409,'action_cooldown');
 const count=await first(db,"SELECT count(*) AS count FROM jobs WHERE status IN ('queued','running')");
 if(count.count>=5)throw new Failure(409,'queue_full');
 const id=random(),at=now();
 const result=await run(db,"INSERT OR IGNORE INTO jobs(id,action,created,expires,status,identity_only) VALUES(?,?,?,?,'queued',?)",
  [id,action,at,at+120,Number(automatic&&action==='sql_probe')]);
 if(result.meta.changes){
  await run(db,'INSERT INTO audit(at,event,job_id) VALUES(?,?,?)',[at,automatic?'automatic_probe_queued':'operator_probe_queued',id]);
  return {id,deduplicated:false};
 }
 const winner=await first(db,"SELECT id FROM jobs WHERE action=? AND status IN ('queued','running')",[action]);
 if(!winner)throw new Failure(409,'queue_changed');
 return {id:winner.id,deduplicated:true};
}
async function poll(db){
 for(const [action,interval] of [['sql_probe',60],['server_probe',300],['backup_probe',300]]){
  let last=await first(db,'SELECT created FROM jobs WHERE action=? ORDER BY created DESC LIMIT 1',[action]);
  if(action==='sql_probe'){
   const reconnect=await first(db,'SELECT created FROM jobs WHERE action=? ORDER BY created DESC LIMIT 1',['reconnect']);
   if(reconnect&&(!last||reconnect.created>last.created))last=reconnect;
  }
  if(!last||now()-last.created>=interval){try{await enqueue(db,action,true);}catch(e){if(!(e instanceof Failure))throw e;}}
 }
 const at=now(),lease=random()+random();
 const result=await db.batch([
  prepare(db,"UPDATE jobs SET status='expired' WHERE status IN ('queued','running') AND expires<?",[at]),
  prepare(db,'INSERT OR REPLACE INTO agent(id,last_seen) VALUES(?,?)',['JAWAFFASVR',at]),
  prepare(db,"UPDATE jobs SET status='running',lease=? WHERE id=(SELECT id FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1) AND NOT EXISTS(SELECT 1 FROM jobs WHERE status='running') RETURNING id,action,expires,identity_only",[lease])
 ]);
 const job=result[2].results?.[0];
 if(!job)return null;
 await run(db,'INSERT INTO audit(at,event,job_id) VALUES(?,?,?)',[at,'probe_leased',job.id]);
 return {...job,lease,identity_only:Boolean(job.identity_only)};
}
function validSQL(sql){
 return object(sql)&&sql.state==='verified_read'&&object(sql.identity)&&sql.identity.database_name==='Jawafa_DB'&&
  String(sql.identity.server_name||'').split('\\')[0].split('.')[0].toUpperCase()==='JAWAFFASVR';
}
async function complete(db,data){
 if(Object.keys(data).sort().join(',')!=='id,lease,output'||typeof data.id!=='string'||typeof data.lease!=='string'||
  !object(data.output)||typeof data.output.execution_ok!=='boolean')throw new Failure(400,'invalid_result');
 const output=data.output;
 for(const key of ['sql','diagnostics','backups'])if(key in output&&!object(output[key]))throw new Failure(400,'invalid_result');
 if(object(output.backups)&&'files' in output.backups&&!Array.isArray(output.backups.files))throw new Failure(400,'invalid_result');
 const job=await first(db,'SELECT action,status,expires,lease FROM jobs WHERE id=?',[data.id]);
 if(!job||job.status!=='running'||job.expires<now()||!await equal(job.lease,data.lease))throw new Failure(409,'invalid_or_expired_lease');
 const at=now(),sql=output.sql;
 let schema=null,latest=null;
 if(['sql_probe','reconnect'].includes(job.action)){
  let schemaInfo=await meta(db,'latest_schema');
  if(object(sql)&&Array.isArray(sql.catalog)&&Array.isArray(sql.relations)&&sql.metadataMode!=='identity_only'&&validSQL(sql)){
   const raw=JSON.stringify({catalog:sql.catalog,relations:sql.relations});
   const hash=[...await digest(raw)].map(v=>v.toString(16).padStart(2,'0')).join('');
   schemaInfo={hash,mode:sql.metadataMode==='sample'?'sample':'discovered',
    catalog_total:sql.catalogTotal??sql.catalog.length,relation_total:sql.relationTotal??sql.relations.length};
   schema={raw,info:schemaInfo};
  }
  latest={at,ok:output.execution_ok&&validSQL(sql),identity:object(sql?.identity)?sql.identity:null,schema:schemaInfo};
 }
 const stored=JSON.parse(JSON.stringify(output));
 if(object(stored.sql)){
  stored.sql.catalog_rows=Array.isArray(sql.catalog)?sql.catalog.length:0;
  stored.sql.relations_rows=Array.isArray(sql.relations)?sql.relations.length:0;
  delete stored.sql.catalog;delete stored.sql.relations;
 }
 const receipt=random(),status=output.execution_ok?'completed':'failed';
 // A receipt makes metadata/audit writes conditional on this exact atomic completion.
 const operations=[prepare(db,"UPDATE jobs SET status=?,completed=?,output=?,lease=? WHERE id=? AND lease=? AND status='running' AND expires>=? RETURNING id",
  [status,at,JSON.stringify(stored),receipt,data.id,data.lease,at])];
 const condition=" WHERE EXISTS(SELECT 1 FROM jobs WHERE id=? AND lease=? AND status IN ('completed','failed'))";
 if(schema){
  // Input is capped below D1's per-value limit; keep Unicode intact in one immutable row.
  operations.push(prepare(db,'INSERT OR IGNORE INTO schema_chunks(hash,part,content) SELECT ?,?,?'+condition,
   [schema.info.hash,0,schema.raw,data.id,receipt]));
  operations.push(prepare(db,'INSERT OR REPLACE INTO metadata(key,value) SELECT ?,?'+condition,
   ['latest_schema',JSON.stringify(schema.info),data.id,receipt]));
 }
 if(job.action==='backup_probe'&&object(output.backups)){
  const key=output.execution_ok?'latest_backup':'latest_backup_error';
  operations.push(prepare(db,'INSERT OR REPLACE INTO metadata(key,value) SELECT ?,?'+condition,
   [key,JSON.stringify({at,...output.backups}),data.id,receipt]));
 }
 if(latest)operations.push(prepare(db,'INSERT OR REPLACE INTO metadata(key,value) SELECT ?,?'+condition,
  ['latest_sql',JSON.stringify(latest),data.id,receipt]));
 operations.push(prepare(db,'INSERT INTO audit(at,event,job_id) SELECT ?,?,?'+condition,
  [at,'probe_'+status,data.id,data.id,receipt]));
 const results=await db.batch(operations);
 if(!results[0].results?.length)throw new Failure(409,'invalid_or_expired_lease');
}
function exportProgress(jobs){
 const samples=[];
 for(const job of [...jobs].sort((a,b)=>(b.completed||0)-(a.completed||0))){
  const value=job.output?.diagnostics?.export;
  if(value?.state==='observed'&&Array.isArray(value.items)&&object(value.items[0]))samples.push({...value.items[0],observed_at:job.completed});
  if(samples.length===2)break;
 }
 const result={state:'insufficient_evidence',completeness:'unverified',samples};
 if(samples.length===2&&samples.every(s=>typeof s.count==='number'&&typeof s.bytes==='number')){
  result.count_delta=samples[0].count-samples[1].count;result.bytes_delta=samples[0].bytes-samples[1].bytes;
  result.state=result.count_delta||result.bytes_delta?'change_observed':'unchanged_samples';
 }
 return result;
}
async function controlState(db){
 const [seen,list,audit,id,backup,backupError]=await Promise.all([
  first(db,'SELECT last_seen FROM agent WHERE id=?',['JAWAFFASVR']),
  rows(db,'SELECT id,action,created,expires,status,completed,output FROM jobs ORDER BY created DESC LIMIT 30'),
  rows(db,'SELECT at,event,job_id FROM audit ORDER BY id DESC LIMIT 50'),broker(db),meta(db,'latest_backup'),meta(db,'latest_backup_error')
 ]);
 for(const job of list){
  if(['queued','running'].includes(job.status)&&job.expires<now())job.status='expired';
  job.output=job.output?JSON.parse(job.output):null;
 }
 return {broker_id:id,agent_id:'JAWAFFASVR',last_seen:seen?.last_seen??null,online:Boolean(seen&&now()-seen.last_seen<=90),
  agent_configured:true,jobs:list,audit,actions:ACTIONS,export_monitor:exportProgress(list),
  backups:backup?{...backup,files:Array.isArray(backup.files)?backup.files:[],source:'agent_inventory',restorability:'not_verified',
   stale:!seen||now()-seen.last_seen>90||now()-backup.at>600||Boolean(backupError&&backupError.at>backup.at)}
   :{state:'awaiting_agent_inventory',files:[],source:'agent_inventory',restorability:'not_verified',stale:true},
  hosting:{provider:'cloudflare',storage:'persistent_d1',free_tier_compatible:true}};
}
async function snapshot(db){
 const [latest,seen]=await Promise.all([meta(db,'latest_sql'),first(db,'SELECT last_seen FROM agent WHERE id=?',['JAWAFFASVR'])]);
 const fresh=Boolean(latest?.ok&&seen&&now()-seen.last_seen<=90&&now()-latest.at<=90);
 let data=null;
 if(latest?.ok){
  let schema={catalog:[],relations:[]};
  if(latest.schema?.hash){
   const chunks=await rows(db,'SELECT content FROM schema_chunks WHERE hash=? ORDER BY part',[latest.schema.hash]);
   if(chunks.length)schema=JSON.parse(chunks.map(r=>r.content).join(''));
  }
  data={identity:latest.identity,...schema,schema_state:latest.schema?.mode||'not_discovered',
   sections:Object.fromEntries(SECTIONS.map(k=>[k,{state:'unmapped',rows:[]}])),
   investigation:{findings:[],coverage:'not_assessed',missing_vouchers:{state:'not_assessed'}}};
 }
 return {state:fresh?'connected':'disconnected',stale:!fresh,last_success:latest?.ok?latest.at:null,
  error:fresh?null:'agent_sql_not_verified',failures:fresh?0:1,events:[],data};
}
async function login(request,env,db,admin){
 if(!configured(env))throw new Failure(503,'admin_not_configured');
 sameOrigin(request);const data=await body(request);
 if(typeof data.token!=='string')throw new Failure(400,'invalid_json');
 const at=now();
 const attempt=await db.batch([
  prepare(db,'DELETE FROM login_attempts WHERE at<?',[at-60]),
  prepare(db,'INSERT INTO login_attempts(at) SELECT ? WHERE (SELECT count(*) FROM login_attempts)<10',[at])
 ]);
 if(!attempt[1].meta.changes)throw new Failure(429,'try_later');
 if(!await equal(data.token,admin?env.TRUSTSOFT_ADMIN_TOKEN:env.TRUSTSOFT_API_TOKEN))throw new Failure(401,'unauthorized');
 return json(200,{ok:true},await issueCookie(admin?'admin':'reader',env));
}
export default {
 async fetch(request,env){
  const url=new URL(request.url);
  if(url.protocol!=='https:'&&!['127.0.0.1','localhost','[::1]'].includes(url.hostname)){
   url.protocol='https:';
   return new Response(null,{status:308,headers:{...commonHeaders,Location:url.toString()}});
  }
  const path=url.pathname;
  try{
   if(path==='/control/')return new Response(null,{status:308,headers:{...commonHeaders,Location:'/control'}});
   if(path==='/healthz')return json(200,{process:'running',storage:'d1'});
   if(!path.startsWith('/api/')){
    if(!['GET','HEAD'].includes(request.method))throw new Failure(405,'method_not_allowed');
    const files={'/':'/control.html','/control':'/control.html','/control/':'/control.html','/dashboard':'/index.html','/app.js':'/app.js','/style.css':'/style.css'};
    if(!(path in files))throw new Failure(404,'not_found');
    const url=new URL(request.url);url.pathname=files[path];
    const asset=await env.ASSETS.fetch(new Request(url,{method:request.method}));
    const headers=new Headers(asset.headers);for(const [key,value] of Object.entries(commonHeaders))headers.set(key,value);
    return new Response(asset.body,{status:asset.status,headers});
   }
   const db=env.DB.withSession?env.DB.withSession('first-primary'):env.DB;
   if(request.method==='POST'&&['/api/login','/api/admin/login'].includes(path))return await login(request,env,db,path==='/api/admin/login');
   if(path.startsWith('/api/agent/')){
    if(!configured(env)||!await equal(request.headers.get('Authorization')||'','Bearer '+env.TRUSTSOFT_AGENT_TOKEN))throw new Failure(401,'agent_unauthorized');
    if(request.method!=='POST')throw new Failure(405,'method_not_allowed');
    const data=await body(request,path==='/api/agent/result'?MAX_BODY:4096);
    if(path==='/api/agent/result'){await complete(db,data);return json(200,{accepted:true});}
    if(Object.keys(data).join(',')!=='agent_id'||data.agent_id!=='JAWAFFASVR')throw new Failure(409,'agent_not_enrolled');
    if(path==='/api/agent/hello')return json(200,{broker_id:await broker(db)});
    if(path==='/api/agent/poll')return json(200,{job:await poll(db)});
    throw new Failure(404,'not_found');
   }
   if(path==='/api/control/jobs'){
    if(!await authorized(request,env,true))throw new Failure(403,'admin_required');
    if(request.method!=='POST')throw new Failure(405,'method_not_allowed');
    sameOrigin(request);if(request.headers.get('X-TrustSoft-Action')!=='1')throw new Failure(403,'cross_site_request');
    const data=await body(request);if(Object.keys(data).join(',')!=='action')throw new Failure(400,'invalid_request');
    return json(202,await enqueue(db,data.action));
   }
   if(!await authorized(request,env))throw new Failure(401,'unauthorized');
   if(request.method!=='GET')throw new Failure(405,'read_only_api');
   if(path==='/api/control/state'){
    if(!await authorized(request,env,true))throw new Failure(403,'admin_required');
    return json(200,await controlState(db));
   }
   if(path==='/api/snapshot')return json(200,await snapshot(db));
   if(path==='/api/health'){
    const state=await snapshot(db);delete state.data;return json(state.stale?503:200,state);
   }
   throw new Failure(404,'not_found');
  }catch(e){
   if(e instanceof Failure)return json(e.status,{error:e.message});
   // Do not expose tokens, SQL errors, D1 messages or customer data.
   return json(503,{error:'control_storage_or_runtime_unavailable'});
  }
 }
};
