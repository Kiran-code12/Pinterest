// Headless-browser smoke test of the whole journey against a RUNNING app started with PINTEREST_PROVIDER=mock:
//   PINTEREST_PROVIDER=mock ADMIN_PASSWORD=smoke-test-pass-1 DATA_DIR=/tmp/engine-smoke \
//     python -m uvicorn app.main:app_factory --factory --port 8765
//   npm i playwright && node scripts/ui_smoke.js /tmp/shots
// Fails on console errors (e.g. CSP violations), failed requests or HTTP >= 400.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8765', OUT = process.argv[2] || '.';
const PASSWORD = process.env.ADMIN_PASSWORD || 'smoke-test-pass-1';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const problems = [];
  page.on('console', m => { if (['error', 'warning'].includes(m.type())) problems.push('console ' + m.type() + ': ' + m.text()); });
  page.on('pageerror', e => problems.push('pageerror: ' + e.message));
  page.on('requestfailed', r => problems.push('requestfailed: ' + r.url()));
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('favicon')) problems.push(r.status() + ' ' + r.url()); });
  const step = async (name, fn) => { await fn(); console.log('ok  ', name); };
  const shot = n => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });

  await step('login', async () => { await page.goto(BASE + '/login'); await page.fill('#u', 'admin'); await page.fill('#p', PASSWORD); await page.click('button[type=submit]'); await page.waitForURL('**/dashboard'); });
  await step('discover', async () => { await page.goto(BASE + '/products'); await page.fill('#rt', 'Find 8 skincare products suitable for Pinterest'); await page.selectOption('#cat', 'Skincare'); await page.click('form[action="/products/discover"] button[type=submit]'); await page.waitForSelector('table tr td a'); });
  await step('select product', async () => { await page.click('table tr td a'); await page.waitForSelector('text=Affiliate destination'); await page.click('text=Select'); await page.waitForSelector('text=Product marked selected'); });
  await step('create pins', async () => { await page.click('text=Create pins'); await page.fill('#n', '5'); await page.click('button:has-text("Generate copy + designs")'); await page.waitForSelector('.pin img'); });
  await shot('pins');
  await step('approve', async () => { await page.click('.pin >> nth=0'); await page.click('button:has-text("Approve")'); await page.waitForSelector('text=Pin approved'); });
  await step('connect (mock oauth)', async () => { await page.goto(BASE + '/settings'); await page.click('button:has-text("Connect Pinterest")'); await page.click('text=Authorize (simulated)'); await page.waitForSelector('text=@mockaccount'); });
  await step('default board', async () => { await page.selectOption('select[name=board_id]', { label: 'Beauty Finds' }); await page.click('button:has-text("Save default board")'); await page.waitForSelector('text=Default board: Beauty Finds'); });
  await step('publish', async () => { await page.goto(BASE + '/pins'); await page.click('.pin >> nth=0'); await page.selectOption('#board', { label: 'Skincare' }); page.once('dialog', d => d.accept()); await page.click('#pubform button[type=submit]'); await page.waitForSelector('text=Pinterest Pin ID'); });
  await shot('published');
  await step('other pages', async () => { for (const p of ['/queue', '/analytics', '/templates', '/dashboard']) { await page.goto(BASE + p); await page.waitForLoadState('networkidle'); } });
  console.log('\nPROBLEMS:', problems.length ? '\n' + problems.join('\n') : 'none');
  await browser.close();
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
