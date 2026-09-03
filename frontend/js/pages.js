function htmlEscape(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function statusClass(value) {
  return htmlEscape(String(value || '').toLowerCase().replace(/_/g, '-'));
}

const pageMonthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];

function friendlyStatus(value) {
  const status = String(value || 'NOT_CONFIGURED').toUpperCase();
  return ({
    ACTIVE: 'Ready',
    LOCAL_TEST: 'Ready from upload',
    PENDING_REVIEW: 'Review needed',
    REJECTED: 'Rejected',
    SYNC_FAILED: 'Sync failed',
    NOT_CONFIGURED: 'Needs setup',
  })[status] || status.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

function reportPath(reportType, reportId = '') {
  const base = {
    'new-releases': 'new-releases/index.html',
    'book-catalog': 'book-catalog/index.html',
    'out-of-stock': 'out-of-stock/index.html',
    'mms-indent': 'mms-indent/index.html',
  }[reportType] || 'history/index.html';
  return reportId ? `${base}?report_id=${encodeURIComponent(reportId)}` : base;
}

function relativeReportPath(reportType, reportId = '') {
  return `../${reportPath(reportType, reportId)}`;
}

function formatReportPeriod(report) {
  const month = Number(report.month || 0);
  const label = pageMonthNames[month - 1] || report.month || '';
  return `${label} ${report.year || ''}`.trim();
}

function draftCard(report, prefix = '') {
  const href = `${prefix}${reportPath(report.report_type, report.id)}`;
  return `<a class="draft-card" href="${href}">
    <div><strong>${htmlEscape(report.label || report.title)}</strong><span>${htmlEscape(formatReportPeriod(report))} · ${htmlEscape(report.market || '')}</span></div>
    <b>Continue</b>
  </a>`;
}

function sourceCard(s) {
  const rawStatus = s.last_status || s.status || 'NOT_CONFIGURED';
  return `<div class="source-card">
    <div class="source-title-row">
      <strong>${htmlEscape(s.label)}</strong>
      <span class="status-badge status-${statusClass(rawStatus)}">${htmlEscape(friendlyStatus(rawStatus))}</span>
    </div>
    <div class="source-meta"><span>Drive file</span><b>${htmlEscape(s.drive_file_name || 'Not configured')}</b></div>
    <div class="source-meta"><span>Drive updated</span><b class="local-time" data-iso="${htmlEscape(s.drive_modified_time || '')}">${htmlEscape(s.drive_modified_time || '—')}</b></div>
    <div class="source-meta"><span>Last successful sync</span><b class="local-time" data-iso="${htmlEscape(s.last_successful_sync || '')}">${htmlEscape(s.last_successful_sync || '—')}</b></div>
    <div class="source-meta"><span>Records</span><b>${htmlEscape(s.record_count || 0)}</b></div>
  </div>`;
}

function sourceLine(sources) {
  const parts = (sources || []).map(s => {
    const rawStatus = s.last_status || s.status || 'NOT_CONFIGURED';
    return `${s.label} (${friendlyStatus(rawStatus)})`;
  });
  return parts.length ? parts.join(' · ') : 'No source data configured.';
}

function draftOptionLabel(report) {
  const updated = report.updated_at || report.created_at || '';
  const updatedLabel = updated && !Number.isNaN(new Date(updated).getTime())
    ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(updated))
    : 'recent draft';
  return `${formatReportPeriod(report)} · ${report.market || 'Market'} · updated ${updatedLabel}`;
}

async function hydrateDraftRowCounts(drafts) {
  return Promise.all((drafts || []).map(async report => {
    if (Number.isInteger(report.row_count)) return report;
    try {
      const data = await jsonFetch(`/api/report/${encodeURIComponent(report.id)}`);
      return {...report, row_count: (data.report?.draft?.rows || []).length};
    } catch (_) {
      return {...report, row_count: null};
    }
  }));
}

let recentDraftListVersion=0;

function markRecentDraftListChanged(){
  recentDraftListVersion+=1;
}

function updateCatalogDraftCount() {
  const drafts = window.RECENT_REPORT_DRAFTS || [];
  const count = document.getElementById('catalogDraftCount');
  if (count) count.textContent = String(drafts.length);
  const hint = document.getElementById('catalogDraftHint');
  if (!hint) return;
  if (!drafts.length) {
    hint.textContent = 'No unfinished catalog.';
    return;
  }
  hint.textContent = `${drafts.length} saved report${drafts.length === 1 ? '' : 's'}.`;
}

function setCatalogBreadcrumb(sectionLabel = '') {
  const breadcrumbs = document.getElementById('catalogBreadcrumbs');
  if (!breadcrumbs) return;
  const section = sectionLabel ? `<span class="catalog-crumb-separator">/</span><span>${htmlEscape(sectionLabel)}</span>` : '';
  breadcrumbs.innerHTML = `<button type="button" onclick="showCatalogChooser()">Book Catalog</button>${section}`;
}

