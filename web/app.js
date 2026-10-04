import { lookup, statusOf, runChangeTest, findings, DEFAULT_AS_OF } from './engine.js';

const [rules, addresses, meta] = await Promise.all(['rules', 'addresses', 'meta'].map(f => fetch(`data/${f}.json`).then(r => r.json())));
const byId = Object.fromEntries(rules.map(r => [r.team_rule_id, r]));
const S = { asOf: meta.as_of || DEFAULT_AS_OF, view: 'lookup', addr: null, q: '', city: '', lang: 'en', rf: { j: '', c: '', s: '' } };

const T = {
  en: { legal: 'Not legal advice. This prototype shows what public law text says for an address; it is not a compliance certification.',
    tagline: 'Which rules apply at this address on this date — every answer cited to its source.', asof: 'As of', tab_lookup: 'Address lookup', tab_changes: 'Change tracking', tab_rules: 'Rules', tab_method: 'Method & audit', today: 'Default date',
    search: 'Search 500 sample addresses (street, city, ZIP or id)', allcities: 'All cities', pick: 'Pick an address to see the rules that reach it.',
    applies: 'Applies', unknown: 'Unknown', superseded: 'Superseded', not_yet_effective: 'Not yet effective', pending: 'Pending — not law', failed: 'Failed', in_force: 'In force',
    why: 'Why', source: 'Source and quoted text', norule: 'No rule at this level', conflict: 'Conflict — needs human review', built: 'Year built', units: 'Units', use: 'Use', juris: 'Jurisdiction',
    cats: { rent_increase_limits: 'Rent increases', just_cause_eviction: 'Just-cause eviction', security_deposits: 'Security deposit', application_screening_fees: 'Application & screening fees', screening_restrictions: 'Screening restrictions', algorithmic_rent_setting: 'Algorithmic rent-setting' } },
  es: { legal: 'No es asesoría legal. Este prototipo muestra lo que dice el texto público de la ley para una dirección; no es una certificación de cumplimiento.',
    tagline: 'Qué reglas aplican en esta dirección en esta fecha — cada respuesta citada a su fuente.', asof: 'A fecha de', tab_lookup: 'Buscar dirección', tab_changes: 'Cambios en la ley', tab_rules: 'Reglas', tab_method: 'Método y auditoría', today: 'Fecha por defecto',
    search: 'Buscar entre 500 direcciones (calle, ciudad, código postal o id)', allcities: 'Todas las ciudades', pick: 'Elija una dirección para ver las reglas que le aplican.',
    applies: 'Aplica', unknown: 'Desconocido', superseded: 'Reemplazada', not_yet_effective: 'Aún no vigente', pending: 'Pendiente — no es ley', failed: 'Fallida', in_force: 'Vigente',
    why: 'Por qué', source: 'Fuente y texto citado', norule: 'No hay regla en este nivel', conflict: 'Conflicto — requiere revisión humana', built: 'Año de construcción', units: 'Unidades', use: 'Uso', juris: 'Jurisdicción',
    cats: { rent_increase_limits: 'Aumentos de renta', just_cause_eviction: 'Desalojo con causa justa', security_deposits: 'Depósito de garantía', application_screening_fees: 'Cuotas de solicitud y evaluación', screening_restrictions: 'Restricciones de evaluación', algorithmic_rent_setting: 'Fijación algorítmica de rentas' } },
};
const t = k => T[S.lang][k] ?? T.en[k] ?? k;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const $ = s => document.querySelector(s);
const view = $('#view');
const text = (r, f) => (S.lang === 'es' && r[f + '_es']) || r[f];
const unitsTxt = a => a.units_min == null && a.units_max == null ? '—' : a.units_min === a.units_max ? a.units_min : `${a.units_min ?? '?'}–${a.units_max ?? '+'}`;

