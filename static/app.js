/* Aegis v2 presentation. Persistent data uses the local API; provider keys stay in memory. */
'use strict';

const ICON_PATHS = {
  shield: 'M12 3 20 6v6c0 5-8 9-8 9S4 17 4 12V6Z M8 12l3 3 5-6',
  layers: 'm12 3 9 5-9 5-9-5Z M3 12l9 5 9-5 M3 16l9 5 9-5',
  checklist: 'M9 5h11 M9 12h11 M9 19h11 M3 5l1 1 2-3 M3 12l1 1 2-3 M3 19l1 1 2-3',
  report: 'M6 3h9l4 4v14H6Z M14 3v5h5 M9 12h7 M9 16h7',
  book: 'M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1Z M12 5v15',
  settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  cloud: 'M6 18a5 5 0 0 1-1-10 7 7 0 0 1 13-1 5 5 0 0 1 0 11Z',
  sparkles: 'm12 3 2 6 6 3-6 2-2 7-2-7-6-2 6-3Z M20 2v4 M18 4h4',
  check: 'm5 12 4 4L19 6',
  database: 'M20 5c0 2-16 2-16 0s16-2 16 0Z M4 5v14c0 3 16 3 16 0V5 M4 11c0 3 16 3 16 0 M4 16c0 3 16 3 16 0',
  lock: 'M5 11h14v10H5Z M8 11V7a4 4 0 0 1 8 0v4 M12 15v2',
  cpu: 'M6 6h12v12H6Z M10 10h4v4h-4Z M9 3v3 M15 3v3 M9 18v3 M15 18v3 M3 9h3 M3 15h3 M18 9h3 M18 15h3',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M3 12h18 M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
  archive: 'M3 4h18v5H3Z M5 9v12h14V9 M9 13h6',
  info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 11v6 M12 7v1',
  'arrow-right': 'M4 12h16 M14 6l6 6-6 6',
  'arrow-left': 'M20 12H4 M10 6l-6 6 6 6',
  'arrow-up-right': 'M6 18 18 6 M6 6h12v12',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  close: 'm6 6 12 12 M6 18 18 6',
  plus: 'M12 5v14 M5 12h14',
  folder: 'M3 6h7l2 3h9v11H3Z M3 6V4h7l2 2h7v3',
  dashboard: 'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z',
  search: 'M18 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z M15 15l6 6',
  save: 'M4 3h14l3 3v15H3V3Z M7 3v6h10V3 M7 21v-8h10v8',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 7v5l3 2'
};
const $ = id => document.getElementById(id);
const TOKEN = document.querySelector('meta[name=advisory-token]').content;
const ANSWERS = [['yes', 'Yes'], ['no', 'No'], ['not_sure', 'Not sure'], ['not_applicable', 'Not applicable']];
const COLORS = { planned: '#777777', gap: '#b52029', unresolved: '#aaaaaa', remaining: '#dddddd' };
const state = {
  kb: null, workspace: { folders: [], assessments: [] }, active: null,
  view: 'dashboard', folder: null, category: 0, report: null, reportId: null,
  dirty: false, editVersion: 0, navBusy: false, reloading: false, conflictAssessmentId: null,
  settings: { provider: 'mock', api_key: '', model: '' }
};
let saveTimer;
let saveQueue = Promise.resolve();
let toastTimer;

function icon(name) {
  return `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="${ICON_PATHS[name] || ICON_PATHS.layers}"></path></svg>`;
}
function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
}
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}
function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? 'Recently' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
function showError(error) {
  const conflict = error.status === 409 && state.active && (!error.assessmentId || error.assessmentId === state.active.id);
  if (conflict) state.conflictAssessmentId = state.active.id;
  $('app-error').innerHTML = `<span>${esc(error.message || String(error))}</span>${conflict ? '<button id="reload-saved" class="button button-secondary" type="button">Reload saved version</button>' : ''}`;
  $('app-error').hidden = false;
  toast(error.message || 'The operation could not be completed.');
}
function clearError() {
  $('app-error').hidden = true;
  $('app-error').replaceChildren();
  state.conflictAssessmentId = null;
}
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, {
    ...options,
    signal: AbortSignal.timeout(65000),
    headers: { 'X-Advisory-Token': TOKEN, ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers }
  }); } catch (error) { throw new Error(error.name === 'TimeoutError' ? 'The request timed out. Check the saved record before retrying; your local input is retained.' : 'The local application could not be reached. Check the connection and retry; your input is retained.'); }
  if (!response.ok) {
    let message = 'The request could not be completed. Please try again.';
    try { const data = await response.json(); if (typeof data.detail === 'string') message = data.detail; } catch {}
    if (response.status === 409 && !path.startsWith('/api/folders')) message = 'This assessment changed in another window. Your unsaved edits are still here. Reload the saved version to continue, or copy your notes first.';
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response;
}
async function jsonApi(path, options) { return (await api(path, options)).json(); }
function scopeQuestions(scope) {
  if (scope.mode === 'full') return state.kb.questions;
  if (scope.mode === 'partial') return state.kb.questions.filter(q => scope.categories.includes(q.category));
  const service = state.kb.services.find(item => item.id === scope.service_id);
  if (!service) return [];
  return state.kb.questions.filter(q => service.requirement_ids.includes(q.id) || (q.category === service.category && q.kind === 'control'));
}
function scopeCategories(scope) {
  const ids = new Set(scopeQuestions(scope).map(q => q.category));
  return state.kb.categories.filter(category => ids.has(category.id));
}
function scopeLabel(scope) {
  if (scope.mode === 'full') return 'Full assessment';
  if (scope.mode === 'single') return state.kb.services.find(service => service.id === scope.service_id)?.name || 'Single service';
  return 'Partial · ' + scopeCategories(scope).map(category => category.name).join(', ');
}
function completedQuestions(assessment) {
  return scopeQuestions(assessment.scope).filter(q => assessment.answers?.[q.id]).length;
}
function completion(summary) {
  const total = scopeQuestions(summary.scope).length;
  const answered = Math.min(total, Number(summary.answered_count ?? completedQuestions(summary)));
  return { total, answered, percent: total ? Math.round(answered / total * 100) : 0 };
}
function percentageParts(values) {
  const total = values.reduce((sum, n) => sum + n, 0);
  if (!total) return values.map(() => 0);
  const exact = values.map(value => value / total * 100);
  const rounded = exact.map(Math.floor);
  const order = exact.map((value, index) => ({ index, remainder: value - rounded[index] })).sort((a, b) => b.remainder - a.remainder);
  let remaining = 100 - rounded.reduce((sum, n) => sum + n, 0);
  order.forEach(item => { if (remaining > 0) { rounded[item.index]++; remaining--; } });
  return rounded;
}
function donutChart(parts, center, caption, ariaLabel) {
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const percentages = percentageParts(parts.map(part => part.value));
  let cumulative = 0;
  const gradient = total ? parts.map(part => {
    const start = cumulative;
    cumulative += part.value / total * 100;
    return `${part.color} ${start}% ${cumulative}%`;
  }).join(', ') : `${COLORS.remaining} 0% 100%`;
  return `<div class="donut-layout"><div class="donut-chart" role="img" aria-label="${esc(ariaLabel)}" style="background:conic-gradient(${gradient})"><div class="donut-hole"><strong>${esc(center)}</strong><span>${esc(caption)}</span></div></div><div class="chart-legend">${parts.map((part, index) => `<div class="legend-row"><span class="legend-color" style="background:${part.color}"></span><div><strong>${esc(part.label)}</strong><small>${part.value} ${esc(part.unit || 'questions')}</small></div><b>${percentages[index]}%</b></div>`).join('')}${!total ? '<p class="field-help">No entries in this scope yet.</p>' : ''}</div></div>`;
}

