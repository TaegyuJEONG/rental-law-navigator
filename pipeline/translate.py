"""Stretch: Spanish plain-language titles and requirements for the renter view (machine translation, labelled as such)."""
import json, os
from common import ROOT, llm_json

PROMPT = '''Translate the "title" and "requirement" of each housing rule into clear, plain Latin American Spanish that a renter can understand.
Keep legal citations, numbers, dates and proper names unchanged. Do not add or remove meaning.
Return ONLY JSON: {{"items":[{{"id":"...","title_es":"...","requirement_es":"..."}}]}}
{items}'''
path = os.path.join(ROOT, 'cache', 'rules_merged.json')
data = json.load(open(path))
rules = data['rules']
n = 0
for i in range(0, len(rules), 12):
    batch = rules[i:i + 12]
    res, _, _ = llm_json(PROMPT.format(items=json.dumps([dict(id=r['team_rule_id'], title=r['title'], requirement=r['requirement']) for r in batch], ensure_ascii=False)), tag=f'translate:{i}')
    got = {x['id']: x for x in res.get('items', [])}
    for r in batch:
        if r['team_rule_id'] in got:
            r['title_es'], r['requirement_es'] = got[r['team_rule_id']].get('title_es'), got[r['team_rule_id']].get('requirement_es'); n += 1
json.dump(data, open(path, 'w'), indent=1, ensure_ascii=False)
print(f'translated {n}/{len(rules)} rules to Spanish')
