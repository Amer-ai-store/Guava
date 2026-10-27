import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.mjs';

test('public HTTP upgrades to HTTPS before reading credentials or storage',async()=>{
 const untouched=new Proxy({}, {get(){throw new Error('Credentials or data touched before HTTPS');}});
 const request=new Request('http://test-only.workers.dev/api/admin/login',{method:'POST',body:'{"token":"test-only"}'});
 const response=await worker.fetch(request,untouched);
 assert.equal(response.status,308);
 assert.equal(response.headers.get('Location'),'https://test-only.workers.dev/api/admin/login');
 assert.equal(response.headers.get('Cache-Control'),'no-store');
});
test('unknown HTTPS routes expose no files or bindings and retain security headers',async()=>{
 const untouched=new Proxy({}, {get(){throw new Error('Unexpected binding access');}});
 const response=await worker.fetch(new Request('https://test-only.workers.dev/../config.json'),untouched);
 assert.equal(response.status,404);
 assert.match(response.headers.get('Content-Security-Policy'),/frame-ancestors 'none'/);
 assert.equal(response.headers.get('Cache-Control'),'no-store');
});
