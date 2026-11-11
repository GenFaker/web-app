"""Web-only folder management and command-console browser regression checks."""
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8767'
with sync_playwright() as engine:
    browser = engine.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1600,'height':1100})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    expect(page.locator('.graph-node')).to_have_count(11)
    headers = {'X-Advisory-Token':page.locator('meta[name=advisory-token]').get_attribute('content')}
    suffix = uuid4().hex[:6]
    def post(path, data):
        response = page.request.post(BASE+path,headers=headers,data=data)
        assert response.ok, response.text()
        return response.json()
    source = post('/api/folders',{'name':'Web source '+suffix})
    target = post('/api/folders',{'name':'Web destination '+suffix})
    record = post('/api/assessments',{'title':'Architecture review '+suffix,'folder_id':source['id'],'scope':{'mode':'full'}})
    knowledge = page.request.get(BASE+'/api/knowledge',headers=headers).json()
    answers = {q['id']:'yes' if q['kind']=='requirement' else 'no' for q in knowledge['questions']}
    response = page.request.put(BASE+'/api/assessments/'+record['id'],headers=headers,data={'title':record['title'],'revision':record['revision'],'answers':answers})
    assert response.ok
    record = response.json()
    report = post('/api/evaluate',{'assessment_id':record['id'],'title':record['title'],'revision':record['revision'],'answers':answers,'scope':record['scope']})
    page.reload()
    page.locator('#security-assessment').select_option(record['id'])
    expect(page.locator('#security-source-note')).to_contain_text('Saved advisory')
    page.locator('#graph-search').fill('Lambda')
    expect(page.locator('[data-service]')).to_have_count(1)
    page.locator('[data-service=lambda]').click()
    expect(page.locator('#network-detail')).to_contain_text('AWS Lambda')
    page.locator('#graph-search').fill('')
    page.locator('#graph-expand').click()
    expect(page.locator('.expanded-map')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('.expanded-map')).to_have_count(0)
    page.locator('#graph-motion').click()
    expect(page.locator('#network-canvas')).to_have_class('motion-paused')
    Path('work/browser-checks').mkdir(parents=True,exist_ok=True)
    page.screenshot(path='work/browser-checks/web-command-center.png',full_page=True)
    page.locator('nav [data-view=library]').click()
    page.locator('#folder-search').fill(suffix)
    expect(page.locator('.folder-card')).to_have_count(2)
    page.locator(f'[data-rename-folder="{source["id"]}"]').click()
    page.locator('#folder-new-name').fill('')
    page.locator('#folder-manage-submit').click()
    expect(page.locator('#folder-manage-dialog .dialog-error')).to_contain_text('Enter a folder name')
    page.locator('#folder-new-name').fill('Renamed '+suffix)
    page.locator('#folder-manage-submit').click()
    expect(page.locator('#folder-manage-dialog')).not_to_be_visible()
    expect(page.locator('#folder-list')).to_contain_text('Renamed '+suffix)
    page.locator(f'[data-folder-id="{source["id"]}"]').click()
    page.locator('#library-layout').click()
    expect(page.locator('#library-list')).to_have_class('assessment-grid list-layout')
    page.locator(f'[data-open-assessment="{record["id"]}"]').click()
    expect(page.locator('#review-view')).to_be_visible()
    page.locator('#record-folder').select_option(target['id'])
    page.locator('#move-record').click()
    expect(page.locator('#review-view')).to_contain_text(target['name'])
    page.locator('nav [data-view=library]').click()
    page.locator(f'[data-delete-folder="{target["id"]}"]').click()
    page.locator('#folder-manage-submit').click()
    expect(page.locator('#folder-manage-dialog .dialog-error')).to_contain_text('Choose a destination')
    page.locator('#folder-destination').select_option(source['id'])
    page.locator('#folder-manage-submit').click()
    expect(page.locator('#folder-manage-dialog')).not_to_be_visible()
    expect(page.locator(f'[data-folder-id="{target["id"]}"]')).to_have_count(0)
    restored = page.request.get(BASE+'/api/assessments/'+record['id'],headers=headers).json()
    assert restored['folder_id'] == source['id']
    assert restored['latest_report_id'] == report['handle']
    assert page.request.get(BASE+'/api/reports/'+report['handle'],headers=headers).ok
    page.locator('#folder-search').fill('')
    page.locator('#library-all-folders').click() if page.locator('#library-all-folders').is_visible() else None
    for theme in ['dark','light']:
        if page.locator('html').get_attribute('data-theme')!=theme:
            page.locator('#theme-toggle').click()
        for width in [1600, 390]:
            page.set_viewport_size({'width':width,'height':1050})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {theme} {width}'
            page.screenshot(path=f'work/browser-checks/web-library-{theme}-{width}.png',full_page=True)
    assert not errors, errors
    print('Web console, service search, expanded map, folder search/rename/delete, assessment move, history preservation, list view, and both responsive themes passed.')
    browser.close()
