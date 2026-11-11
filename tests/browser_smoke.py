"""Exercise the v2 desktop web interface against an isolated running application.

Run app.py --no-open first with AEGIS_DATA_DIR set to a temporary directory.
The test creates assessments and report versions; never target personal data.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',default='http://127.0.0.1:8767')
parser.add_argument('--artifacts',default='work/browser-checks')
args=parser.parse_args()
root=Path(args.artifacts).resolve();root.mkdir(parents=True,exist_ok=True)
base=args.url.rstrip('/')
executable=os.environ.get('BROWSER_EXECUTABLE') or shutil.which('chromium') or shutil.which('google-chrome')
checks=[]
suffix=uuid4().hex[:6]
with sync_playwright() as engine:
    browser=engine.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(base,wait_until='networkidle')
    expect(page.locator('#dashboard-view')).to_be_visible()
    token=page.locator('meta[name=advisory-token]').get_attribute('content')
    def get(path):
        response=page.request.get(base+path,headers={'X-Advisory-Token':token})
        assert response.ok,response.text()
        return response.json()
    kb=get('/api/knowledge')
    def wait_item(item_id,predicate):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            item=get('/api/assessments/'+item_id)
            if predicate(item):return item
            time.sleep(.1)
        raise AssertionError('Expected saved assessment state was not reached: '+json.dumps(item))
    def create(title,mode='full',categories=(),service=None,folder=None):
        library()
        page.locator('.create-trigger:visible').first.click()
        page.locator('#create-title').fill(title)
        page.locator(f'input[name=assessment-mode][value={mode}]').check()
        page.locator('#create-folder').select_option(folder or get('/api/workspace')['folders'][0]['id'])
        if mode=='partial':
            for category in categories:page.locator(f'#scope-categories input[value={category}]').check()
        if mode=='single':page.locator('#scope-service').select_option(service)
        page.locator('#create-submit').click()
        expect(page.locator('#assessment-editor')).to_be_visible()
        expect(page.locator('#project-title')).to_have_value(title)
        return next(item for item in get('/api/workspace')['assessments'] if item['title']==title)
    def library():
        page.locator('nav [data-view=library]').click()
        expect(page.locator('#library-view')).to_be_visible()
        if page.locator('#library-all-folders').is_visible():page.locator('#library-all-folders').click()
    def open_item(item_id):
        library()
        page.locator(f'#library-list [data-open-assessment="{item_id}"]').first.click()
        expect(page.locator('#review-view')).to_be_visible()
        page.locator('#begin-edit').click()
        expect(page.locator('#assessment-editor')).to_be_visible()
    def scope_questions(scope):
        if scope['mode']=='full':return kb['questions']
        if scope['mode']=='partial':return [q for q in kb['questions'] if q['category'] in scope['categories']]
        service=next(s for s in kb['services'] if s['id']==scope['service_id'])
        return [q for q in kb['questions'] if q['id'] in service['requirement_ids'] or (q['category']==service['category'] and q['kind']=='control')]
    def answer(item,values):
        qs=scope_questions(item['scope'])
        for category in kb['categories']:
            current=[q for q in qs if q['category']==category['id']]
            if not current:continue
            page.get_by_role('tab',name=category['name'],exact=True).click()
            for question in current:
                page.locator(f'#checklist input[name="{question["id"]}"][value="{values[question["id"]]}"]').check()
        page.locator('#save-assessment').click()
        return wait_item(item['id'],lambda saved:saved['answers']==values)
    def generate():
        page.locator('#generate-report').click()
        expect(page.locator('#report-view')).to_be_visible(timeout=15000)
        expect(page.locator('#busy-overlay')).to_be_hidden()

    assert page.locator('#checklist input').count()==0
    assert page.locator('#side-categories').count()==0
    assert page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)")>=16
    checks.append('assessment-first home and enlarged default text')
    assert page.locator('.graph-node').count()>=1
    assert 'NaN' not in page.locator('#dashboard-view').inner_text()
    checks.append('dashboard chart and zero state')
    library()
    page.locator('#new-folder').click()
    page.locator('#folder-name').fill('Capstone plans '+suffix)
    page.locator('#folder-submit').click()
    expect(page.locator('#folder-dialog')).not_to_be_visible()
    folder=next(folder for folder in get('/api/workspace')['folders'] if folder['name']=='Capstone plans '+suffix)
    full=create('Company website '+suffix,folder=folder['id'])
    assert len(scope_questions(full['scope']))==39
    assert page.locator('#category-tabs [role=tab]').count()==5
    page.locator('#checklist input[name=storage_files][value=yes]').check()
    page.locator('#save-assessment').click()
    page.locator('nav [data-view=planning]').click()
    page.locator('#notes-assessment').select_option(full['id'])
    expect(page.locator('#notes-panel')).to_be_visible()
    page.locator('#planning-notes').fill('Confirm object access boundaries before launch.')
    page.locator('#plan-status').select_option('in_progress')
    page.locator('#save-notes').click()
    wait_item(full['id'],lambda item:item['answers'].get('storage_files')=='yes' and item['notes'].startswith('Confirm object') and item['plan_status']=='in_progress')
    checks.append('folder creation and independent full assessment draft')
    page.reload(wait_until='networkidle')
    open_item(full['id'])
    expect(page.locator('#checklist input[name=storage_files][value=yes]')).to_be_checked()
    assert get('/api/assessments/'+full['id'])['notes']=='Confirm object access boundaries before launch.'
    checks.append('durable draft and planning notes after reload')
    qs=scope_questions(full['scope'])
    yes={'storage_files','storage_static','network_domain','network_delivery','security_access','security_tls'}
    values={q['id']:('yes' if q['id'] in yes else 'no') if q['kind']=='requirement' else ('yes' if q['category'] in {'storage','network','security'} else 'not_applicable') for q in qs}
    full=answer(full,values)
    expect(page.locator('#progress-percent')).to_have_text('100%')
    checks.append('full checklist scoped completion percentage')
    generate()
    assert page.locator('.service-report').count()==5
    assert page.locator('#report-content .donut-chart').count()==0
    assert page.locator('#report-content .category-chart').count()==0
    assert 'recommended action' in page.locator('#report-content').inner_text().lower()
    page.screenshot(path=str(root/'advisory-desktop.png'))
    with page.expect_download() as download:page.locator('#export-md').click()
    download.value.save_as(str(root/'sample-advisory.md'))
    with page.expect_download() as download:page.locator('#export-txt').click()
    download.value.save_as(str(root/'sample-advisory.txt'))
    assert 'Amazon S3' in (root/'sample-advisory.md').read_text()
    checks.append('report charts and both export downloads')
    full=get('/api/assessments/'+full['id']);first_handle=full['latest_report_id']
    page.locator('#edit-assessment').click()
    page.locator('#begin-edit').click()
    page.get_by_role('tab',name='Storage',exact=True).click()
    page.locator('#checklist input[name=storage_iam][value=no]').check()
    page.locator('#save-assessment').click()
    wait_item(full['id'],lambda item:item['answers']['storage_iam']=='no')
    generate()
    full=wait_item(full['id'],lambda item:item['report_count']==2)
    assert get('/api/reports/'+first_handle)['report']['answers']['storage_iam']=='yes'
    assert get('/api/reports/'+full['latest_report_id'])['report']['answers']['storage_iam']=='no'
    page.locator('#edit-assessment').click()
    page.locator(f'#report-history [data-report-id="{first_handle}"]').click()
    expect(page.locator('#report-view')).to_be_visible()
    checks.append('immutable multi-version report history and review')
    page.locator('nav [data-view=dashboard]').click()
    partial=create('Storage and database '+suffix,'partial',categories=('storage','database'))
    assert len(scope_questions(partial['scope']))==15
    assert page.locator('#category-tabs [role=tab]').count()==2
    assert page.locator('#category-tabs').get_by_role('tab',name='Compute',exact=True).count()==0
    values={q['id']:'yes' for q in scope_questions(partial['scope'])}
    partial=answer(partial,values);generate()
    report=get('/api/assessments/'+partial['id'])['latest_report']
    assert set(report['assessed_categories'])=={'storage','database'}
    assert set(s['service_id'] for s in report['recommendations'])=={'s3','rds','dynamodb'}
    checks.append('partial category selection and excluded coverage semantics')
    page.locator('nav [data-view=dashboard]').click()
    single=create('S3 focused review '+suffix,'single',service='s3')
    assert len(scope_questions(single['scope']))==8
    assert page.locator('#category-tabs [role=tab]').count()==1
    values={q['id']:('yes' if q['kind']=='requirement' else {'Identity and Access Management':'yes','Network Security':'no','Data Security':'yes','Monitoring':'not_sure','Configuration':'not_applicable'}[q['dimension']]) for q in scope_questions(single['scope'])}
    single=answer(single,values);generate()
    report=get('/api/assessments/'+single['id'])['latest_report']
    assert [s['service_id'] for s in report['recommendations']]==['s3']
    counts={status:sum(r['status']==status for s in report['recommendations'] for r in s['risks']) for status in ['planned','gap','unresolved']}
    assert counts=={'planned':2,'gap':1,'unresolved':2}
    text=page.locator('#report-content').inner_text()
    assert 'control gap' in text.lower() and 'unresolved' in text.lower()
    checks.append('single-service charts match 40/20/40 percent inputs')
    library();assert page.locator('#library-list [data-open-assessment]').count()>=3
    page.locator('#assessment-search').fill('S3 focused review '+suffix)
    assert page.locator('#library-list [data-open-assessment]').count()==1
    page.locator('#assessment-search').fill('')
    checks.append('multiple independent assessments and searchable library')
    page.locator('nav [data-view=planning]').click()
    assert 'Company website '+suffix in page.locator('#notes-assessment').inner_text()
    checks.append('planning board reads saved assessment stages')
    page.locator('nav [data-view=dashboard]').click()
    page.mouse.move(0,0);page.screenshot(path=str(root/'dashboard-desktop.png'))
    open_item(single['id'])
    page.get_by_role('tab',name='Storage',exact=True).click()
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('.question legend')).fontSize)")>=16
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('.answer-option')).fontSize)")>=14
    page.screenshot(path=str(root/'assessment-desktop.png'))
    checks.append('desktop checklist font sizes and readable controls')
    page.locator('#project-title').fill('<img src=x onerror=alert(1)>')
    page.locator('#save-assessment').click()
    wait_item(single['id'],lambda item:item['title']=='<img src=x onerror=alert(1)>')
    generate()
    assert page.locator('#report-content img').count()==0
    assert '<img src=x onerror=alert(1)>' in page.locator('#report-content').inner_text()
    page.locator('#edit-assessment').click()
    page.locator('#begin-edit').click()
    page.locator('#project-title').fill('S3 focused review '+suffix)
    page.locator('#save-assessment').click();wait_item(single['id'],lambda item:item['title']=='S3 focused review '+suffix)
    checks.append('stored titles rendered as inert text')
    # Simulate a second window updating this record while the current editor is open.
    remote=get('/api/assessments/'+single['id'])
    response=page.request.put(base+'/api/assessments/'+single['id'],headers={'X-Advisory-Token':token},data={
        'title':remote['title'],'answers':remote['answers'],'notes':'Saved from another window',
        'plan_status':remote['plan_status'],'revision':remote['revision'],'selected_category':remote['selected_category']})
    assert response.ok,response.text()
    page.locator('#project-title').fill('Unsaved local title after external edit')
    page.locator('#save-assessment').click()
    expect(page.locator('#reload-saved')).to_be_visible()
    page.once('dialog',lambda dialog:dialog.accept())
    page.locator('#reload-saved').click()
    expect(page.locator('#project-title')).to_have_value(remote['title'])
    checks.append('cross-window conflict retains data and offers confirmed reload recovery')
    page.locator('.sidebar .settings-trigger').click()
    page.locator('#provider-select').select_option('groq')
    page.locator('#api-key').fill('session-only-test-key')
    page.locator('#settings-form button[type=submit]').click()
    assert 'session-only-test-key' not in page.evaluate('JSON.stringify({...localStorage})')
    assert 'session-only-test-key' not in json.dumps(get('/api/assessments/'+single['id']))
    page.reload(wait_until='networkidle');open_item(single['id'])
    expect(page.locator('#provider-name')).to_have_text('Offline mode')
    checks.append('API key is absent from persistent assessment and local storage')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.mouse.move(0,0);page.screenshot(path=str(root/'assessment-mobile.png'))
    brand=page.locator('.brand').bounding_box();navigation=page.get_by_role('navigation',name='Main navigation').bounding_box()
    assert brand and navigation
    assert navigation['y']>=brand['y']+brand['height'] or navigation['x']>=brand['x']+brand['width']
    checks.append('390px mobile layout has no overflow or overlapping navigation')
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
    page.reload(wait_until='networkidle');open_item(single['id']);generate()
    checks.append('offline assessment save and report generation with remote network blocked')
    assert not errors,errors
    result={'status':'passed','javascript_errors':errors,'checks':checks,'assessment_ids':[full['id'],partial['id'],single['id']]}
    (root/'browser-results.json').write_text(json.dumps(result,indent=2))
    browser.close()
    print(f'{len(checks)} browser integration checks passed; artifacts in {root}')
