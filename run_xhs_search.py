"""Run 6 XHS searches sequentially, save results as UTF-8 JSON files."""
import subprocess
import sys
import time
import os
import json

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
XHS_CLIENT = os.path.join(BASE_DIR, "skills", "xiaohongshu-mcp", "scripts", "xhs_client.py")
PYTHON = sys.executable

SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

MCP_EXE = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe"
MCP_PROC = None

def start_mcp():
    global MCP_PROC
    env = os.environ.copy()
    env["COOKIES_FILE"] = r"C:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json"
    MCP_PROC = subprocess.Popen(
        [MCP_EXE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    print("[MCP] Starting server, waiting 4s...")
    time.sleep(4)
    print("[MCP] Server should be ready on port 18060")

def stop_mcp():
    global MCP_PROC
    if MCP_PROC:
        MCP_PROC.terminate()
        try:
            MCP_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            MCP_PROC.kill()
        print("[MCP] Server stopped")

def run_search(keyword, output_file):
    """Run xhs_client.py search and capture output to file."""
    cmd = [PYTHON, XHS_CLIENT, "search", keyword, "--sort", "最多点赞", "--json"]
    print(f"  Searching: {keyword} ...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8")
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stderr:
            print(f"  stderr: {stderr[:200]}")
        # Validate JSON
        if stdout:
            try:
                data = json.loads(stdout)
                feeds = data.get("feeds") or data.get("data", {}).get("feeds", [])
                if isinstance(data, list):
                    feeds = data
                feed_count = len(feeds) if feeds else 0
                # Write as UTF-8
                out_path = os.path.join(BASE_DIR, output_file)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(stdout)
                print(f"  -> OK: {feed_count} feeds saved to {output_file}")
                return True
            except json.JSONDecodeError:
                print(f"  -> WARN: output is not valid JSON, len={len(stdout)}")
                print(f"  -> First 200 chars: {stdout[:200]}")
                return False
        else:
            print(f"  -> WARN: empty output (cookie expired?)")
            return False
    except subprocess.TimeoutExpired:
        print(f"  -> TIMEOUT after 60s")
        return False
    except Exception as e:
        print(f"  -> ERROR: {e}")
        return False


if __name__ == "__main__":
    # Start MCP server
    start_mcp()
    
    success_count = 0
    fail_count = 0
    
    try:
        for i, (keyword, filename) in enumerate(SEARCHES):
            print(f"\n[{i+1}/6] {keyword}")
            ok = run_search(keyword, filename)
            if ok:
                success_count += 1
            else:
                fail_count += 1
            # Wait between searches
            if i < len(SEARCHES) - 1:
                time.sleep(3)
    finally:
        stop_mcp()
    
    print(f"\n=== DONE: {success_count} success, {fail_count} failed ===")
    
    if fail_count > 0:
        print("Some searches failed - likely cookie expired. Will use existing data.")
        sys.exit(1)
    else:
        sys.exit(0)
