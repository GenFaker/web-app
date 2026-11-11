/* Browser workspace interactions, independent from the desktop application. */
let folderOperation = null;

function initWebUI() {
  $('refresh-library').onclick = async () => {
    $('refresh-library').disabled = true;
    try { await flushSave(); await loadWorkspace(); clearError(); toast('Workspace refreshed.'); }
    catch (error) { showError(error); }
    finally { $('refresh-library').disabled = false; }
  };
  $('folder-search').addEventListener('input', renderFolders);
  $('folder-sort').addEventListener('change', renderFolders);
  $('library-sort').addEventListener('change', renderLibrary);
  $('library-layout').onclick = () => {
    const list = $('library-list').classList.toggle('list-layout');
    $('library-layout').textContent = list ? 'Grid view' : 'List view';
    $('library-layout').setAttribute('aria-pressed', String(list));
  };
  $('graph-expand').onclick = () => {
    const expanded = document.querySelector('.network-panel').classList.toggle('expanded-map');
    $('graph-expand').textContent = expanded ? 'Close expanded map' : 'Expand map';
    $('graph-expand').setAttribute('aria-pressed', String(expanded));
    document.body.classList.toggle('map-open', expanded);
  };
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.querySelector('.expanded-map')) $('graph-expand').click();
  });
  $('folder-list').addEventListener('click', async event => {
    const rename = event.target.closest('[data-rename-folder]');
    const remove = event.target.closest('[data-delete-folder]');
    if (!rename && !remove) return;
    try {
      await flushSave();
      const id = rename?.dataset.renameFolder || remove.dataset.deleteFolder;
      const folder = state.workspace.folders.find(f => f.id === id);
      if (!folder) throw new Error('The folder no longer exists. Refresh the library.');
      folderOperation = { kind: rename ? 'rename' : 'delete', folder: {...folder} };
      $('folder-manage-title').textContent = rename ? 'Rename folder' : 'Delete folder';
      $('folder-new-name').value = folder.name;
      $('folder-rename-fields').hidden = !rename;
      $('folder-destination-fields').hidden = !!rename || !folder.assessment_count;
      const destinations = state.workspace.folders.filter(f => f.id !== id);
      $('folder-destination').innerHTML = '<option value="">Choose a destination folder</option>' + destinations.map(f => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');
      $('folder-manage-description').textContent = rename ? 'Update the name shown throughout your workspace.' : folder.assessment_count ? `“${folder.name}” contains ${folder.assessment_count} assessment(s). Move them to another folder before removing this folder.${destinations.length ? '' : ' Create another folder in the library first.'}` : `Delete the empty folder “${folder.name}”? This removes the folder permanently.`;
      $('folder-manage-submit').textContent = rename ? 'Save name' : folder.assessment_count ? 'Move assessments & delete folder' : 'Delete empty folder';
      $('folder-manage-submit').disabled = !rename && folder.assessment_count > 0 && !destinations.length;
      $('folder-manage-dialog').showModal();
      if (rename) $('folder-new-name').focus();
    } catch (error) { showError(error); }
  });
  $('folder-manage-form').onsubmit = async event => {
    event.preventDefault();
    if (!folderOperation || $('folder-manage-submit').disabled) return;
    const {kind, folder} = folderOperation;
    const name = $('folder-new-name').value.trim();
    const destination = $('folder-destination').value;
    if (kind === 'rename' && !name) { dialogError('folder-manage-dialog', 'Enter a folder name.'); return; }
    if (kind === 'delete' && folder.assessment_count && !destination) { dialogError('folder-manage-dialog', 'Choose a destination for the assessments.'); return; }
    $('folder-manage-submit').disabled = true;
    try {
      await jsonApi(`/api/folders/${encodeURIComponent(folder.id)}`, {
        method: kind === 'rename' ? 'PATCH' : 'DELETE',
        body: JSON.stringify(kind === 'rename' ? {name, revision: folder.revision} : {revision: folder.revision, destination_id: destination || null})
      });
      $('folder-manage-dialog').close();
      if (kind === 'delete' && state.folder === folder.id) state.folder = destination || null;
      await loadWorkspace();
      if (state.active) state.active = await jsonApi(`/api/assessments/${encodeURIComponent(state.active.id)}`);
      if (notesRecord && !notesDirty) notesRecord = await jsonApi(`/api/assessments/${encodeURIComponent(notesRecord.id)}`);
      toast(kind === 'rename' ? 'Folder renamed.' : 'Folder deleted. All contained assessments were preserved.');
    } catch (error) {
      if ($('folder-manage-dialog').open) dialogError('folder-manage-dialog', error.message + (error.status === 409 ? ' Close this dialog and refresh before retrying.' : ''));
      else showError(error);
    } finally { $('folder-manage-submit').disabled = false; }
  };
}

function renderRecordManagement() {
  const record = state.active;
  const panel = document.createElement('div');
  panel.className = 'record-folder-manager';
  panel.innerHTML = `<div><label for="record-folder">Assessment folder</label><p class="field-help">Move this record while preserving all advisory history.</p></div><select id="record-folder">${state.workspace.folders.map(folder => `<option value="${esc(folder.id)}" ${record.folder_id === folder.id ? 'selected' : ''}>${esc(folder.name)}</option>`).join('')}</select><button id="move-record" class="button button-secondary">Move record</button>`;
  $('assessment-record').querySelector('.record-summary').append(panel);
  $('move-record').onclick = async () => {
    const destination = $('record-folder').value;
    if (!destination || destination === record.folder_id) { toast('Choose a different folder to move this record.'); return; }
    $('move-record').disabled = true;
    try {
      state.active = await jsonApi(`/api/assessments/${encodeURIComponent(record.id)}/folder`, {
        method: 'PATCH', body: JSON.stringify({folder_id: destination, revision: record.revision})
      });
      await loadWorkspace();
      renderAssessmentReview();
      toast('Assessment moved. Advisory history preserved.');
    } catch (error) { showError(error); }
    finally { if ($('move-record')) $('move-record').disabled = false; }
  };
}
