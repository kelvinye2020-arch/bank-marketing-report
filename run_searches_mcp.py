"""Search xiaohongshu via MCP HTTP endpoint directly.

注意：推荐使用 update_report.py 作为主入口（含登录检查、分批搜索、报告生成、git推送）。
本脚本仅负责搜索部分，可单独使用。

改动记录：
  2026-05-15: 间隔从3秒改为随机10-15秒，6组分2批（每批3组），批次间冷却30秒
"""
import requests
import json
import sys
import time
import random
import os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = str(Path(__file__).resolve().parent)
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

# 降频参数
BATCH_SIZE = 3
SEARCH_DELAY_MIN = 10   # 秒
SEARCH_DELAY_MAX = 15   # 秒
BATCH_COOLDOWN = 30     # 秒

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
total = len(SEARCHES)

# Split into batches
batches = [SEARCHES[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

for batch_idx, batch in enumerate(batches):
    if batch_idx > 0:
        cooldown = BATCH_COOLDOWN + random.randint(0, 10)
        print(f"\n[冷却] 批次间等待 {cooldown} 秒...", flush=True)
        time.sleep(cooldown)

    print(f"\n--- 第 {batch_idx+1}/{len(batches)} 批 ---", flush=True)

    for j, (keyword, filename) in enumerate(batch):
        global_idx = batch_idx * BATCH_SIZE + j
        outpath = os.path.join(BASE_DIR, filename)
        print(f"\n[搜索 {global_idx+1}/{total}] {keyword} -> {filename}", flush=True)

        try:
            resp = session.post(URL, json={
                "jsonrpc": "2.0", "id": global_idx + 10, "method": "tools/call",
                "params": {"name": "search_feeds", "arguments": {"keyword": keyword}}
            }, headers=HEADERS, timeout=90)

            data = json.loads(resp.text)

            if "result" in data:
                content = data["result"].get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        text = item["text"]
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

        # 随机间隔（非最后一组）
        is_last = (batch_idx == len(batches) - 1 and j == len(batch) - 1)
        if not is_last:
            delay = random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX)
            print(f"  [等待] {delay:.1f} 秒...", flush=True)
            time.sleep(delay)

print(f"\n[完成] 成功: {success_count}/{total}, 失败: {fail_count}/{total}")
if fail_count > 0:
    print("[提示] 部分搜索失败，可能需要重新登录")
    if success_count == 0:
        sys.exit(1)