function renderCatalogDraftList() {
  updateCatalogDraftCount();
  const list = document.getElementById('catalogDraftList');
  if (!list) return;
  const drafts = window.RECENT_REPORT_DRAFTS || [];
  if (!drafts.length) {
    list.innerHTML = '<div class="empty-cell">No unfinished Book Catalog drafts.</div>';
    return;
  }
  list.innerHTML = drafts.map(report => {
    const rowCount = report.row_count == null ? 'Books not loaded' : `${report.row_count} book${report.row_count === 1 ? '' : 's'}`;
    const updated = report.updated_at || report.created_at || '';
    const updatedLabel = updated && !Number.isNaN(new Date(updated).getTime())
      ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(updated))
      : 'Updated recently';
    return `<div class="catalog-draft-row">
      <div>
        <strong>${htmlEscape(formatReportPeriod(report) || 'Untitled draft')}</strong>
        <span>${htmlEscape(report.market || 'Market')} market · ${htmlEscape(rowCount)} · ${report.status === 'FINAL' ? 'Downloaded' : 'Draft'} · ${htmlEscape(updatedLabel)}</span>
      </div>
      <div class="catalog-draft-actions">
        <button class="btn primary" type="button" onclick="continueCatalogDraft('${htmlEscape(report.id)}')">Continue</button>
        <button class="btn secondary danger-outline" type="button" onclick="deleteCatalogDraft('${htmlEscape(report.id)}')">Delete</button>
      </div>
    </div>`;
  }).join('');
}

async function showCatalogChooser() {
  if (typeof currentReport !== 'undefined' && currentReport && typeof saveDraft === 'function') {
    await saveDraft();
  }
  setCatalogBreadcrumb();
  document.getElementById('catalogEntry')?.classList.remove('hidden');
  document.getElementById('catalogDraftListPanel')?.classList.add('hidden');
  document.getElementById('catalogWorkspace')?.classList.add('hidden');
}

function showCatalogDrafts() {
  setCatalogBreadcrumb('Select Draft');
  document.getElementById('catalogEntry')?.classList.add('hidden');
  document.getElementById('catalogWorkspace')?.classList.add('hidden');
  document.getElementById('catalogDraftListPanel')?.classList.remove('hidden');
  renderCatalogDraftList();
}

function openCatalogWorkspace(sectionLabel = '') {
  const defaultLabel = currentReport ? 'Continue draft' : 'Create new';
  setCatalogBreadcrumb(sectionLabel || defaultLabel);
  document.getElementById('catalogEntry')?.classList.add('hidden');
  document.getElementById('catalogDraftListPanel')?.classList.add('hidden');
  document.getElementById('catalogWorkspace')?.classList.remove('hidden');
  if (typeof renderEditor === 'function') renderEditor();
  if (typeof updateCatalogWorkspaceStatus === 'function') updateCatalogWorkspaceStatus();
}

async function chooseNewCatalog() {
  if (typeof currentReport !== 'undefined' && currentReport && typeof saveDraft === 'function') {
    await saveDraft();
  }
  if (typeof currentReport !== 'undefined') currentReport = null;
  if (typeof originalDraft !== 'undefined') originalDraft = null;
  if (typeof undoStack !== 'undefined') undoStack = [];
  if (typeof redoStack !== 'undefined') redoStack = [];
  if (typeof catalogEditingRowId !== 'undefined') catalogEditingRowId = null;
  if (typeof updateUndoButton === 'function') updateUndoButton();
  if (!(typeof loadPreferredFileName === 'function' && loadPreferredFileName()) && typeof refreshDefaultFileName === 'function') refreshDefaultFileName(true);
  openCatalogWorkspace('Create new');
}

function continueCatalogDraft(reportId) {
  if (!reportId) return;
  location.href = relativeReportPath('book-catalog', reportId);
}

