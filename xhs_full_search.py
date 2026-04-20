"""Search 5 keywords via MCP HTTP. Minimal, robust."""
import subprocess, json, os, sys, time, http.client

XHS = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIES = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
BASE = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
PORT = 18060

KEYWORDS = [
    "银行消费达标活动",
    "银行立减金活动", 
    "银行无损达标攻略",
    "银行薅羊毛攻略",
    "工行月月花抽奖",
]

# Start server
print("Starting MCP server...")
env = {**os.environ, "COOKIES_FILE": COOKIES}
proc = subprocess.Popen([XHS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
time.sleep(3)
if proc.poll() is not None:
    print("Server died on start"); sys.exit(1)
print(f"PID: {proc.pid}")

# Init - get session
c = http.client.HTTPConnection('localhost', PORT, timeout=15)
c.request('POST', '/mcp', json.dumps({
    "jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"protocolVersion":"2024-11-05","capabilities":{},
              "clientInfo":{"name":"xhs","version":"1.0"}}
}), {'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
r = c.getresponse()
sid = r.getheader('Mcp-Session-Id','')
r.read()
print(f"Session: {sid[:20]}...")

time.sleep(1)

# Search each keyword
ok = 0
for i, kw in enumerate(KEYWORDS, 1):
    out = os.path.join(BASE, f"test_search_result_{i}.json")
    print(f"\n[{i}] {kw}")
    try:
        cs = http.client.HTTPConnection('localhost', PORT, timeout=30)
        cs.request('POST', '/mcp', json.dumps({
            "jsonrpc":"2.0","id":100+i,"method":"tools/call",
            "params":{"name":"search_feeds","arguments":{"keyword":kw}}
        }), {'Content-Type':'application/json','Accept':'application/json, text/event-stream','Mcp-Session-Id':sid})
        rs = cs.getresponse()
        raw = rs.read().decode('utf-8', errors='replace')
        ct = rs.getheader('Content-Type','')
        
        resp = None
        if 'text/event-stream' in ct:
            for line in raw.split('\n'):
                if line.startswith('data:'):
                    try: resp = json.loads(line[5:].strip()); break
                    except: continue
        else:
            try: resp = json.loads(raw)
            except: pass
        
        if resp and "result" in resp:
            content = resp["result"].get("content",[])
            if content:
                text = content[0].get("text","")
                try:
                    data = json.loads(text)
                    with open(out, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    feeds = data.get("data",{}).get("feeds",[])
                    if not feeds:
                        feeds = data.get("feeds",[])
                    print(f"  OK: {len(feeds)} notes")
                    ok += 1
                except json.JSONDecodeError:
                    with open(out, 'w', encoding='utf-8') as f:
                        f.write(text)
                    print(f"  OK: saved raw")
                    ok += 1
            else:
                print(f"  FAIL: no content")
        elif resp and "error" in resp:
            print(f"  FAIL: {str(resp['error'])[:150]}")
        else:
            print(f"  FAIL: {raw[:200]}")
    except Exception as e:
        print(f"  ERR: {e}")
    time.sleep(3)

print(f"\nResult: {ok}/{len(KEYWORDS)}")
proc.kill(); proc.wait()
