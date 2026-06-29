import json, urllib.request, time

BASE = 'http://localhost:7860/api'
results = []

def test(name, fn):
    try:
        ok, detail = fn()
        status = 'PASS' if ok else 'FAIL'
    except Exception as e:
        status = 'FAIL'
        detail = str(e)[:120]
    results.append((name, status, detail))
    print(f'  [{status}] {name}: {detail}', flush=True)

def post(path, data):
    req = urllib.request.Request(BASE + path, json.dumps(data).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())

def humanize(text, passes=3, tone='casual', model=None, preserve='', avoid=''):
    body = {'text': text, 'passes': passes, 'tone': tone}
    if model: body['model'] = model
    if preserve: body['preserve'] = preserve
    if avoid: body['avoid'] = avoid
    r = post('/humanize', body)
    job_id = r.get('job_id')
    if not job_id: return r
    for i in range(120):
        time.sleep(1)
        j = get(f'/progress/{job_id}')
        if j.get('status') == 'done': return j
        if j.get('status') == 'error': return j
    return {'status': 'timeout'}

SAMPLE = 'The implementation of artificial intelligence in healthcare has been extensively studied by researchers. According to Smith et al. (2024), the results demonstrated significant improvements in diagnostic accuracy. The neural network achieved 95.3% accuracy (p < 0.001) across multiple test cases.'

print('=== HUMANIZEAI V5 - FULL TEST SUITE ===')
print()

# 1
def t1():
    r = humanize(SAMPLE, passes=3, tone='casual')
    out = r.get('result', '')
    ok = r.get('status') == 'done' and len(out) > 50
    return ok, f'status={r.get("status")}, output={len(out.split())}w'
test('1. Basic humanize', t1)

# 2
def t2():
    r = humanize(SAMPLE, passes=3, tone='casual')
    out = r.get('result', '')
    has_cite = 'Smith' in out or 'et al' in out or '2024' in out
    has_num = '95.3' in out or '95' in out
    return has_cite and has_num, f'citation={has_cite}, numbers={has_num}'
test('2. Citation protection', t2)

# 3
def t3():
    r = humanize(SAMPLE, passes=2, tone='academic')
    out = r.get('result', '')
    return r.get('status') == 'done' and len(out) > 50, f'status={r.get("status")}, words={len(out.split())}'
test('3. Tone: academic', t3)

# 4
def t4():
    r = humanize(SAMPLE, passes=2, tone='business')
    out = r.get('result', '')
    return r.get('status') == 'done' and len(out) > 50, f'status={r.get("status")}, words={len(out.split())}'
test('4. Tone: business', t4)

# 5
def t5():
    r = get('/stats')
    return 'total_jobs' in r and r['total_jobs'] > 0, f'jobs={r.get("total_jobs")}'
test('5. Stats tracking', t5)

# 6
def t6():
    r = get('/history')
    return isinstance(r, list) and len(r) > 0, f'entries={len(r) if isinstance(r,list) else "?"}'
test('6. History', t6)

# 7
def t7():
    r = get('/versions')
    return isinstance(r, list), f'versions={len(r) if isinstance(r,list) else "?"}'
test('7. Version history', t7)

# 8
def t8():
    long_text = (SAMPLE + ' ') * 20
    r = post('/preview', {'text': long_text, 'passes': 2, 'tone': 'casual'})
    has_output = 'preview_output' in r and len(r.get('preview_output','')) > 10
    return has_output, f'keys={list(r.keys())[:5]}, len={len(r.get("preview_output",""))}'
test('8. Preview API', t8)

# 9
def t9():
    r = post('/analyze', {'text': SAMPLE})
    return 'score' in r, f'score={r.get("score")}, keys={list(r.keys())[:5]}'
test('9. Analyze/scoring', t9)

# 10
def t10():
    r = humanize('The UMP university in Malaysia has AI systems.', passes=2, tone='casual', preserve='UMP, Malaysia, AI')
    out = r.get('result', '')
    preserved = 'UMP' in out
    return preserved, f'UMP={preserved}, output={out[:80]}'
test('10. Custom preserve', t10)

# 11
def t11():
    r = humanize('The system utilizes various methodologies to optimize performance.', passes=2, tone='casual', avoid='utilizes, methodologies, optimize')
    out = r.get('result', '')
    avoided = 'utilizes' not in out.lower() and 'methodologies' not in out.lower()
    return avoided, f'avoided={avoided}, output={out[:80]}'
test('11. Custom avoid', t11)

# 12
def t12():
    req = urllib.request.Request(BASE + '/download', json.dumps({'text': 'Hello world test'}).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read()
    return data[:2] == b'PK', f'docx={data[:2] == b"PK"}, size={len(data)}'
test('12. Export docx', t12)

# 13
def t13():
    req = urllib.request.Request(BASE + '/download/txt', json.dumps({'text': 'Hello world test'}).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode()
    return 'Hello' in data, f'txt={data[:50]}'
test('13. Export txt', t13)

# 14
def t14():
    req = urllib.request.Request(BASE + '/download/md', json.dumps({'text': 'Hello world test'}).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode()
    return len(data) > 5, f'md={data[:50]}'
test('14. Export markdown', t14)

# 15
def t15():
    texts = [SAMPLE[:100], SAMPLE[100:200]]
    r = post('/batch', {'texts': texts, 'passes': 2, 'tone': 'casual'})
    job_id = r.get('job_id')
    if not job_id: return False, f'no job_id, r={r}'
    for i in range(120):
        time.sleep(1)
        j = get(f'/progress/{job_id}')
        if j.get('status') == 'done':
            res = j.get('results', [])
            return len(res) == 2, f'results={len(res)}, status=done'
        if j.get('status') == 'error':
            return False, f'error: {j.get("error","")[:80]}'
    return False, 'timeout'
test('15. Batch processing', t15)

# 16
def t16():
    txt = 'Cache test unique string abc999'
    r1 = humanize(txt, passes=1, tone='casual')
    t0 = time.time()
    r2 = humanize(txt, passes=1, tone='casual')
    elapsed = time.time() - t0
    return r2.get('status') == 'done', f'ok={r2.get("status")}, 2nd={elapsed:.1f}s'
test('16. Response caching', t16)

# 17
def t17():
    long_text = (SAMPLE + ' ') * 15
    r = humanize(long_text, passes=2, tone='casual')
    out = r.get('result', '')
    in_w = len(long_text.split())
    out_w = len(out.split())
    ratio = out_w / in_w if in_w else 0
    return r.get('status') == 'done' and ratio > 0.5, f'status={r.get("status")}, ratio={ratio:.2f}, out={out_w}w'
test('17. Smart chunking', t17)

# 18
def t18():
    r = humanize('Model test sentence here.', passes=1, tone='casual', model='ag/gemini-3-flash')
    out = r.get('result', '')
    return r.get('status') == 'done' and len(out) > 5, f'status={r.get("status")}, output={out[:60]}'
test('18. Model selection', t18)

print()
passed = sum(1 for _,s,_ in results if s == 'PASS')
failed = [(n,d) for n,s,d in results if s == 'FAIL']
total = len(results)
print(f'RESULTS: {passed}/{total} PASSED ({passed*100//total}%)')
if failed:
    print()
    print('FAILURES:')
    for n,d in failed:
        print(f'  {n}: {d}')