async function deleteCatalogDraft(reportId) {
  const report = (window.RECENT_REPORT_DRAFTS || []).find(item => item.id === reportId) || {id: reportId};
  const ok = await askConfirm({
    title: 'Delete unfinished draft?',
    message: `${formatReportPeriod(report) || 'This draft'} will be permanently removed from this system.`,
    confirmText: 'Delete draft',
    cancelText: 'Keep draft',
    danger: true,
  });
  if (!ok) return;
  try {
    await jsonFetch(`/api/report/${encodeURIComponent(reportId)}/delete`, { method: 'POST', body: '{}' });
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  clearDeletedReportState(reportId);
  try {
    await refreshReportDraftList('book-catalog');
  } catch (_) {
    window.RECENT_REPORT_DRAFTS = (window.RECENT_REPORT_DRAFTS || []).filter(item => item.id !== reportId);
    renderCatalogDraftList();
  }
  showToast('Draft deleted.', 'success');
}

function clearDeletedReportState(reportId) {
  if (typeof currentReport === 'undefined' || currentReport?.id !== reportId) return;
  currentReport = null;
  if (typeof originalDraft !== 'undefined') originalDraft = null;
  if (typeof undoStack !== 'undefined') undoStack = [];
  if (typeof redoStack !== 'undefined') redoStack = [];
  if (typeof saveTimer !== 'undefined') clearTimeout(saveTimer);
  if (typeof updateUndoButton === 'function') updateUndoButton();
}

async function refreshReportDraftList(reportType = window.REPORT_TYPE) {
  const requestVersion=++recentDraftListVersion;
  const data = await jsonFetch(`/api/frontend/report/${encodeURIComponent(reportType)}`);
  const drafts=await hydrateDraftRowCounts(data.recent_drafts || []);
  if(requestVersion!==recentDraftListVersion)return;
  window.RECENT_REPORT_DRAFTS = drafts;
  if (reportType === 'book-catalog') renderCatalogDraftList();
  else renderReportDraftList();
}

function updateReportDraftCount() {
  const drafts = window.RECENT_REPORT_DRAFTS || [];
  const count = document.getElementById('reportDraftCount');
  if (count) count.textContent = String(drafts.length);
  const hint = document.getElementById('reportDraftHint');
  if (!hint) return;
  hint.textContent = drafts.length
    ? `${drafts.length} saved report${drafts.length === 1 ? '' : 's'}.`
    : 'No saved reports.';
}

function setReportBreadcrumb(sectionLabel = '') {
  const breadcrumbs = document.getElementById('reportBreadcrumbs');
  if (!breadcrumbs) return;
  const section = sectionLabel ? `<span class="catalog-crumb-separator">/</span><span>${htmlEscape(sectionLabel)}</span>` : '';
  breadcrumbs.innerHTML = `<button type="button" onclick="showReportChooser()">${htmlEscape(window.REPORT_LABEL || 'Report')}</button>${section}`;
}

function renderReportDraftList() {
  updateReportDraftCount();
  const list = document.getElementById('reportDraftList');
  if (!list) return;
  const drafts = window.RECENT_REPORT_DRAFTS || [];
  if (!drafts.length) {
    list.innerHTML = `<div class="empty-cell">No unfinished ${htmlEscape(window.REPORT_LABEL || 'report')} drafts.</div>`;
    return;
  }
  list.innerHTML = drafts.map(report => {
    const rowCount = report.row_count == null ? 'Books not loaded' : `${report.row_count} book${report.row_count === 1 ? '' : 's'}`;
    const updated = report.updated_at || report.created_at || '';
    const updatedLabel = updated && !Number.isNaN(new Date(updated).getTime())
      ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(updated))
      : 'Updated recently';
    return `<div class="catalog-draft-row">
      <div>
        <strong>${htmlEscape(formatReportPeriod(report) || 'Untitled draft')}</strong>
        <span>${htmlEscape(report.market || 'Market')} market · ${htmlEscape(rowCount)} · ${report.status === 'FINAL' ? 'Downloaded' : 'Draft'} · ${htmlEscape(updatedLabel)}</span>
      </div>
      <div class="catalog-draft-actions">
        <button class="btn primary" type="button" onclick="continueReportDraft('${htmlEscape(report.id)}')">Continue</button>
        <button class="btn secondary danger-outline" type="button" onclick="deleteReportDraft('${htmlEscape(report.id)}')">Delete</button>
      </div>
    </div>`;
  }).join('');
}

async function showReportChooser() {
  if (typeof currentReport !== 'undefined' && currentReport && typeof saveDraft === 'function') {
    await saveDraft();
  }
  setReportBreadcrumb();
  document.getElementById('reportEntry')?.classList.remove('hidden');
  document.getElementById('reportDraftListPanel')?.classList.add('hidden');
  document.getElementById('reportWorkspace')?.classList.add('hidden');
}

function showReportDrafts() {
  setReportBreadcrumb('Select Draft');
  document.getElementById('reportEntry')?.classList.add('hidden');
  document.getElementById('reportWorkspace')?.classList.add('hidden');
  document.getElementById('reportDraftListPanel')?.classList.remove('hidden');
  renderReportDraftList();
}

function openReportWorkspace(sectionLabel = '') {
  const defaultLabel = currentReport ? 'Draft' : 'Create new';
  setReportBreadcrumb(sectionLabel || defaultLabel);
  document.getElementById('reportEntry')?.classList.add('hidden');
  document.getElementById('reportDraftListPanel')?.classList.add('hidden');
  document.getElementById('reportWorkspace')?.classList.remove('hidden');
  if (typeof renderEditor === 'function') renderEditor();
}

async function chooseNewReport() {
  if (typeof currentReport !== 'undefined' && currentReport && typeof saveDraft === 'function') {
    await saveDraft();
  }
  if (typeof currentReport !== 'undefined') currentReport = null;
  if (typeof originalDraft !== 'undefined') originalDraft = null;
  if (typeof undoStack !== 'undefined') undoStack = [];
  if (typeof redoStack !== 'undefined') redoStack = [];
  if (typeof updateUndoButton === 'function') updateUndoButton();
  if (!(typeof loadPreferredFileName === 'function' && loadPreferredFileName()) && typeof refreshDefaultFileName === 'function') refreshDefaultFileName(true);
  const message = document.getElementById('reportMessage');
  if (message) message.innerHTML = '';
  openReportWorkspace('Create new');
}

function continueReportDraft(reportId) {
  if (!reportId) return;
  location.href = relativeReportPath(window.REPORT_TYPE, reportId);
}

