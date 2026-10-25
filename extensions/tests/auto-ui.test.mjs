import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { webcrypto } from 'node:crypto';
import { createContext, runInContext } from 'node:vm';

const require = createRequire(new URL('../../desktop/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const script = await readFile(new URL('../worklead-messages/auto-content.js', import.meta.url), 'utf8');
const title = '합성 웹 개발 공고';
const intro = '합성 기본 자기소개';
function harness({ selected = true, extraOutside = false, path = '/job-posts/test-123456789abc' } = {}) {
  const dom = new JSDOM(`<header><button aria-label="합성 프로필 메뉴"><img src="/synthetic-avatar.png"></button></header><main><h1>${title}</h1><section id="application-region"><button id="open">지원하기</button></section></main>`, { url: `https://jobs.daangn.com${path}` });
  const { window } = dom;
  window.HTMLElement.prototype.getClientRects = function () { return [{}]; };
  const intervals = [];
  const context = createContext({ document: window.document, location: window.location, getComputedStyle: window.getComputedStyle.bind(window), crypto: webcrypto, TextEncoder, chrome: { runtime: { sendMessage: async () => {} } }, setInterval: fn => intervals.push(fn), Date });
  let opens = 0, submits = 0;
  const region = window.document.querySelector('#application-region');
  window.document.querySelector('#open').addEventListener('click', () => {
    opens++;
    region.innerHTML = `<div role="dialog"><div id="intro-card"><h2>제출할 자기소개를 확인하세요</h2><button ${selected ? 'aria-selected="true"' : ''}>기본</button><button>추가</button><textarea>${intro}</textarea><button id="final">지원하기</button></div>${extraOutside ? '<label>근무 가능 요일<input required></label>' : ''}</div>`;
    window.document.querySelector('#final').addEventListener('click', () => {
      submits++;
      region.innerHTML = '<button>지원 취소</button>';
    });
  });
  runInContext(script, context);
  const call = (operation, expected = null) => context.__workleadAutoUi(operation, expected);
  const identity = async () => ({ job_id: '123456789abc', title, account_hash: (await call('inspect')).account_hash });
  return { call, identity, window, context, counts: () => ({ opens, submits }) };
}

test('synthetic existing default intro uses two separate clicks and returns only hashes, then official cancel state', async () => {
  const h = harness();
  const expected = await h.identity();
  const initial = await h.call('inspect', expected);
  assert.equal(initial.state, 'openable');
  assert.equal((await h.call('open', expected)).state, 'opened');
  const ready = await h.call('inspect', expected);
  assert.equal(ready.state, 'ready');
  assert.match(ready.profile_hash, /^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(ready).includes(intro), false);
  assert.equal(JSON.stringify(ready).includes('합성 프로필'), false);
  const submit = { ...expected, profile_hash: ready.profile_hash, attempt_id: 'synthetic-attempt', permit_until: Date.now() + 1000 };
  assert.equal((await h.call('submit', submit)).state, 'clicked');
  assert.equal((await h.call('inspect', expected)).state, 'already_applied');
  assert.deepEqual(h.counts(), { opens: 1, submits: 1 });
  assert.notEqual((await h.call('submit', submit)).state, 'clicked');
  assert.deepEqual(h.counts(), { opens: 1, submits: 1 });
});

test('different job or account cannot open or submit, and expired permit cannot click', async () => {
  const h = harness();
  const expected = await h.identity();
  assert.notEqual((await h.call('open', { ...expected, job_id: 'aaaaaaaaaaaa' })).state, 'opened');
  assert.equal((await h.call('open', { ...expected, account_hash: 'b'.repeat(64) })).reason_code, 'account_changed');
  assert.deepEqual(h.counts(), { opens: 0, submits: 0 });
  await h.call('open', expected);
  const ready = await h.call('inspect', expected);
  assert.notEqual((await h.call('submit', { ...expected, profile_hash: ready.profile_hash, attempt_id: 'expired', permit_until: Date.now() - 1 })).state, 'clicked');
  assert.notEqual((await h.call('submit', { ...expected, profile_hash: 'c'.repeat(64), attempt_id: 'different-intro', permit_until: Date.now() + 1000 })).state, 'clicked');
  assert.equal(h.counts().submits, 0);
});

test('saved default selection must be evidenced; merely having a default button is insufficient', async () => {
  const h = harness({ selected: false });
  const expected = await h.identity();
  await h.call('open', expected);
  const view = await h.call('inspect', expected);
  assert.equal(view.state, 'held');
  assert.equal(h.counts().submits, 0);
});

test('additional required question outside the intro card but in the same dialog blocks submission', async () => {
  const h = harness({ extraOutside: true });
  const expected = await h.identity();
  await h.call('open', expected);
  const view = await h.call('inspect', expected);
  assert.equal(view.state, 'held');
  assert.equal(view.reason_code, 'questions_required');
  assert.equal(h.counts().submits, 0);
});

test('ambiguous duplicate panels and changed title are held without a final click', async () => {
  const h = harness();
  const expected = await h.identity();
  await h.call('open', expected);
  const card = h.window.document.querySelector('#intro-card');
  const duplicate = card.cloneNode(true);
  h.window.document.querySelector('#application-region').append(duplicate);
  assert.equal((await h.call('inspect', expected)).state, 'held');
  duplicate.remove();
  h.window.document.querySelector('h1').textContent = '다른 합성 공고';
  assert.equal((await h.call('inspect', expected)).state, 'held');
  assert.equal(h.counts().submits, 0);
});

test('account switch while asynchronous profile hashing is pending blocks the final click', async () => {
  const h = harness();
  const expected = await h.identity();
  await h.call('open', expected);
  const ready = await h.call('inspect', expected);
  let calls = 0;
  h.context.crypto = { subtle: { digest: async (...args) => {
    calls++;
    if (calls === 2) h.window.document.querySelector('header button').setAttribute('aria-label', '다른 합성 프로필 메뉴');
    return webcrypto.subtle.digest(...args);
  } } };
  const result = await h.call('submit', { ...expected, profile_hash: ready.profile_hash, attempt_id: 'account-race', permit_until: Date.now() + 1000 });
  assert.notEqual(result.state, 'clicked');
  assert.equal(h.counts().submits, 0);
});

for (const mutation of ['intro', 'job', 'required']) test(`${mutation} change while asynchronous hashing is pending blocks the final click`, async () => {
  const h = harness();
  const expected = await h.identity();
  await h.call('open', expected);
  const ready = await h.call('inspect', expected);
  let calls = 0;
  h.context.crypto = { subtle: { digest: async (...args) => {
    calls++;
    if (calls === 2) {
      if (mutation === 'intro') h.window.document.querySelector('textarea').value = '변경된 합성 자기소개';
      if (mutation === 'job') h.window.history.replaceState(null, '', '/job-posts/other-aaaaaaaaaaaa');
      if (mutation === 'required') h.window.document.querySelector('#intro-card').insertAdjacentHTML('beforeend', '<input required>');
    }
    return webcrypto.subtle.digest(...args);
  } } };
  const result = await h.call('submit', { ...expected, profile_hash: ready.profile_hash, attempt_id: `race-${mutation}`, permit_until: Date.now() + 1000 });
  assert.notEqual(result.state, 'clicked');
  assert.equal(h.counts().submits, 0);
});

for (const mutation of ['account', 'job', 'title']) test(`read-only completion inspect rejects ${mutation} changes during account hashing`, async () => {
  const h = harness();
  const expected = await h.identity();
  h.window.document.querySelector('#application-region').innerHTML = '<button>지원 취소</button>';
  h.context.crypto = { subtle: { digest: async (...args) => {
    if (mutation === 'account') h.window.document.querySelector('header button').setAttribute('aria-label', '다른 합성 프로필 메뉴');
    if (mutation === 'job') h.window.history.replaceState(null, '', '/job-posts/other-aaaaaaaaaaaa');
    if (mutation === 'title') h.window.document.querySelector('h1').textContent = '다른 공고';
    return webcrypto.subtle.digest(...args);
  } } };
  const result = await h.call('inspect', expected);
  assert.equal(result.state, 'held');
  assert.notEqual(result.state, 'already_applied');
  assert.deepEqual(h.counts(), { opens: 0, submits: 0 });
});
