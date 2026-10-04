// Deterministic rules engine. The same file runs in the browser (live "as of" queries) and in Node
// (building lookups.json / changes.json), so the demo and the submission cannot drift apart.
// No law is written here: every date, threshold and interaction comes from the extracted rule records.

export const DEFAULT_AS_OF = '2026-10-01';

const cmp = (asOf, d) => {            // compare at the precision the source gave (YYYY, YYYY-MM or YYYY-MM-DD)
  const a = asOf.slice(0, d.length);
  return a < d ? -1 : a > d ? 1 : 0;
};

export function statusOf(rule, asOf = DEFAULT_AS_OF) {
  if (rule.legal_stage === 'struck') return 'failed';
  if (rule.legal_stage === 'bill') {
    const end = rule.legislative_session_end_year;
    return end && Number(asOf.slice(0, 4)) > Number(end) ? 'failed' : 'pending';   // a bill dies with its session
  }
  // only the law's own start date can make it "not yet effective"; amendment and annual-figure dates describe a law already in force
  if (rule.effective_date && rule.effective_date_kind !== 'amendment_or_periodic' && cmp(asOf, rule.effective_date) < 0) return 'not_yet_effective';
  if (rule.sunset_date && cmp(asOf, rule.sunset_date) > 0) return 'expired';
  return 'in_force';
}

const inGeography = (rule, a) => rule.level === 'state' ? rule.jurisdiction === a.state : rule.jurisdiction === a.city;

// Coverage tests -> 'pass' | 'excluded' | 'unknown', each with a plain-language reason.
function coverage(rule, a, asOf, byId) {
  let c = rule.coverage_conditions || {};
  const own = c.co_on_or_before || c.exempt_if_newer_than_years != null;
  const parent = !own && c.inherits_coverage_from && byId ? byId[c.inherits_coverage_from] : null;
  if (parent) c = { ...c, co_on_or_before: (parent.coverage_conditions || {}).co_on_or_before, exempt_if_newer_than_years: (parent.coverage_conditions || {}).exempt_if_newer_than_years };
  const out = [];
  const add = (state, why) => out.push({ state, why });
  if (c.co_on_or_before) {
    const y = Number(String(c.co_on_or_before).slice(0, 4));
    if (a.year_built == null) add('unknown', `coverage needs a certificate of occupancy on or before ${c.co_on_or_before}, and the public record has no year built`);
    else if (a.year_built < y) add('pass', `built ${a.year_built}, before the ${c.co_on_or_before} cutoff`);
    else if (a.year_built === y) add('unknown', `built in ${y}, the cutoff year; the test uses the certificate-of-occupancy date (${c.co_on_or_before}), which the public record does not give`);
    else add('excluded', `built ${a.year_built}, after the ${c.co_on_or_before} cutoff`);
  }
  if (c.exempt_if_newer_than_years != null) {
    const n = Number(c.exempt_if_newer_than_years), cut = Number(asOf.slice(0, 4)) - n;
    if (a.year_built == null) add('unknown', `buildings newer than ${n} years are exempt, and the public record has no year built`);
    else if (a.year_built < cut) add('pass', `built ${a.year_built}, more than ${n} years before ${asOf}`);
    else if (a.year_built === cut) add('unknown', `built ${a.year_built}, right at the rolling ${n}-year cutoff; the exact certificate-of-occupancy date is not in the public record`);
    else add('excluded', `built ${a.year_built}, within the last ${n} years (exempt)`);
  }
  if (c.min_units != null) {
    const m = Number(c.min_units);
    if (a.units_min != null && a.units_min >= m) add('pass', `at least ${a.units_min} units (rule needs ${m}+)`);
    else if (a.units_max != null && a.units_max < m) add('excluded', `at most ${a.units_max} units (rule needs ${m}+)`);
    else add('unknown', `rule applies to buildings with ${m}+ units, and the public record has no unit count`);
  }
  if (c.owner_occupied_exempt_max_units != null) {
    const m = Number(c.owner_occupied_exempt_max_units);
    if (a.units_min != null && a.units_min > m) add('pass', `building has ${a.units_min}+ units, so the owner-occupied exemption (${m} units or fewer) cannot apply`);
    else add('unknown', `owner-occupied buildings with ${m} or fewer units are exempt; owner occupancy${a.units_min == null ? ' and unit count are' : ' is'} not in the public record`);
  }
  // Coverage that turns on a building-specific fact, with no date test we could run from public data, cannot be settled.
  const dated = out.some(t => /cutoff|years/.test(t.why) && t.state === 'pass');
  if (c.depends_on_building_facts && !c.excluded_where_covered_by && !dated && !out.some(t => t.state === 'unknown'))
    add('unknown', `coverage depends on building-specific facts that public assessor data does not hold${c.coverage_basis ? ' (' + String(c.coverage_basis).replace(/\.$/, '') + ')' : ''}`);
  if (parent) for (const t of out) {
    // borrowed coverage is an approximation: it can show a building is covered, but not that it is outside this rule
    if (t.state === 'excluded') { t.state = 'unknown'; t.why += `; that is ${parent.team_rule_id}'s test, and this rule may reach further than ${parent.team_rule_id} does`; }
    else t.why += ` (coverage follows ${parent.team_rule_id})`;
  }
  return out;
}

