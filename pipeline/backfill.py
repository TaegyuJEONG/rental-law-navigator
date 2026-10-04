"""Module A, step 3: citation backfill.

A rule whose only support is an independently saved link-only page does not count for the citation metric.
For each such rule, ask the model whether any DISTRIBUTED corpus document of the same jurisdiction contains a
verbatim passage supporting it. The quote is accepted only if code finds it in that document.
"""
import json, os, concurrent.futures as cf
from common import ROOT, llm_json, norm, load_docs, audit
from extract import chunks

PROMPT = '''A rental-housing rule is described below. Decide whether the DOCUMENT contains a passage that directly supports this same rule
(the same law and the same requirement, not merely a related topic).
If yes, copy ONE contiguous passage VERBATIM from the document (40-400 characters, exact characters, no ellipsis). If no, return null.
Return ONLY JSON: {{"supports": true/false, "quoted_span": "..." or null, "citation_in_document": "the cite as written in the document, or null"}}

RULE
jurisdiction: {jur}
category: {cat}
law: {law} ({cit})
requirement: {req}

DOCUMENT (doc_id={doc_id}, part {n} of {parts})
<document>
{text}
</document>'''


def main():
    path = os.path.join(ROOT, 'cache', 'rules_merged.json')
    data = json.load(open(path))
    docs = [d for d in load_docs() if d['in_corpus']]
    todo = [r for r in data['rules'] if not r['in_distributed_corpus'] and r['legal_stage'] != 'struck']
    jobs = []
    for r in todo:
        state = r['jurisdiction'][-2:]
        for d in docs:
            listed = d['jurisdictions']
            if listed == r['jurisdiction'] or listed == state:
                parts = chunks(d['text'])
                for n, part in enumerate(parts, 1):
                    jobs.append((r, d, n, len(parts), part))

    def run(job):
        r, d, n, parts, part = job
        res, _, _ = llm_json(PROMPT.format(jur=r['jurisdiction'], cat=r['category'], law=r['law_name'], cit=r['citation'], req=r['requirement'],
                                           doc_id=d['doc_id'], n=n, parts=parts, text=part), tag=f"backfill:{r['team_rule_id']}:{d['doc_id']}:{n}")
        q = res.get('quoted_span') or ''
        ok = bool(res.get('supports')) and len(q) >= 40 and norm(q) in norm(d['text'])
        return r['team_rule_id'], d, q if ok else None

    found = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for rid, d, q in ex.map(run, jobs):
            if q and rid not in found:
                found[rid] = (d, q)
    for r in todo:
        if r['team_rule_id'] in found:
            d, q = found[r['team_rule_id']]
            r['sources'].insert(0, dict(doc_id=d['doc_id'], url=d['url'], retrieved_at=d['retrieved_at'], quoted_span=q, evidence_type='corpus_backfill',
                                        in_distributed_corpus=True, citation=r['citation'], effective_date=None))
            r.update(quoted_span=q, source_doc_id=d['doc_id'], source_url=d['url'], retrieved_at=d['retrieved_at'], in_distributed_corpus=True,
                     confidence=min(0.75, max(r.get('confidence') or 0.5, 0.65)))
    json.dump(data, open(path, 'w'), indent=1, ensure_ascii=False)
    audit('backfill', candidates=len(todo), jobs=len(jobs), backfilled=sorted(found))
    print(f"{len(todo)} rules lacked a corpus quote; {len(jobs)} document checks; corpus quote found for {len(found)}: {sorted(found)}")
    print('still without corpus text:', sorted(r['team_rule_id'] for r in todo if r['team_rule_id'] not in found))


if __name__ == '__main__':
    main()
