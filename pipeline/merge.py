"""Module A, step 2: consolidate raw per-document records into one rule per law, per category, per jurisdiction.

The same law is usually described by several documents (statute text, agency page, law-firm alert).
An LLM clusters records that describe the same law; code then assembles each rule so that
  - the quoted span comes from distributed corpus text whenever any member has one (citation metric),
  - missing dates are filled from the other documents, and disagreements become conflict flags,
  - state/local interactions are derived from flags the extractor read out of the text.
"""
import json, os, re, collections
from common import ROOT, SCOPE, CATEGORIES, llm_json, audit

ABBR = dict(rent_increase_limits='RENT', just_cause_eviction='EVICT', security_deposits='DEP', application_screening_fees='FEE',
            screening_restrictions='SCREEN', algorithmic_rent_setting='ALG')
EV_RANK = {'primary_law_text': 0, 'official_summary': 1, 'secondary_report': 2}

CLUSTER_PROMPT = '''Below are rule records extracted independently from different documents. All are for jurisdiction "{jur}", category "{cat}".
Group the records that describe THE SAME law, ordinance, bill or ballot measure into one cluster (different documents often cite the same law
differently: a code section, a bill number, a popular name, an ordinance number).

Rules:
- Different laws stay in different clusters (a statewide statute vs. a different bill; two different bills; an enacted law vs. a struck ballot measure).
- A record that only gives a periodic figure or a sub-provision of a law already present (annual allowable increase announcement, relocation
  payment schedule, interest rate, fee pass-through, notice form) belongs in the cluster of the law it implements.
- Every record id must appear in exactly one cluster.
- For each cluster give:
  canonical_law_name: short common name of the law.
  canonical_citation: the most official cite, written in standard legal citation form using ONLY identifiers that appear in the records
    (code section > public law number > ordinance number > bill number), e.g. "Cal. Civ. Code § 1947.12", "N.J.S.A. 46:8-21.2", "M.G.L. c. 186, § 15B", "S.F. Admin. Code § 37.9".
  best_member_id: the record that best states the main requirement of the law for this category (not a side provision or a one-year figure).
  tier: "core" if this is THE main rule of this category for this jurisdiction that an address-level answer must report
        (the rent cap / rent-control ordinance or the statute barring it; the just-cause eviction law; the deposit cap; the application-fee rule;
        the criminal-history or source-of-income screening law; the algorithmic rent-setting ban or bill; a ballot measure or bill that would create one of these);
        "ancillary" if it is a narrower side provision (relocation amounts, retaliation, lockouts, foreclosure, special populations, expired emergency
        moratoria, disclosure add-ons, general notice periods);
        "not_a_rule" if it is only general information or not actually a rule of this category.

Return ONLY JSON: {{"clusters":[{{"member_ids":["..."],"canonical_law_name":"...","canonical_citation":"...","best_member_id":"...","tier":"core"}}]}}

Records:
{records}'''


HINT_PROMPT = '''The document below is the organisers' participant guide for a housing-law dataset. List every statement in it that gives a
coverage cutoff for a specific jurisdiction's rules (for example a certificate-of-occupancy date that decides which buildings a city's rent ordinance covers).
jurisdiction must be one of {scope}. Quote the supporting sentence verbatim.
Return ONLY JSON: {{"hints":[{{"jurisdiction":"...","co_on_or_before":"YYYY-MM-DD","applies_to":"short description of the ordinance or rule family","quote":"..."}}]}}
<document>
{text}
</document>'''