function sourceBlock(r) {
  const rows = (r.sources || []).map(s => `<blockquote>“${esc(s.quoted_span)}”<br><span class="kv">${esc(s.doc_id)} · ${esc(s.evidence_type || '')} · retrieved ${esc(s.retrieved_at || 'n/a')} · ${s.in_distributed_corpus ? 'distributed corpus text' : 'independently saved link-only text (research only)'}${s.url && s.url.startsWith('http') ? ` · <a href="${esc(s.url)}" target="_blank" rel="noopener">open source</a>` : ''}</span></blockquote>`).join('');
  return `<details><summary>${t('source')} (${(r.sources || []).length})</summary>${rows}
    <div class="kv">Effective date: ${esc(r.effective_date || 'not stated in the sources')}${r.effective_date_basis ? ` — basis: “${esc(r.effective_date_basis)}” (${esc(r.effective_date_source_doc_id)})` : ''}${r.enacted_date ? ` · enacted ${esc(r.enacted_date)}` : ''}${r.sunset_date ? ` · sunsets ${esc(r.sunset_date)}` : ''}</div>
    <div class="kv">Coverage: ${esc((r.coverage_conditions || {}).summary || '—')}</div>
    ${r.exemptions ? `<div class="kv">Exemptions: ${esc(r.exemptions)}</div>` : ''}${r.penalty ? `<div class="kv">Penalty: ${esc(r.penalty)}</div>` : ''}
    ${r.interaction ? `<div class="kv">Interaction: ${esc(r.interaction)}</div>` : ''}${r.conflict_note ? `<div class="kv">Review note: ${esc(r.conflict_note)}</div>` : ''}
    <div class="kv">Extraction confidence ${r.confidence ?? '—'} · rule id ${esc(r.team_rule_id)}</div></details>`;
}

function ruleCard(x) {
  const r = byId[x.team_rule_id];
  return `<div class="rule"><div class="top"><span class="badge b-${x.result}">${t(x.result)}</span>${x.conflict_flag ? `<span class="badge b-conflict">${t('conflict')}</span>` : ''}<span class="t">${esc(text(r, 'title'))}</span></div>
    <div class="req">${esc(text(r, 'requirement'))}</div>${r.key_value ? `<div class="kv">${esc(r.key_value)}</div>` : ''}
    <div class="why"><b>${t('why')}:</b> ${esc(x.explanation)}</div>
    <div class="cite">${esc(r.citation)} · ${esc(r.source_doc_id)} · retrieved ${esc(r.retrieved_at || 'n/a')} · as of ${S.asOf}</div>${sourceBlock(r)}</div>`;
}

// History view: every rule shown above for this address, in date order, with the kind of date the sources give.
function timeline(a, res) {
  const key = r => r.legal_stage !== 'enacted' ? '9' : !r.effective_date ? '8' : (r.effective_date_kind === 'amendment_or_periodic' ? '7' : '0') + (r.effective_date + '-01-01').slice(0, 10);
  const when = r => r.legal_stage !== 'enacted' ? ['—', 'a bill, not law'] : !r.effective_date ? ['not stated', 'no start date in the sources; treated as in force']
    : r.effective_date_kind === 'amendment_or_periodic' ? [r.effective_date, 'date of the latest amendment or yearly figure; the law itself is older'] : [r.effective_date, 'start date'];
  const rows = [...res].sort((x, y) => key(byId[x.team_rule_id]) < key(byId[y.team_rule_id]) ? -1 : 1).map(x => {
    const r = byId[x.team_rule_id], [d, kind] = when(r);
    return `<tr><td class="d">${esc(d)}</td><td><span class="badge b-${x.result}">${t(x.result)}</span></td><td>${esc(text(r, 'title'))}<div class="kv">${esc(r.citation)} · ${kind}</div></td></tr>`;
  }).join('');
  const failed = rules.filter(r => (r.level === 'state' ? r.jurisdiction === a.state : r.jurisdiction === a.city) && statusOf(r, S.asOf) === 'failed')
    .map(r => `<tr><td class="d">—</td><td><span class="badge b-failed">${t('failed')}</span></td><td>${esc(text(r, 'title'))}<div class="kv">${esc(r.citation)} · never became law, not reported above</div></td></tr>`).join('');
  return `<h3>Timeline — all ${res.length} rules shown above, by date</h3><div class="tw"><table class="tlt"><tr><th>Date</th><th>On ${S.asOf}</th><th>Rule</th></tr>${rows}${failed}</table></div>`;
}

