"""Only localhost: deterministic rendering, responsive images and reader controls."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
from playwright.sync_api import sync_playwright
from oursteps.parser import soup_of
pages=[p for p in (ROOT/'data/preview').glob('*.html') if p.stem.isdigit() and soup_of(p.read_bytes()).select('.reader-body img')]
with sync_playwright() as p:
 b=p.chromium.launch(channel='chrome',headless=True);c=b.new_context(viewport={'width':1440,'height':1000});page=c.new_page();errors=[]
 page.on('pageerror',lambda e:errors.append(type(e).__name__))
 page.goto('http://localhost:8080');row_height=page.locator('.card').first.evaluate('(e)=>e.getBoundingClientRect().height');assert row_height<=43
 total=0
 for file in pages:
  page.goto('http://localhost:8080/'+file.name)
  imgs=page.locator('.reader-body img');imgs.evaluate_all('(nodes)=>nodes.forEach(n=>n.loading="eager")')
  loaded=imgs.evaluate_all('async (nodes)=>{await Promise.all(nodes.map(n=>n.decode().catch(()=>{})));return nodes.every(n=>n.complete && n.naturalWidth>0)}')
  assert loaded, file.name+' image decode failed'
  total+=imgs.count()
  assert imgs.evaluate_all('(nodes)=>nodes.every(n=>n.getBoundingClientRect().width<=n.parentElement.getBoundingClientRect().width+1)')
 assert pages
 assert page.locator('.reader-body').first.evaluate('(n)=>getComputedStyle(n).fontSize')=='18px'
 page.locator('[data-font="up"]').click();page.reload()
 assert page.locator('.reader-body').first.evaluate('(n)=>getComputedStyle(n).fontSize')=='20px'
 page.locator('[data-font="reset"]').click()
 page.set_viewport_size({'width':390,'height':844});page.reload()
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 assert not errors,errors
 result=dict(ok=True,image_articles=len(pages),loaded_images=total,default_font='18px',persisted_font='20px',homepage_row_height=row_height,mobile_overflow=False)
 (ROOT/'data/reader-browser-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
 b.close()
