/* Assessment-derived visuals. Reference nodes never imply discovered AWS resources. */
let securitySnapshot = null;
let securityAssessmentId = '';
let securityRequest = 0;
let graphSelected = null;
let graphTransform = { x: 0, y: 0, scale: 0.8 };
const graphNodePositions = new Map();
let graphIgnoreClickUntil = 0;

function initSecurityUI() {
  let theme = 'light';
  try { theme = localStorage.getItem('aegis-theme') || 'light'; } catch (_) { /* Optional preference storage. */ }
  function applyTheme(value) {
    document.documentElement.dataset.theme = value;
    $('theme-toggle').textContent = value === 'dark' ? 'Light mode' : 'Dark mode';
    $('theme-toggle').setAttribute('aria-pressed', String(value === 'light'));
  }
  applyTheme(theme);
  $('theme-toggle').onclick = () => {
    const value = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(value);
    try { localStorage.setItem('aegis-theme', value); } catch (_) { /* Session still works. */ }
  };
  $('security-assessment').onchange = () => { securityAssessmentId = $('security-assessment').value; refreshSecurityOverview(false); };
  $('graph-motion').onclick = () => { const paused = $('network-canvas').classList.toggle('motion-paused'); $('graph-motion').setAttribute('aria-pressed', String(paused)); $('graph-motion').textContent = paused ? 'Resume motion' : 'Pause motion'; };
  $('graph-filter').onchange = renderSecurityGraph;
  $('graph-search').oninput = renderSecurityGraph;
  $('graph-zoom-in').onclick = () => zoomGraph(0.15);
  $('graph-zoom-out').onclick = () => zoomGraph(-0.15);
  $('graph-reset').onclick = () => { graphTransform = { x: 0, y: 0, scale: 0.8 }; graphNodePositions.clear(); renderSecurityGraph(); };
  const viewport = $('network-viewport');
  new ResizeObserver(applyGraphTransform).observe(viewport);
  let drag = null;
  viewport.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0) return;
    const node = event.target.closest('[data-service]');
    if (event.target.closest('button') && !node) return;
    if (node) node.focus({preventScroll: true});
    drag = { pointerId: event.pointerId, node, x: event.clientX, y: event.clientY,
      ox: node ? parseFloat(node.style.left) : graphTransform.x,
      oy: node ? parseFloat(node.style.top) : graphTransform.y, moved: false };
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    drag.moved = true;
    if (drag.node) {
      drag.node.classList.add('dragging');
      moveGraphNode(drag.node.dataset.service, drag.ox + dx / graphTransform.scale, drag.oy + dy / graphTransform.scale);
    } else {
      graphTransform.x = drag.ox + dx;
      graphTransform.y = drag.oy + dy;
      applyGraphTransform();
    }
  });
  function endDrag(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const ended = drag;
    drag = null;
    ended.node?.classList.remove('dragging');
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    if (ended.node) {
      graphIgnoreClickUntil = performance.now() + 250;
      if (!ended.moved && event.type === 'pointerup') selectGraphService(ended.node.dataset.service);
    }
  }
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);
  viewport.addEventListener('lostpointercapture', endDrag);
  viewport.addEventListener('keydown', event => {
    const node = event.target.closest('[data-service]');
    if (node && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      const step = event.shiftKey ? 40 : 15;
      moveGraphNode(node.dataset.service, parseFloat(node.style.left) + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0), parseFloat(node.style.top) + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0));
      return;
    }
    if (event.target !== viewport || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    graphTransform.x += event.key === 'ArrowLeft' ? 40 : event.key === 'ArrowRight' ? -40 : 0;
    graphTransform.y += event.key === 'ArrowUp' ? 40 : event.key === 'ArrowDown' ? -40 : 0;
    applyGraphTransform();
  });
  $('network-canvas').addEventListener('click', event => {
    if (event.detail > 0 && performance.now() < graphIgnoreClickUntil) return;
    const node = event.target.closest('[data-service]');
    if (node) selectGraphService(node.dataset.service);
    if (event.target.closest('[data-graph-menu]')) renderGraphMenu();
  });
}
function selectGraphService(id) {
  graphSelected = id;
  document.querySelectorAll('[data-service]').forEach(node => {
    node.classList.toggle('selected', node.dataset.service === id);
    node.setAttribute('aria-pressed', String(node.dataset.service === id));
  });
  document.querySelectorAll('[data-edge]').forEach(edge => edge.classList.toggle('active-edge', edge.dataset.edge === id));
  renderServiceDetail(id);
}
function moveGraphNode(id, x, y) {
  const node = [...document.querySelectorAll('[data-service]')].find(node => node.dataset.service === id);
  if (!node) return;
  const canvas = $('network-canvas').getBoundingClientRect();
  const viewport = $('network-viewport').getBoundingClientRect();
  const scale = graphTransform.scale;
  // Keep the atom and its readable label in the currently visible viewport.
  const left = Math.max(70, (viewport.left - canvas.left + 90) / scale);
  const right = Math.min(770, (viewport.right - canvas.left - 90) / scale);
  const top = Math.max(60, (viewport.top - canvas.top + 50) / scale);
  const bottom = Math.min(540, (viewport.bottom - canvas.top - 80) / scale);
  const position = {x: Math.min(Math.max(x, Math.min(left, right)), Math.max(left, right)), y: Math.min(Math.max(y, Math.min(top, bottom)), Math.max(top, bottom))};
  graphNodePositions.set(id, position);
  node.style.left = `${position.x}px`;
  node.style.top = `${position.y}px`;
  const edge = [...document.querySelectorAll('[data-edge]')].find(edge => edge.dataset.edge === id);
  if (edge) { edge.setAttribute('x2', position.x); edge.setAttribute('y2', position.y); }
}
function zoomGraph(delta) { graphTransform.scale = Math.max(0.8, Math.min(1.6, graphTransform.scale + delta)); applyGraphTransform(); }
function applyGraphTransform() {
  $('graph-zoom-out').disabled = graphTransform.scale <= 0.8;
  $('graph-zoom-in').disabled = graphTransform.scale >= 1.6;
  $('map-zoom-level').textContent = `Map ${Math.round(graphTransform.scale * 100)}%`;
  $('network-canvas').style.setProperty('--graph-zoom', graphTransform.scale);
  const centerOffset = ($('network-viewport').clientWidth - 840) / 2;
  $('network-canvas').style.transform = `translate(${centerOffset + graphTransform.x}px,${graphTransform.y}px) scale(${graphTransform.scale})`;
}

