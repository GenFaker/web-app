"""UI regression checks against an isolated local preview (port 8766)."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8767'
with sync_playwright() as engine:
    browser = engine.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1500, 'height': 1100})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    expect(page.locator('.graph-node')).to_have_count(11)
    token = page.locator('meta[name=advisory-token]').get_attribute('content')
    headers = {'X-Advisory-Token': token}
    kb = page.request.get(BASE + '/api/knowledge', headers=headers).json()
    folder = page.request.post(BASE + '/api/folders', headers=headers, data={'name':'Security checks'}).json()
    created = page.request.post(BASE + '/api/assessments', headers=headers, data={
        'title': 'Security map verification', 'folder_id': folder['id'], 'scope': {'mode': 'full'}}).json()
    answers = {q['id']: 'yes' if q['kind'] == 'requirement' else 'no' for q in kb['questions']}
    saved = page.request.put(BASE + '/api/assessments/' + created['id'], headers=headers, data={
        'title': created['title'], 'answers': answers, 'revision': created['revision']})
    assert saved.ok, saved.text()
    created = saved.json()
    response = page.request.post(BASE + '/api/evaluate', headers=headers, data={
        'title': created['title'], 'answers': answers, 'scope': created['scope'],
        'assessment_id': created['id'], 'revision': created['revision']})
    assert response.ok, response.text()
    page.reload()
    page.locator('#security-assessment').select_option(created['id'])
    expect(page.locator('#security-source-note')).to_contain_text('Saved advisory')
    expect(page.locator('#dashboard-stats')).to_contain_text('Control gaps')
    assert page.locator('.graph-node.gap').count() > 0
    page.locator('#graph-filter').select_option('compute')
    expect(page.locator('[data-service]')).to_have_count(2)
    page.locator('[data-service=ec2]').click()
    expect(page.locator('#network-detail')).to_contain_text('Assessment findings')
    assert page.locator('#network-detail a').first.get_attribute('href').startswith('https://docs.aws.amazon.com/')
    page.locator('#graph-zoom-in').click()
    page.locator('#graph-reset').click()
    for theme in ('dark', 'light'):
        if page.locator('html').get_attribute('data-theme') != theme:
            page.locator('#theme-toggle').click()
        for width in (1500, 390):
            page.set_viewport_size({'width': width, 'height': 1100})
            small = page.evaluate('''() => [...document.querySelectorAll('body *')].filter(e => e.getClientRects().length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 13).map(e => ({tag:e.tagName, cls:e.className, size:getComputedStyle(e).fontSize}))''')
            assert not small, small
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflows horizontally'
            Path('work/browser-checks').mkdir(parents=True, exist_ok=True)
            page.screenshot(path=f'work/browser-checks/security-{theme}-{width}.png', full_page=True)
    page.reload()
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    page.set_viewport_size({'width': 1500, 'height': 1100})
    page.locator('.sidebar .settings-trigger').click()
    for provider in ('openai', 'claude'):
        page.locator('#provider-select').select_option(provider)
        expect(page.locator('#api-key')).to_be_visible()
        assert page.locator('#model-name').get_attribute('placeholder')
    page.locator('[data-close=settings-dialog]').first.click()
    page.locator('[data-graph-menu]').click()
    page.locator('#network-detail [data-view=library]').click()
    expect(page.locator('#library-view')).to_be_visible()
    assert not errors, errors
    print(json.dumps({'status': 'passed', 'checks': ['findings', 'service filters', 'official guides', 'graph navigation', 'themes persisted', '13px minimum', 'mobile overflow', 'provider settings'], 'errors': errors}))
    browser.close()

