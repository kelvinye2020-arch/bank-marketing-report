import subprocess, json, os, time, threading

XHS = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIES = r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"

env = {**os.environ, "COOKIES_FILE": COOKIES}
proc = subprocess.Popen([XHS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

collected_stdout = []
collected_stderr = []

def read_stdout():
    for line in proc.stdout:
        collected_stdout.append(line.decode('utf-8', errors='replace').strip())

def read_stderr():
    for line in proc.stderr:
        collected_stderr.append(line.decode('utf-8', errors='replace').strip())

t1 = threading.Thread(target=read_stdout, daemon=True)
t2 = threading.Thread(target=read_stderr, daemon=True)
t1.start(); t2.start()

# Send init
proc.stdin.write((json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1.0"}}}) + "\n").encode())
proc.stdin.flush()
time.sleep(1)

# Send initialized notification
proc.stdin.write((json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n").encode())
proc.stdin.flush()
time.sleep(0.5)

# Send search
proc.stdin.write((json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"keyword":"银行消费达标活动","page":1,"sort":"general","noteType":0}}}) + "\n").encode())
proc.stdin.flush()

# Wait up to 8s for search result
for i in range(16):
    time.sleep(0.5)
    for line in collected_stdout:
        if '"id":' in line or '"id": ' in line:
            try:
                r = json.loads(line)
                if r.get("id") == 2:
                    print("GOT SEARCH RESPONSE")
                    break
            except: pass

proc.kill()
t1.join(timeout=2); t2.join(timeout=2)

print(f"STDOUT lines: {len(collected_stdout)}")
for l in collected_stdout[:10]:
    print(f"  OUT: {l[:200]}")
print(f"STDERR lines: {len(collected_stderr)}")
for l in collected_stderr[:10]:
    print(f"  ERR: {l[:200]}")
