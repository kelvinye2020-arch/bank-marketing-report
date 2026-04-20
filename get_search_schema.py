import subprocess, os, time, json, requests, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
env = os.environ.copy()
env['COOKIES_FILE'] = r'C:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json'
p = subprocess.Popen(
    [r'C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
)
time.sleep(4)
s = requests.Session()
r = s.post('http://localhost:18060/mcp', json={
    'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
    'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
               'clientInfo': {'name': 't', 'version': '1.0'}}
}, timeout=10)
sid = r.headers.get('Mcp-Session-Id', '')
h = {'Mcp-Session-Id': sid}
s.post('http://localhost:18060/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers=h, timeout=5)
r2 = s.post('http://localhost:18060/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, headers=h, timeout=10)
data = r2.json()
tools = data.get('result', {}).get('tools', [])
for t in tools:
    if t['name'] == 'search_feeds':
        print(json.dumps(t, ensure_ascii=False, indent=2))
        break
p.terminate()
p.wait(timeout=5)
