"""Search via already-running MCP HTTP server on port 18060."""
import json, sys, time, urllib.request, urllib.error

MCP_URL = "http://localhost:18060/mcp"

KEYWORDS = [
    "银行消费达标活动",
    "银行立减金活动",
    "银行无损达标攻略",
    "银行薅羊毛攻略",
    "工行月月花抽奖",
]

def mcp_post(url, body, headers=None, timeout=120):
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        resp_headers = {k: v for k, v in resp.getheaders()}
        resp_body = resp.read().decode("utf-8")
        return resp_body, resp_headers
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return json.dumps({"error": f"HTTP {e.code}: {body_text[:500]}"}), {}
    except Exception as e:
        return json.dumps({"error": str(e)}), {}

def parse_response(text):
    """Parse JSON or SSE response."""
    try:
        return json.loads(text)
    except:
        pass
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except:
                pass
    return None

# Step 1: Initialize
print("Initializing...")
resp_text, resp_headers = mcp_post(MCP_URL, {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
}, timeout=15)

session_id = None
for k, v in resp_headers.items():
    if k.lower() == "mcp-session-id":
        session_id = v.strip()
        break

if not session_id:
    print(f"FAIL: no session ID")
    print(f"Headers: {resp_headers}")
    print(f"Body: {resp_text[:500]}")
    sys.exit(1)

print(f"Session: {session_id[:30]}...")

r = parse_response(resp_text)
if r and "result" in r:
    print(f"Server: {r['result'].get('serverInfo', {})}")

# Step 2: Initialized notification
mcp_post(MCP_URL, {"jsonrpc": "2.0", "method": "notifications/initialized"},
         headers={"Mcp-Session-Id": session_id}, timeout=5)
time.sleep(1)

# Step 3: Check login status
print("\nChecking login...")
resp_text, _ = mcp_post(MCP_URL, {
    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
    "params": {"name": "check_login_status", "arguments": {}}
}, headers={"Mcp-Session-Id": session_id}, timeout=30)
r = parse_response(resp_text)
if r and "result" in r:
    content = r["result"].get("content", [])
    if content:
        login_text = content[0].get("text", "")
        print(f"Login status: {login_text[:200]}")
        if "not logged" in login_text.lower() or "not login" in login_text.lower():
            print("\nNeed to login first! Run login tool to get QR code.")
            sys.exit(1)
elif r and "error" in r:
    print(f"Login check error: {r['error']}")
    print("Proceeding anyway...")

# Step 4: Search
print(f"\n=== Searching {len(KEYWORDS)} keywords ===")
success = 0

for i, kw in enumerate(KEYWORDS, 1):
    outfile = f"search_result_new_{i}.json"
    print(f"\n[{i}/{len(KEYWORDS)}] {kw}")
    
    resp_text, _ = mcp_post(MCP_URL, {
        "jsonrpc": "2.0", "id": 100 + i,
        "method": "tools/call",
        "params": {"name": "search_feeds", "arguments": {"keyword": kw}}
    }, headers={"Mcp-Session-Id": session_id}, timeout=120)
    
    r = parse_response(resp_text)
    if r and "result" in r:
        content = r["result"].get("content", [])
        if content:
            text = content[0].get("text", "")
            if text:
                with open(outfile, "w", encoding="utf-8") as f:
                    f.write(text)
                try:
                    feeds = json.loads(text)
                    count = len(feeds) if isinstance(feeds, list) else "?"
                except:
                    count = f"{len(text)}ch"
                print(f"  OK: {count} results -> {outfile}")
                success += 1
            else:
                print("  FAIL: empty text")
        else:
            print("  FAIL: no content")
    elif r and "error" in r:
        print(f"  FAIL: {r['error']}")
    else:
        print(f"  FAIL: raw={resp_text[:200]}")
    
    time.sleep(3)

print(f"\n=== Result: {success}/{len(KEYWORDS)} ===")
