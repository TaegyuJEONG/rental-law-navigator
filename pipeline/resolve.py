"""Module B, step 1: resolve every sample address to its legal jurisdiction stack and building facts.

postal_city is a mailing name, not the legal city, so the Census Geocoder decides:
batch geocode -> coordinates -> Incorporated Place / County. Rows the geocoder cannot match are retried
with a normalised street; a retry is accepted only if house number and state agree with the input.
Rows that still fail are inferred from resolved neighbours with the same ZIP / postal city and marked as such.
"""
import csv, io, json, os, re, collections, urllib.request, urllib.parse, uuid, concurrent.futures as cf
from common import ROOT, SCOPE, audit

G = 'https://geocoding.geo.census.gov/geocoder/geographies/'
BM = dict(benchmark='Public_AR_Current', vintage='Current_Current')
CACHE = os.path.join(ROOT, 'cache', 'geocode_cache.json')


def getj(url):
    err = None
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(url, timeout=40))
        except Exception as e:
            err = e
    return {'error': str(err)}


def batch(rows):
    buf = io.StringIO(); w = csv.writer(buf)
    for r in rows:
        w.writerow([r['address_id'], r['street_address'], r['postal_city'], r['state'], r['zip']])
    b = uuid.uuid4().hex
    part = lambda n, v, fn=None: f'--{b}\r\nContent-Disposition: form-data; name="{n}"' + (f'; filename="{fn}"\r\nContent-Type: text/csv' if fn else '') + f'\r\n\r\n{v}\r\n'
    body = (part('benchmark', BM['benchmark']) + part('vintage', BM['vintage']) + part('addressFile', buf.getvalue(), 'a.csv') + f'--{b}--\r\n').encode()
    req = urllib.request.Request(G + 'addressbatch', data=body, headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    return {r[0]: r for r in csv.reader(io.StringIO(urllib.request.urlopen(req, timeout=300).read().decode()))}


def stack_xy(x, y):
    d = getj(G + 'coordinates?' + urllib.parse.urlencode(dict(x=x, y=y, layers='Incorporated Places,County Subdivisions,Counties,States', format='json', **BM)))
    g = d.get('result', {}).get('geographies', {})
    first = lambda k: (g.get(k) or [{}])[0]
    return dict(place=first('Incorporated Places').get('NAME'), place_geoid=first('Incorporated Places').get('GEOID'),
                cousub=first('County Subdivisions').get('NAME'), county=first('Counties').get('NAME'), state_name=first('States').get('NAME'))


def house_no(s):
    m = re.match(r'\s*(\d+)', s)
    return m.group(1) if m else None


def normalise_street(s):
    s = re.sub(r'^(\d+)[\d.\-\s]*?(?=\s+[A-Za-z])', r'\1', s.strip())       # "876-878 S 14TH" / "322-322.5 Western" -> first number
    s = re.sub(r'\b0+(\d+(ST|ND|RD|TH))\b', r'\1', s, flags=re.I)               # "05TH" -> "5TH"
    s = re.sub(r'\bAV\b', 'AVE', s, flags=re.I)
    s = re.sub(r'\s+(#|APT|UNIT|LOT)\b.*$', '', s, flags=re.I)
    return s


def retry(r):
    st = normalise_street(r['street_address'])
    if not house_no(st):
        return None
    for q in (f"{st}, {r['postal_city']}, {r['state']} {r['zip']}".strip(), f"{st}, {r['postal_city']}, {r['state']}"):
        d = getj(G + 'onelineaddress?' + urllib.parse.urlencode(dict(address=q, format='json', **BM)))
        for m in d.get('result', {}).get('addressMatches', []):
            comp = m.get('addressComponents', {})
            if comp.get('state') == r['state'] and house_no(m['matchedAddress']) == house_no(st):   # reject look-alike matches
                return m['matchedAddress'], m['coordinates']['x'], m['coordinates']['y']
    return None


def unit_range(r):
    """Units from the assessor's count, else a lower/upper bound parsed from the public use description."""
    u = r['units'].strip()
    if u:
        v = int(float(u)); return v, v, 'assessor units field'
    d = r['use_description']
    m = re.findall(r'(\d+)\s*U\b', d)
    if m:
        v = [int(x) for x in m]; return min(v), sum(v), 'use description'
    m = re.search(r'(\d+)\s*(?:-|to)\s*(\d+)[\s-]*UNIT', d, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), 'use description'
    m = re.search(r'>\s*(\d+)[\s-]*UNIT', d, re.I)
    if m:
        return int(m.group(1)) + 1, None, 'use description'
    m = re.search(r'(\d+)\s*units? or more|(\d+)\+\s*units?', d, re.I)
    if m:
        return int(m.group(1) or m.group(2)), None, 'use description'
    if re.search(r'five or more', d, re.I):
        return 5, None, 'use description'
    m = re.search(r'(\d+)\s*units? or less', d, re.I)
    if m:
        return None, int(m.group(1)), 'use description'
    return None, None, None


def main():
    rows = list(csv.DictReader(open(os.path.join(ROOT, 'starter', 'data', 'sample_addresses.csv'))))
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [r for r in rows if r['address_id'] not in cache]
    if todo:
        B = batch(todo)

        def work(r):
            g = B.get(r['address_id'], [])
            o = dict(batch_status=g[2] if len(g) > 2 else 'missing', method=None)
            if o['batch_status'] == 'Match':
                x, y = g[5].split(','); o.update(method='census_batch', match_type=g[3], matched_address=g[4], x=x, y=y)
            else:
                m = retry(r)
                if m:
                    o.update(method='census_retry_normalised', matched_address=m[0], x=m[1], y=m[2])
            if o.get('x'):
                o.update(stack_xy(o['x'], o['y']))
            return r['address_id'], o
        with cf.ThreadPoolExecutor(8) as ex:
            for aid, o in ex.map(work, todo):
                cache[aid] = o
        json.dump(cache, open(CACHE, 'w'), indent=1)

    # neighbours: for rows the geocoder cannot place, infer from resolved rows sharing ZIP, then postal city
    by_zip, by_city = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for r in rows:
        c = cache[r['address_id']]
        if c.get('place'):
            key = (c['place'], c.get('place_geoid'), c.get('county'))
            if r['zip'].strip():
                by_zip[(r['state'], r['zip'])][key] += 1
            by_city[(r['state'], r['postal_city'])][key] += 1
    out = []
    for r in rows:
        c = dict(cache[r['address_id']])
        conf, note = 1.0, None
        if c.get('method') == 'census_retry_normalised':
            conf = 0.9
        if c.get('match_type') == 'Non_Exact':
            conf = 0.9
        if not c.get('place'):
            src = by_zip.get((r['state'], r['zip'])) or by_city.get((r['state'], r['postal_city']))
            if src and len(src) == 1:
                (place, geoid, county), n = src.most_common(1)[0]
                c.update(place=place, place_geoid=geoid, county=county, method='inferred_from_postal_peers')
                conf, note = 0.7, f'Census Geocoder could not match this address; legal city inferred from {n} resolved sample addresses with the same ZIP/postal city.'
            else:
                conf, note = 0.0, 'Jurisdiction could not be resolved.'
        city = re.sub(r'\s+(city|town|borough|village|township)$', '', c['place']) + ', ' + r['state'] if c.get('place') else None
        umin, umax, usrc = unit_range(r)
        yb = r['year_built'].strip()
        out.append(dict(address_id=r['address_id'], street_address=r['street_address'], postal_city=r['postal_city'], state=r['state'], zip=r['zip'],
                        year_built=int(yb) if yb.isdigit() and int(yb) > 1700 else None, units_min=umin, units_max=umax, units_source=usrc,
                        use_code=r['use_code'], use_description=r['use_description'], source_dataset=r['source_dataset'], facts_retrieved_at=r['retrieved_at'],
                        city=city, city_in_scope=city in SCOPE, place=c.get('place'), place_geoid=c.get('place_geoid'), county=c.get('county'),
                        geocode_method=c.get('method'), matched_address=c.get('matched_address'),
                        lon=float(c['x']) if c.get('x') else None, lat=float(c['y']) if c.get('y') else None,
                        jurisdiction_confidence=conf, jurisdiction_note=note,
                        postal_city_differs=bool(city) and city.split(',')[0].lower() != r['postal_city'].lower()))
    json.dump(out, open(os.path.join(ROOT, 'cache', 'addresses.json'), 'w'), indent=1)
    c = collections.Counter((o['city'], o['geocode_method']) for o in out)
    for k, v in sorted(c.items(), key=str):
        print(v, k)
    print('unit bounds known:', sum(o['units_min'] is not None or o['units_max'] is not None for o in out), '/ year known:', sum(o['year_built'] is not None for o in out),
          '/ postal city differs from legal city:', sum(o['postal_city_differs'] for o in out))
    audit('resolve', addresses=len(out), methods=dict(collections.Counter(str(o['geocode_method']) for o in out)))


if __name__ == '__main__':
    main()
