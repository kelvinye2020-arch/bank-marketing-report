import subprocess, json, os, sys, time

XHS = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIES = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
kw = sys.argv[1]
out = sys.argv[2]

env = {**os.environ, "COOKIES_FILE": COOKIES}
proc = subprocess.Popen([XHS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

init = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1.0"}}}) + "\n"
proc.stdin.write(init.encode()); proc.stdin.flush()
proc.stdout.readline()  # init response

notif = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n"
proc.stdin.write(notif.encode()); proc.stdin.flush()
time.sleep(0.3)

req = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"keyword":kw,"page":1,"sort":"general","noteType":0}}}) + "\n"
proc.stdin.write(req.encode()); proc.stdin.flush()

start = time.time()
while time.time() - start < 25:
    line = proc.stdout.readline().decode('utf-8', errors='replace').strip()
    if not line: time.sleep(0.1); continue
    try:
        r = json.loads(line)
        if r.get("id") == 2:
            if "result" in r:
                c = r["result"].get("content",[])
                if c:
                    d = json.loads(c[0]["text"])
                    with open(out,'w',encoding='utf-8') as f: json.dump(d,f,ensure_ascii=False,indent=2)
                    n = len(d.get("data",{}).get("feeds",[]))
                    print(f"OK: {n} notes")
                    break
            elif "error" in r:
                print(f"ERR: {r['error']}"); break
    except: continue
else:
    print("TIMEOUT")

proc.kill(); proc.wait()
