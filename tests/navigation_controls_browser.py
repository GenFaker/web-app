from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
 b=p.chromium.launch(channel='chrome',headless=True)
 page=b.new_page(viewport={'width':1440,'height':1000})
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8771')
 assert page.locator('[data-view=methodology],#methodology-view').count()==0
 expect(page.locator('#graph-zoom-out')).to_be_disabled()
 title=page.locator('#dashboard-view h1').evaluate('e=>getComputedStyle(e).fontSize')
 page.locator('#graph-zoom-in').click()
 page.locator('#graph-zoom-out').click()
 expect(page.locator('#graph-zoom-out')).to_be_disabled()
 assert title==page.locator('#dashboard-view h1').evaluate('e=>getComputedStyle(e).fontSize')
 page.evaluate('''async()=>{
 const folder=await jsonApi('/api/folders',{method:'POST',body:JSON.stringify({name:'Navigation test'})});
 const record=await jsonApi('/api/assessments',{method:'POST',body:JSON.stringify({title:'Navigation test',folder_id:folder.id,scope:{mode:'single',service_id:'s3'}})});
 await loadWorkspace();await openAssessment(record.id);
 }''')
 page.locator('#begin-edit').click()
 expect(page.locator('#editor-snapshots')).to_be_visible()
 page.locator('#reading-size').select_option('1.5')
 assert page.locator('.question p').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')==24
 page.locator('#editor-snapshots').click()
 expect(page.locator('#review-view')).to_be_visible()
 expect(page.locator('#report-history')).to_be_visible()
 for width in [390,800,2200]:
  page.set_viewport_size({'width':width,'height':1000})
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 assert not errors,errors
 b.close()
 print('Methodology removed; map-only zoom limit, readable text sizing, and saved snapshot navigation passed.')
