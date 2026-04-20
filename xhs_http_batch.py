"""Search Xiaohongshu via MCP HTTP (/mcp endpoint) - mimicking mcp-call.sh."""
import subprocess, json, sys, time, os, urllib.request, urllib.error

MCP_EXE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
COOKIE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
MCP_URL = "http://localhost:18060/mcp"
PORT = 18060

KEYWORDS = [
    "银行消费达标活动",
    "银行立减金活动",
    "银行无损达标攻略",
    "银行薅羊毛攻略",
    "工行月月花抽奖",
]

def kill_old():
    os.system('taskkill /IM "xiaohongshu-mcp-windows-amd64.exe" /F >nul 2>&1')
    time.sleep(2)

def wait_for_server(timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request("http://localhost:18060/", method="GET")
            urllib.request.urlopen(req, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # 404 means server is up
        except:
            time.sleep(0.5)
    return False

def mcp_post(url, body, headers=None, timeout=120):
    """POST JSON to MCP endpoint, return (response_body, response_headers)."""
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        resp_headers = dict(resp.getheaders())
        resp_body = resp.read().decode("utf-8")
        return resp_body, resp_headers
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8") if e.fp else ""
        return json.dumps({"error": f"HTTP {e.code}: {body_text[:500]}"}), {}

# Main
print("=== Xiaohongshu Search via MCP HTTP ===")
print()

# Kill old
print("Killing old processes...")
kill_old()

# Start MCP server with -port flag (like start-mcp.sh)
print("Starting MCP server...")
proc = subprocess.Popen(
    [MCP_EXE, "-port", f":{PORT}", "--cookie-file", COOKIE],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    creationflags=0x08000000  # CREATE_NO_WINDOW
)
print(f"PID: {proc.pid}")

# Wait for HTTP server
print("Waiting for HTTP server...")
if not wait_for_server(30):
    print("FAIL: server did not start")
    proc.kill()
    sys.exit(1)
print("Server ready!")

# Step 1: Initialize - get Session ID
print("\nInitializing...")
init_body = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
}
resp_text, resp_headers = mcp_post(MCP_URL, init_body, timeout=15)

# Extract Session ID from headers
session_id = None
for k, v in resp_headers.items():
    if k.lower() == "mcp-session-id":
        session_id = v.strip()
        break

if not session_id:
    print(f"FAIL: no session ID in headers")
    print(f"Headers: {resp_headers}")
    print(f"Body: {resp_text[:500]}")
    proc.kill()
    sys.exit(1)

print(f"Session ID: {session_id[:20]}...")

# Parse init response
try:
    init_resp = json.loads(resp_text)
    print(f"Server: {init_resp.get('result', {}).get('serverInfo', {})}")
except:
    # SSE format?
    for line in resp_text.split("\n"):
        if line.startswith("data:"):
            try:
                d = json.loads(line[5:].strip())
                print(f"Server: {d.get('result', {}).get('serverInfo', {})}")
            except:
                pass

# Step 2: Send initialized notification
print("Sending initialized notification...")
notif_body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
mcp_post(MCP_URL, notif_body, headers={"Mcp-Session-Id": session_id}, timeout=5)
time.sleep(1)

# Step 3: Search each keyword
print(f"\n=== Searching {len(KEYWORDS)} keywords ===")
success = 0

for i, kw in enumerate(KEYWORDS, 1):
    outfile = f"search_result_new_{i}.json"
    print(f"\n[{i}/{len(KEYWORDS)}] {kw}")
    
    call_body = {
        "jsonrpc": "2.0", "id": 100 + i,
        "method": "tools/call",
        "params": {"name": "search_feeds", "arguments": {"keyword": kw}}
    }
    
    try:
        resp_text, _ = mcp_post(
            MCP_URL, call_body,
            headers={"Mcp-Session-Id": session_id},
            timeout=120
        )
        
        # Parse response (might be SSE or JSON)
        result = None
        try:
            result = json.loads(resp_text)
        except:
            # Try SSE format
            for line in resp_text.split("\n"):
                if line.startswith("data:"):
                    try:
                        result = json.loads(line[5:].strip())
                    except:
                        pass
        
        if result and "result" in result:
            content = result["result"].get("content", [])
            if content:
                text = content[0].get("text", "")
                if text:
                    with open(outfile, "w", encoding="utf-8") as f:
                        f.write(text)
                    # Count feeds
                    try:
                        feeds = json.loads(text)
                        count = len(feeds) if isinstance(feeds, list) else "?"
                    except:
                        count = f"{len(text)} chars"
                    print(f"  OK: {count} results -> {outfile}")
                    success += 1
                else:
                    print(f"  FAIL: empty text in content")
            else:
                print(f"  FAIL: no content array")
                print(f"  Response: {json.dumps(result, ensure_ascii=False)[:300]}")
        elif result and "error" in result:
            print(f"  FAIL: {result['error']}")
        else:
            print(f"  FAIL: unexpected response")
            print(f"  Raw: {resp_text[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")
    
    time.sleep(3)  # Rate limiting between searches

print(f"\n\n=== Result: {success}/{len(KEYWORDS)} ===")

# Cleanup
proc.kill()
proc.wait()
print("Server stopped. Done.")
