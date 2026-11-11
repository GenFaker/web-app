/* Explicit record lifecycle: view -> edit -> save, with separate remediation notes. */
let notesRecord = null;
let notesDirty = false;
let notesRequest = 0;

function dialogError(id, message) {
  const dialog = $(id);
  let notice = dialog.querySelector('.dialog-error');
  if (!notice) {
    notice = document.createElement('p');
    notice.className = 'error-banner dialog-error';
    notice.setAttribute('role', 'alert');
    dialog.querySelector('form').prepend(notice);
  }
  notice.textContent = message || 'The operation failed. Your input has been retained; please retry.';
  notice.hidden = false;
  notice.scrollIntoView({ block: 'nearest' });
}

function initWorkflowUI() {
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('close', () => { const error = dialog.querySelector('.dialog-error'); if (error) error.remove(); });
  });
  $('finish-editing').onclick = async () => {
    try { await flushSave(); renderAssessmentReview(); setView('review'); } catch (error) { showError(error); }
  };
  $('assessment-record').addEventListener('click', async event => {
    if (event.target.closest('#begin-edit')) {
      if (!state.active) return;
      renderAssessment();
      setView('assessment');
      $('project-title').focus();
    }
    if (event.target.closest('#delete-assessment')) await deleteAssessment();
  });
  $('notes-assessment').onchange = async () => {
    if (notesDirty && !window.confirm('Discard your unsaved remediation notes and open another assessment?')) {
      $('notes-assessment').value = notesRecord?.id || '';
      return;
    }
    const request = ++notesRequest;
    const id = $('notes-assessment').value;
    notesDirty = false;
    notesRecord = null;
    $('notes-panel').hidden = true;
    if (!id) return;
    try {
      const record = await jsonApi(`/api/assessments/${encodeURIComponent(id)}`);
      if (request !== notesRequest) return;
      notesRecord = record;
      $('planning-notes').value = record.notes;
      $('plan-status').value = record.plan_status;
      $('notes-status').textContent = 'Saved notes loaded. Changes are saved only when you choose Save.';
      $('notes-panel').hidden = false;
    } catch (error) { if (request === notesRequest) showError(error); }
  };
  for (const id of ['planning-notes', 'plan-status']) $(id).addEventListener('input', () => {
    notesDirty = true;
    $('notes-status').textContent = 'Unsaved notes';
  });
  $('save-notes').onclick = async () => {
    if (!notesRecord || !notesDirty) return;
    $('save-notes').disabled = true;
    $('notes-assessment').disabled = true;
    $('planning-notes').disabled = true;
    $('plan-status').disabled = true;
    try {
      notesRecord = await jsonApi(`/api/assessments/${encodeURIComponent(notesRecord.id)}`, {
        method: 'PUT', body: JSON.stringify({ title: notesRecord.title, answers: notesRecord.answers,
          notes: $('planning-notes').value, plan_status: $('plan-status').value,
          revision: notesRecord.revision, selected_category: notesRecord.selected_category })
      });
      notesDirty = false;
      if (state.active?.id === notesRecord.id) state.active = notesRecord;
      $('notes-status').textContent = 'Remediation notes saved.';
      await loadWorkspace();
    } catch (error) {
      $('notes-status').textContent = error.status === 409 ? 'This record changed elsewhere. Copy your notes, then reselect the assessment to load the latest record. Your text remains here.' : 'Save failed. Your notes remain here; try Save again.';
    } finally {
      for (const id of ['save-notes', 'notes-assessment', 'planning-notes', 'plan-status']) $(id).disabled = false;
    }
  };
  window.addEventListener('beforeunload', event => { if (notesDirty) { event.preventDefault(); event.returnValue = ''; } });
}

function renderAssessmentReview() {
  if (!state.active) return;
  const assessment = state.active;
  const folder = state.workspace.folders.find(f => f.id === assessment.folder_id);
  const report = assessment.latest_report;
  const stale = report && (report.title !== assessment.title || Object.keys({...report.answers, ...assessment.answers}).some(key => report.answers[key] !== assessment.answers[key]));
  $('assessment-record').innerHTML = `<div class="page-heading"><div><div class="eyebrow">READ-ONLY ASSESSMENT RECORD</div><h1>${esc(assessment.title)}</h1><p>${esc(folder?.name || 'Legacy record · no folder')} / ${esc(scopeLabel(assessment.scope))}</p></div><div class="record-actions"><button id="begin-edit" class="button button-primary">Edit assessment</button><button id="delete-assessment" class="button button-danger">Delete assessment</button></div></div><section class="panel record-summary"><span class="status-chip">${report ? 'Advisory saved' : 'Draft · not yet assessed'}</span><p>Last saved ${esc(dateLabel(assessment.updated_at))} · Revision ${assessment.revision}</p><p>${esc(report?.summary || 'This record has no generated advisory. Use Edit assessment to complete the requirements and generate one.')}</p>${report ? `<button class="button button-secondary" data-report-id="${esc(assessment.latest_report_id)}">Read latest advisory</button>` : ''}</section><section class="panel record-answers"><h2>Recorded requirements & controls</h2><p>Saved responses are displayed below. Choose Edit assessment to change them.</p>${activeCategories().map(category => `<details><summary>${esc(category.name)}</summary><dl>${activeQuestions().filter(q => q.category === category.id).map(q => `<div><dt>${esc(q.text || q.summary)}</dt><dd>${esc(ANSWERS.find(([value]) => value === assessment.answers[q.id])?.[1] || 'Not answered')}</dd></div>`).join('')}</dl></details>`).join('')}</section>`;
  renderHistory();
  renderRecordManagement();
  if (stale) {
    const note = document.createElement('p');
    note.className = 'report-notice';
    note.textContent = 'These saved answers or the title changed after the latest advisory. Edit the assessment and generate a new advisory to update its findings.';
    $('assessment-record').querySelector('.record-summary').append(note);
  }
}

async function deleteAssessment() {
  if (!state.active || state.navBusy) return;
  const record = state.active;
  if (!window.confirm(`Delete “${record.title}” and all its saved advisory versions? This cannot be undone.`)) return;
  state.navBusy = true;
  const button = $('delete-assessment');
  button.disabled = true;
  try {
    await jsonApi(`/api/assessments/${encodeURIComponent(record.id)}?revision=${record.revision}`, { method: 'DELETE' });
    state.active = null;
    state.report = null;
    state.reportId = null;
    state.dirty = false;
    if (notesRecord?.id === record.id) { notesRecord = null; notesDirty = false; $('notes-panel').hidden = true; }
    $('resume-assessment').hidden = true;
    $('assessment-record').replaceChildren();
    $('report-content').replaceChildren();
    state.workspace.assessments = state.workspace.assessments.filter(item => item.id !== record.id);
    renderLibrary();
    setView('library');
    await loadWorkspace();
    toast('Assessment and advisory history deleted.');
  } catch (error) { showError(error); } finally { state.navBusy = false; button.disabled = false; }
}