async function loadWorkspace() {
  state.workspace = await jsonApi('/api/workspace');
  if (state.folder && !state.workspace.folders.some(folder => folder.id === state.folder)) state.folder = null;
  renderFolders();
  renderDashboard();
  renderLibrary();
  renderPlanning();
}
function renderFolders() {
  $('library-count').textContent = state.workspace.assessments.length;
  const query = $('folder-search').value.trim().toLowerCase();
  const folders = [...state.workspace.folders].filter(f => f.name.toLowerCase().includes(query));
  folders.sort((a,b) => $('folder-sort').value === 'count' ? b.assessment_count - a.assessment_count : a.name.localeCompare(b.name));
  $('folder-list').innerHTML = folders.length ? folders.map(folder => `<article class="folder-card ${state.folder === folder.id ? 'selected' : ''}"><button class="folder-open" data-folder-id="${esc(folder.id)}">${icon('folder')}<span><strong>${esc(folder.name)}</strong><small>${folder.assessment_count} assessment${folder.assessment_count === 1 ? '' : 's'}</small></span>${icon('arrow-up-right')}</button><div class="folder-actions"><button data-rename-folder="${esc(folder.id)}" aria-label="Rename ${esc(folder.name)}">Rename</button><button data-delete-folder="${esc(folder.id)}" aria-label="Delete ${esc(folder.name)}">Delete folder</button></div></article>`).join('') : '<div class="sidebar-empty">' + (query ? 'No folders match your search.' : 'Create your first folder to organize assessments.') + '</div>';
}
function renderDashboard() {
  refreshSecurityOverview();
}
function assessmentRow(assessment) {
  const progress = completion(assessment);
  const folder = state.workspace.folders.find(item => item.id === assessment.folder_id);
  return `<button class="assessment-row" data-open-assessment="${esc(assessment.id)}"><span class="row-symbol">${icon(assessment.report_count ? 'report' : 'checklist')}</span><span class="row-info"><strong>${esc(assessment.title)}</strong><small>${esc(scopeLabel(assessment.scope))}${folder ? ' · ' + esc(folder.name) : ''}</small></span><span class="row-progress"><span>${progress.percent}% complete</span><span class="mini-track"><i style="width:${progress.percent}%"></i></span></span><span class="status-chip ${assessment.report_count ? 'advisory' : ''}">${assessment.report_count ? 'Advisory saved' : 'Draft'}</span><span class="row-arrow">${icon('arrow-right')}</span></button>`;
}
function emptyState(title, copy, create = false) {
  return `<div class="empty-state"><span>${icon('folder')}</span><h2>${esc(title)}</h2><p>${esc(copy)}</p>${create ? `<button class="button button-primary create-trigger">${icon('plus')}Create assessment</button>` : ''}</div>`;
}
function renderLibrary() {
  const folder = state.workspace.folders.find(item => item.id === state.folder);
  const recovered = state.workspace.folders.find(item => item.recovered && item.assessment_count > 0);
  $('recovery-notice').hidden = !recovered;
  if (recovered) $('recovery-notice').innerHTML = `<div><strong>Previous assessments recovered</strong><p>${recovered.assessment_count} older record${recovered.assessment_count === 1 ? '' : 's'} without a valid folder ${recovered.assessment_count === 1 ? 'is' : 'are'} available in ${esc(recovered.name)}. Answers and advisory history are preserved.</p></div><button class="button button-secondary" data-folder-id="${esc(recovered.id)}">Open recovered records${icon('arrow-right')}</button>`;
  $('library-title').textContent = folder ? folder.name : 'My assessments';
  $('library-all-folders').hidden = !state.folder;
  const query = $('assessment-search').value.trim().toLowerCase();
  const filter = $('library-filter').value;
  const items = state.workspace.assessments.filter(a => (!state.folder || a.folder_id === state.folder) && a.title.toLowerCase().includes(query) && (filter === 'all' || (filter === 'advisory' ? a.report_count > 0 : !a.report_count)));
  items.sort((a,b) => $('library-sort').value === 'name' ? a.title.localeCompare(b.title) : String(b.updated_at).localeCompare(String(a.updated_at)));
  $('library-list').innerHTML = items.length ? items.map(assessment => {
    const progress = completion(assessment);
    const folderName = state.workspace.folders.find(item => item.id === assessment.folder_id)?.name;
    return `<article class="assessment-library-card"><div class="library-card-top"><span class="row-symbol">${icon(assessment.report_count ? 'report' : 'checklist')}</span><span class="status-chip ${assessment.report_count ? 'advisory' : ''}">${assessment.report_count ? `${assessment.report_count} advisory version${assessment.report_count === 1 ? '' : 's'}` : 'Draft'}</span></div><h2>${esc(assessment.title)}</h2><p>${esc(scopeLabel(assessment.scope))}</p>${folderName ? `<div class="folder-tag">${icon('folder')}${esc(folderName)}</div>` : '<div class="folder-tag">All assessments</div>'}<div class="library-card-bottom"><small>Updated ${esc(dateLabel(assessment.updated_at))}</small><button class="button button-secondary" data-open-assessment="${esc(assessment.id)}">View record${icon('arrow-right')}</button></div></article>`;
  }).join('') : emptyState(query || filter !== 'all' ? 'No matching assessments' : 'No assessments here yet', query || filter !== 'all' ? 'Try another search or status filter.' : 'Create an assessment in this folder to get started.', !query && filter === 'all');
}
function renderPlanning() {
  const selected = $('notes-assessment').value;
  $('notes-assessment').innerHTML = '<option value="">Choose an assessment</option>' + state.workspace.assessments.map(a => `<option value="${esc(a.id)}">${esc(a.title)}</option>`).join('');
  $('notes-assessment').value = selected;
}

