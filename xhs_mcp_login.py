import requests
import json
import base64
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URL = "http://localhost:18060/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream"
}

session = requests.Session()

# Initialize
resp = session.post(URL, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "workbuddy", "version": "1.0"}}
}, headers=HEADERS)

session_id = resp.headers.get("Mcp-Session-Id")
if session_id:
    HEADERS["Mcp-Session-Id"] = session_id

session.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)

# Call get_login_qrcode
resp = session.post(URL, json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "get_login_qrcode", "arguments": {}}
}, headers=HEADERS, timeout=60)

data = json.loads(resp.text)
qr_saved = False

if "result" in data:
    content = data["result"].get("content", [])
    for item in content:
        if item.get("type") == "image":
            img_data = base64.b64decode(item["data"])
            with open("qr_code.png", "wb") as f:
                f.write(img_data)
            print("QR code saved to qr_code.png")
            qr_saved = True
        elif item.get("type") == "text":
            text = item.get("text", "")
            print(f"Message: {text}")
    if not qr_saved:
        # Maybe QR is embedded differently, dump raw
        print(f"\nRaw content items: {len(content)}")
        for i, item in enumerate(content):
            print(f"  Item {i}: type={item.get('type')}, keys={list(item.keys())}")
elif "error" in data:
    print(f"Error: {data['error']}")

print(f"\nQR code saved: {qr_saved}")
