import requests, json, base64, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = "http://localhost:18060/mcp"
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

# init
r = requests.post(BASE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "x", "version": "1.0"}}}, headers=H, timeout=30)
sid = r.headers.get("Mcp-Session-Id", "")
H["Mcp-Session-Id"] = sid
requests.post(BASE, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=H, timeout=10)

action = sys.argv[1] if len(sys.argv) > 1 else "check"

if action == "check":
    print("=== Checking login status ===")
    r = requests.post(BASE, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "check_login_status", "arguments": {}}}, headers=H, timeout=60)
elif action == "qrcode":
    print("=== Getting login QR code ===")
    r = requests.post(BASE, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_login_qrcode", "arguments": {}}}, headers=H, timeout=60)

print(f"Status: {r.status_code}")
data = r.json()
if "result" in data:
    for item in data["result"].get("content", []):
        if item.get("type") == "text":
            print(item["text"])
        elif item.get("type") == "image":
            img_b64 = item.get("data", "")
            if img_b64:
                with open("qr_code.png", "wb") as f:
                    f.write(base64.b64decode(img_b64))
                print("[QR code saved to qr_code.png - please scan with Xiaohongshu app]")
elif "error" in data:
    print(f"Error: {data['error']}")
else:
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