function updateSaveStatus(text, dirty = false) {
  $('save-state').innerHTML = icon(dirty ? 'clock' : 'database') + esc(text);
  $('save-state').classList.toggle('unsaved', dirty);
}
function markEdited() {
  state.editVersion++;
  state.dirty = true;
  updateSaveStatus('Unsaved changes', true);
  clearTimeout(saveTimer);
  // Edits are explicit: Save draft, Finish editing, or navigation saves the draft.
}
function saveActive() {
  clearTimeout(saveTimer);
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    if (!state.active || !state.dirty || state.reloading) return;
    const id = state.active.id;
    const version = state.editVersion;
    const payload = {
      title: state.active.title.trim(), answers: { ...state.active.answers },
      notes: state.active.notes || '', plan_status: state.active.plan_status || 'planning',
      revision: state.active.revision,
      selected_category: activeCategories()[state.category]?.id || null
    };
    updateSaveStatus('Saving assessment…', true);
    try {
      if (!payload.title) throw new Error('Enter an assessment name before saving. Your edits are retained.');
      const saved = await jsonApi(`/api/assessments/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(payload) });
      if (state.active?.id === id) {
        if (state.editVersion === version) {
          state.active = saved;
          state.dirty = false;
          updateSaveStatus('All changes saved');
        } else {
          state.active.revision = saved.revision;
          state.active.updated_at = saved.updated_at;
          updateSaveStatus('New changes waiting to save', true);
        }
        renderHistory();
      }
      await loadWorkspace();
    } catch (error) {
      updateSaveStatus(error.status === 409 ? 'Save conflict · unsaved edits retained' : 'Save failed · retry Save draft', true);
      error.assessmentId = id;
      throw error;
    }
  });
  return saveQueue;
}
async function flushSave() {
  if (notesDirty) throw new Error('Save your remediation notes before leaving this page.');
  await saveActive();
  if (state.dirty) await saveActive();
}
function setView(view) {
  if (!['dashboard', 'library', 'planning', 'assessment', 'review', 'report'].includes(view)) return;
  state.view = view;
  for (const name of ['dashboard', 'library', 'planning', 'assessment', 'review', 'report']) $(`${name}-view`).hidden = name !== view;
  document.querySelectorAll('nav [data-view]').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('breadcrumb-title').textContent = { dashboard: 'Home', library: 'Assessment library', planning: 'Remediation notes', assessment: 'Edit assessment', review: 'Assessment record', report: 'Advisory report' }[view];
  window.scrollTo({ top: 0, behavior: 'instant' });
}
async function navigate(view) {
  if (state.navBusy) return;
  state.navBusy = true;
  try { await flushSave(); clearError(); if (view === 'assessment' || view === 'review') { renderAssessmentReview(); view = state.active ? 'review' : 'library'; } setView(view); } catch (error) { showError(error); } finally { state.navBusy = false; }
}
async function openAssessment(id) {
  if (state.navBusy) return;
  state.navBusy = true;
  try {
    await flushSave();
    const assessment = await jsonApi(`/api/assessments/${encodeURIComponent(id)}`);
    state.active = assessment;
    state.dirty = false;
    state.editVersion = 0;
    const categories = activeCategories();
    state.category = Math.max(0, categories.findIndex(category => category.id === assessment.selected_category));
    state.report = null;
    state.reportId = null;
    clearError();
    $('resume-assessment').hidden = false;
    renderAssessmentReview();
    setView('review');
    updateSaveStatus('All changes saved');
  } catch (error) { showError(error); } finally { state.navBusy = false; }
}
async function reloadSavedAssessment() {
  const id = state.conflictAssessmentId;
  if (!id || state.active?.id !== id || state.navBusy) return;
  if (!window.confirm('Reload the latest saved assessment? Your unsaved local answers and planning notes will be discarded after the saved version loads successfully.')) return;
  state.navBusy = true;
  state.reloading = true;
  const draftVersion = state.editVersion;
  clearTimeout(saveTimer);
  const button = $('reload-saved');
  if (button) button.disabled = true;
  try {
    // Let an in-flight save finish before reading; queued saves skip while reloading.
    await saveQueue.catch(() => {});
    const fresh = await jsonApi(`/api/assessments/${encodeURIComponent(id)}`);
    // Keep the local draft untouched unless the fresh saved copy was retrieved.
    if (state.active?.id !== id) return;
    if (state.editVersion !== draftVersion) throw new Error('The local draft changed while the saved version was loading. Review your new edits and try reload again.');
    state.active = fresh;
    state.dirty = false;
    state.editVersion = 0;
    const categories = activeCategories();
    state.category = Math.max(0, categories.findIndex(category => category.id === fresh.selected_category));
    state.report = null;
    state.reportId = null;
    saveQueue = Promise.resolve();
    if (state.view === 'review') renderAssessmentReview(); else renderAssessment();
    clearError();
    updateSaveStatus('Latest saved version loaded');
    toast('Saved assessment loaded. Your local draft now matches the saved version.');
  } catch (error) {
    // Failed reloads preserve answers, notes, revision, and the recovery action.
    updateSaveStatus('Reload failed · unsaved edits retained', true);
    $('app-error').innerHTML = `<span>${esc(error.message || 'Could not reload the saved assessment.')} Your unsaved local draft is still retained.</span><button id="reload-saved" class="button button-secondary" type="button">Reload saved version</button>`;
    $('app-error').hidden = false;
    toast('Could not reload. Your unsaved local draft is still here.');
  } finally {
    state.reloading = false;
    state.navBusy = false;
    const reload = $('reload-saved');
    if (reload) reload.disabled = false;
  }
}
function activeQuestions() { return state.active ? scopeQuestions(state.active.scope) : []; }
function activeCategories() { return state.active ? scopeCategories(state.active.scope) : []; }
function questionsForCategory(id) { return activeQuestions().filter(q => q.category === id); }
function renderAssessment() {
  $('assessment-empty').hidden = !!state.active;
  $('assessment-editor').hidden = !state.active;
  if (!state.active) return;
  $('project-title').value = state.active.title;


  const folder = state.workspace.folders.find(item => item.id === state.active.folder_id);
  $('assessment-meta').textContent = `${folder ? folder.name + ' · ' : ''}Created ${dateLabel(state.active.created_at)} · Saved in web workspace`;
  const selected = activeCategories();
  const omitted = state.kb.categories.filter(category => !selected.some(item => item.id === category.id));
  $('assessment-scope').innerHTML = `<span class="scope-symbol">${icon(state.active.scope.mode === 'full' ? 'layers' : state.active.scope.mode === 'single' ? 'cpu' : 'checklist')}</span><div><strong>${esc(scopeLabel(state.active.scope))}</strong><p>${activeQuestions().length} questions · ${selected.length} ${selected.length === 1 ? 'category' : 'categories'}${omitted.length ? ' · Outside scope: ' + esc(omitted.map(item => item.name).join(', ')) : ' · All five service categories'}</p></div><span class="small-badge">SCOPE SET AT CREATION</span>`;

  renderCategory();
  renderHistory();
  providerSummary();
}
function renderCategory(focus = false) {
  if (!state.active) return;
  const categories = activeCategories();
  state.category = Math.min(state.category, categories.length - 1);
  const category = categories[state.category];
  if (!category) return;
  const questions = questionsForCategory(category.id);
  $('category-tabs').innerHTML = categories.map((item, index) => `<button class="category-tab ${index === state.category ? 'active' : ''}" id="tab-${esc(item.id)}" role="tab" aria-selected="${index === state.category}" aria-controls="checklist" tabindex="${index === state.category ? '0' : '-1'}" data-category="${index}">${icon(item.icon)}${esc(item.name)}</button>`).join('');
  $('category-counter').textContent = `CATEGORY ${state.category + 1} OF ${categories.length}`;
  $('category-icon').innerHTML = icon(category.icon);
  $('category-name').textContent = state.active.scope.mode === 'single' ? state.kb.services.find(item => item.id === state.active.scope.service_id).name : category.name;
  $('category-subtitle').textContent = state.active.scope.mode === 'single' ? 'Focused service review · five risk dimensions' : category.subtitle;
  $('checklist').setAttribute('role', 'tabpanel');
  $('checklist').setAttribute('aria-labelledby', `tab-${category.id}`);
  let number = 0;
  const groups = [
    {title: 'Deployment requirements', icon: 'layers', questions: questions.filter(q => q.kind === 'requirement'), kind: 'requirement'},
    ...state.kb.dimensions.map(dimension => ({title: dimension, icon: {'Identity and Access Management':'lock','Network Security':'globe','Data Security':'database','Monitoring':'search','Configuration':'settings'}[dimension] || 'shield', questions: questions.filter(q => q.kind === 'control' && q.dimension === dimension), kind: 'control'}))
  ].filter(group => group.questions.length);
  $('checklist').innerHTML = groups.map((group, index) => `<section class="dimension-card" aria-labelledby="dimension-heading-${index}"><header class="dimension-heading"><span class="dimension-symbol">${icon(group.icon)}</span><div><span class="dimension-kicker">${group.kind === 'control' ? 'RISK DIMENSION' : 'DEPLOYMENT SCOPE'}</span><h3 id="dimension-heading-${index}">${esc(group.title)}</h3></div><span class="dimension-progress"></span></header><div class="dimension-questions">${group.questions.map(q => {
    number++;
    return `<fieldset class="question"><legend><span class="question-number">${String(number).padStart(2, '0')}</span>${esc(q.text)}</legend><p id="help-${esc(q.id)}">${esc(q.help)}</p><div class="answer-options">${ANSWERS.map(([value, label]) => `<label class="answer-option"><input type="radio" name="${esc(q.id)}" value="${value}" aria-describedby="help-${esc(q.id)}" ${state.active.answers[q.id] === value ? 'checked' : ''}><span class="radio-dot"></span>${label}</label>`).join('')}</div></fieldset>`;
  }).join('')}</div></section>`).join('');
  $('previous-category').disabled = state.category === 0;
  $('next-category').innerHTML = state.category === categories.length - 1 ? `Generate advisory${icon('sparkles')}` : `Continue to ${esc(categories[state.category + 1].name.toLowerCase())}${icon('arrow-right')}`;
  updateProgress();
  if (focus) { $('category-tabs').scrollIntoView({ behavior: 'smooth', block: 'start' }); $(`tab-${category.id}`).focus({ preventScroll: true }); }
}
function updateProgress() {
  if (!state.active) return;
  document.querySelectorAll('.dimension-card').forEach(card => {
    const total = card.querySelectorAll('fieldset').length;
    const answered = card.querySelectorAll('input:checked').length;
    card.querySelector('.dimension-progress').textContent = `${answered} / ${total} answered`;
    card.classList.toggle('dimension-complete', answered === total);
  });
  const questions = activeQuestions();
  const answered = questions.filter(q => state.active.answers[q.id]).length;
  const percent = questions.length ? Math.round(answered / questions.length * 100) : 0;
  $('progress-percent').textContent = percent + '%';
  $('progress-copy').textContent = `${answered} of ${questions.length} scoped questions answered`;
  $('progress-fill').style.width = percent + '%';
  $('generate-report').disabled = answered !== questions.length;
  $('generate-hint').textContent = answered === questions.length ? 'Ready to generate and save a report version.' : 'Answer every question in this scope to generate.';
  $('progress-categories').innerHTML = activeCategories().map(category => {
    const items = questionsForCategory(category.id);
    const done = items.filter(q => state.active.answers[q.id]).length;
    return `<div class="progress-row ${done === items.length ? 'complete' : ''}">${icon(category.icon)}<span>${esc(category.name)}</span><strong>${done} / ${items.length}</strong></div>`;
  }).join('');
  const current = questionsForCategory(activeCategories()[state.category]?.id);
  const done = current.filter(q => state.active.answers[q.id]).length;
  $('category-progress').textContent = `${done} / ${current.length} answered`;
  $('category-footer-status').textContent = `${done} of ${current.length} complete`;
}
function renderHistory() {
  if (!state.active) return;
  const reports = state.active.reports || [];
  $('report-history').innerHTML = reports.length ? reports.map((report, index) => `<button class="history-row" data-report-id="${esc(report.id)}"><span class="row-symbol">${icon('report')}</span><div><strong>Open advisory snapshot ${reports.length - index}</strong><small>${esc(dateLabel(report.created_at))} · ${esc(report.provider_used || 'mock')} explanations</small><p>${esc(report.summary || 'Saved report snapshot')}</p></div><span>${icon('arrow-right')}</span></button>`).join('') : '<div class="panel-empty">No advisory versions yet. Complete the scoped questions and generate your first report.</div>';

}
function changeCategory(index) {
  state.category = Number(index);
  if (state.active) { state.active.selected_category = activeCategories()[state.category]?.id; markEdited(); }
  renderCategory(true);
}

function safeLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && ['docs.aws.amazon.com', 'www.cisecurity.org', 'www.nist.gov', 'nvlpubs.nist.gov'].includes(url.hostname)) return esc(url.href);
  } catch {}
  return '#';
}
function bulletList(items) { return `<ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>`; }
function reportOriginNotice(report) {
  const remote = report.provider_used !== 'mock' && Object.keys(report.ai_explanations || {}).length > 0;
  const provider = { claude: 'Claude', groq: 'Groq' }[report.provider_used] || report.provider_used;
  return `<aside class="expert-review-notice" aria-label="Explanation source and expert review status">
    <strong>Not reviewed by a cybersecurity expert</strong>
    <p>${remote ? `${esc(provider)} API succeeded for this saved advisory. Its explanation text passed the application's format and basic content checks; these checks do not verify factual accuracy.` : 'This advisory uses offline explanations. No successful AI-generated explanation is included.'}</p>
    <div class="explanation-roles"><p><b>Rule-based findings</b>Services, risks, actions, framework mappings, and AWS links come from the assessment rules.</p><p><b>${remote ? esc(provider) + ' explanations' : 'Offline explanations'}</b>${remote ? 'AI adds plain-language context beneath each service. It does not create or change the security findings.' : 'The application explains the rule-based findings without AI.'}</p></div>
    <small>This is the application’s review notice, not text generated by AI. Expert review is still needed before relying on the guidance.</small>
  </aside>`;
}

function renderReport() {
  const report = state.report;
  if (!report) return;
  const scope = report.scope || { mode: 'full', categories: [], service_id: null };
  const risks = report.recommendations.flatMap(service => service.risks);
  const concerns = [...report.unresolved, ...report.contradictions];
  const isChanged = state.active && (state.active.title !== report.title || JSON.stringify(state.active.answers) !== JSON.stringify(report.answers));
  let html = `<section class="panel report-summary"><div class="eyebrow">${esc(report.id)} · SAVED SNAPSHOT</div><h2>${esc(report.title)}</h2><div class="report-meta">${esc(dateLabel(report.created_at))} · ${esc(scopeLabel(scope))} · Knowledge base ${esc(report.knowledge_base_version)}</div><p>${esc(report.summary)}</p>${reportOriginNotice(report)}${report.provider_used === 'mock' ? `<div class="report-notice">${esc(report.provider_notice)}</div>` : ''}${isChanged ? '<div class="report-notice warning">This is a previous snapshot. Your current draft has different answers. Generate a new advisory to include those changes.</div>' : ''}<p class="field-help">${esc(report.review_status)}</p></section>`;
  if (report.excluded_categories?.length) html += `<div class="scope-banner outside-scope"><span>${icon('info')}</span><div><strong>Outside this assessment</strong><p>${esc(report.excluded_categories.map(id => state.kb.categories.find(category => category.id === id)?.name || id).join(', '))}. These categories were not assessed and are excluded from all percentages.</p></div></div>`;
  if (concerns.length) html += `<section class="uncertainty-box"><h2>Clarify these requirements</h2>${bulletList(concerns)}</section>`;
  if (!report.recommendations.length) html += '<section class="uncertainty-box"><h2>No covered service selected</h2><p>Your stated requirements do not establish an applicable covered service. This does not establish a secure or deployable architecture.</p></section>';
  html += '<div class="advisory-service-grid">';
  for (const service of report.recommendations) {
    const category = state.kb.categories.find(item => item.id === service.category);
    html += `<article class="service-report panel"><header class="service-header"><span class="category-icon">${icon(category?.icon || 'cloud')}</span><div><div class="eyebrow">${esc(service.category)}</div><h2>${esc(service.name)}</h2></div><span class="status-chip ${service.conditional ? 'conditional' : 'advisory'}">${service.conditional ? 'Conditional' : 'Applicable'}</span></header><div class="service-body"><p class="service-reason">${esc(service.reason)}</p><div class="risk-dimension-grid">${service.risks.map(risk => `<details class="risk-card"><summary class="risk-topline"><div><span class="dimension-tag">${esc(risk.dimension)}</span><h3>${esc(risk.title)}</h3></div><div class="risk-badges"><span class="risk-badge ${esc(risk.status)}">${esc({ gap: 'Control gap', unresolved: 'Unresolved', planned: 'Planned · unverified', not_applicable: 'Not applicable' }[risk.status] || risk.status)}</span><span class="risk-badge">${esc(risk.severity)} advisory priority</span></div><span class="risk-toggle" aria-hidden="true">+</span></summary><div class="risk-body"><p>${esc(risk.description)}</p><div class="risk-action"><strong>Recommended action</strong>${esc(risk.action)}</div><div class="risk-frameworks">${risk.frameworks.map(reference => `<a href="${safeLink(reference.url)}" target="_blank" rel="noopener noreferrer">${esc(reference.framework)} · ${esc(reference.reference)} ↗</a>`).join('')}</div><a class="aws-link" href="${safeLink(risk.aws_doc_url)}" target="_blank" rel="noopener noreferrer">Official AWS documentation ${icon('arrow-up-right')}</a></div></details>`).join('')}</div>${report.ai_explanations[service.service_id] ? `<div class="ai-note"><strong>${report.provider_used === 'mock' ? 'OFFLINE EXPLANATION' : 'AI EXPLANATION · NOT REVIEWED BY AN EXPERT'}</strong>${esc(report.ai_explanations[service.service_id])}</div>` : ''}</div></article>`;
  }
  html += `</div><details class="limitations"><summary><h2>Limitations and uncertainty</h2><span aria-hidden="true">+</span></summary><div class="risk-body">${bulletList(report.limitations)}<p>These findings describe stated requirements. They do not verify security, compliance, or deployment readiness.</p></div></details>`;
  $('report-content').innerHTML = html;
  $('export-md').disabled = !state.reportId;
  $('export-txt').disabled = !state.reportId;
}
async function openReport(id) {
  if (state.navBusy) return;
  state.navBusy = true;
  try {
    await flushSave();
    const response = await jsonApi(`/api/reports/${encodeURIComponent(id)}`);
    state.report = response.report;
    state.reportId = response.handle || id;
    renderReport();
    clearError();
    setView('report');
  } catch (error) { showError(error); } finally { state.navBusy = false; }
}
async function generateReport() {
  if (!state.active) { openCreate(); return; }
  const missing = activeQuestions().find(q => !state.active.answers[q.id]);
  if (missing) {
    state.category = activeCategories().findIndex(category => category.id === missing.category);
    renderCategory(true);
    toast('Answer all questions in this scope. Choose “Not sure” for an unresolved requirement.');
    return;
  }
  $('busy-overlay').hidden = false;
  try {
    await flushSave();
    const response = await jsonApi('/api/evaluate', {
      method: 'POST', body: JSON.stringify({ title: state.active.title, answers: state.active.answers,
        scope: state.active.scope, assessment_id: state.active.id, revision: state.active.revision, settings: state.settings })
    });
    state.active = response.assessment;
    state.dirty = false;
    state.editVersion = 0;
    state.report = response.report;
    state.reportId = response.handle;
    await loadWorkspace();
    renderHistory();
    renderReport();
    clearError();
    updateSaveStatus('Advisory snapshot saved');
    setView('report');
    toast('Your advisory was generated and saved to assessment history.');
  } catch (error) { showError(error); } finally { $('busy-overlay').hidden = true; }
}
async function exportReport(format) {
  if (!state.reportId) return;
  try {
    const response = await api(`/api/reports/${encodeURIComponent(state.reportId)}/export?format=${format}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = (response.headers.get('content-disposition') || '').match(/filename="([^"]+)"/)?.[1] || `aegis-advisory.${format}`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Advisory exported.');
  } catch (error) { showError(error); }
}

