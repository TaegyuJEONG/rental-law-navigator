"""Module A, step 1: an LLM reads every document and emits raw rule records.

Nothing about any specific law is written in this file. The model reads the text; code only
(1) chunks long documents, (2) caches by content hash so reruns are reproducible, and
(3) rejects any record whose quoted_span is not a verbatim substring of the source.

Usage: python3 pipeline/extract.py [DOC_ID ...] [--file path.txt --jurisdiction "Cambridge, MA"]
"""
import csv, hashlib, json, os, re, sys, time, concurrent.futures as cf
from common import ROOT, SCOPE, CATEGORIES, llm_json, norm, audit, load_docs, MODEL

CHUNK = 45000
OVERLAP = 2500

PROMPT = '''You extract rental-housing rules from ONE source document into structured records.
Return ONLY a JSON object {{"rules":[...]}}.

SCOPE
- jurisdiction must be exactly one of: {scope}. Skip rules for any other place.
  A document may describe rules of a jurisdiction other than its listed one (e.g. a state law-firm alert describing city ordinances).
- category must be one of: {cats}. If nothing in the document fits, return {{"rules":[]}}.

GRANULARITY (important)
- ONE record per distinct law/ordinance, per category, per jurisdiction. Example: an entire rent stabilization ordinance's
  rent-increase regime is ONE rent_increase_limits record; its eviction protections are ONE just_cause_eviction record.
- Put sub-provisions (surcharges, pass-throughs, notice periods, relocation amounts) inside requirement / key_value / exemptions.
- Administrative machinery (boards, registration, hearings, forms) is NOT a rule.
- A statute that BARS local governments from regulating (e.g. bars local rent control) is a record with rule_kind "bars_local_regulation".

FIELDS of each record
- law_name: short canonical name of the law/ordinance/bill (e.g. the popular name or "<Code> § <section>")
- citation: the official cite as written in the document (code section, chapter number, public law number, or bill number)
- bill_number: e.g. "S.2983" if this is a bill or initiative petition, else null
- jurisdiction, level ("state"|"city"), category
- rule_kind: "requirement" | "bars_local_regulation"
- legal_stage: "enacted" | "bill" (bill, petition or proposal not yet law) | "struck" (defeated, struck down, removed from ballot, repealed)
- legislative_session_end_year: for a bill, the last year of the legislative session shown in the document (e.g. 2024 for "193rd (2023 - 2024)"), else null
- enacted_date: date signed/adopted/approved if stated (YYYY-MM-DD, YYYY-MM or YYYY), else null
- effective_date: when the rule takes/took effect (YYYY-MM-DD, YYYY-MM or YYYY). Use a date only if the document states it, or
  states a formula you can compute from dates in the document. Otherwise null. Never guess.
- effective_date_basis: the exact words in the document the effective date rests on, else null
- sunset_date: date the rule expires/is repealed by its own terms, if stated, else null
- title: short title
- requirement: one or two plain-language sentences a renter could act on
- key_value: the headline number or formula (e.g. "5% + CPI, max 10%", "1.5 months' rent"), else null
- coverage: object describing which buildings/tenancies are covered, with these keys (null when not stated):
    co_on_or_before: "YYYY-MM-DD" -- covered only if the building's certificate of occupancy / construction is on or before this date
    exempt_if_newer_than_years: integer -- buildings newer than this many years are exempt (rolling cutoff)
    min_units: integer -- applies only to buildings with at least this many units
    owner_occupied_exempt_max_units: integer -- owner-occupied buildings with at most this many units are exempt
    small_owner_exception: string -- an exception/variation that depends on who the owner is (natural person, number of properties...), else null
    other_unresolvable: string -- any other coverage test that depends on facts not found in public assessor data, else null
    summary: one sentence describing coverage in plain language
- exemptions: string or null
- penalty: string or null
- yields_to_stricter_local: true if the text itself says this rule does not apply where a more protective/restrictive LOCAL ordinance applies, else false
- bars_or_preempts_local: true if the text bars municipalities from enacting/maintaining conflicting local rules, else false
- interaction: one sentence on how this rule relates to other state/local rules as stated in the document, else null
- quoted_span: ONE contiguous passage copied VERBATIM from the document (40-400 characters, exact characters and spelling,
  no ellipsis, no paraphrase, no stitching) that best supports the requirement
- evidence_type: "primary_law_text" | "official_summary" | "secondary_report"
- confidence: 0-1
- conflict_note: string if the document itself shows conflicting dates/values or signals a possible conflict with another law, else null

Never invent anything not supported by the document. If the document is only navigation, a form, or unrelated material, return {{"rules":[]}}.

Document metadata: doc_id={doc_id}; listed jurisdiction={jur}; source_type={stype}; url={url}; part {part} of {parts}
<document>
{text}
</document>'''


