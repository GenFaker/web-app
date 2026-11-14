"""Report disclosure, collapsed reading layout, and matte theme regressions."""
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True)
 page=browser.new_page(viewport={'width':1500,'height':1000})
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8771')
 page.evaluate('''async () => {
 const answers=Object.fromEntries(state.kb.questions.map(q=>[q.id,q.kind==='requirement'?'yes':'no']));
 const response=await jsonApi('/api/evaluate',{method:'POST',body:JSON.stringify({title:'Architecture security review',answers,settings:{provider:'mock'}})});
 state.report=response.report;state.reportId=response.handle;renderReport();setView('report');
 }''')
 expect(page.locator('.expert-review-notice')).to_contain_text('offline explanations')
 assert page.locator('.service-report').count()>1
 assert page.locator('.service-report[open],.risk-card[open],.limitations[open]').count()==0
 # Synthetic success snapshot tests the disclosure for saved Claude results; no remote API call.
 page.evaluate("state.report.provider_used='claude';renderReport()")
 expect(page.locator('.expert-review-notice')).to_contain_text('Claude API succeeded for this saved advisory')
 expect(page.locator('.expert-review-notice')).to_contain_text('Not reviewed by a cybersecurity expert')
 expect(page.locator('.service-body').first).to_be_visible()
 cards=page.locator('.service-report').first.locator('.risk-card')
 assert cards.count()>=2
 boxes=cards.evaluate_all('(es)=>es.slice(0,2).map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,width:e.getBoundingClientRect().width}))')
 assert boxes[1]['x']>boxes[0]['x']+boxes[0]['width']
 assert boxes[0]['y']==boxes[1]['y']
 expect(cards.nth(0)).to_contain_text('Identity and Access Management')
 expect(cards.nth(1)).to_contain_text('Network Security')
 heading_size=page.locator('#report-view .page-heading h1').evaluate('e=>getComputedStyle(e).fontSize')

 expect(page.locator('.ai-note').first).to_contain_text('NOT REVIEWED BY AN EXPERT')
 page.locator('.risk-topline').first.click()
 expect(page.locator('.risk-body').first).to_be_visible()
 assert page.locator('.risk-card[open]').count()==1
 for theme in ['light','dark']:
  if page.locator('html').get_attribute('data-theme')!=theme:page.locator('#theme-toggle').click()
  for width in [1500,1024,800,390]:
   page.set_viewport_size({'width':width,'height':1000})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   if width==390:
    assert page.locator('.risk-dimension-grid').first.evaluate('e=>getComputedStyle(e).gridTemplateColumns.split(" ").length')==1
   assert page.locator('#report-view .page-heading h1').evaluate('e=>getComputedStyle(e).fontSize')==heading_size
   assert page.locator('.risk-body').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')==16
   page.evaluate('window.scrollTo(0,0)')
   page.screenshot(path=f'work/browser-checks/advisory-matte-{theme}-{width}.png',full_page=True)
 assert not errors,errors
 browser.close()
 print('Expert disclosure, collapsed cards, keyboard expansion, side-by-side risk dimensions, single-column mobile, stable text sizes, and both themes passed.')

