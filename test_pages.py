import requests
BASE = 'http://127.0.0.1:7000'
s = requests.Session()
r = s.post(f'{BASE}/api/auth/login', json={'username':'admin','password':'admin123'})
print('Login:', r.status_code)
for p in ['/', '/projects', '/settings', '/reports', '/logs']:
    r = s.get(f'{BASE}{p}')
    ok = 'OK' if r.status_code == 200 else 'ERR ' + str(r.status_code)
    print(p, ok)
    if r.status_code != 200:
        print(r.text[:500])