COVERAGE_PROMPT = '''These are the consolidated rental-housing rules for jurisdiction "{jur}". For EACH rule decide two things about its coverage,
using only the coverage, exemptions and requirement text given.

1. depends_on_building_facts (true/false): true if whether an ORDINARY multifamily apartment building in this jurisdiction is covered depends on a
   building-specific fact such as construction or certificate-of-occupancy date, a registration or exemption filing, or participation in a program.
   false if the rule reaches essentially every ordinary residential rental in the jurisdiction and its exemptions concern unusual property types or
   situations only (hotels, care facilities, dormitories, public or subsidised housing, owner-occupied small buildings, seasonal rentals, who the landlord is).
2. inherits_coverage_from: if the rule says it covers "units covered by" / "subject to" another ordinance or rule in this list (for example an eviction or
   deposit-interest rule that applies to units under the rent ordinance), give that rule's id; otherwise null. Never point a rule at itself.
   Give the id even if this rule may also reach some units beyond the other rule: the link is used only to show a building IS covered, never to exclude one.

3. excluded_where_covered_by: if the rule covers only units that are NOT subject to another ordinance or rule in this list (for example a citywide
   just-cause ordinance that protects units outside the rent stabilization ordinance), give that other rule's id; otherwise null.
4. effective_date_is: look at the rule's effective_date and the words it rests on. Answer "law_start" if that is the date the law or ordinance itself
   first took (or will take) effect; "amendment_or_periodic" if it is the date of a later amendment, a new version of a section of a law that already
   existed, or a periodic figure (annual allowable increase, yearly relocation amount, interest rate); "none" if no date is given.
   A long-standing code section or ordinance whose CURRENT VERSION or current-year figure starts on the date is "amendment_or_periodic"
   (signals: the title or requirement names a year's adjustment, a ballot measure amending an existing ordinance, or a section "as amended";
   the sources list several different dates). A newly created prohibition, fee cap or act whose first-ever effect is that date is "law_start".

Return ONLY JSON: {{"rules":[{{"id":"...","depends_on_building_facts":true,"inherits_coverage_from":null,"excluded_where_covered_by":null,"effective_date_is":"law_start","why":"one short sentence"}}]}}

Rules:
{records}'''


def precision(d):
    return len(d) if d else 0


def consistent(a, b):
    """Dates agree if one is a prefix of the other (2025-07 vs 2025-07-09)."""
    return a.startswith(b) or b.startswith(a)


def rank(r):
    return (not r['in_distributed_corpus'], EV_RANK.get(r.get('evidence_type'), 3), -(r.get('confidence') or 0))


