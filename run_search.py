"""Run 6 xiaohongshu searches sequentially, save results as JSON files."""
import subprocess
import sys
import json
import time
import os

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
XHS_SCRIPT = os.path.join(BASE_DIR, "skills", "xiaohongshu-mcp", "scripts", "xhs_client.py")

SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

success_count = 0
fail_count = 0

for keyword, filename in SEARCHES:
    outpath = os.path.join(BASE_DIR, filename)
    print(f"\n--- Searching: {keyword} -> {filename} ---")
    try:
        r = subprocess.run(
            [sys.executable, XHS_SCRIPT, "search", keyword, "--sort", "最多点赞", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=90
        )
        data = r.stdout.strip()
        if r.returncode != 0 or not data:
            print(f"  FAILED: rc={r.returncode}, stdout_len={len(data)}")
            if r.stderr:
                print(f"  STDERR: {r.stderr[:300]}")
            fail_count += 1
            continue

        # Validate JSON
        parsed = json.loads(data)
        feeds = parsed.get("feeds") or parsed.get("data", {}).get("feeds", [])
        print(f"  OK: {len(feeds)} feeds, {len(data)} bytes")

        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        success_count += 1

    except json.JSONDecodeError as e:
        print(f"  JSON PARSE ERROR: {e}")
        fail_count += 1
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT (90s)")
        fail_count += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        fail_count += 1

    # Pause between searches
    if keyword != SEARCHES[-1][0]:
        print("  Waiting 3s...")
        time.sleep(3)

print(f"\n=== DONE: {success_count} success, {fail_count} failed ===")
