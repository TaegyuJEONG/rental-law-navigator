// Modules B + C: run the engine over all 500 addresses and write the three submission files plus the web data.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { statusOf, lookup, findings, runChangeTest, DEFAULT_AS_OF } from '../web/engine.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rd = p => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const wr = (p, o) => { fs.mkdirSync(path.dirname(path.join(ROOT, p)), { recursive: true }); fs.writeFileSync(path.join(ROOT, p), JSON.stringify(o, null, 1)); };

const SCOPE = ['CA', 'NJ', 'MA', 'Los Angeles, CA', 'San Francisco, CA', 'San Diego, CA', 'Berkeley, CA', 'Santa Ana, CA', 'Jersey City, NJ', 'Hoboken, NJ', 'Newark, NJ', 'Boston, MA', 'Cambridge, MA'];
const CATS = ['rent_increase_limits', 'just_cause_eviction', 'security_deposits', 'application_screening_fees', 'screening_restrictions', 'algorithmic_rent_setting'];
const asOf = process.argv[2] || DEFAULT_AS_OF;

const full = rd('cache/rules_merged.json').rules;
const addresses = rd('cache/addresses.json');
const tests = rd('starter/dev/change_tests.json');

// rules.json: schema fields first, then our extra time/place/evidence fields (the schema allows additional properties)
const SCHEMA_STATUS = { in_force: 'in_force', not_yet_effective: 'not_yet_effective', pending: 'pending', failed: 'failed' };
const live = full.filter(r => statusOf(r, asOf) !== 'expired');
const rules = live.map(r => ({
  team_rule_id: r.team_rule_id, jurisdiction: r.jurisdiction, level: r.level, category: r.category,
  status: SCHEMA_STATUS[statusOf(r, asOf)], title: r.title, requirement: r.requirement, key_value: r.key_value ?? null,
  coverage_conditions: r.coverage_conditions ?? null, exemptions: r.exemptions ?? null, overrides: r.overrides || [], interaction: r.interaction ?? null,
  effective_date: r.effective_date ?? null, citation: r.citation, source_doc_id: r.source_doc_id, source_url: r.source_url, quoted_span: r.quoted_span,
  confidence: r.confidence ?? null, conflict_flag: !!r.conflict_flag, conflict_note: r.conflict_note ?? null,
  // extensions
  law_name: r.law_name, penalty: r.penalty ?? null, rule_kind: r.rule_kind, legal_stage: r.legal_stage, bill_number: r.bill_number ?? null,
  legislative_session_end_year: r.legislative_session_end_year ?? null, enacted_date: r.enacted_date ?? null, sunset_date: r.sunset_date ?? null,
  effective_date_basis: r.effective_date_basis ?? null, effective_date_source_doc_id: r.effective_date_source_doc_id ?? null,
  effective_date_kind: r.effective_date_kind || 'none', conflict_with: r.conflict_with || [], effective_date_alternatives: r.effective_date_alternatives || [],
  yields_to_stricter_local: !!r.yields_to_stricter_local, bars_or_preempts_local: !!r.bars_or_preempts_local,
  title_es: r.title_es ?? null, requirement_es: r.requirement_es ?? null,
  retrieved_at: r.retrieved_at, in_distributed_corpus: !!r.in_distributed_corpus, evidence_type: r.evidence_type, sources: r.sources, status_as_of: asOf,
}));
wr('submission/rules.json', { rules });

// lookups.json
const lookups = {};
const counts = {};
for (const a of addresses) {
  lookups[a.address_id] = lookup(a, rules, asOf).map(x => { counts[x.result] = (counts[x.result] || 0) + 1; return { team_rule_id: x.team_rule_id, result: x.result, explanation: x.explanation, conflict_flag: x.conflict_flag }; });
}
wr('submission/lookups.json', { as_of: asOf, lookups });

// changes.json
const changes = {}, changeDetail = {};
for (const t of tests) {
  const r = runChangeTest(t, addresses, rules);
  const unmapped = Object.entries(r.mapped).filter(([, v]) => !v.length).map(([k]) => k);
  changes[t.test_id] = {
    affected_address_ids: r.affected_address_ids, conflict_flag_address_ids: r.conflict_flag_address_ids,
    notes: `${t.title}. Organiser rule ids mapped to team rules: ${Object.entries(r.mapped).map(([k, v]) => `${k} -> ${v.join(', ') || 'no matching rule'}`).join('; ')}. ` +
      (t.type === 'as_of' ? `Compared lookups as of ${t.as_of_before} and ${t.as_of_after}; affected = addresses whose result changes.` :
        t.type === 'boundary' ? `As of ${t.as_of}; affected = addresses where either local rule reaches the address (legal city from the Census Geocoder, not the postal city).` :
          t.type === 'pending' ? `As of ${t.as_of}; these bills are pending, not in force. Affected = addresses they would reach if enacted.` :
            `As of ${t.as_of}; the measure is recorded as failed, so it reaches no address.`) + (unmapped.length ? ` WARNING: no rule found for ${unmapped.join(', ')}.` : ''),
  };
  changeDetail[t.test_id] = { ...t, mapped: r.mapped, n_affected: r.affected_address_ids.length, n_conflict: r.conflict_flag_address_ids.length };
}
wr('submission/changes.json', changes);
wr('submission/findings.json', { as_of: asOf, findings: findings(rules, SCOPE, CATS, asOf) });

// web data
const docs = {};
for (const line of fs.readFileSync(path.join(ROOT, 'starter/corpus/corpus_manifest.csv'), 'utf8').split('\n').slice(1)) {
  const m = line.match(/^(D\d+),("?)(.*?)\2,(https?:[^,]+),/); if (m) docs[m[1]] = { jurisdiction: m[3], url: m[4] };
}
wr('web/data/rules.json', rules);
wr('web/data/addresses.json', addresses);
wr('web/data/meta.json', { as_of: asOf, built_at: new Date().toISOString(), scope: SCOPE, categories: CATS, tests, changes, changeDetail, docs,
  findings: findings(rules, SCOPE, CATS, asOf), model: process.env.NAVIGATOR_MODEL || 'gpt-5.5' });

console.log(`rules ${rules.length} (${Object.entries(rules.reduce((m, r) => (m[r.status] = (m[r.status] || 0) + 1, m), {})).map(e => e.join(' ')).join(', ')})`);
console.log(`lookups: ${Object.keys(lookups).length} addresses, results ${JSON.stringify(counts)}`);
for (const t of tests) console.log(`${t.test_id}: affected ${changes[t.test_id].affected_address_ids.length}, conflict ${changes[t.test_id].conflict_flag_address_ids.length} | ${Object.entries(changeDetail[t.test_id].mapped).map(([k, v]) => k + '->' + (v.join('+') || 'NONE')).join(' ')}`);
