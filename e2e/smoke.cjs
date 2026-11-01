// Browser smoke test against a running stack (docker compose or dev servers).
// Usage: BASE_URL=http://localhost:8080 E2E_PASSWORD=<demo password> node smoke.cjs
const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8080';
const fail = (m) => { console.error('FAIL:', m); process.exit(1); };

async function login(page, email) {
  await page.goto(BASE + '/');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(process.env.E2E_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForSelector('text=documents you can access');
}

async function ask(page, question) {
  await page.getByLabel('Your question').fill(question);
  const n = await page.getByTestId('answer').count();
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.waitForFunction((k) => {
    const answers = document.querySelectorAll('[data-testid="answer"]');
    return answers.length > k && answers[answers.length - 1].getAttribute('aria-busy') === 'false';
  }, n);
  return page.getByTestId('answer').last().innerText();
}

(async () => {
  const b = await chromium.launch();
  const bad = [];
  const page = await b.newPage({ viewport: { width: 1300, height: 900 } });
  page.on('response', (r) => { if (r.status() >= 500) bad.push(`${r.status()} ${r.url()}`); });
  await login(page, 'alice@example.com');
  const total = await page.locator('text=documents you can access').innerText();
  console.log('alice library:', total);
  await page.goto(BASE + '/ask');
  await page.getByRole('button', { name: 'New conversation' }).click();
  await page.waitForURL(/\/ask\/.+/);
  const a1 = await ask(page, 'How many characters must a password contain?');
  if (!/answered/.test(a1) || !/\[1\]/.test(a1)) fail('expected a cited answer: ' + a1);
  console.log('answer with citation: ok');
  const bobInvoice = 'INV-20260500';
  const a2 = await ask(page, `What is the total due on invoice ${bobInvoice}?`);
  if (!/not found/.test(a2)) fail("alice must not get an answer from bob's invoice: " + a2);
  console.log("bob's invoice hidden from alice: ok");
  await page.getByRole('button', { name: 'Show source 1' }).first().click();
  await page.getByRole('link', { name: 'Open page with passage highlighted →' }).click();
  await page.waitForSelector('mark');
  console.log('citation opens highlighted passage: ok');
  // Upload a file and wait until the background worker has processed it.
  await page.goto(BASE + '/');
  await page.getByLabel('Files').setInputFiles({ name: 'smoke-note.txt', mimeType: 'text/plain',
    buffer: Buffer.from('Smoke test memo. The parking garage closes at 22:00 on weekdays.') });
  await page.getByRole('button', { name: /^Upload/ }).click();
  await page.waitForSelector('text=uploaded, processing');
  await page.waitForSelector('tr:has-text("smoke-note") >> text=ready', { timeout: 60000 });
  console.log('upload processed by worker: ok');
  if (bad.length) fail('5xx responses: ' + bad.join(', '));
  console.log('PASS');
  await b.close();
})();