export function evaluate(rule, a, asOf = DEFAULT_AS_OF, byId = null) {
  const status = statusOf(rule, asOf);
  if (status === 'failed' || status === 'expired') return null;      // never report a rule that is not law and not pending
  if (!a.city || !inGeography(rule, a)) return null;
  const tests = coverage(rule, a, asOf, byId);
  if (tests.some(t => t.state === 'excluded')) return null;          // leave out rules that don't apply
  const unknown = tests.filter(t => t.state === 'unknown');
  let result = status === 'pending' ? 'pending' : status === 'not_yet_effective' ? 'not_yet_effective' : unknown.length ? 'unknown' : 'applies';
  if (a.jurisdiction_confidence === 0) result = 'unknown';
  const where = rule.level === 'state' ? `Statewide ${rule.jurisdiction} rule` : `${rule.jurisdiction} ordinance; address resolves to ${a.city}`;
  const parts = [where];
  if (status === 'pending') parts.push(`pending ${rule.bill_number || 'proposal'}, not law; it would reach this address if enacted`);
  if (status === 'not_yet_effective') parts.push(`enacted but not effective until ${rule.effective_date}`);
  if (rule.rule_kind === 'bars_local_regulation') parts.push('this state law bars local regulation of this kind, so no local rule of this category exists here');
  for (const t of tests) parts.push(t.why);
  const c = rule.coverage_conditions || {};
  if (c.other_unresolvable && (result === 'applies' || result === 'superseded'))
    parts.push(`not checked against public data, which cannot show it: ${String(c.other_unresolvable).replace(/\.$/, '').slice(0, 160)}`);
  if (c.small_owner_exception) parts.push('an owner-type exception exists in the text; owner names are not public, so whether it applies cannot be confirmed');
  if (a.jurisdiction_note) parts.push(a.jurisdiction_note.replace(/\.$/, ''));
  return { team_rule_id: rule.team_rule_id, category: rule.category, status, result, reasons: parts, tests };
}

// Full answer for one address: evaluate every rule, then apply state/local interactions among rules that actually reach the address.
export function lookup(a, rules, asOf = DEFAULT_AS_OF) {
  const byId = Object.fromEntries(rules.map(r => [r.team_rule_id, r]));
  const res = rules.map(r => evaluate(r, a, asOf, byId)).filter(Boolean);
  let got = Object.fromEntries(res.map(x => [x.team_rule_id, x]));
  // rules that cover only units outside another ordinance (e.g. a citywide just-cause law for units not under rent stabilization)
  for (const x of [...res]) {
    const other = (byId[x.team_rule_id].coverage_conditions || {}).excluded_where_covered_by;
    if (!other || !byId[other] || x.status !== 'in_force') continue;
    const o = got[other];
    if (o && o.result === 'applies') { res.splice(res.indexOf(x), 1); delete got[x.team_rule_id]; }
    else if (o && o.result === 'unknown') { x.result = 'unknown'; x.reasons.push(`it covers units that are not under ${other}, and whether ${other} covers this building cannot be settled from public data`); }
    else if (x.result === 'applies') x.reasons.push(`${other} does not cover this building, so this rule does`);
  }
  for (const x of res) {
    const r = byId[x.team_rule_id];
    x.conflict_flag = false;
    const peers = (r.overrides || []).map(id => got[id]).filter(Boolean);
    if (r.level === 'state' && r.yields_to_stricter_local && (x.result === 'applies' || x.result === 'unknown')) {
      const local = peers.filter(p => byId[p.team_rule_id].level === 'city' && p.status === 'in_force');
      const hit = local.find(p => p.result === 'applies');
      const maybe = local.find(p => p.result === 'unknown');
      if (hit) { x.result = 'superseded'; x.reasons.push(`the stricter local rule ${hit.team_rule_id} covers this address and governs instead`); }
      else if (maybe && x.result === 'applies') { x.result = 'unknown'; x.reasons.push(`it yields to the local rule ${maybe.team_rule_id} if that rule covers this building, which the public record cannot settle`); }
    }
    // preemption-type conflict: flagged where both sides of the conflict reach this address
    const other = (r.conflict_with || []).map(id => got[id]).find(Boolean);
    if (other) { x.conflict_flag = true; x.reasons.push(`possible conflict with ${other.team_rule_id}; flagged for human review, not resolved here`); }
    // competing published effective dates: flagged only when they would change the answer on this date
    const alt = (r.effective_date_alternatives || []).find(d => statusOf({ ...r, effective_date: d }, asOf) !== statusOf(r, asOf));
    if (alt) { x.conflict_flag = true; x.reasons.push(`sources publish another effective date (${alt}) that would change this answer; flagged for human review`); }
    x.explanation = x.reasons.join('; ') + '.';
  }
  return res;
}

