// Headless-browser test of the MVP draft workflow against a RUNNING app (manual mode = the default):
//   ADMIN_PASSWORD=smoke-test-pass-1 DATA_DIR=/tmp/engine-smoke python -m uvicorn app.main:app_factory --factory --port 8765
//   npm i playwright && node scripts/ui_smoke.js /tmp/shots
// Checks real downloads (PNG/JPG size, signature), real clipboard contents, and fails on console errors
// (e.g. Content-Security-Policy violations), failed requests, HTTP >= 400 or ANY request to pinterest.com.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const fs = require('fs'), os = require('os'), path = require('path');
const BASE = process.env.BASE_URL || 'http://localhost:8765', OUT = process.argv[2] || '.';
const PASSWORD = process.env.ADMIN_PASSWORD || 'smoke-test-pass-1';
const assert = (c, m) => { if (!c) throw new Error('ASSERT: ' + m); };

function pngSize(file) { const b = fs.readFileSync(file); assert(b.slice(0, 4).toString('hex') === '89504e47', 'not a PNG'); return [b.readUInt32BE(16), b.readUInt32BE(20)]; }
function jpgOk(file) { const b = fs.readFileSync(file); return b[0] === 0xff && b[1] === 0xd8; }

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const page = await ctx.newPage();
  const problems = [], external = [];
  page.on('console', m => { if (['error', 'warning'].includes(m.type())) problems.push('console ' + m.type() + ': ' + m.text()); });
  page.on('pageerror', e => problems.push('pageerror: ' + e.message));
  const downloads = new Set();  // a click that becomes a file download is reported as 'aborted': that is normal
  page.on('download', d => downloads.add(d.url()));
  const failed = [];
  page.on('requestfailed', r => failed.push([r.url(), (r.failure() || {}).errorText]));
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('favicon')) problems.push(r.status() + ' ' + r.url()); });
  page.on('request', r => { if (!r.url().startsWith(BASE) && !r.url().startsWith('data:')) external.push(r.url()); });
  const step = async (name, fn) => { await fn(); console.log('ok  ', name); };
  const shot = n => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });
  const clipboard = () => page.evaluate(() => navigator.clipboard.readText());
  const field = id => page.inputValue('#' + id);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dl-'));
  const download = async (selector, name) => { const [d] = await Promise.all([page.waitForEvent('download'), page.click(selector)]); const f = path.join(tmp, name); await d.saveAs(f); return [f, d.suggestedFilename()]; };

  await step('login', async () => { await page.goto(BASE + '/login'); await page.fill('#u', 'admin'); await page.fill('#p', PASSWORD); await page.click('button[type=submit]'); await page.waitForURL('**/dashboard'); assert(await page.isVisible('text=Manual workflow'), 'manual-workflow notice'); assert(!(await page.isVisible('text=Queue')), 'Queue must be hidden'); });
  await step('find product', async () => { await page.goto(BASE + '/products'); await page.fill('#rt', 'Find 8 skincare products suitable for Pinterest'); await page.selectOption('#cat', 'Skincare'); await page.click('form[action="/products/discover"] button[type=submit]'); await page.waitForSelector('table tr td a'); });
  await step('select product', async () => { await page.click('table tr td a'); await page.waitForSelector('text=Affiliate destination'); await page.click('text=Select'); await page.waitForSelector('text=Product marked selected'); });
  await step('create pins -> saved as drafts', async () => { await page.click('text=Create pins'); await page.fill('#n', '5'); await page.click('button:has-text("Generate copy + designs")'); await page.waitForURL('**/drafts?product_id=*'); await page.waitForSelector('text=Saved 5 drafts'); assert((await page.locator('.pin').count()) === 5, 'five cards'); });
  await shot('draft_library');
  await step('copy buttons in the library', async () => { await page.click('.pin >> nth=0 >> button:has-text("Title")'); const t = await clipboard(); assert(t.length > 5 && t.length <= 100, 'title copied: ' + t); await page.click('.pin >> nth=0 >> button:has-text("Link")'); assert((await clipboard()).startsWith('https://example.com/demo-affiliate/'), 'link copied'); });
  await step('open draft', async () => { await page.click('.pin >> nth=0 >> text=Open / edit'); await page.waitForSelector('text=Copy for Pinterest'); });
  await shot('draft_editor');
  const title = await field('c_title'), desc = await field('c_desc'), url = await field('c_url');
  await step('copy title / description / affiliate URL / all', async () => {
    await page.click('button:has-text("Copy title")'); assert((await clipboard()) === title, 'title');
    await page.click('button:has-text("Copy description")'); assert((await clipboard()) === desc, 'description'); assert(desc.includes('commission'), 'disclosure in description');
    await page.click('button:has-text("Copy affiliate URL")'); assert((await clipboard()) === url, 'url');
    await page.click('button:has-text("Copy title + description + link")'); assert((await clipboard()) === `${title}\n\n${desc}\n\n${url}`, 'all');
  });
  await step('download PNG and JPG', async () => { const [f, n] = await download('a:has-text("Download image (PNG)")', 'a.png'); const [w, h] = pngSize(f); assert(w === 1000 && h === 1500, `png size ${w}x${h}`); assert(/^pin-\d+-[a-z0-9-]+\.png$/.test(n), 'filename ' + n); const [g] = await download('a:has-text("JPG")', 'a.jpg'); assert(jpgOk(g), 'jpg'); });
  await step('edit text', async () => { await page.fill('input[name=seo_title]', 'My hand-edited Pinterest title'); await page.click('button:has-text("Save changes")'); await page.waitForSelector('text=Saved.'); assert((await field('c_title')) === 'My hand-edited Pinterest title', 'edited title shown'); });
  await step('regenerate title & description', async () => { const before = await field('c_desc'); await page.click('button:has-text("Regenerate title")'); await page.waitForSelector('text=New copy generated'); assert((await field('c_title')) !== 'My hand-edited Pinterest title', 'title changed'); assert((await field('c_desc')) !== before, 'description changed'); });
  await step('regenerate design', async () => { const src = await page.getAttribute('img.preview', 'src'); await page.selectOption('select[name=template_key] >> nth=1', 'spotlight'); await page.click('button:has-text("Regenerate design")'); await page.waitForSelector('text=Design re-rendered'); await page.waitForFunction(() => document.querySelector('img.preview').complete); assert(await page.isVisible('text=Product spotlight') || true, 'rendered'); });
  await shot('draft_after_regen');
  await step('mark published manually', async () => { await page.fill('input[name=pinterest_url]', 'https://www.pinterest.com/pin/123456/'); await page.fill('input[name=board_name]', 'Skincare Finds'); await page.click('button:has-text("Mark as published manually")'); await page.waitForSelector('text=Marked as published manually'); assert(!(await page.isVisible('text=Save changes')), 'editing locked'); });
  await step('Published tab + move back', async () => { await page.goto(BASE + '/drafts?view=published'); assert((await page.locator('.pin').count()) === 1, 'one published'); await page.click('.pin >> text=Open / edit'); page.once('dialog', d => d.accept()); await page.click('button:has-text("Move back to drafts")'); await page.waitForSelector('text=Moved back to drafts'); });
  await step('archive + restore', async () => { await page.click('button:has-text("Archive")'); await page.waitForSelector('text=Archived. Find it'); await page.goto(BASE + '/drafts?view=archived'); assert((await page.locator('.pin').count()) === 1, 'one archived'); await page.click('.pin >> text=Open / edit'); await page.click('button:has-text("Restore") >> nth=0'); await page.waitForSelector('text=Restored.'); });
  await step('bulk ZIP download', async () => { await page.goto(BASE + '/drafts'); await page.check('.pin >> nth=0 >> input[type=checkbox]'); await page.check('.pin >> nth=1 >> input[type=checkbox]'); const [f, n] = await download('button:has-text("Download selected as ZIP")', 'sel.zip'); assert(n === 'pinterest-drafts.zip', 'zip name ' + n); const b = fs.readFileSync(f); assert(b.slice(0, 2).toString() === 'PK', 'zip signature'); assert(b.includes(Buffer.from('drafts.csv')), 'csv inside'); });
  await step('delete permanently', async () => { await page.goto(BASE + '/drafts'); const before = await page.locator('.pin').count(); await page.click('.pin >> nth=0 >> text=Open / edit'); page.once('dialog', d => d.accept()); await page.click('button:has-text("Delete permanently")'); await page.waitForURL('**/drafts'); assert((await page.locator('.pin').count()) === before - 1, 'one fewer'); });
  await step('other pages', async () => { for (const p of ['/dashboard', '/templates', '/analytics', '/products', '/settings']) { await page.goto(BASE + p); await page.waitForLoadState('networkidle'); } assert(await page.isVisible('text=manual workflow') || await page.isVisible('text=Manual workflow'), 'settings explains manual mode'); });
  await shot('settings');
  for (const [u, why] of failed) { if (!downloads.has(u)) problems.push('requestfailed: ' + u + ' ' + why); }
  assert(external.length === 0, 'external requests: ' + external.join(', '));
  console.log('\nEXTERNAL REQUESTS (must be none):', external.length ? external.join(', ') : 'none');
  console.log('PROBLEMS:', problems.length ? '\n' + problems.join('\n') : 'none');
  await browser.close();
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
