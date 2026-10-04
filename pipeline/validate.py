"""Self-validation. The organisers do not distribute score.py or an answer key, so these checks stand in for it:
schema conformance, verbatim citations, full address coverage and the stated expected behaviour of T1-T5."""
import collections, csv, json, os, re, sys
from common import ROOT, norm, load_docs

J = lambda p: json.load(open(os.path.join(ROOT, p)))
schema = J('starter/schema/rule_record.schema.json')
rules = J('submission/rules.json')['rules']
look = J('submission/lookups.json')
changes = J('submission/changes.json')
addrs = {a['address_id']: a for a in J('cache/addresses.json')}
docs = {d['doc_id']: d for d in load_docs()}
byid = {r['team_rule_id']: r for r in rules}
checks = []


def check(name, ok, detail):
    checks.append(dict(name=name, **{'pass': bool(ok)}, detail=detail))


# 1 schema
TYPES = {'string': str, 'number': (int, float), 'boolean': bool, 'array': list, 'object': dict, 'null': type(None)}
bad = []
for r in rules:
    for f in schema['required']:
        if r.get(f) in (None, ''):
            bad.append(f"{r.get('team_rule_id')}: missing {f}")
    for f, spec in schema['properties'].items():
        if f not in r:
            continue
        v = r[f]
        if 'enum' in spec and v not in spec['enum']:
            bad.append(f"{r['team_rule_id']}: {f}={v!r} not in enum")
        if 'type' in spec:
            ts = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
            if not any(isinstance(v, TYPES[t]) and not (t == 'number' and isinstance(v, bool)) for t in ts):
                bad.append(f"{r['team_rule_id']}: {f} has wrong type")
        if 'pattern' in spec and isinstance(v, str) and not re.match(spec['pattern'], v):
            bad.append(f"{r['team_rule_id']}: {f}={v!r} fails pattern")
        if 'minLength' in spec and isinstance(v, str) and len(v) < spec['minLength']:
            bad.append(f"{r['team_rule_id']}: {f} shorter than {spec['minLength']}")
        if f == 'confidence' and v is not None and not 0 <= v <= 1:
            bad.append(f"{r['team_rule_id']}: confidence out of range")
if len({r['team_rule_id'] for r in rules}) != len(rules):
    bad.append('duplicate team_rule_id')
check('rules.json matches the organiser schema', not bad, f"{len(rules)} rules; " + ('no violations' if not bad else '; '.join(bad[:5])))

# 2 verbatim quotes
texts = {k: norm(d['text']) for k, d in docs.items()}
absent = [r['team_rule_id'] for r in rules if r['source_doc_id'] not in texts]   # link-only research text is not redistributed in the repository
miss = [r['team_rule_id'] for r in rules if r['source_doc_id'] in texts and norm(r['quoted_span']) not in texts[r['source_doc_id']]]
check('every quoted_span is found verbatim in its source document', not miss, f"{len(rules) - len(miss) - len(absent)}/{len(rules)} verified" +
      (f"; {len(absent)} rest on link-only text not present here and cannot be re-verified from this copy" if absent else '') + (f"; not found: {miss[:5]}" if miss else ''))
allsrc = [s for r in rules for s in r['sources']]
src_miss = sum(s['doc_id'] in texts and norm(s['quoted_span']) not in texts[s['doc_id']] for s in allsrc)
check('every supporting-source quote is verbatim too', src_miss == 0, f"{sum(s['doc_id'] in texts for s in allsrc) - src_miss}/{len(allsrc)} verified; {sum(s['doc_id'] not in texts for s in allsrc)} from link-only text not present here")

# 3 citation metric proxy
app = [(aid, x) for aid, ls in look['lookups'].items() for x in ls if x['result'] == 'applies']
in_corpus = sum(byid[x['team_rule_id']]['in_distributed_corpus'] for _, x in app)
check('"applies" answers backed by a quote in the distributed corpus', True, f"{in_corpus}/{len(app)} = {100 * in_corpus / max(1, len(app)):.1f}% (the rest rest on link-only sources we saved ourselves, which the organisers said do not count)")

# 4 coverage of addresses
ALLOWED = {'applies', 'unknown', 'superseded', 'not_yet_effective', 'pending'}
badres = [x for ls in look['lookups'].values() for x in ls if x['result'] not in ALLOWED or x['team_rule_id'] not in byid or not x['explanation']]
check('lookups.json covers all 500 addresses with valid results', len(look['lookups']) == 500 and set(look['lookups']) == set(addrs) and not badres,
      f"{len(look['lookups'])} addresses, {sum(len(v) for v in look['lookups'].values())} results, {len(badres)} invalid")
