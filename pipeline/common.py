"""Shared helpers: document loading, cached LLM calls, audit log."""
import csv, hashlib, json, os, re, time, threading, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.environ.get('NAVIGATOR_MODEL', 'gpt-5.5')
_cfg = json.load(open(os.path.join(ROOT, 'config', 'scope.json')))
SCOPE = _cfg['jurisdictions']      # which jurisdictions are in scope is configuration, not code
CATEGORIES = _cfg['categories']
_lock = threading.Lock()


def _key():
    for l in open(os.path.join(ROOT, '.env')):
        if l.startswith('OPENAI_API_KEY='):
            return l.split('=', 1)[1].strip().strip('"')
    raise SystemExit('OPENAI_API_KEY missing in .env')


def norm(s):
    """Whitespace/quote-insensitive form used to verify that a quote really occurs in the source."""
    for a, b in (('“', '"'), ('”', '"'), ('’', "'"), ('‘', "'"), ('\xa0', ' ')):
        s = s.replace(a, b)
    return re.sub(r'\s+', ' ', s).strip()


def audit(event, **kw):
    with _lock:
        os.makedirs(os.path.join(ROOT, 'audit'), exist_ok=True)
        with open(os.path.join(ROOT, 'audit', 'log.jsonl'), 'a') as f:
            f.write(json.dumps(dict(ts=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), event=event, **kw), ensure_ascii=False) + '\n')


def llm_json(prompt, tag=''):
    """One JSON-mode completion, cached by sha256(model+prompt) so a rerun reproduces the same output."""
    h = hashlib.sha256((MODEL + '\n' + prompt).encode()).hexdigest()
    p = os.path.join(ROOT, 'cache', 'llm', h + '.json')
    if os.path.exists(p):
        d = json.load(open(p))
        return d['result'], d.get('usage', {}), True
    body = json.dumps(dict(model=MODEL, messages=[dict(role='user', content=prompt)], response_format=dict(type='json_object'))).encode()
    err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=body,
                                         headers={'Authorization': 'Bearer ' + _key(), 'Content-Type': 'application/json'})
            d = json.load(urllib.request.urlopen(req, timeout=900))
            result = json.loads(d['choices'][0]['message']['content'])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(dict(tag=tag, model=MODEL, prompt_sha256=h, usage=d.get('usage', {}), result=result), open(p, 'w'), ensure_ascii=False)
            audit('llm_call', tag=tag, model=MODEL, prompt_sha256=h, usage=d.get('usage', {}))
            return result, d.get('usage', {}), False
        except urllib.error.HTTPError as e:
            err = f'HTTP {e.code} {e.read().decode()[:300]}'
        except Exception as e:
            err = repr(e)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'LLM call failed ({tag}): {err}')


def load_docs():
    """Distributed corpus text (counts for citations) + independently saved link-only texts (research only)."""
    docs = []
    for r in csv.DictReader(open(os.path.join(ROOT, 'starter', 'corpus', 'corpus_manifest.csv'))):
        cp = os.path.join(ROOT, 'starter', 'corpus', 'text', r['doc_id'] + '.txt')
        rp = os.path.join(ROOT, 'research', r['doc_id'] + '.txt')
        path, in_corpus = (cp, True) if os.path.exists(cp) else (rp, False)
        if not os.path.exists(path):
            continue
        text = open(path).read()
        m = re.search(r'^RETRIEVED: (.+)$', text, re.M)
        docs.append(dict(doc_id=r['doc_id'], jurisdictions=r['jurisdictions'], url=r['url'], source_type=r['source_type'],
                         retrieved_at=r['retrieved_at'] or (m.group(1).strip() if m else None), text=text,
                         sha256=hashlib.sha256(text.encode()).hexdigest(), in_corpus=in_corpus))
    # the organisers' participant guide states coverage cutoffs that the corpus pages omit; read it like any other document (never used for citations)
    g = os.path.join(ROOT, 'starter', 'README.md')
    if os.path.exists(g):
        text = open(g).read()
        docs.append(dict(doc_id='GUIDE', jurisdictions='CA; NJ; MA', url='starter/README.md (organiser participant guide)', source_type='organiser participant guide (secondary)',
                         retrieved_at='2026-10-03', text=text, sha256=hashlib.sha256(text.encode()).hexdigest(), in_corpus=False))
    return docs