def build(members, canon):
    members = sorted(members, key=rank)
    best = next((m for m in members if m['raw_id'] == canon.get('best_member_id')), members[0])
    corpus = [m for m in members if m['in_distributed_corpus']]
    # descriptive fields come from the record that best states the rule; the quote must come from distributed corpus text when any exists
    rep = best if (best['in_distributed_corpus'] or not corpus) else corpus[0]
    rule = {k: rep.get(k) for k in ('jurisdiction', 'level', 'category', 'rule_kind', 'title', 'requirement', 'key_value', 'exemptions', 'penalty',
                                    'interaction', 'quoted_span', 'source_doc_id', 'source_url', 'retrieved_at', 'bill_number', 'legislative_session_end_year')}
    rule['law_name'] = canon.get('canonical_law_name') or rep.get('law_name')
    rule['citation'] = canon.get('canonical_citation') or rep.get('citation') or rep.get('bill_number') or rule['law_name'] or rep.get('title')
    rule['level'] = 'state' if len(rule['jurisdiction']) == 2 else 'city'
    rule['rule_kind'] = rule['rule_kind'] or 'requirement'
    notes = []

    stages = [m.get('legal_stage') for m in members]
    rule['legal_stage'] = 'struck' if 'struck' in stages else ('enacted' if 'enacted' in stages else 'bill')
    if len(set(stages)) > 1:
        notes.append('Sources capture different stages of this law: ' + ', '.join(f"{m['source_doc_id']}={m.get('legal_stage')}" for m in members))

    def months(d):
        p = (d + '-01-01')[:10].split('-'); return int(p[0]) * 12 + int(p[1])
    for field in ('effective_date', 'enacted_date', 'sunset_date'):
        vals = [(m[field], m) for m in members if m.get(field)]
        rule[field] = None
        if not vals:
            continue
        # the law's own date: the record that best states the rule, else the earliest date any source gives
        if field == 'sunset_date':   # only the law's own text can end the law; periodic figures and notices carry period end dates, not sunsets
            vals = [v for v in vals if v[1] is best and v[1].get('evidence_type') == 'primary_law_text']
            if not vals:
                continue
        own = [v for v in vals if v[1] is best] or sorted(vals, key=lambda v: (v[0] + '-01-01')[:10])[:1]
        base = own[0]
        agree = [v for v in vals if consistent(v[0], base[0])]
        pick = sorted(agree, key=lambda v: -precision(v[0]))[0]
        rule[field] = pick[0]
        if field == 'effective_date':
            rule['effective_date_basis'] = pick[1].get('effective_date_basis')
            rule['effective_date_source_doc_id'] = pick[1]['source_doc_id']
            others = sorted({(v[0], v[1]['source_doc_id']) for v in vals if not consistent(v[0], base[0])})
            rule['effective_date_alternatives'] = [d for d, _ in others]
            if others:
                txt = f"{pick[0]} ({pick[1]['source_doc_id']}) vs " + ', '.join(f"{d} ({doc})" for d, doc in others)
                near = any(abs(months(d) - months(pick[0])) <= 12 for d, _ in others)
                # dates within a year of each other are competing published dates for one change; farther apart they are amendments or annual figures
                notes.append(('[flag] Published effective dates differ: ' if near else 'Other dates in the sources (amendments or periodic figures): ') + txt)
    rule.setdefault('effective_date_alternatives', [])
    rule.setdefault('effective_date_basis', None); rule.setdefault('effective_date_source_doc_id', None)

    cov = {}
    for m in members:
        for k, v in (m.get('coverage') or {}).items():
            if v not in (None, '', []) and k not in cov:
                cov[k] = v
    rule['coverage_conditions'] = cov or None
    official = [m for m in members if EV_RANK.get(m.get('evidence_type'), 3) < 2]
    rule['yields_to_stricter_local'] = any(m.get('yields_to_stricter_local') for m in official)
    rule['bars_or_preempts_local'] = any(m.get('bars_or_preempts_local') for m in official) or rule['rule_kind'] == 'bars_local_regulation'
    for m in members:
        if m.get('conflict_note'):
            notes.append(f"{m['source_doc_id']}: {m['conflict_note']}")
    rule['in_distributed_corpus'] = rep['in_distributed_corpus']
    conf = rep.get('confidence') or 0.6
    if not rep['in_distributed_corpus']:
        conf = min(conf, 0.6)   # supported only by independently saved link-only text: research-grade
    if EV_RANK.get(rep.get('evidence_type'), 3) == 2:
        conf = min(conf, 0.5)
    rule['confidence'] = round(conf, 2)
    rule['evidence_type'] = rep.get('evidence_type')
    rule['sources'] = [dict(doc_id=m['source_doc_id'], url=m['source_url'], retrieved_at=m['retrieved_at'], quoted_span=m['quoted_span'],
                            evidence_type=m.get('evidence_type'), in_distributed_corpus=m['in_distributed_corpus'], citation=m.get('citation'),
                            effective_date=m.get('effective_date')) for m in members]
    rule['_notes'] = notes
    return rule