exp = {'Los Angeles, CA': 80, 'San Francisco, CA': 80, 'San Diego, CA': 50, 'Berkeley, CA': 40, 'Jersey City, NJ': 50, 'Hoboken, NJ': 40, 'Newark, NJ': 50, 'Boston, MA': 60, 'Cambridge, MA': 50}
got = collections.Counter(a['city'] for a in addrs.values())
check('legal-city counts match the participant guide', dict(got) == exp, ', '.join(f"{k.split(',')[0]} {v}" for k, v in sorted(got.items())))

# 5 change tests
ids = lambda pred: {k for k, a in addrs.items() if pred(a)}
CA, NJ, MA = ids(lambda a: a['state'] == 'CA'), ids(lambda a: a['state'] == 'NJ'), ids(lambda a: a['state'] == 'MA')
HOB, JC = ids(lambda a: a['city'] == 'Hoboken, NJ'), ids(lambda a: a['city'] == 'Jersey City, NJ')
S = lambda t, k='affected_address_ids': set(changes[t].get(k, []))
check('T1 CA AB 325 / SB 763: every CA address changes between 2025-12-31 and 2026-01-02', S('T1') == CA, f"{len(S('T1'))} affected vs {len(CA)} CA addresses")
check('T2 local bans stay inside their own city limits', S('T2') == HOB | JC, f"{len(S('T2'))} affected = Hoboken {len(HOB)} + Jersey City {len(JC)}; Newark {len(S('T2') & ids(lambda a: a['city'] == 'Newark, NJ'))}")
check('T3 NJ FAIR Act: not yet effective now, applies 2027-07-02, conflict flags in Jersey City and Hoboken', S('T3') == NJ and S('T3', 'conflict_flag_address_ids') == HOB | JC,
      f"{len(S('T3'))} affected vs {len(NJ)} NJ addresses; {len(S('T3', 'conflict_flag_address_ids'))} conflict flags")
check('T4 MA S.2983 / H.5222 pending for every MA address', S('T4') == MA, f"{len(S('T4'))} affected vs {len(MA)} MA addresses")
caps = [(aid, x['team_rule_id']) for aid in MA for x in look['lookups'][aid]
        if byid[x['team_rule_id']]['category'] == 'rent_increase_limits' and byid[x['team_rule_id']]['rule_kind'] != 'bars_local_regulation']
failed = [r['team_rule_id'] for r in rules if r['jurisdiction'] in ('MA', 'Boston, MA', 'Cambridge, MA') and r['category'] == 'rent_increase_limits' and r['status'] == 'failed']
check('T5 no rent cap reported in Boston or Cambridge; ballot question recorded as failed', not S('T5') and not caps and failed,
      f"affected {len(S('T5'))}; rent-cap results at MA addresses {len(caps)}; recorded as failed: {', '.join(failed)}")
t1b = J('web/data/meta.json')['changeDetail']
check('every organiser test rule id maps to an extracted rule', all(v for t in t1b.values() for v in t['mapped'].values()), '; '.join(f"{k}->{'+'.join(v)}" for t in t1b.values() for k, v in t['mapped'].items()))

# 6 as-of sanity: which rules change status across the T1 window, and why
import subprocess
flips = json.loads(subprocess.run(['node', '--input-type=module', '-e',
    "import fs from 'node:fs';import {statusOf} from './web/engine.js';const r=JSON.parse(fs.readFileSync('submission/rules.json')).rules;"
    "console.log(JSON.stringify(r.map(x=>[x.team_rule_id,statusOf(x,'2025-12-31'),statusOf(x,'2026-10-01'),x.effective_date]).filter(x=>x[1]!==x[2])))"],
    capture_output=True, text=True, cwd=ROOT).stdout or '[]')
started = [f for f in flips if f[3] and '2025-12-31' < (f[3] + '-01-01')[:10] <= '2026-10-01']
check('as-of 2025-12-31 vs 2026-10-01: only laws that started in that window change status', len(started) == len(flips),
      '; '.join(f"{f[0]} {f[1]}->{f[2]} (eff {f[3]})" for f in flips) or 'no rule changes status')
res = collections.Counter(x['result'] for ls in look['lookups'].values() for x in ls)
report = dict(as_of=look['as_of'], rules=len(rules), rules_by_status=dict(collections.Counter(r['status'] for r in rules)), lookup_results=dict(res), checks=checks,
              passed=sum(c['pass'] for c in checks), total=len(checks))
meta = J('web/data/meta.json'); meta['validation'] = report
json.dump(meta, open(os.path.join(ROOT, 'web/data/meta.json'), 'w'), indent=1)
json.dump(report, open(os.path.join(ROOT, 'submission/validation_report.json'), 'w'), indent=1)
print(f"SELF-VALIDATION  as of {look['as_of']}  -  {report['passed']}/{report['total']} checks pass")
for c in checks:
    print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['name']}\n         {c['detail']}")
print(f"  rules by status: {report['rules_by_status']}\n  lookup results:  {report['lookup_results']}")
sys.exit(0 if report['passed'] == report['total'] else 1)
