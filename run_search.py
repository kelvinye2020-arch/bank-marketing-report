"""Batch search xiaohongshu MCP and save results to JSON files."""
import json
import time
import requests
import sys

BASE_URL = "http://localhost:18060"
HEADERS = {"Connection": "close"}
TIMEOUT = 60
BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"

KEYWORDS = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

success_count = 0
fail_count = 0

for i, (keyword, filename) in enumerate(KEYWORDS, 1):
    print(f"[{i}/6] Searching: {keyword} ...", flush=True)
    try:
        payload = {
            "keyword": keyword,
            "filters": {
                "sort_by": "最多点赞",
                "note_type": "不限",
                "publish_time": "不限"
            }
        }
        resp = requests.post(
            f"{BASE_URL}/api/v1/feeds/search",
            json=payload,
            headers=HEADERS,
            timeout=TIMEOUT
        )
        data = resp.json()
        
        if data.get("success"):
            feeds = data.get("data", {}).get("feeds", [])
            print(f"    OK: {len(feeds)} notes found", flush=True)
            # Save to file with UTF-8 encoding
            filepath = f"{BASE_DIR}\\{filename}"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data.get("data", {}), f, ensure_ascii=False, indent=2)
            success_count += 1
        else:
            error_msg = data.get("error", "Unknown error")
            print(f"    FAILED: {error_msg}", flush=True)
            fail_count += 1
    except Exception as e:
        print(f"    ERROR: {e}", flush=True)
        fail_count += 1
    
    # Wait between requests to avoid rate limiting
    if i < len(KEYWORDS):
        print(f"    Waiting 5 seconds...", flush=True)
        time.sleep(5)

print(f"\nDone: {success_count} success, {fail_count} failed")
if fail_count > 0:
    sys.exit(1)