function creationScope() {
  const mode = document.querySelector('input[name=assessment-mode]:checked').value;
  return { mode, categories: mode === 'partial' ? [...$('scope-categories').querySelectorAll('input:checked')].map(input => input.value) : [], service_id: mode === 'single' ? $('scope-service').value : null };
}
function updateScopePreview() {
  const scope = creationScope();
  $('partial-options').hidden = scope.mode !== 'partial';
  $('single-options').hidden = scope.mode !== 'single';
  const count = scopeQuestions(scope).length;
  $('scope-preview').innerHTML = icon('info') + `<span>${count} questions in this scope. ${scope.mode === 'full' ? 'All five categories are included.' : 'Other categories are outside scope and will not count toward charts or percentages.'}</span>`;
}
function openCreate() {
  if (!state.kb) return;
  if (state.view !== 'library') { setView('library'); }
  if (!state.workspace.folders.length) { showError(new Error('Create a folder in the library before creating an assessment.')); $('new-folder').focus(); return; }
  $('create-title').value = '';
  $('create-folder').innerHTML = '<option value="" disabled>Select a folder (required)</option>' + state.workspace.folders.map(folder => `<option value="${esc(folder.id)}">${esc(folder.name)}</option>`).join('');
  $('create-folder').value = state.folder || '';
  document.querySelector('input[name=assessment-mode][value=full]').checked = true;
  $('scope-categories').innerHTML = state.kb.categories.map(category => `<label><input type="checkbox" value="${esc(category.id)}">${icon(category.icon)}${esc(category.name)}</label>`).join('');
  $('scope-service').innerHTML = state.kb.services.map(service => `<option value="${esc(service.id)}">${esc(service.name)}</option>`).join('');
  updateScopePreview();
  $('create-dialog').showModal();
  $('create-title').focus();
}
async function createAssessment(event) {
  event.preventDefault();
  const title = $('create-title').value.trim();
  const scope = creationScope();
  if (!$('create-folder').value) { dialogError('create-dialog', 'Choose the folder where this assessment will be saved.'); $('create-folder').focus(); return; }
  if (!title) { $('create-title').focus(); dialogError('create-dialog', 'Enter an assessment name.'); return; }
  if (scope.mode === 'partial' && !scope.categories.length) { dialogError('create-dialog', 'Select at least one category.'); return; }
  $('create-submit').disabled = true;
  try {
    await flushSave();
    const assessment = await jsonApi('/api/assessments', { method: 'POST', body: JSON.stringify({ title, folder_id: $('create-folder').value || null, scope }) });
    state.active = assessment;
    state.category = 0;
    state.dirty = false;
    state.editVersion = 0;
    state.report = null;
    state.reportId = null;
    $('create-dialog').close();
    $('resume-assessment').hidden = false;
    renderAssessment();
    clearError();
    updateSaveStatus('New assessment saved');
    setView('assessment');
    await loadWorkspace();
    toast('Assessment created. Save your draft when you finish editing.');
  } catch (error) { if ($('create-dialog').open) dialogError('create-dialog', error.message); else showError(error); } finally { $('create-submit').disabled = false; }
}
async function createFolder(event) {
  event.preventDefault();
  const name = $('folder-name').value.trim();
  if (!name) { dialogError('folder-dialog', 'Enter a folder name.'); return; }
  $('folder-submit').disabled = true;
  try {
    await flushSave();
    const folder = await jsonApi('/api/folders', { method: 'POST', body: JSON.stringify({ name }) });
    $('folder-dialog').close();
    state.workspace.folders.push(folder);
    state.folder = folder.id;
    renderFolders();
    renderLibrary();
    setView('library');
    clearError();
    await loadWorkspace();
    toast('Folder created. Add assessments to keep related plans together.');
  } catch (error) { if ($('folder-dialog').open) dialogError('folder-dialog', error.message); else showError(error); } finally { $('folder-submit').disabled = false; }
}
function providerFields() {
  $('provider-test-status').textContent = 'Not tested. Test a sample explanation before generating (provider usage charges may apply).';
  const provider = $('provider-select').value;
  $('remote-settings').hidden = !['groq', 'claude'].includes(provider);
  $('model-settings').hidden = provider === 'mock';
  $('model-name').placeholder = { claude: 'claude-sonnet-5-5', groq: 'qwen/qwen3.8-27b', }[provider] || '';
  $('provider-privacy').innerHTML = icon(provider === 'mock' ? 'lock' : 'info') + esc({ claude: 'Selected service and risk facts are sent to Anthropic when you generate.', mock: 'No data leaves your device in offline mode.', groq: 'Selected service and risk facts are sent to Groq when you generate.', }[provider]);
}
function openSettings() {
  $('provider-select').value = state.settings.provider;
  $('api-key').value = state.settings.api_key;
  $('model-name').value = state.settings.model;
  providerFields();
  $('settings-dialog').showModal();
}
function providerSummary() {
  const provider = state.settings.provider;
  $('provider-name').textContent = { claude: 'Anthropic Claude', mock: 'Offline mode', groq: 'Groq', }[provider];
  $('provider-description').textContent = provider === 'mock' ? 'Deterministic explanations' : 'Optional AI explanations';
  $('privacy-copy').textContent = provider === 'mock' ? 'No data is sent to an AI service.' : 'Selected facts are sent when you generate.';
}
function saveSettings(event) {
  event.preventDefault();
  const provider = $('provider-select').value;
  const model = $('model-name').value.trim();
  if (model && !/^[a-zA-Z0-9_.:/-]+$/.test(model)) { dialogError('settings-dialog', 'Use a valid model identifier.'); return; }
  if (['groq', 'claude'].includes(provider) && !$('api-key').value.trim()) { dialogError('settings-dialog', 'Enter an API key, or choose Offline mode.'); return; }
  state.settings = { provider, api_key: ['groq', 'claude'].includes(provider) ? $('api-key').value.trim() : '', model: provider === 'mock' ? '' : model };
  $('api-key').value = '';
  $('settings-dialog').close();
  providerSummary();
  toast('Provider saved for this session only.');
}
async function testProvider() {
  const button = $('test-provider');
  button.disabled = true;
  $('provider-test-status').textContent = 'Testing authentication, model access, and explanation output…';
  try {
    const result = await jsonApi('/api/providers/test', { method: 'POST', body: JSON.stringify({
      provider: $('provider-select').value, api_key: $('api-key').value.trim(), model: $('model-name').value.trim()
    }) });
    $('provider-test-status').textContent = `${result.ok ? 'Connection verified' : 'Connection failed'} · ${result.model}. ${result.message}`;
  } catch (error) { $('provider-test-status').textContent = error.message; }
  finally { button.disabled = false; }
}
function bindEvents() {
  $('reading-size').addEventListener('change', () => {
    document.documentElement.style.setProperty('--reading-scale', $('reading-size').value);
  });
  $('editor-snapshots').addEventListener('click', async () => {
    try { await flushSave(); renderAssessmentReview(); setView('review'); $('report-history').closest('section').scrollIntoView({block:'start'}); }
    catch (error) { showError(error); }
  });
  $('test-provider').addEventListener('click', testProvider);
  ['api-key', 'model-name'].forEach(id => $(id).addEventListener('input', () => { $('provider-test-status').textContent = 'Settings changed. Test again to verify.'; }));
  document.addEventListener('click', async event => {
    if (event.target.closest('#reload-saved')) { await reloadSavedAssessment(); return; }
    const create = event.target.closest('.create-trigger');
    if (create) { openCreate(); return; }
    const close = event.target.closest('[data-close]');
    if (close) { if (close.dataset.close === 'settings-dialog') $('api-key').value = ''; $(close.dataset.close).close(); return; }
    const view = event.target.closest('[data-view]');
    if (view) { event.preventDefault(); await navigate(view.dataset.view); return; }
    const open = event.target.closest('[data-open-assessment]');
    if (open) { await openAssessment(open.dataset.openAssessment); return; }
    const folder = event.target.closest('[data-folder-id]');
    if (folder) { await flushSave().then(() => { state.folder = folder.dataset.folderId; $('assessment-search').value = ''; $('library-filter').value = 'all'; renderFolders(); renderLibrary(); setView('library'); }).catch(showError); return; }
    const report = event.target.closest('[data-report-id]');
    if (report) { await openReport(report.dataset.reportId); return; }
    const category = event.target.closest('[data-category]');
    if (category) { changeCategory(category.dataset.category); return; }
    if (event.target.closest('.settings-trigger')) openSettings();
  });
  $('assessment-search').addEventListener('input', renderLibrary);
  $('library-filter').addEventListener('change', renderLibrary);
  $('library-all-folders').addEventListener('click', () => { state.folder = null; $('assessment-search').value = ''; $('library-filter').value = 'all'; renderFolders(); renderLibrary(); });
  $('new-folder').addEventListener('click', () => { $('folder-name').value = ''; $('folder-dialog').showModal(); $('folder-name').focus(); });
  $('folder-form').addEventListener('submit', createFolder);
  $('create-form').addEventListener('submit', createAssessment);
  $('create-form').addEventListener('change', updateScopePreview);
  $('checklist').addEventListener('change', event => {
    if (!state.active || event.target.type !== 'radio') return;
    state.active.answers[event.target.name] = event.target.value;
    markEdited();
    updateProgress();
  });
  $('project-title').addEventListener('input', event => { if (state.active) { state.active.title = event.target.value; markEdited(); } });
  $('save-assessment').addEventListener('click', async () => { try { await flushSave(); clearError(); toast('Assessment and planning notes saved.'); } catch (error) { showError(error); } });
  $('category-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const count = activeCategories().length;
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 : (state.category + (event.key === 'ArrowRight' ? 1 : count - 1)) % count;
    changeCategory(target);
  });
  $('previous-category').addEventListener('click', () => changeCategory(Math.max(0, state.category - 1)));
  $('next-category').addEventListener('click', () => state.category < activeCategories().length - 1 ? changeCategory(state.category + 1) : generateReport());
  $('generate-report').addEventListener('click', generateReport);

  $('edit-assessment').addEventListener('click', () => navigate('review'));
  $('export-md').addEventListener('click', () => exportReport('md'));
  $('export-txt').addEventListener('click', () => exportReport('txt'));
  $('provider-select').addEventListener('change', () => { $('api-key').value = ''; $('model-name').value = ''; providerFields(); });
  $('settings-form').addEventListener('submit', saveSettings);
  $('settings-dialog').addEventListener('cancel', () => { $('api-key').value = ''; });
  window.addEventListener('beforeunload', event => {
    if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
  });
}
async function init() {
  initWorkflowUI();
  initWebUI();
  initSecurityUI();
  hydrateIcons();
  bindEvents();
  try {
    state.kb = await jsonApi('/api/knowledge');
    await loadWorkspace();
    providerSummary();
    renderAssessment();
    setView('dashboard');
  } catch (error) { showError(error); updateSaveStatus('Workspace unavailable', true); }
}
init();