// "No rule at this level" findings: jurisdiction x category cells with no enacted requirement.
export function findings(rules, jurisdictions, categories, asOf = DEFAULT_AS_OF) {
  const out = [];
  for (const j of jurisdictions) for (const c of categories) {
    const here = rules.filter(r => r.jurisdiction === j && r.category === c);
    const live = here.filter(r => ['in_force', 'not_yet_effective'].includes(statusOf(r, asOf)) && r.rule_kind !== 'bars_local_regulation');
    if (live.length) continue;
    const state = j.length === 2 ? j : j.slice(-2);
    const bar = rules.find(r => r.jurisdiction === state && r.category === c && r.rule_kind === 'bars_local_regulation' && statusOf(r, asOf) === 'in_force');
    out.push({
      jurisdiction: j, category: c, finding: 'no_rule_at_this_level', as_of: asOf,
      barred_by: bar ? bar.team_rule_id : null,
      pending: here.filter(r => statusOf(r, asOf) === 'pending').map(r => r.team_rule_id),
      failed: here.filter(r => statusOf(r, asOf) === 'failed').map(r => r.team_rule_id),
      note: bar ? `State law (${bar.citation}) bars local rules of this kind.` : 'No enacted rule of this category was found for this jurisdiction in the corpus.',
    });
  }
  return out;
}

// Map an organiser test id such as "HOB-ALG-01" or "MA-ALG-P1" to our rule(s) by jurisdiction, category and legal stage.
const CAT = { ALG: 'algorithmic_rent_setting', RENT: 'rent_increase_limits', EVICT: 'just_cause_eviction', DEP: 'security_deposits', FEE: 'application_screening_fees', SCREEN: 'screening_restrictions' };
export function mapTestRule(id, rules) {
  const [pre, cat, tail] = id.split('-');
  const want = CAT[cat];
  const jurMatch = r => {
    if (r.level === 'state') return r.jurisdiction === pre;
    const name = r.jurisdiction.split(',')[0].toUpperCase();
    const initials = name.split(/\s+/).map(w => w[0]).join('');
    return pre.length > 2 ? name.replace(/\s+/g, '').startsWith(pre) : initials === pre && name.includes(' ');
  };
  const proposed = /^P/.test(tail || '');
  let c = rules.filter(r => r.category === want && jurMatch(r) && r.rule_kind !== 'bars_local_regulation' && (proposed ? r.legal_stage !== 'enacted' : r.legal_stage === 'enacted'));
  c.sort((x, y) => (x.team_rule_id < y.team_rule_id ? -1 : 1));
  if (proposed && c.length > 1) { const n = Number((tail || 'P1').slice(1)) - 1; return c[n] ? [c[n]] : []; }
  return c;
}

export function runChangeTest(t, addresses, rules) {
  const mapped = Object.fromEntries(t.rule_ids.map(id => [id, mapTestRule(id, rules).map(r => r.team_rule_id)]));
  const ids = new Set(Object.values(mapped).flat());
  const inStates = a => !t.states || t.states.includes(a.state);
  const pick = (a, asOf) => lookup(a, rules, asOf).filter(x => ids.has(x.team_rule_id));
  let affected = [], conflicts = [], detail = {};
  if (t.type === 'as_of') {
    for (const a of addresses.filter(inStates)) {
      const b = pick(a, t.as_of_before), f = pick(a, t.as_of_after);
      const sig = l => l.map(x => x.team_rule_id + ':' + x.result).sort().join('|');
      if (sig(b) !== sig(f)) { affected.push(a.address_id); detail[a.address_id] = { before: b.map(x => x.result), after: f.map(x => x.result) }; }
    }
    if (t.conflict_with) {
      const cids = new Set(t.conflict_with.flatMap(id => mapTestRule(id, rules).map(r => r.team_rule_id)));
      conflicts = addresses.filter(a => lookup(a, rules, t.as_of_before).some(x => cids.has(x.team_rule_id))).map(a => a.address_id);
    }
  } else if (t.type === 'boundary') {
    for (const a of addresses) { const l = pick(a, t.as_of).filter(x => x.result === 'applies' || x.result === 'unknown'); if (l.length) { affected.push(a.address_id); detail[a.address_id] = l.map(x => x.team_rule_id); } }
  } else if (t.type === 'pending') {
    for (const a of addresses.filter(inStates)) if (pick(a, t.as_of).some(x => x.result === 'pending')) affected.push(a.address_id);
  } else if (t.type === 'negative') {
    // affected = addresses that would get an enforceable rule of this category from the failed measure (expected: none)
    for (const a of addresses.filter(inStates)) if (pick(a, t.as_of).length) affected.push(a.address_id);
  }
  return { mapped, affected_address_ids: affected, conflict_flag_address_ids: conflicts, detail };
}
