"""Single keyword search with long timeout."""
import subprocess, json, sys, time, threading, os

MCP_EXE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
TIMEOUT = 90  # seconds per request

keyword = sys.argv[1] if len(sys.argv) > 1 else "bank"
outfile = sys.argv[2] if len(sys.argv) > 2 else "test_out.json"

# Kill old processes
os.system('taskkill /IM "xiaohongshu-mcp-windows-amd64.exe" /F >nul 2>&1')
time.sleep(1)

proc = subprocess.Popen(
    [MCP_EXE, "--cookie-file", COOKIE],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
print(f"PID: {proc.pid}")

# Thread to drain stderr
def drain_err():
    for line in proc.stderr:
        pass
threading.Thread(target=drain_err, daemon=True).start()

def send(obj):
    data = json.dumps(obj) + "\n"
    proc.stdin.write(data.encode())
    proc.stdin.flush()

def recv(timeout_sec):
    """Read one line with timeout."""
    result = [None]
    def reader():
        try:
            line = proc.stdout.readline()
            if line:
                result[0] = json.loads(line.decode())
        except:
            pass
    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout_sec)
    return result[0]

# Init
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}})
r = recv(15)
if not r:
    print("FAIL: init timeout")
    proc.kill()
    sys.exit(1)
print(f"Init OK: {r.get('result',{}).get('serverInfo',{})}")

send({"jsonrpc":"2.0","method":"notifications/initialized"})
time.sleep(1)

# Search
print(f"\nSearching: {keyword}")
print(f"Timeout: {TIMEOUT}s")
send({"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"search_feeds","arguments":{"keyword":keyword}}})

start = time.time()
r = recv(TIMEOUT)
elapsed = time.time() - start
print(f"Elapsed: {elapsed:.1f}s")

if r is None:
    print("FAIL: search timed out")
elif "result" in r:
    content = r["result"].get("content", [])
    if content:
        text = content[0].get("text", "")
        print(f"Response length: {len(text)} chars")
        # Save
        with open(outfile, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Saved to {outfile}")
        # Preview
        print(f"Preview: {text[:300]}")
    else:
        print("FAIL: empty content")
        print(json.dumps(r, ensure_ascii=False)[:500])
elif "error" in r:
    print(f"FAIL: {r['error']}")
else:
    print(f"Unexpected: {json.dumps(r, ensure_ascii=False)[:500]}")

proc.kill()
proc.wait()
print("\nDone.")
