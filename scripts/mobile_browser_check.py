"""Real Chromium + Android viewport emulation against an isolated local test server.
No production SQL, external hosts, real credentials, or Windows agent are used.
Requires Playwright and a local Chromium, for development validation only.
"""
import json
from pathlib import Path
import secrets
import tempfile
import threading
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect, Error as BrowserError
from trustsoft.control import ControlStore
from trustsoft.server import App, Monitor
from trustsoft.source import SourceError

class OfflineSource:
    def snapshot(self):
        raise SourceError('sql_not_configured')

def main():
    output=Path('docs/evidence')
    output.mkdir(parents=True,exist_ok=True)
    reader,admin,agent=(secrets.token_urlsafe(48) for _ in range(3))
    report={'engine':'Chromium','android_emulation':True,'physical_android_tested':False,
            'production_sql_connected':False,'public_site_deployed':False,'cases':[]}
    environment={'TRUSTSOFT_ADMIN_TOKEN':admin,'TRUSTSOFT_AGENT_TOKEN':agent,'TRUSTSOFT_SECURE_COOKIE':'0'}
    with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',environment):
        store=ControlStore(Path(folder,'control.sqlite'))
        monitor=Monitor(OfflineSource())
        monitor.refresh()
        app=App(('127.0.0.1',0),monitor,reader,control=store)
        thread=threading.Thread(target=app.serve_forever,daemon=True)
        thread.start()
        address='http://127.0.0.1:'+str(app.server_port)
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True)
                for width,height in ((393,851),(360,800),(320,640),(851,393)):
                    context=browser.new_context(viewport={'width':width,'height':height},
                        is_mobile=True,has_touch=True,device_scale_factor=1,
                        user_agent=p.devices['Pixel 5']['user_agent'])
                    # Reproduce the API capability missing in older Android/WebViews.
                    context.add_init_script('AbortSignal.timeout = undefined;')
                    page=context.new_page()
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(address+'/control')
                    page.locator('#token').fill(admin)
                    page.locator('#login-submit').click()
                    page.locator('#workspace').wait_for(state='visible')
                    page.locator('[data-action="server_probe"]:enabled').wait_for()
                    assert page.locator('#control-panel').is_visible()
                    assert page.locator('#token').input_value()==''
                    assert page.locator('#control-actions button').count()==5
                    geometry=page.evaluate("""() => ({
                        width:innerWidth,pageWidth:document.documentElement.scrollWidth,
                        targets:[...document.querySelectorAll('#control-actions button')].map(b=>({
                            height:b.getBoundingClientRect().height,left:b.getBoundingClientRect().left,
                            right:b.getBoundingClientRect().right
                        }))
                    })""")
                    assert geometry['pageWidth']<=geometry['width'],geometry
                    assert all(b['height']>=44 and b['left']>=0 and b['right']<=width for b in geometry['targets']),geometry
                    with page.expect_response(lambda r:r.url.endswith('/api/control/jobs')) as response:
                        page.locator('[data-action="server_probe"]').click()
                    assert response.value.status==202
                    assert any(j['action']=='server_probe' for j in store.snapshot()['jobs'])
                    if width==393:
                        page.screenshot(path=str(output/'android-control.png'),full_page=True)
                        context.set_offline(True)
                        page.locator('#refresh').click()
                        expect(page.locator('#alert')).to_contain_text('تعذر تحديث')
                        context.set_offline(False)
                        page.evaluate("window.dispatchEvent(new Event('online'))")
                        expect(page.locator('#status')).to_have_text('قناة الإدارة غير متصلة')
                        report['cases'].append({'case':'network_resume','passed':True})
                    assert not errors,errors
                    report['cases'].append({'case':'mobile_browser_login_and_job','width':width,'height':height,
                        'without_abortsignal_timeout':True,'horizontal_overflow':False,'touch_targets_min_44px':True,'passed':True})
                    context.close()
                context=browser.new_context()
                page=context.new_page()
                network=[]
                page.on('request',lambda request:network.append(request.url))
                try:
                    page.goto(Path('web/control.html').resolve().as_uri())
                    page.locator('#browser-message').wait_for(state='visible')
                    assert page.locator('#login-submit').is_disabled()
                    assert 'HTTPS' in page.locator('#browser-message').inner_text()
                    assert not any(url.startswith(('http:','https:')) for url in network)
                    report['file_url_check']={'state':'passed'}
                except BrowserError as exc:
                    if 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(exc):
                        raise
                    report['file_url_check']={'state':'blocked_by_browser_policy','guard_tested_in_node':True}
                context.close()
                context=browser.new_context()
                page=context.new_page()
                page.route('**/api/admin/login',lambda route:route.fulfill(status=200,
                    content_type='application/json',body='{"ok":true}'))
                page.goto(address+'/control')
                page.locator('#token').fill(admin)
                page.locator('#login-submit').click()
                expect(page.locator('#login-error')).to_contain_text('HTTPS')
                report['cases'].append({'case':'missing_session_cookie_has_actionable_error','passed':True})
                context.close()
                browser.close()
            report['all_passed']=all(c['passed'] for c in report['cases'])
            (output/'android-browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            app.shutdown()
            app.server_close()
            thread.join()
            store.close()

if __name__=='__main__':
    main()
