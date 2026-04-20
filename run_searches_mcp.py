"""Search xiaohongshu via MCP HTTP endpoint directly."""
import requests
import json
import sys
import time
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
URL = "http://localhost:18060/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream"
}

SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

session = requests.Session()

# Initialize MCP session
print("[初始化] MCP session...", flush=True)
resp = session.post(URL, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "workbuddy", "version": "1.0"}}
}, headers=HEADERS, timeout=30)

session_id = resp.headers.get("Mcp-Session-Id")
if session_id:
    HEADERS["Mcp-Session-Id"] = session_id
    print(f"  Session ID: {session_id[:20]}...", flush=True)

session.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)
print("  OK", flush=True)

success_count = 0
fail_count = 0

for i, (keyword, filename) in enumerate(SEARCHES):
    outpath = os.path.join(BASE_DIR, filename)
    print(f"\n[搜索 {i+1}/{len(SEARCHES)}] {keyword} -> {filename}", flush=True)
    
    try:
        resp = session.post(URL, json={
            "jsonrpc": "2.0", "id": i + 10, "method": "tools/call",
            "params": {"name": "search_feeds", "arguments": {"keyword": keyword}}
        }, headers=HEADERS, timeout=90)
        
        data = json.loads(resp.text)
        
        if "result" in data:
            content = data["result"].get("content", [])
            # Find the text content with search results
            for item in content:
                if item.get("type") == "text":
                    text = item["text"]
                    # Save raw text response
                    with open(outpath, "w", encoding="utf-8") as f:
                        f.write(text)
                    print(f"  OK, {len(text)} bytes", flush=True)
                    success_count += 1
                    break
            else:
                print(f"  WARN: no text content in response", flush=True)
                fail_count += 1
        elif "error" in data:
            print(f"  ERROR: {data['error']}", flush=True)
            fail_count += 1
        else:
            print(f"  WARN: unexpected response", flush=True)
            fail_count += 1
            
    except requests.exceptions.Timeout:
        print(f"  TIMEOUT (90s)", flush=True)
        fail_count += 1
    except Exception as e:
        print(f"  EXCEPTION: {e}", flush=True)
        fail_count += 1
    
    # 间隔3秒
    if i < len(SEARCHES) - 1:
        time.sleep(3)

print(f"\n[完成] 成功: {success_count}, 失败: {fail_count}")
if fail_count > 0:
    print("[提示] 部分搜索失败，可能需要重新登录")
    sys.exit(1)
