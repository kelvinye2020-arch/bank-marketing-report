#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_with_restart.py — 「每组搜索前重启 MCP」的补跑脚本。

背景（2026-09-28 实测）：
  MCP（rod 浏览器）跑几组搜索后会进入卡死态：后续 search_feeds 全部 150s 读超时，
  命中率掉到 ~30%。但**重启 MCP 后的前几组搜索都成功**（原始轮 #1 成、补跑轮 #1/#2 成）。
  所以策略：每组搜索前先 start_mcp_service.py 重启（清理旧进程+清端口+headless 拉起），
  再建新 session 搜一次。重启成本仅约 1-10s，远低于一次 150s 挂起。

关键词**不做复制**：直接 import update_report.SEARCH_GROUPS（automation 硬规则：
不准自创/改字/加减关键词）。已今天更新过的文件会跳过（--force 可强制重跑）。

用法：
    python retry_with_restart.py                      # 补跑所有非今天更新的组
    python retry_with_restart.py --force              # 14 组全跑
    python retry_with_restart.py --group sentiment    # 只跑指定组
"""
import sys
import os
import time
import random
import subprocess
import argparse
from datetime import datetime, date

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import update_report as ur  # noqa: E402  （复用 MCP_URL / SEARCH_GROUPS / init_mcp_session）

import requests  # noqa: E402

# 单次搜索超时（秒）。实测成功请求 ~10-20s 就返回，失败的请求是「卡死」不是「慢」
# （150s / 240s 都超时），所以给 90s：成功不受影响，卡死的快速失败+重启重试，省时间。
SEARCH_TIMEOUT = int(os.environ.get("XHS_SEARCH_TIMEOUT", "90"))
RESTART_WAIT = 3            # 重启后额外等待（秒）
GAP_MIN, GAP_MAX = 20, 30   # 组间间隔
MAX_ATTEMPTS = 2            # 每组最多尝试次数（每次都会先重启 MCP）


def restart_mcp():
    r = subprocess.run([sys.executable, os.path.join(BASE_DIR, "start_mcp_service.py")],
                       cwd=BASE_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"    ❌ MCP 重启失败: {(r.stdout or '').strip()[:200]}", flush=True)
        return False
    time.sleep(RESTART_WAIT)
    return True


def is_fresh_today(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path)).date()
    return mtime == date.today()


def do_search(keyword, filename, idx):
    """重启 MCP → 新建 session → 搜一次。成功写入文件并返回 True。"""
    outpath = os.path.join(BASE_DIR, filename)
    if not restart_mcp():
        return False
    try:
        headers = ur.init_mcp_session()
    except Exception as e:
        print(f"    ❌ MCP 会话初始化失败: {e}", flush=True)
        return False

    try:
        resp = requests.post(ur.MCP_URL, json={
            "jsonrpc": "2.0", "id": idx + 100, "method": "tools/call",
            "params": {"name": "search_feeds", "arguments": {"keyword": keyword}},
        }, headers=headers, timeout=SEARCH_TIMEOUT)
        data = resp.json()
        if "result" in data:
            for item in data["result"].get("content", []):
                if item.get("type") == "text":
                    text = item["text"]
                    if not text.strip():
                        print("    ⚠️ 返回空内容", flush=True)
                        return False
                    with open(outpath, "w", encoding="utf-8") as f:
                        f.write(text)
                    print(f"    ✅ 成功, {len(text)} bytes → {filename}", flush=True)
                    return True
            print("    ⚠️ 响应中无 text 内容", flush=True)
        else:
            print(f"    ❌ 错误: {str(data.get('error'))[:200]}", flush=True)
    except Exception as e:
        print(f"    ❌ 异常: {e}", flush=True)
    return False


def main():
    ap = argparse.ArgumentParser(description="重启式补跑：每组搜索前重启 MCP")
    ap.add_argument("--group", action="append", choices=list(ur.SEARCH_GROUPS),
                    help="只跑指定分组，可重复传（默认全量三组）")
    ap.add_argument("--force", action="store_true", help="强制重跑（含今天已更新的组）")
    args = ap.parse_args()

    groups = args.group or list(ur.GROUP_ORDER)
    targets = []
    for g in groups:
        targets.extend(ur.SEARCH_GROUPS[g])

    pending = []
    for kw, fn in targets:
        if not args.force and is_fresh_today(fn):
            print(f"  ⏭  跳过（今天已更新）: {kw} → {fn}", flush=True)
            continue
        pending.append((kw, fn))

    print(f"🚀 重启式补跑：待跑 {len(pending)} 组 / 总计 {len(targets)} 组", flush=True)
    ok_count, failed = 0, []
    for i, (kw, fn) in enumerate(pending, 1):
        print(f"\n  [{i}/{len(pending)}] 搜索: {kw}", flush=True)
        done = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                print(f"    🔁 第 {attempt} 次尝试（先重启 MCP）", flush=True)
            if do_search(kw, fn, i):
                ok_count += 1
                done = True
                break
        if not done:
            failed.append(kw)
        if i < len(pending):
            delay = random.uniform(GAP_MIN, GAP_MAX)
            print(f"    ⏳ 等待 {delay:.1f} 秒...", flush=True)
            time.sleep(delay)

    print(f"\n补跑完成: 成功 {ok_count}/{len(pending)}", flush=True)
    if failed:
        print(f"仍失败: {failed}", flush=True)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