async function deleteReportDraft(reportId) {
  const report = (window.RECENT_REPORT_DRAFTS || []).find(item => item.id === reportId) || {id: reportId};
  const ok = await askConfirm({
    title: 'Delete unfinished draft?',
    message: `${formatReportPeriod(report) || 'This draft'} will be permanently removed from this system.`,
    confirmText: 'Delete draft',
    cancelText: 'Keep draft',
    danger: true,
  });
  if (!ok) return;
  try {
    await jsonFetch(`/api/report/${encodeURIComponent(reportId)}/delete`, { method: 'POST', body: '{}' });
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  clearDeletedReportState(reportId);
  try {
    await refreshReportDraftList();
  } catch (_) {
    window.RECENT_REPORT_DRAFTS = (window.RECENT_REPORT_DRAFTS || []).filter(item => item.id !== reportId);
    renderReportDraftList();
  }
  showToast('Draft deleted.', 'success');
}

function syncCurrentReportDraft() {
  if (window.REPORT_TYPE === 'book-catalog' || !currentReport || !Array.isArray(window.RECENT_REPORT_DRAFTS)) return;
  const record = {
    id: currentReport.id,
    report_type: currentReport.report_type,
    market: currentReport.market,
    month: currentReport.month,
    year: currentReport.year,
    title: currentReport.title,
    label: window.REPORT_LABEL,
    status: currentReport.status,
    updated_at: currentReport.updated_at,
    created_at: currentReport.created_at,
    row_count: (currentReport.draft?.rows || []).length,
  };
  markRecentDraftListChanged();
  window.RECENT_REPORT_DRAFTS = [record, ...window.RECENT_REPORT_DRAFTS.filter(item => item.id !== record.id)];
  renderReportDraftList();
}

async function loadHomePage() {
  const grid = document.getElementById('homeSourceGrid');
  if (!grid) return;
  try {
    const data = await jsonFetch('/api/frontend/home');
    const readiness = document.getElementById('readinessPanel');
    if (readiness) {
      const readyReports = (data.report_status || []).filter(r => r.ready).length;
      readiness.innerHTML = `<div class="readiness-grid">
        <div class="readiness-card"><span>Reports ready</span><strong>${readyReports}/${(data.report_status || []).length}</strong><small>Can be started with current data</small></div>
        <div class="readiness-card"><span>Sources ready</span><strong>${htmlEscape(data.source_counts?.ready || 0)}/${htmlEscape(data.source_counts?.total || 0)}</strong><small>${htmlEscape(data.source_counts?.needs_setup || 0)} need setup</small></div>
        <div class="readiness-card"><span>Drafts waiting</span><strong>${htmlEscape((data.recent_drafts || []).length)}</strong><small>Continue or clean up from History</small></div>
      </div>
      <div class="report-readiness-list">${(data.report_status || []).map(r => `<a class="readiness-row ${r.ready ? 'ready' : 'blocked'}" href="${reportPath(r.report_type)}">
        <span>${htmlEscape(r.label)}</span>
        <b>${r.ready ? 'Ready' : `Needs ${htmlEscape(r.missing.join(', '))}`}</b>
      </a>`).join('')}</div>`;
    }
    const drafts = document.getElementById('recentDraftsPanel');
    if (drafts) {
      drafts.innerHTML = (data.recent_drafts || []).length ? `<div class="section-title-row compact"><div><h2>Continue a draft</h2><p class="muted">Pick up unfinished reports without creating duplicates.</p></div><a class="btn secondary" href="history/index.html?status=DRAFT">View all drafts</a></div><div class="draft-grid">${data.recent_drafts.map(r => draftCard(r)).join('')}</div>` : '';
    }
    grid.innerHTML = data.sources.map(sourceCard).join('');
    localizeTimes();
  } catch (e) {
    grid.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

async function loadReportPage() {
  if (!window.REPORT_TYPE) return;
  const requestVersion=++recentDraftListVersion;
  try {
    const data = await jsonFetch(`/api/frontend/report/${window.REPORT_TYPE}`);
    window.REPORT_LABEL = data.report_label;
    window.REPORT_READY = !data.missing.length;
    window.REPORT_MISSING = data.missing || [];
    const drafts=await hydrateDraftRowCounts(data.recent_drafts || []);
    if(requestVersion===recentDraftListVersion){
      window.RECENT_REPORT_DRAFTS = drafts;
      if (window.REPORT_TYPE === 'book-catalog') renderCatalogDraftList();
      else renderReportDraftList();
    }
    const source = document.getElementById('reportSourceLine');
    if (source && window.REPORT_TYPE !== 'book-catalog') {
      source.classList.toggle('warning', !!data.missing.length);
      source.innerHTML = `<span><strong>Source data used:</strong> ${htmlEscape(sourceLine(data.source_cards))}</span><a href="../database/index.html">Data Sources</a>`;
    }
    const missing = document.getElementById('missingAlert');
    if (data.missing.length) {
      if (missing) {
        missing.classList.remove('hidden');
        missing.innerHTML = `<strong>This report is not ready yet.</strong> Set up or accept these source files first: ${htmlEscape(data.missing.join(', '))}. <a href="../database/index.html">Open Data Sources</a>`;
      }
    } else if (missing) {
      missing.classList.add('hidden');
    }
    const isContinuingDraft = new URLSearchParams(location.search).has('report_id');
    if (window.REPORT_TYPE !== 'book-catalog' && !isContinuingDraft) setReportBreadcrumb();
    localizeTimes();
  } catch (e) {
    const message = document.getElementById('reportMessage');
    if (message) message.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

function selectedDraft() {
  const id = document.getElementById('unfinishedDraftSelect')?.value;
  return (window.RECENT_REPORT_DRAFTS || []).find(report => report.id === id);
}

function continueSelectedDraft() {
  const draft = selectedDraft();
  if (!draft) {
    showToast('Choose an unfinished draft first.', 'error');
    return;
  }
  location.href = relativeReportPath(draft.report_type, draft.id);
}

async function deleteSelectedDraft() {
  const draft = selectedDraft();
  if (!draft) {
    showToast('Choose an unfinished draft first.', 'error');
    return;
  }
  const ok = await askConfirm({
    title: 'Delete unfinished draft?',
    message: `${draftOptionLabel(draft)} will be permanently removed from this system.`,
    confirmText: 'Delete draft',
    cancelText: 'Keep draft',
    danger: true,
  });
  if (!ok) return;
  try {
    await jsonFetch(`/api/report/${draft.id}/delete`, { method: 'POST', body: '{}' });
  } catch (e) {
    showToast(e.message, 'error');
    return;
  }
  clearDeletedReportState(draft.id);
  try {
    await refreshReportDraftList(draft.report_type || window.REPORT_TYPE);
  } catch (_) {
    window.RECENT_REPORT_DRAFTS = (window.RECENT_REPORT_DRAFTS || []).filter(item => item.id !== draft.id);
    if (window.REPORT_TYPE === 'book-catalog') renderCatalogDraftList();
    else renderReportDraftList();
  }
  showToast('Draft deleted.', 'success');
}

function databaseSourceLink(sourceType) {
  return `index.html?source_type=${encodeURIComponent(sourceType)}`;
}

async function loadDatabasePage() {
  const params = new URLSearchParams(location.search);
  const sourceType = params.get('source_type');
  if (sourceType) return loadDatabaseSourcePage(sourceType);

  const root = document.getElementById('databaseContent');
  try {
    const data = await jsonFetch('/api/frontend/database');
    root.innerHTML = `<div class="hero-card">
      <div><div class="eyebrow">Admin area</div><h1>Manage source files safely.</h1><p>Source files power lookup, validation, and generated reports. New uploads and Drive syncs are staged for review before replacing current data.</p></div>
      <button class="btn primary" onclick="syncAll(event)">Check Sources</button>
    </div>
    <div class="alert info"><strong>Safe update flow:</strong> choose a source, upload or sync a workbook, review the differences, then accept it only when it looks correct.</div>
    <div class="database-list">${data.sources.map(s => `<a class="database-row" href="${databaseSourceLink(s.source_type)}">
      <div><strong>${htmlEscape(s.label)}</strong><span class="database-sub">${htmlEscape(s.drive_file_name || 'No active file yet')}</span></div>
      <div><span>Drive updated</span><b class="local-time" data-iso="${htmlEscape(s.drive_modified_time || '')}">${htmlEscape(s.drive_modified_time || '—')}</b></div>
      <div><span>Last accepted</span><b class="local-time" data-iso="${htmlEscape(s.last_successful_sync || '')}">${htmlEscape(s.last_successful_sync || '—')}</b></div>
      <div><span>Records</span><b>${htmlEscape(s.record_count || 0)}</b></div>
      <div><span class="status-badge status-${statusClass(s.last_status || 'NOT_CONFIGURED')}">${htmlEscape(friendlyStatus(s.last_status || 'NOT_CONFIGURED'))}</span></div>
    </a>`).join('')}</div>`;
    localizeTimes();
  } catch (e) {
    root.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

function diffTable(kind, rows) {
  if (!rows || !rows.length) return '';
  if (kind === 'added') {
    return `<details open><summary>Added records (${rows.length})</summary><div class="diff-scroll"><table class="history-table"><thead><tr><th>Key</th><th>New record</th></tr></thead><tbody>${rows.map(x => `<tr><td>${htmlEscape(x.key)}</td><td><pre>${htmlEscape(x.new_pretty || JSON.stringify(x.new || {}, null, 2))}</pre></td></tr>`).join('')}</tbody></table></div></details>`;
  }
  if (kind === 'removed') {
    return `<details><summary>Removed records (${rows.length})</summary><div class="diff-scroll"><table class="history-table"><thead><tr><th>Key</th><th>Previous record</th></tr></thead><tbody>${rows.map(x => `<tr><td>${htmlEscape(x.key)}</td><td><pre>${htmlEscape(x.old_pretty || JSON.stringify(x.old || {}, null, 2))}</pre></td></tr>`).join('')}</tbody></table></div></details>`;
  }
  return `<details open><summary>Changed records (${rows.length})</summary><div class="diff-scroll"><table class="history-table"><thead><tr><th>Key</th><th>Column</th><th>Previous</th><th>New</th></tr></thead><tbody>${rows.flatMap(x => (x.columns || []).map(c => `<tr><td>${htmlEscape(x.key)}</td><td>${htmlEscape(c.column)}</td><td>${htmlEscape(c.old)}</td><td>${htmlEscape(c.new)}</td></tr>`)).join('')}</tbody></table></div></details>`;
}

async function loadDatabaseSourcePage(sourceType) {
  const root = document.getElementById('databaseContent');
  try {
    const data = await jsonFetch(`/api/frontend/database/${encodeURIComponent(sourceType)}`);
    const s = data.source;
    const pending = data.pending;
    const download = s.active_local_path ? `<a class="btn secondary" href="${fileUrl(`/source-file/${sourceType}`)}">Download Active Copy</a>` : '';
    const pendingHtml = pending ? (() => {
      const d = pending.diff || {};
      return `<div class="panel pending-review-card">
        <div class="eyebrow">Pending review</div>
        <div class="pending-title-row"><div><h2>Verify changes before replacing the active version</h2><p class="muted">${htmlEscape(pending.drive_file_name || 'Uploaded workbook')} · <span class="local-time" data-iso="${htmlEscape(pending.synced_at)}">${htmlEscape(pending.synced_at)}</span></p></div><span class="status-badge status-pending-review">PENDING REVIEW</span></div>
        <div class="change-summary-grid">
          <div class="validation-stat"><b>${htmlEscape(d.old_count || 0)}</b><span>Previous records</span></div>
          <div class="validation-stat"><b>${htmlEscape(d.new_count || 0)}</b><span>New records</span></div>
          <div class="validation-stat ${d.added_count ? 'warn' : ''}"><b>${htmlEscape(d.added_count || 0)}</b><span>Added</span></div>
          <div class="validation-stat ${d.removed_count ? 'warn' : ''}"><b>${htmlEscape(d.removed_count || 0)}</b><span>Removed</span></div>
          <div class="validation-stat ${d.changed_count ? 'warn' : ''}"><b>${htmlEscape(d.changed_count || 0)}</b><span>Changed</span></div>
        </div>
        ${diffTable('added', d.added)}${diffTable('removed', d.removed)}${diffTable('changed', d.changed)}
        ${!d.added_count && !d.removed_count && !d.changed_count ? '<div class="alert success">No row-level changes were detected.</div>' : ''}
        <div class="button-row review-actions"><button class="btn primary" onclick="reviewSourceVersion(${pending.id},'accept')">Accept New Version</button><button class="btn secondary danger-outline" onclick="reviewSourceVersion(${pending.id},'reject')">Reject New Version</button></div>
      </div>`;
    })() : '';

    root.innerHTML = `<div class="source-detail-grid">
      <div class="panel"><div class="eyebrow">Source configuration</div><h2>${htmlEscape(s.label)}</h2>
        <div class="detail-list">
          <div><span>Status</span><b>${htmlEscape(friendlyStatus(s.last_status || 'NOT_CONFIGURED'))}</b></div>
          <div><span>Active file</span><b>${htmlEscape(s.drive_file_name || '—')}</b></div>
          <div><span>Drive modified</span><b class="local-time" data-iso="${htmlEscape(s.drive_modified_time || '')}">${htmlEscape(s.drive_modified_time || '—')}</b></div>
          <div><span>Last sync attempt</span><b class="local-time" data-iso="${htmlEscape(s.last_sync_attempt || '')}">${htmlEscape(s.last_sync_attempt || '—')}</b></div>
          <div><span>Last accepted version</span><b class="local-time" data-iso="${htmlEscape(s.last_successful_sync || '')}">${htmlEscape(s.last_successful_sync || '—')}</b></div>
          <div><span>Records</span><b>${htmlEscape(s.record_count || 0)}</b></div>
        </div>
        <label class="stack-label">Google Drive file ID<input id="driveFileId" value="${htmlEscape(s.drive_file_id || '')}" placeholder="Paste Drive file ID"></label>
        <p class="helper">For admins only. This tells the server which Drive file to read; it never edits Google Drive.</p>
        <div class="button-row"><button class="btn secondary" onclick="saveSourceConfig('${htmlEscape(sourceType)}')">Save Source</button><button class="btn primary" onclick="syncSource('${htmlEscape(sourceType)}')">Sync & Verify</button>${download}</div>
        <div id="sourceActionMessage"></div>
        <div class="local-test-box"><div class="eyebrow">Manual upload</div><p class="helper">Upload a new workbook from this computer. It will be validated and compared first; it will not replace the active version until accepted.</p><input id="localTestFile" type="file" accept=".xlsx,.xlsm"><button class="btn secondary" onclick="uploadLocalTestSource('${htmlEscape(sourceType)}')">Upload & Verify</button></div>
      </div>
      <div class="panel"><div class="eyebrow">Validation protection</div><h2>Current active version</h2><p>A newly uploaded or synced workbook is staged as <b>Pending Review</b>. The active database stays unchanged until a user accepts the new version.</p>${s.last_error ? `<div class="alert error"><strong>Latest error:</strong> ${htmlEscape(s.last_error)}</div>` : '<div class="alert success">No current validation error.</div>'}</div>
    </div>${pendingHtml}
    <div class="section-title-row"><div><h2>Update history</h2><p class="muted">Every upload, sync, review decision and version is kept here. Times are shown in your device's local timezone.</p></div></div>
    <div class="table-card"><table class="history-table"><thead><tr><th>Time</th><th>Drive modified</th><th>Status</th><th>Records</th><th>File</th><th>Change summary</th></tr></thead><tbody>${data.history.length ? data.history.map(h => `<tr><td class="local-time" data-iso="${htmlEscape(h.synced_at)}">${htmlEscape(h.synced_at)}</td><td class="local-time" data-iso="${htmlEscape(h.drive_modified_time || '')}">${htmlEscape(h.drive_modified_time || '—')}</td><td><span class="status-badge status-${statusClass(h.status)}">${htmlEscape(h.status)}</span></td><td>${htmlEscape(h.record_count || 0)}</td><td>${htmlEscape(h.drive_file_name || '—')}</td><td>${htmlEscape(h.summary || h.error_message || '—')}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No version history yet.</td></tr>'}</tbody></table></div>`;
    document.querySelector('.page-heading').textContent = s.label;
    localizeTimes();
  } catch (e) {
    root.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

function historyLink(params) {
  const q = new URLSearchParams(params);
  const value = q.toString();
  return value ? `index.html?${value}` : 'index.html';
}

function filterHistoryRows(value){
  const query=String(value||'').trim().toLowerCase();
  document.querySelectorAll('[data-history-search]').forEach(row=>{
    row.hidden=!!query && !row.dataset.historySearch.includes(query);
  });
}

function toggleHistorySelection(checked){
  document.querySelectorAll('.history-select').forEach(box=>{box.checked=checked;});
}

async function bulkDeleteHistory(scope,status='',reportType=''){
  const selected=Array.from(document.querySelectorAll('.history-select:checked')).map(box=>box.value);
  if(scope==='selected' && !selected.length){
    showToast('Select at least one report first.','info');
    return;
  }
  const label=scope==='selected'
    ? `${selected.length} selected report(s)`
    : `all ${status==='DRAFT'?'draft':'finalized'} report(s)${reportType ? ' in this report type' : ''}`;
  const ok=await askConfirm({
    title:'Permanently delete reports?',
    message:`This deletes ${label} and any saved final files. This cannot be undone.`,
    confirmText:'Permanently Delete',
    danger:true,
  });
  if(!ok)return;
  try{
    const data=await jsonFetch('/api/reports/delete-bulk',{
      method:'POST',
      body:JSON.stringify(scope==='selected' ? {report_ids:selected} : {status,report_type:reportType}),
    });
    showToast(data.message||'Reports deleted.','success');
    setTimeout(()=>location.reload(),450);
  }catch(e){showToast(e.message,'error');}
}

async function loadHistoryPage() {
  const params = new URLSearchParams(location.search);
  const reportId = params.get('report_id');
  if (reportId) return loadHistoryDetailPage(reportId);
  const root = document.getElementById('historyContent');
  const type = params.get('type') || '';
  const status = params.get('status') || '';
  try {
    const data = await jsonFetch(`/api/frontend/history?type=${encodeURIComponent(type)}&status=${encodeURIComponent(status)}`);
    const reportChips = [['', 'All'], ...Object.entries(data.labels)].map(([key, label]) => `<a class="chip ${data.selected_type === key || (!data.selected_type && !key) ? 'active' : ''}" href="${historyLink({...(key ? {type:key} : {}), ...(data.selected_status ? {status:data.selected_status} : {})})}">${htmlEscape(label)}</a>`).join('');
    const statusChips = [['', 'All'], ['DRAFT', 'Draft'], ['FINAL', 'Finalized']].map(([key, label]) => `<a class="chip ${data.selected_status === key || (!data.selected_status && !key) ? 'active' : ''}" href="${historyLink({...(data.selected_type ? {type:data.selected_type} : {}), ...(key ? {status:key} : {})})}">${htmlEscape(label)}</a>`).join('');
    root.innerHTML = `<div class="hero-card"><div><div class="eyebrow">Drafts & final files</div><h1>Continue drafts or download finished reports.</h1><p>Drafts stay editable. Final reports keep their generated files and source snapshot.</p></div></div>
    <div class="history-filter-stack"><div class="filter-row"><span class="filter-label">Report:</span>${reportChips}</div><div class="filter-row"><span class="filter-label">Status:</span>${statusChips}</div></div>
    <div class="history-bulk-bar"><input type="search" placeholder="Search reports" aria-label="Search reports" oninput="filterHistoryRows(this.value)"><div class="history-bulk-actions"><button class="btn secondary danger-outline" type="button" onclick="bulkDeleteHistory('selected')">Delete Selected</button><button class="btn secondary danger-outline" type="button" onclick="bulkDeleteHistory('status','DRAFT','${htmlEscape(data.selected_type)}')">Delete All Drafts</button><button class="btn secondary danger-outline" type="button" onclick="bulkDeleteHistory('status','FINAL','${htmlEscape(data.selected_type)}')">Delete All Finalized</button></div></div>
    <div class="table-card"><table class="history-table"><thead><tr><th class="history-select-cell"><input type="checkbox" aria-label="Select all reports" onchange="toggleHistorySelection(this.checked)"></th><th>Report</th><th>File name</th><th>Market</th><th>Period</th><th>Status</th><th>Created</th><th>Finalized</th><th>Actions</th></tr></thead><tbody>${data.reports.length ? data.reports.map(r => `<tr data-history-search="${htmlEscape([r.title,r.file_name,r.market,r.report_type,r.status,formatReportPeriod(r)].join(' ').toLowerCase())}"><td class="history-select-cell"><input class="history-select" type="checkbox" value="${htmlEscape(r.id)}" aria-label="Select ${htmlEscape(r.title)}"></td><td><strong>${htmlEscape(r.title)}</strong></td><td>${htmlEscape(r.file_name || (r.status === 'DRAFT' ? 'Draft not finalized' : '—'))}</td><td>${htmlEscape(r.market)}</td><td>${htmlEscape(formatReportPeriod(r))}</td><td><span class="status-badge status-${statusClass(r.status)}">${htmlEscape(r.status === 'DRAFT' ? 'Draft' : 'Final')}</span></td><td class="local-time" data-iso="${htmlEscape(r.created_at)}">${htmlEscape(r.created_at)}</td><td class="local-time" data-iso="${htmlEscape(r.finalized_at || '')}">${htmlEscape(r.finalized_at || '—')}</td><td><div class="history-actions">${r.status === 'DRAFT' && r.report_type !== 'mms-indent' ? `<a class="text-link" href="${relativeReportPath(r.report_type, r.id)}">Continue</a>` : `<a class="text-link" href="${historyLink({report_id:r.id})}">View</a>`}<button class="text-link danger-link" onclick="confirmDeleteReport('${htmlEscape(r.id)}','${htmlEscape(r.file_name || r.title)}')">Delete</button></div></td></tr>`).join('') : '<tr><td colspan="9" class="empty-cell">No reports match these filters.</td></tr>'}</tbody></table></div>`;
    localizeTimes();
  } catch (e) {
    root.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

async function loadHistoryDetailPage(reportId) {
  const root = document.getElementById('historyContent');
  try {
    const data = await jsonFetch(`/api/frontend/history/${encodeURIComponent(reportId)}`);
    const r = data.report;
    const sourceVersions = Object.entries(r.source_versions || {}).map(([key, s]) => `<div class="history-source"><strong>${htmlEscape(s.label)}</strong><span>${htmlEscape(s.drive_file_name || '—')}</span><small>Drive: <span class="local-time" data-iso="${htmlEscape(s.drive_modified_time || '')}">${htmlEscape(s.drive_modified_time || '—')}</span> · Synced: <span class="local-time" data-iso="${htmlEscape(s.last_successful_sync || '')}">${htmlEscape(s.last_successful_sync || '—')}</span></small></div>`).join('');
    const attachments = Object.entries(r.attachment_links || {}).map(([name, url]) => `<a class="btn secondary" href="${fileUrl(url)}">Download ${htmlEscape(name)}</a>`).join('');
    const audit = (r.audit_events || []).length ? `<div class="changes-list">${r.audit_events.map(a => `<div class="change-item"><strong>${htmlEscape(a.message)}</strong>${a.time ? `<small class="local-time" data-iso="${htmlEscape(a.time)}">${htmlEscape(a.time)}</small>` : ''}</div>`).join('')}</div>` : ((r.human_changes || []).length ? `<div class="changes-list">${r.human_changes.map(c => `<div class="change-item">${htmlEscape(c)}</div>`).join('')}</div>` : '<p class="muted">No manual changes were recorded.</p>');
    const mainAction = r.status === 'DRAFT' && r.report_type !== 'mms-indent'
      ? `<a class="btn primary" href="${relativeReportPath(r.report_type, r.id)}">Continue Draft</a>`
      : (r.output_file ? `<a class="btn primary" href="${fileUrl(`/report-download/${r.id}`)}">Download Final File</a>` : '');
    root.innerHTML = `<div class="source-detail-grid"><div class="panel"><div class="eyebrow">Report record</div><h2>${htmlEscape(r.title)} · ${htmlEscape(formatReportPeriod(r))}</h2><div class="detail-list"><div><span>File name</span><b>${htmlEscape(r.file_name || (r.status === 'DRAFT' ? 'Draft not finalized' : '—'))}</b></div><div><span>Market</span><b>${htmlEscape(r.market)}</b></div><div><span>Status</span><b>${htmlEscape(r.status === 'DRAFT' ? 'Draft' : 'Final')}</b></div><div><span>Created</span><b class="local-time" data-iso="${htmlEscape(r.created_at)}">${htmlEscape(r.created_at)}</b></div><div><span>Updated</span><b class="local-time" data-iso="${htmlEscape(r.updated_at)}">${htmlEscape(r.updated_at)}</b></div><div><span>Finalized</span><b class="local-time" data-iso="${htmlEscape(r.finalized_at || '')}">${htmlEscape(r.finalized_at || '—')}</b></div></div>${mainAction}${attachments}</div><div class="panel"><div class="eyebrow">Imported from</div><h2>Source versions used</h2>${sourceVersions}</div></div><div class="panel"><div class="eyebrow">Documentation</div><h2>Manual changes</h2>${audit}</div>`;
    document.querySelector('.page-heading').textContent = r.title;
    localizeTimes();
  } catch (e) {
    root.innerHTML = `<div class="alert error">${htmlEscape(e.message)}</div>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.page === 'home') loadHomePage();
  if (document.body.dataset.page === 'report') loadReportPage();
  if (document.body.dataset.page === 'database') loadDatabasePage();
  if (document.body.dataset.page === 'history') loadHistoryPage();
});
