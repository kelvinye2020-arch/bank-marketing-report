"""
Search xiaohongshu via MCP Streamable HTTP transport at localhost:18060/mcp
"""
import http.client
import json
import sys
import time
import os

keyword = sys.argv[1]
outfile = sys.argv[2]

# Step 1: Initialize session via POST /mcp
c = http.client.HTTPConnection('localhost', 18060, timeout=15)

init_body = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
})

c.request('POST', '/mcp', init_body, {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream'
})
r = c.getresponse()
init_resp = r.read().decode('utf-8', errors='replace')
session_id = r.getheader('Mcp-Session-Id', '')
print(f"Init: {r.status} session={session_id}")
print(f"Init body: {init_resp[:300]}")

# Step 2: Send initialized notification
c2 = http.client.HTTPConnection('localhost', 18060, timeout=15)
notif_body = json.dumps({
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
})
headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
if session_id:
    headers['Mcp-Session-Id'] = session_id
c2.request('POST', '/mcp', notif_body, headers)
r2 = c2.getresponse()
print(f"Notif: {r2.status}")
r2.read()

# Step 3: Search
c3 = http.client.HTTPConnection('localhost', 18060, timeout=30)
search_body = json.dumps({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "search",
        "arguments": {
            "keyword": keyword,
            "page": 1,
            "sort": "general",
            "noteType": 0
        }
    }
})
headers3 = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
if session_id:
    headers3['Mcp-Session-Id'] = session_id
c3.request('POST', '/mcp', search_body, headers3)
r3 = c3.getresponse()
search_resp = r3.read().decode('utf-8', errors='replace')
print(f"Search: {r3.status}")

# Parse response - could be JSON or SSE
if r3.getheader('Content-Type', '').startswith('text/event-stream'):
    # Parse SSE
    for line in search_resp.split('\n'):
        line = line.strip()
        if line.startswith('data:'):
            data = line[5:].strip()
            try:
                resp = json.loads(data)
                if resp.get("id") == 2 and "result" in resp:
                    content = resp["result"].get("content", [])
                    if content:
                        result_data = json.loads(content[0]["text"])
                        with open(outfile, 'w', encoding='utf-8') as f:
                            json.dump(result_data, f, ensure_ascii=False, indent=2)
                        feeds = result_data.get("data", {}).get("feeds", [])
                        print(f"OK: {len(feeds)} notes saved to {outfile}")
                        sys.exit(0)
            except json.JSONDecodeError:
                continue
else:
    # Direct JSON response
    try:
        resp = json.loads(search_resp)
        if "result" in resp:
            content = resp["result"].get("content", [])
            if content:
                result_data = json.loads(content[0]["text"])
                with open(outfile, 'w', encoding='utf-8') as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=2)
                feeds = result_data.get("data", {}).get("feeds", [])
                print(f"OK: {len(feeds)} notes saved to {outfile}")
                sys.exit(0)
        elif "error" in resp:
            print(f"Error: {resp['error']}")
    except json.JSONDecodeError:
        pass

print(f"Response body: {search_resp[:1000]}")
print("FAILED")
