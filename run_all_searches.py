"""Run all 6 XHS searches sequentially, saving results as JSON files."""
import subprocess
import sys
import time
import os
import json

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
SCRIPT = os.path.join(BASE_DIR, "skills", "xiaohongshu-mcp", "scripts", "xhs_client.py")

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

success_count = 0
fail_count = 0

for i, (keyword, filename) in enumerate(SEARCHES, 1):
    outpath = os.path.join(BASE_DIR, filename)
    print(f"\n[{i}/6] Searching: {keyword} -> {filename}")
    
    try:
        result = subprocess.run(
            [sys.executable, SCRIPT, "search", keyword, "--sort", "最多点赞", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        
        stdout = result.stdout.strip()
        
        # Validate JSON
        if stdout:
            try:
                data = json.loads(stdout)
                feeds = data.get("feeds") or data.get("data", {}).get("feeds", [])
                if isinstance(data, list):
                    feeds = data
                
                with open(outpath, "w", encoding="utf-8") as f:
                    f.write(stdout)
                
                print(f"  OK: {len(feeds)} feeds, {len(stdout)} bytes")
                success_count += 1
            except json.JSONDecodeError:
                print(f"  WARN: Invalid JSON response, len={len(stdout)}")
                print(f"  First 200 chars: {stdout[:200]}")
                fail_count += 1
        else:
            print(f"  WARN: Empty response")
            if result.stderr:
                print(f"  STDERR: {result.stderr[:300]}")
            fail_count += 1
            
    except subprocess.TimeoutExpired:
        print(f"  ERROR: Timeout (60s)")
        fail_count += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        fail_count += 1
    
    # Wait between searches to avoid rate limiting
    if i < len(SEARCHES):
        print("  Waiting 3s...")
        time.sleep(3)

print(f"\n=== Done: {success_count} success, {fail_count} failed ===")
