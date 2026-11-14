from pathlib import Path
from playwright.sync_api import sync_playwright, expect
BASE='http://127.0.0.1:8771'
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1000})
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(BASE)
 expect(page.locator('#dashboard-view')).to_be_visible()
 assert page.locator('nav [data-view="dashboard"]').count()==0
 page.locator('.settings-trigger').click()
 assert page.locator('#provider-select option').evaluate_all('(els)=>els.map(e=>e.value)')==['mock','claude','groq']
 page.locator('#provider-select').select_option('groq')
 page.locator('#test-provider').click()
 expect(page.locator('#provider-test-status')).to_contain_text('API key is required')
 page.locator('#api-key').fill('test-session-key')
 page.locator('#settings-form button[type=submit]').click()
 page.locator('nav [data-view=library]').click()
 page.locator('.brand').click()
 expect(page.locator('#dashboard-view')).to_be_visible()
 page.locator('.settings-trigger').click()
 expect(page.locator('#api-key')).to_have_value('test-session-key')
 page.locator('[data-close=settings-dialog]').first.click()
 for theme in ['dark','light']:
  if page.locator('html').get_attribute('data-theme')!=theme:page.locator('#theme-toggle').click()
  for width in [1440,390]:
   page.set_viewport_size({'width':width,'height':1000})
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
   page.screenshot(path=f'work/browser-checks/matte-{theme}-{width}.png',full_page=True)
 assert not errors, errors
 browser.close()
 print('Provider settings, home navigation, session key retention, themes, and mobile layout passed.')
