"""Direct MCP search via JSON-RPC - search_feeds tool."""
import json
import os
import subprocess
import sys
import time
import requests

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
MCP_EXE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
MCP_COOKIES = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
MCP_URL = "http://localhost:18060/mcp"

SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

class McpClient:
    def __init__(self):
        self.process = None
        self.session = requests.Session()
        self.session_id = None
        self._req_id = 0

    def start(self):
        env = os.environ.copy()
        env["COOKIES_FILE"] = MCP_COOKIES
        self.process = subprocess.Popen(
            [MCP_EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
        )
        print("[MCP] Starting server...")
        time.sleep(4)
        r = self.session.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "batch_search", "version": "1.0"}}
        }, timeout=10)
        r.raise_for_status()
        self.session_id = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        headers = {"Mcp-Session-Id": self.session_id} if self.session_id else {}
        self.session.post(MCP_URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers, timeout=5)
        print(f"[MCP] Ready (session: {self.session_id[:16]}...)")

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            print("[MCP] Stopped")

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def search_feeds(self, keyword, sort_by="最多点赞", retries=2):
        for attempt in range(retries + 1):
            try:
                headers = {"Mcp-Session-Id": self.session_id} if self.session_id else {}
                r = self.session.post(MCP_URL, json={
                    "jsonrpc": "2.0", "id": self._next_id(),
                    "method": "tools/call",
                    "params": {
                        "name": "search_feeds",
                        "arguments": {
                            "keyword": keyword,
                            "filters": {"sort_by": sort_by}
                        }
                    }
                }, headers=headers, timeout=120)
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    print(f"  RPC error: {data['error']}")
                    return None
                result = data.get("result", {})
                content = result.get("content", [])
                if isinstance(content, list) and content:
                    text = content[0].get("text", "")
                    if text:
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            print(f"  Non-JSON response ({len(text)} chars): {text[:200]}")
                            return {"raw_text": text}
                return None
            except requests.exceptions.ReadTimeout:
                if attempt < retries:
                    wait = 10 * (attempt + 1)
                    print(f"  Timeout (attempt {attempt+1}), retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  Timeout after {retries+1} attempts")
                    return None
            except Exception as e:
                print(f"  Error: {e}")
                if attempt < retries:
                    time.sleep(5)
                else:
                    return None


def main():
    mcp = McpClient()
    mcp.start()

    success = 0
    fail = 0

    try:
        for i, (keyword, filename) in enumerate(SEARCHES):
            print(f"\n[{i+1}/6] Searching: {keyword}")
            data = mcp.search_feeds(keyword)
            if data and isinstance(data, dict):
                feeds = data.get("feeds", [])
                print(f"  -> {len(feeds)} feeds")
                if len(feeds) > 0:
                    out_path = os.path.join(BASE_DIR, filename)
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"  -> Saved to {filename}")
                    success += 1
                else:
                    print(f"  -> 0 feeds (cookie may be expired)")
                    fail += 1
            elif data and isinstance(data, str):
                # Check if it's an error message about login
                if "登录" in data or "login" in data.lower():
                    print(f"  -> Need login: {data[:100]}")
                else:
                    print(f"  -> Unexpected string response: {data[:100]}")
                fail += 1
            else:
                print(f"  -> Failed or empty response")
                fail += 1

            if i < len(SEARCHES) - 1:
                time.sleep(3)
    finally:
        mcp.stop()

    print(f"\n=== Results: {success} ok, {fail} failed ===")
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