def chunks(text):
    if len(text) <= CHUNK * 1.3:
        return [text]
    out, i = [], 0
    while i < len(text):
        j = min(len(text), i + CHUNK)
        if j < len(text):
            k = text.rfind('\n', i + CHUNK - 3000, j)
            j = k if k > 0 else j
        out.append(text[i:j])
        if j >= len(text):
            break
        i = j - OVERLAP
    return out


def extract_doc(doc):
    text = doc['text']
    parts = chunks(text)
    nt = norm(text)
    recs, usage = [], {'in': 0, 'out': 0}
    for n, part in enumerate(parts, 1):
        prompt = PROMPT.format(scope=json.dumps(SCOPE), cats=', '.join(CATEGORIES), doc_id=doc['doc_id'], jur=doc['jurisdictions'],
                               stype=doc['source_type'], url=doc['url'], part=n, parts=len(parts), text=part)
        res, u, cached = llm_json(prompt, tag=f"extract:{doc['doc_id']}:{n}")
        usage['in'] += u.get('prompt_tokens', 0); usage['out'] += u.get('completion_tokens', 0)
        for r in res.get('rules', []):
            r.update(source_doc_id=doc['doc_id'], source_url=doc['url'], retrieved_at=doc['retrieved_at'],
                     in_distributed_corpus=doc['in_corpus'], source_type=doc['source_type'], doc_sha256=doc['sha256'])
            span = r.get('quoted_span') or ''
            r['span_verified'] = len(span) >= 20 and norm(span) in nt
            if r['span_verified']:
                r['span_offset'] = nt.find(norm(span))
            r['in_scope'] = r.get('jurisdiction') in SCOPE and r.get('category') in CATEGORIES
            recs.append(r)
    kept = [r for r in recs if r['span_verified'] and r['in_scope']]
    audit('extract', doc_id=doc['doc_id'], sha256=doc['sha256'], model=MODEL, parts=len(parts), raw=len(recs), kept=len(kept),
          rejected_span=sum(not r['span_verified'] for r in recs), rejected_scope=sum(not r['in_scope'] for r in recs), tokens=usage)
    for i, r in enumerate(kept, 1):
        r['raw_id'] = f"{doc['doc_id']}-{i}"
    return doc['doc_id'], kept, len(recs)


def main():
    args = sys.argv[1:]
    docs = load_docs()
    if '--file' in args:  # extend with a brand-new document (new ordinance / new jurisdiction)
        p = args[args.index('--file') + 1]
        jur = args[args.index('--jurisdiction') + 1] if '--jurisdiction' in args else 'unknown'
        t = open(p).read()
        docs = [dict(doc_id='NEW-' + hashlib.sha256(t.encode()).hexdigest()[:6], jurisdictions=jur, url='file://' + os.path.basename(p), source_type='new document',
                     retrieved_at=time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), text=t, sha256=hashlib.sha256(t.encode()).hexdigest(), in_corpus=False)]
    else:
        want = [a for a in args if re.match(r'(D\d+|GUIDE)$', a)]
        if want:
            docs = [d for d in docs if d['doc_id'] in want]
    out_path = os.path.join(ROOT, 'cache', 'raw_rules.json')
    allrecs = json.load(open(out_path)) if os.path.exists(out_path) else {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(6) as ex:
        for doc_id, kept, raw in ex.map(extract_doc, docs):
            allrecs[doc_id] = kept
            print(f'{doc_id}: kept {len(kept)}/{raw}', flush=True)
    json.dump(allrecs, open(out_path, 'w'), indent=1, ensure_ascii=False)
    n = sum(len(v) for v in allrecs.values())
    print(f'raw rules: {n} from {len(allrecs)} docs in {time.time()-t0:.0f}s -> cache/raw_rules.json')


if __name__ == '__main__':
    main()
