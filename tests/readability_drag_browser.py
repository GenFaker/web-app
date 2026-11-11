"""Floating dimension cards and zoom-aware mouse/touch/keyboard node interaction."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('BALLAN_TEST_URL', 'http://127.0.0.1:8769')
with sync_playwright() as engine:
    browser = engine.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1500,'height':1100}, has_touch=True)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    expect(page).to_have_title('AWS BALLAN ADVISORY')
    expect(page.locator('.brand')).to_contain_text('AWS BALLAN')
    page.locator('#graph-filter').select_option('compute')
    page.locator('#graph-zoom-in').click()
    node = page.locator('[data-service=ec2]')
    node.scroll_into_view_if_needed()
    position = lambda: node.evaluate('e => ({x:parseFloat(e.style.left),y:parseFloat(e.style.top)})')
    original = position()
    box = node.bounding_box()
    x,y = box['x']+box['width']/2, box['y']+20
    page.mouse.move(x,y)
    page.mouse.down()
    page.mouse.move(x+95,y+70,steps=12)
    page.mouse.up()
    moved = position()
    assert moved['x'] > original['x']+60 and moved['y'] > original['y']+40
    edge = page.locator('[data-edge=ec2]')
    assert abs(float(edge.get_attribute('x2')) - moved['x']) < .001
    assert abs(float(edge.get_attribute('y2')) - moved['y']) < .001
    page.locator('#graph-filter').select_option('all')
    assert position() == moved
    node.focus()
    page.keyboard.press('ArrowLeft')
    assert position()['x'] == moved['x']-15
    page.keyboard.press('Enter')
    expect(page.locator('#network-detail')).to_contain_text('Amazon EC2')
    # Touch events exercise the same real pointer-capture path as a mobile drag.
    before_touch = position()
    box = node.bounding_box()
    x,y = box['x']+box['width']/2,box['y']+20
    cdp = page.context.new_cdp_session(page)
    cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
    cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':x-60,'y':y+45}]})
    cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
    assert position()['x'] < before_touch['x']-30
    assert position()['y'] > before_touch['y']+20
    page.locator('#graph-filter').select_option('compute')
    page.locator('#graph-reset').click()
    assert position() == original
    node.click()
    expect(page.locator('#network-detail')).to_contain_text('Amazon EC2')
    token = page.locator('meta[name=advisory-token]').get_attribute('content')
    headers = {'X-Advisory-Token':token}
    folder = page.request.post(BASE+'/api/folders',headers=headers,data={'name':'Readability checks'}).json()
    record = page.request.post(BASE+'/api/assessments',headers=headers,data={'title':'Readable security assessment','folder_id':folder['id'],'scope':{'mode':'single','service_id':'s3'}}).json()
    page.reload()
    page.locator('nav [data-view=library]').click()
    page.locator(f'[data-open-assessment="{record["id"]}"]').click()
    page.locator('#begin-edit').click()
    expect(page.locator('.dimension-card')).to_have_count(6)
    headings = page.locator('.dimension-heading h3').all_text_contents()
    assert 'Identity and Access Management' in headings and 'Data Security' in headings
    page.locator('#checklist input[name=storage_iam][value=yes]').check()
    card = page.locator('.dimension-card').filter(has=page.locator('input[name=storage_iam]'))
    expect(card.locator('.dimension-progress')).to_have_text('1 / 1 answered')
    page.locator('#save-assessment').click()
    expect(page.locator('#save-state')).to_contain_text('All changes saved')
    Path('work/browser-checks').mkdir(parents=True,exist_ok=True)
    for theme in ['dark','light']:
        if page.locator('html').get_attribute('data-theme')!=theme: page.locator('#theme-toggle').click()
        for width in [1500,390]:
            page.set_viewport_size({'width':width,'height':1100})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.dimension-heading h3').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)') >= 22
            cards = page.locator('.dimension-card').all()
            first,second = cards[0].bounding_box(),cards[1].bounding_box()
            assert second['y']-(first['y']+first['height']) >= 19
            page.screenshot(path=f'work/browser-checks/ballan-assessment-{theme}-{width}.png',full_page=True)
    assert not errors,errors
    browser.close()
    print('Brand, separated dimension cards, prominent headings, answers, mouse/touch/keyboard dragging, connected edges, filter persistence, reset and responsive themes passed.')
