const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const pagesSource = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'js', 'pages.js'), 'utf8');

function createRuntime(reportType) {
  const elements = {
    reportDraftCount: {textContent: ''},
    reportDraftHint: {textContent: ''},
    reportDraftList: {innerHTML: ''},
    catalogDraftCount: {textContent: ''},
    catalogDraftHint: {textContent: ''},
    catalogDraftList: {innerHTML: ''},
    unfinishedDraftSelect: {value: 'draft-1'},
  };
  const requests = [];
  const toasts = [];
  const context = {
    URLSearchParams,
    Promise,
    Intl,
    console,
    setTimeout,
    clearTimeout,
    document: {
      addEventListener() {},
      getElementById(id) { return elements[id] || null; },
    },
    window: {
      REPORT_TYPE: reportType,
      REPORT_LABEL: reportType === 'book-catalog' ? 'Book Catalog' : 'New Releases',
      RECENT_REPORT_DRAFTS: [{
        id: 'draft-1', report_type: reportType, market: 'INDIAN', month: 9, year: 2026,
        created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-01T12:00:00Z', row_count: 1,
      }],
    },
    askConfirm: async () => true,
    showToast(message, type) { toasts.push({message, type}); },
    jsonFetch: async (url) => {
      requests.push(url);
      if (url.includes('/delete')) return {ok: true};
      if (url.startsWith('/api/frontend/report/')) return {recent_drafts: []};
      throw new Error(`Unexpected request: ${url}`);
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(pagesSource, context, {filename: 'pages.js'});
  return {context, elements, requests, toasts};
}

test('New Releases delete removes the draft through the browser flow', async () => {
  const runtime = createRuntime('new-releases');
  await runtime.context.deleteReportDraft('draft-1');

  assert.deepEqual(runtime.requests, [
    '/api/report/draft-1/delete',
    '/api/frontend/report/new-releases',
  ]);
  assert.equal(runtime.context.window.RECENT_REPORT_DRAFTS.length, 0);
  assert.match(runtime.elements.reportDraftList.innerHTML, /No unfinished New Releases drafts/);
  assert.deepEqual(runtime.toasts, [{message: 'Draft deleted.', type: 'success'}]);
});

test('Book Catalog delete works even when the local draft list is stale', async () => {
  const runtime = createRuntime('book-catalog');
  runtime.context.window.RECENT_REPORT_DRAFTS = [];
  await runtime.context.deleteCatalogDraft('draft-1');

  assert.equal(runtime.requests[0], '/api/report/draft-1/delete');
  assert.equal(runtime.toasts[0].type, 'success');
});

test('Legacy selected-draft delete also refreshes the list instead of reloading', async () => {
  const runtime = createRuntime('new-releases');
  await runtime.context.deleteSelectedDraft();

  assert.deepEqual(runtime.requests, [
    '/api/report/draft-1/delete',
    '/api/frontend/report/new-releases',
  ]);
  assert.equal(runtime.context.window.RECENT_REPORT_DRAFTS.length, 0);
  assert.equal(runtime.toasts[0].type, 'success');
});