async function refreshSecurityOverview(rebuild = true) {
  const request = ++securityRequest;
  if (rebuild) {
    const assessments = state.workspace.assessments;
    if (securityAssessmentId && !assessments.some(a => a.id === securityAssessmentId)) securityAssessmentId = '';
    $('security-assessment').innerHTML = '<option value="">Explore service reference map</option>' + assessments.map(a => `<option value="${esc(a.id)}">${esc(a.title)}${a.report_count ? '' : ' · no advisory yet'}</option>`).join('');
    $('security-assessment').value = securityAssessmentId;
  }
  securitySnapshot = null;
  $('security-source-note').textContent = securityAssessmentId ? 'Loading saved security snapshot…' : 'Reference mode. Select an assessment to view its findings; no security results are inferred here.';
  renderSecurityResults();
  if (!securityAssessmentId) return;
  try {
    const assessment = await jsonApi(`/api/assessments/${encodeURIComponent(securityAssessmentId)}`);
    if (request !== securityRequest) return;
    securitySnapshot = assessment.latest_report;
    const changed = securitySnapshot && JSON.stringify(securitySnapshot.answers) !== JSON.stringify(assessment.answers);
    $('security-source-note').textContent = securitySnapshot ? `Saved advisory: ${dateLabel(securitySnapshot.created_at)}. ${changed ? 'Draft answers have changed; regenerate the advisory to refresh these findings.' : 'Self-reported controls are unverified.'}` : 'No saved advisory yet. Generate an assessment advisory to populate security findings.';
    renderSecurityResults();
  } catch (error) {
    if (request !== securityRequest) return;
    $('security-source-note').textContent = 'Could not load this snapshot. Select the assessment again to retry.';
    showError(error);
  }
}
function renderSecurityResults() {
  const services = securitySnapshot?.recommendations || [];
  const risks = services.flatMap(service => service.risks);
  const count = status => risks.filter(risk => risk.status === status).length;
  const stats = [['shield', count('gap'), 'Control gaps', 'Potential misconfiguration risks'], ['search', count('unresolved'), 'Unresolved controls', 'Require clarification before deployment'], ['cpu', services.length, 'Assessed services', 'Rule-selected in this snapshot'], ['lock', count('planned'), 'Planned safeguards', 'Self-reported · not verified']];
  $('dashboard-stats').innerHTML = stats.map(([symbol, value, label, detail]) => `<article class="stat-card"><span class="stat-icon">${icon(symbol)}</span><strong>${securitySnapshot ? value : '—'}</strong><div>${label}</div><small>${detail}</small></article>`).join('');
  const parts = [['gap', 'Control gaps'], ['unresolved', 'Unresolved'], ['planned', 'Planned · unverified'], ['not_applicable', 'Not applicable']].map(([status, label]) => ({ label, value: count(status), color: COLORS[status] || COLORS.remaining, unit: 'entries' }));
  $('dashboard-donut').innerHTML = securitySnapshot ? donutChart(parts, risks.length, 'control entries', `${risks.length} assessed service-control entries. Not a security score.`) : '<p class="panel-empty">Select an assessment with a saved advisory to see its security findings.</p>';
  $('dashboard-bars').innerHTML = securitySnapshot ? state.kb.dimensions.map(dimension => {
    const entries = risks.filter(risk => risk.dimension === dimension);
    const gap = entries.filter(risk => risk.status === 'gap').length;
    const unresolved = entries.filter(risk => risk.status === 'unresolved').length;
    return `<div class="bar-row"><div class="bar-label"><strong>${esc(dimension)}</strong></div><div class="stacked-track" role="img" aria-label="${esc(dimension)}: ${gap} gaps and ${unresolved} unresolved out of ${entries.length} entries"><span style="width:${entries.length ? gap / entries.length * 100 : 0}%;background:${COLORS.gap}"></span><span style="width:${entries.length ? unresolved / entries.length * 100 : 0}%;background:${COLORS.unresolved}"></span></div><div class="bar-detail">${gap} gaps · ${unresolved} unresolved / ${entries.length} entries</div></div>`;
  }).join('') : '<p class="panel-empty">No assessment findings loaded.</p>';
  renderSecurityGraph();
}
function serviceStatus(id) {
  const service = securitySnapshot?.recommendations.find(s => s.service_id === id);
  if (!service) return { label: securitySnapshot ? 'Not selected' : 'Reference', kind: 'reference', service: null };
  const kind = service.risks.some(r => r.status === 'gap') ? 'gap' : service.risks.some(r => r.status === 'unresolved') ? 'unresolved' : 'planned';
  return { label: { gap: 'Control gap', unresolved: 'Unresolved', planned: 'Review controls' }[kind], kind, service };
}
function renderSecurityGraph() {
  const filter = $('graph-filter').value;
  const services = state.kb.services.filter(service => service.name.toLowerCase().includes($('graph-search').value.trim().toLowerCase())).filter(service => filter === 'compute' ? service.category === 'compute' : filter === 'assessed' ? serviceStatus(service.id).service : filter === 'gap' ? serviceStatus(service.id).kind === 'gap' : true);
  // Fixed readable node sizes; viewport pans instead of shrinking labels below 13px.
  const nodes = services.map((service, index) => {
    const angle = index / Math.max(services.length, 1) * Math.PI * 2 - Math.PI / 2;
    return { service, ...(graphNodePositions.get(service.id) || {x: 420 + Math.cos(angle) * 305, y: 310 + Math.sin(angle) * 245}) };
  });
  $('network-canvas').innerHTML = `<svg class="network-edges" viewBox="0 0 840 620" aria-hidden="true">${nodes.map(n => `<line data-edge="${esc(n.service.id)}" class="${graphSelected === n.service.id ? 'active-edge' : ''}" x1="420" y1="310" x2="${n.x}" y2="${n.y}"/>`).join('')}</svg><button class="graph-node graph-hub" style="left:420px;top:310px" data-graph-menu>${icon('shield')}<strong>Menu</strong><small>Navigation</small></button>${nodes.map(({ service, x, y }) => { const status = serviceStatus(service.id); return `<button class="graph-node ${status.kind} ${graphSelected === service.id ? 'selected' : ''}" style="left:${x}px;top:${y}px" data-service="${esc(service.id)}" aria-label="${esc(service.name)}. Drag or use arrow keys to move; press Enter for details." aria-pressed="${graphSelected === service.id}">${icon(state.kb.categories.find(c => c.id === service.category)?.icon || 'cloud')}<strong>${esc(service.name)}</strong><small>${status.label}</small></button>`; }).join('')}${!services.length ? '<p class="graph-empty">No services match this filter.</p>' : ''}`;
  applyGraphTransform();
  if (graphSelected && services.some(s => s.id === graphSelected)) renderServiceDetail(graphSelected);
  else { graphSelected = null; renderGraphMenu(); }
}
function renderGraphMenu() {
  $('network-detail').innerHTML = `<div class="eyebrow">WORKSPACE NAVIGATION</div><h3>Investigate before deployment</h3><p>Explore a service node for its purpose, security findings, and official configuration guides.</p><div class="graph-menu">${[['library','Assessment library'],['planning','Remediation notes']].map(([view,label]) => `<button class="button button-secondary" data-view="${view}">${label}${icon('arrow-right')}</button>`).join('')}</div><p class="field-help">Red: control gaps · Dashed outline: unresolved · Solid outline: reviewed in the advisory · Gray: reference or not selected. No status verifies an AWS configuration.</p>`;
}
function renderServiceDetail(id) {
  const metadata = state.kb.services.find(s => s.id === id);
  const status = serviceStatus(id);
  const risks = status.service?.risks || [];
  $('network-detail').innerHTML = `<div class="eyebrow">${esc(metadata.category)} / ${esc(status.label)}</div><h3>${esc(metadata.name)}</h3><p>${esc(metadata.description)}</p>${status.service?.conditional ? '<p class="report-notice">Conditional service: clarify the requirement before selecting it.</p>' : ''}<h4>${status.service ? 'Assessment findings' : 'Configuration guide'}</h4>${!status.service ? '<p class="field-help">Reference guidance only; this service has no findings in the selected snapshot.</p>' : ''}${(status.service ? risks : metadata.guidance).map(risk => `<article class="graph-finding"><strong>${esc(risk.title)}</strong>${risk.status ? `<span class="risk-badge ${esc(risk.status)}">${esc(risk.status)} · ${esc(risk.severity)} priority</span>` : ''}<p>${esc(risk.action)}</p><a class="aws-link" href="${safeLink(risk.aws_doc_url || risk.url)}" target="_blank" rel="noopener noreferrer">AWS configuration guide ↗</a></article>`).join('')}${securitySnapshot ? `<button class="button button-primary" id="graph-open-report">Open full advisory</button>` : ''}`;
  if ($('graph-open-report')) $('graph-open-report').onclick = async () => {
    try { const assessment = await jsonApi(`/api/assessments/${encodeURIComponent(securityAssessmentId)}`); await openAssessment(securityAssessmentId); if (assessment.latest_report_id) await openReport(assessment.latest_report_id); } catch (error) { showError(error); }
  };
}