def main():
    raw = json.load(open(os.path.join(ROOT, 'cache', 'raw_rules.json')))
    groups = collections.defaultdict(list)
    for recs in raw.values():
        for r in recs:
            groups[(r['jurisdiction'], r['category'])].append(r)
    rules, dropped = [], []
    for (jur, cat) in sorted(groups, key=lambda k: (SCOPE.index(k[0]), CATEGORIES.index(k[1]))):
        recs = sorted(groups[(jur, cat)], key=lambda r: r['raw_id'])
        byid = {r['raw_id']: r for r in recs}
        if True:
            listing = '\n'.join(json.dumps(dict(id=r['raw_id'], law_name=r.get('law_name'), citation=r.get('citation'), bill_number=r.get('bill_number'), title=r.get('title'),
                                                 legal_stage=r.get('legal_stage'), effective_date=r.get('effective_date'), evidence_type=r.get('evidence_type'),
                                                 requirement=(r.get('requirement') or '')[:260]), ensure_ascii=False) for r in recs)
            res, _, _ = llm_json(CLUSTER_PROMPT.format(jur=jur, cat=cat, records=listing), tag=f'cluster:{jur}:{cat}')
            clusters = res['clusters']
            seen = {i for c in clusters for i in c['member_ids']}
            clusters += [dict(member_ids=[i], canonical_law_name=byid[i].get('law_name'), canonical_citation=byid[i].get('citation'), tier='ancillary') for i in byid if i not in seen]
        for c in clusters:
            allm = [byid[i] for i in dict.fromkeys(c['member_ids']) if i in byid]
            # an enacted law, a pending bill and a struck measure are never the same rule, whatever the clustering says
            # a struck measure is never the same rule as a live one; bill text and its enacted version are the same law
            for struck in (False, True):
                members = [m for m in allm if (m.get('legal_stage') == 'struck') == struck]
                if not members:
                    continue
                if c.get('tier') == 'not_a_rule':
                    dropped.append(dict(jurisdiction=jur, category=cat, law=c.get('canonical_law_name'), members=c['member_ids']))
                    continue
                rule = build(members, c)
                rule['tier'] = c.get('tier', 'core')
                rules.append(rule)

    # stable ids
    counter = collections.Counter()
    for r in sorted(rules, key=lambda r: (SCOPE.index(r['jurisdiction']), CATEGORIES.index(r['category']), r['legal_stage'] != 'enacted', r['citation'] or '')):
        words = r['jurisdiction'].split(',')[0].split()
        prefix = r['jurisdiction'] if r['level'] == 'state' else (''.join(w[0] for w in words) if len(words) > 1 else words[0][:3]).upper()
        key = f"{prefix}-{ABBR[r['category']]}"
        counter[key] += 1
        r['team_rule_id'] = f"{key}-{counter[key]:02d}"

    # state/local interaction, derived from flags read out of the text
    for r in rules:
        r['overrides'], r['conflict_flag'], r['conflict_with'] = [], False, []
    core = [r for r in rules if r.get('tier') != 'ancillary']
    for s in [r for r in core if r['level'] == 'state' and r['legal_stage'] == 'enacted']:
        locals_ = [c for c in core if c['level'] == 'city' and c['jurisdiction'].endswith(', ' + s['jurisdiction']) and c['category'] == s['category']
                   and c['legal_stage'] == 'enacted' and c['rule_kind'] == 'requirement']
        if not locals_:
            continue
        ids = [c['team_rule_id'] for c in locals_]
        if s['yields_to_stricter_local'] and s.get('tier') != 'ancillary' and s['rule_kind'] == 'requirement':
            s['overrides'] = ids
            s['interaction'] = ((s.get('interaction') or '').rstrip('. ') + '. ' if s.get('interaction') else '') + \
                f"Yields to a stricter local ordinance where one covers the unit ({', '.join(ids)}); reported as superseded at those addresses."
            for c in locals_:
                c['overrides'].append(s['team_rule_id'])
                c['interaction'] = ((c.get('interaction') or '').rstrip('. ') + '. ' if c.get('interaction') else '') + f"Where it covers a unit it governs instead of the state rule {s['team_rule_id']}."
        if s['bars_or_preempts_local'] and not s['yields_to_stricter_local']:   # a savings clause for stricter local law settles the relation
            s['conflict_flag'] = True
            s['_notes'].append(f"State text bars conflicting municipal ordinances; possible conflict with {', '.join(ids)}. Flagged for human review, not resolved by this tool.")
            s['overrides'] = sorted(set(s['overrides'] + ids))
            newer = [c for c in locals_ if s.get('effective_date') and (not c.get('effective_date') or c['effective_date'][:7] < s['effective_date'][:7])]
            if not newer:
                s['conflict_flag'] = False; s['_notes'].pop(); s['overrides'] = [i for i in s['overrides'] if i not in ids or s['yields_to_stricter_local']]
            s['conflict_with'] = [c['team_rule_id'] for c in newer]
            for c in newer:
                c['conflict_flag'] = True
                c['conflict_with'].append(s['team_rule_id'])
                c['overrides'].append(s['team_rule_id'])
                c['_notes'].append(f"Possible conflict/preemption with state rule {s['team_rule_id']} ({s['citation']}), whose text bars conflicting municipal ordinances. Flagged for human review.")
    for r in rules:
        notes = r.pop('_notes')
        if any(n.startswith('[flag]') for n in notes):
            r['conflict_flag'] = True
        r['conflict_note'] = ' | '.join(dict.fromkeys(n.replace('[flag] ', '') for n in notes)) or None
        r['overrides'] = sorted(set(r['overrides']))

    # coverage normalisation: which rules turn on building-specific facts, and which borrow another rule's coverage test
    for jur in SCOPE:
        mine = [r for r in rules if r['jurisdiction'] == jur and r.get('tier') != 'ancillary']
        if not mine:
            continue
        listing = '\n'.join(json.dumps(dict(id=r['team_rule_id'], category=r['category'], title=r['title'], requirement=r['requirement'],
                                              coverage=r['coverage_conditions'], exemptions=r['exemptions'], legal_stage=r['legal_stage'], enacted_date=r['enacted_date'],
                                              effective_date=r['effective_date'], effective_date_basis=r['effective_date_basis'],
                                              dates_in_sources=sorted({s_['effective_date'] for s_ in r['sources'] if s_.get('effective_date')})), ensure_ascii=False) for r in mine)
        res, _, _ = llm_json(COVERAGE_PROMPT.format(jur=jur, records=listing), tag=f'coverage:{jur}')
        ans = {x['id']: x for x in res.get('rules', [])}
        ids = {r['team_rule_id'] for r in mine}
        for r in mine:
            a = ans.get(r['team_rule_id'], {})
            cov = r['coverage_conditions'] or {}
            cov['depends_on_building_facts'] = bool(a.get('depends_on_building_facts'))
            inh = a.get('inherits_coverage_from')
            cov['inherits_coverage_from'] = inh if inh in ids and inh != r['team_rule_id'] else None
            exc = a.get('excluded_where_covered_by')
            cov['excluded_where_covered_by'] = exc if exc in ids and exc != r['team_rule_id'] else None
            cov['coverage_basis'] = a.get('why')
            # status is computed from the law's own start date; an amendment or annual-figure date never makes an existing law "not yet effective"
            r['effective_date_kind'] = a.get('effective_date_is') if r['effective_date'] else 'none'
            r['coverage_conditions'] = cov
    # coverage cutoffs stated only in the participant guide: fill them into rules whose own sources leave the cutoff out
    from common import load_docs, norm
    guide = next((d for d in load_docs() if d['doc_id'] == 'GUIDE'), None)
    if guide:
        res, _, _ = llm_json(HINT_PROMPT.format(scope=json.dumps(SCOPE), text=guide['text']), tag='coverage_hints:GUIDE')
        for h in res.get('hints', []):
            if not h.get('co_on_or_before') or norm(h.get('quote', '')).replace('*', '') not in norm(guide['text']).replace('*', ''):
                continue
            for r in rules:
                cov = r['coverage_conditions'] or {}
                if (r['jurisdiction'] == h['jurisdiction'] and r.get('tier') != 'ancillary' and cov.get('depends_on_building_facts') and not cov.get('inherits_coverage_from')
                        and not cov.get('co_on_or_before') and cov.get('exempt_if_newer_than_years') is None and not cov.get('excluded_where_covered_by') and r['category'] == 'rent_increase_limits'):
                    cov['co_on_or_before'] = h['co_on_or_before']
                    cov['co_cutoff_source'] = f"participant guide: \"{h['quote']}\""
                    r['coverage_conditions'] = cov
    ancillary = [r for r in rules if r.get('tier') == 'ancillary']
    rules = [r for r in rules if r.get('tier') != 'ancillary']
    json.dump(dict(rules=rules, ancillary=ancillary, dropped=dropped), open(os.path.join(ROOT, 'cache', 'rules_merged.json'), 'w'), indent=1, ensure_ascii=False)
    audit('merge', raw=sum(len(v) for v in raw.values()), rules=len(rules), dropped=len(dropped))
    print(f"{sum(len(v) for v in raw.values())} raw records -> {len(rules)} core rules, {len(ancillary)} ancillary provisions, {len(dropped)} dropped")
    for r in rules:
        print(f"  {r['team_rule_id']:<14} {r['legal_stage']:<8} eff={str(r['effective_date']):<11} corpus={'Y' if r['in_distributed_corpus'] else 'n'} src={len(r['sources'])} {'CONFLICT ' if r['conflict_flag'] else ''}| {r['citation'][:48]} | {r['title'][:50]}")


if __name__ == '__main__':
    main()