function renderAddress() {
  const a = S.addr;
  if (!a) return `<div class="card">${t('pick')}</div>`;
  const res = lookup(a, rules, S.asOf);
  const fnd = findings(rules, [a.state, a.city], meta.categories, S.asOf);
  let h = `<div class="card"><h2>${esc(a.street_address)}</h2><div class="sub">${esc(a.postal_city)}, ${a.state} ${esc(a.zip)} · ${a.address_id}</div>
    <div class="stack"><span class="kv">${t('juris')}:</span><span class="j">${a.state}</span>›<span class="j">${esc(a.county || 'county n/a')}</span>›<span class="j">${esc(a.city || 'unresolved')}</span></div>
    <div class="facts"><span>${t('built')}: <b>${a.year_built ?? '—'}</b></span><span>${t('units')}: <b>${unitsTxt(a)}</b>${a.units_source && a.units_source !== 'assessor units field' ? ' (from use description)' : ''}</span><span>${t('use')}: <b>${esc(a.use_description)}</b></span><span>${esc(a.source_dataset)}</span></div>
    ${a.postal_city_differs ? `<div class="note">Mailing city “${esc(a.postal_city)}” is not the legal city. The Census Geocoder places this address in ${esc(a.city)}.</div>` : ''}
    ${a.jurisdiction_note ? `<div class="note">${esc(a.jurisdiction_note)}</div>` : ''}
    <div class="kv" style="margin-top:6px">Geocoding: ${esc(a.geocode_method)}${a.matched_address ? ` → ${esc(a.matched_address)}` : ''} · jurisdiction confidence ${a.jurisdiction_confidence}</div>`;
  for (const c of meta.categories) {
    const here = res.filter(x => x.category === c);
    h += `<h3>${T[S.lang].cats[c]}</h3>` + here.map(ruleCard).join('');
    for (const f of fnd.filter(f => f.category === c)) {
      const failed = f.failed.map(id => `${byId[id].citation} — ${byId[id].title} (failed, not law)`).join('; ');
      h += `<div class="none"><b>${t('norule')}: ${esc(f.jurisdiction)}.</b> ${esc(f.note)}${failed ? ` Recorded but not reported as law: ${esc(failed)}.` : ''}</div>`;
    }
  }
  return h + timeline(a, res) + '</div>';
}

