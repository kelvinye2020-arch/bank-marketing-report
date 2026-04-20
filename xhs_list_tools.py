import requests, json

BASE = "http://localhost:18060/mcp"
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

r = requests.post(BASE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "x", "version": "1.0"}}}, headers=H, timeout=30)
sid = r.headers.get("Mcp-Session-Id", "")
H["Mcp-Session-Id"] = sid

requests.post(BASE, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H, timeout=10)

r2 = requests.post(BASE, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=H, timeout=30)
d = r2.json()
tools = d.get("result", {}).get("tools", [])
print(f"Total tools: {len(tools)}\n")
for t in tools:
    print(f"  {t['name']}: {t.get('description', '')[:80]}")
