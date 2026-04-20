"""Quick test: just initialize and list tools"""
import http.client, json

c = http.client.HTTPConnection('localhost', 18060, timeout=10)
init_body = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "t", "version": "1.0"}}
})
c.request('POST', '/mcp', init_body, {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream'
})
r = c.getresponse()
ct = r.getheader('Content-Type', '')
sid = r.getheader('Mcp-Session-Id', '')
body = r.read().decode('utf-8', errors='replace')
print(f"Status: {r.status}")
print(f"Content-Type: {ct}")
print(f"Session-Id: {sid}")
print(f"Body: {body[:1000]}")

# Now list tools
if sid:
    c2 = http.client.HTTPConnection('localhost', 18060, timeout=10)
    list_body = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    c2.request('POST', '/mcp', list_body, {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
        'Mcp-Session-Id': sid
    })
    r2 = c2.getresponse()
    body2 = r2.read().decode('utf-8', errors='replace')
    print(f"\nTools list status: {r2.status}")
    print(f"Tools: {body2[:2000]}")