function renderLookup() {
  const cities = [...new Set(addresses.map(a => a.city))].sort();
  const q = S.q.toLowerCase();
  const list = addresses.filter(a => (!S.city || a.city === S.city) && (!q || `${a.street_address} ${a.postal_city} ${a.zip} ${a.address_id} ${a.city}`.toLowerCase().includes(q))).slice(0, 200);
  view.innerHTML = `<div class="grid"><div class="card"><input type="search" id="q" placeholder="${t('search')}" value="${esc(S.q)}">
    <select id="city" style="margin-top:8px"><option value="">${t('allcities')}</option>${cities.map(c => `<option ${c === S.city ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
    <div class="list">${list.map(a => `<div class="row ${S.addr && a.address_id === S.addr.address_id ? 'on' : ''}" data-a="${a.address_id}">${esc(a.street_address)}<small>${esc(a.city)}${a.postal_city_differs ? ` (mail: ${esc(a.postal_city)})` : ''} · ${a.address_id}</small></div>`).join('')}</div></div>
    <div id="detail">${renderAddress()}</div></div>`;
  $('#q').oninput = e => { S.q = e.target.value; const p = e.target.selectionStart; renderLookup(); const n = $('#q'); n.focus(); n.setSelectionRange(p, p); };
  $('#city').onchange = e => { S.city = e.target.value; renderLookup(); };
  view.querySelectorAll('.row').forEach(el => el.onclick = () => { S.addr = addresses.find(a => a.address_id === el.dataset.a); renderLookup(); window.scrollTo({ top: 0 }); });
}

function renderChanges() {
  let h = `<div class="card"><h2>Change tracking — the five supplied cases</h2><div class="sub">Recomputed in your browser by the same engine that wrote changes.json. Each case uses its own dates, not the date picker.</div></div>`;
  for (const tc of meta.tests) {
    const r = runChangeTest(tc, addresses, rules);
    const byCity = {};
    for (const id of r.affected_address_ids) { const c = addresses.find(a => a.address_id === id).city; byCity[c] = (byCity[c] || 0) + 1; }
    const mapped = Object.entries(r.mapped).map(([k, v]) => `${k} → ${v.map(id => `<span class="pill" data-r="${id}">${id}</span> ${esc(byId[id].citation)} (${statusOf(byId[id], tc.as_of || tc.as_of_before)}${tc.as_of_after ? ' → ' + statusOf(byId[id], tc.as_of_after) : ''})`).join(', ') || 'no matching rule'}`).join('<br>');
    h += `<div class="card" style="margin-top:12px"><h2>${tc.test_id} · ${esc(tc.title)}</h2>
      <div class="sub">${tc.type === 'as_of' ? `as of ${tc.as_of_before} → ${tc.as_of_after}` : `as of ${tc.as_of}`} · expected: ${esc(tc.expected_behavior)}</div>
      <div class="kpis"><div class="kpi"><b>${r.affected_address_ids.length}</b><span>affected addresses</span></div><div class="kpi"><b>${r.conflict_flag_address_ids.length}</b><span>conflict flags</span></div>
      ${Object.entries(byCity).map(([c, n]) => `<div class="kpi"><b>${n}</b><span>${esc(c)}</span></div>`).join('')}</div>
      <div class="kv">${mapped}</div>
      <details><summary>Affected address ids (${r.affected_address_ids.length})</summary><div>${r.affected_address_ids.map(id => `<span class="pill" data-a="${id}">${id}</span>`).join('') || 'none'}</div></details></div>`;
  }
  view.innerHTML = h;
  view.querySelectorAll('.pill[data-a]').forEach(el => el.onclick = () => { S.addr = addresses.find(a => a.address_id === el.dataset.a); go('lookup'); });
  view.querySelectorAll('.pill[data-r]').forEach(el => el.onclick = () => { S.rf = { j: '', c: '', s: '', id: el.dataset.r }; go('rules'); });
}

function renderRules() {
  const f = S.rf;
  const list = rules.filter(r => (!f.j || r.jurisdiction === f.j) && (!f.c || r.category === f.c) && (!f.s || statusOf(r, S.asOf) === f.s) && (!f.id || r.team_rule_id === f.id));
  const opt = (arr, cur, lab) => `<option value="">${lab}</option>` + arr.map(v => `<option ${v === cur ? 'selected' : ''}>${v}</option>`).join('');
  view.innerHTML = `<div class="card"><h2>${rules.length} extracted rules</h2><div class="sub">One record per law, per category, per jurisdiction. Status is computed for ${S.asOf} from the dates in the text.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0"><select id="fj" style="max-width:220px">${opt(meta.scope, f.j, 'All jurisdictions')}</select><select id="fc" style="max-width:240px">${opt(meta.categories, f.c, 'All categories')}</select>
    <select id="fs" style="max-width:200px">${opt(['in_force', 'not_yet_effective', 'pending', 'failed'], f.s, 'All statuses')}</select>${f.id ? `<button id="clr">Showing ${f.id} — clear</button>` : ''}</div>
    ${list.map(r => { const st = statusOf(r, S.asOf); return `<div class="rule"><div class="top"><span class="badge b-${st}">${t(st)}</span>${r.conflict_flag ? `<span class="badge b-conflict">${t('conflict')}</span>` : ''}<span class="t">${esc(r.team_rule_id)} · ${esc(r.jurisdiction)} · ${esc(r.title)}</span></div>
      <div class="req">${esc(text(r, 'requirement'))}</div>${r.key_value ? `<div class="kv">${esc(r.key_value)}</div>` : ''}<div class="cite">${esc(r.citation)} · ${esc(r.source_doc_id)} · retrieved ${esc(r.retrieved_at || 'n/a')}</div>${sourceBlock(r)}</div>`; }).join('')}</div>
    <div class="card" style="margin-top:12px"><h2>“No rule at this level” findings (${meta.findings.length})</h2><div class="tw"><table><tr><th>Jurisdiction</th><th>Category</th><th>Finding</th></tr>
    ${findings(rules, meta.scope, meta.categories, S.asOf).map(x => `<tr><td>${esc(x.jurisdiction)}</td><td>${esc(T.en.cats[x.category])}</td><td>${esc(x.note)}${x.pending.length ? ` Pending: ${x.pending.join(', ')}.` : ''}${x.failed.length ? ` Failed: ${x.failed.join(', ')}.` : ''}</td></tr>`).join('')}</table></div></div>`;
  for (const [id, k] of [['fj', 'j'], ['fc', 'c'], ['fs', 's']]) $('#' + id).onchange = e => { S.rf[k] = e.target.value; S.rf.id = ''; renderRules(); };
  if ($('#clr')) $('#clr').onclick = () => { S.rf.id = ''; renderRules(); };
}

function renderMethod() {
  const v = meta.validation || {};
  const counts = {};
  for (const a of addresses) for (const x of lookup(a, rules, S.asOf)) counts[x.result] = (counts[x.result] || 0) + 1;
  view.innerHTML = `<div class="card"><h2>How an answer is produced</h2>
    <ol class="pipe"><li><b>Extract.</b> A language model (${esc(meta.model)}) reads each corpus document and writes rule records. No rule is hand-coded. Every record must quote the document verbatim; a validator rejects any record whose quote is not found in the source text.</li>
    <li><b>Merge.</b> Records describing the same law are clustered into one rule. The quote comes from distributed corpus text whenever one exists; a missing date is filled from the other documents; dates that disagree within a year raise a conflict flag.</li>
    <li><b>Resolve.</b> Each address goes to the U.S. Census Geocoder, then to its incorporated place. The mailing city is never trusted (${addresses.filter(a => a.postal_city_differs).length} of ${addresses.length} sample addresses have a mailing city that differs from the legal city).</li>
    <li><b>Apply.</b> Deterministic code — the engine running in this page — tests each rule's coverage against public building facts. A missing fact, or a building in the cutoff year, yields “unknown”, never a guess. Where a stricter local rule covers the address the state rule is “superseded”.</li>
    <li><b>Track.</b> Status is computed from dates for any “as of” day, so the same rule set answers for 2025-12-31, today, and 2027-07-02.</li></ol>
    <div class="kpis"><div class="kpi"><b>${rules.length}</b><span>rules</span></div><div class="kpi"><b>${addresses.length}</b><span>addresses</span></div>${Object.entries(counts).map(([k, n]) => `<div class="kpi"><b>${n}</b><span>${t(k)}</span></div>`).join('')}</div>
    <div class="sub">Counts are for ${S.asOf}. Built ${esc(meta.built_at)}.</div></div>
    <div class="card" style="margin-top:12px"><h2>Self-validation</h2><div class="sub">The organisers do not distribute a scoring script or answer key, so the system checks itself.</div>
    <div class="tw"><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>${(v.checks || []).map(c => `<tr><td>${esc(c.name)}</td><td class="${c.pass ? 'pass' : 'fail'}">${c.pass ? 'PASS' : 'FAIL'}</td><td>${esc(c.detail)}</td></tr>`).join('') || '<tr><td colspan="3">Run pipeline/validate.py to populate.</td></tr>'}</table></div></div>
    <div class="card" style="margin-top:12px"><h2>Known limits</h2><ul>
    <li>Public assessor data lacks owner names, many unit counts and many construction years; those answers are “unknown”.</li>
    <li>Year built is not the certificate-of-occupancy date, so buildings in a cutoff year are “unknown”.</li>
    <li>Some city ordinances exist only as link-only sources. Rules supported only by independently saved text are marked research-grade, with lower confidence, and do not count as corpus citations.</li>
    <li>Possible preemption (New Jersey FAIR Act vs. the Jersey City and Hoboken ordinances) is flagged for human review, not decided.</li>
    <li>Extraction uses a language model; outputs are cached by document hash so a rerun reproduces the same records. Every model call is in the audit log.</li></ul></div>`;
}

const views = { lookup: renderLookup, changes: renderChanges, rules: renderRules, method: renderMethod };
function go(v) { S.view = v; document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('on', b.dataset.v === v)); views[v](); }
function chrome() {
  document.querySelectorAll('[data-i]').forEach(el => el.textContent = t(el.dataset.i));
  $('#asof').value = S.asOf;
  $('#today').classList.toggle('on', S.asOf === DEFAULT_AS_OF);
  $('#lang').textContent = S.lang === 'en' ? 'Español' : 'English';
  document.documentElement.lang = S.lang;
}
document.querySelectorAll('#nav button').forEach(b => b.onclick = () => go(b.dataset.v));
const setDate = d => { if (d) { S.asOf = d; chrome(); go(S.view); } };
$('#today').onclick = () => setDate(DEFAULT_AS_OF);

$('#asof').onchange = e => { if (e.target.value) { S.asOf = e.target.value; chrome(); go(S.view); } };
$('#lang').onclick = () => { S.lang = S.lang === 'en' ? 'es' : 'en'; chrome(); go(S.view); };
S.addr = addresses.find(a => a.address_id === 'A0001');
chrome(); go('lookup');
