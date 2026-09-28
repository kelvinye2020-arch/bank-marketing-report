#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_failed_searches.py — 只重跑失败的搜索组（不重新生成报告/不推送）。

背景：MCP（rod 浏览器）偶发抖动，单次 search_feeds 会 150s 读超时。
全量重跑成本高（14 组 × 延迟），所以只补跑失败的那几组。

关键词**不做复制**：直接 import update_report.SEARCH_GROUPS，保证与正式流程完全一致
（automation 硬规则：不准自创/改字/加减关键词）。

用法（MCP 必须已经在同一命令里被 start_mcp_service.py 拉起）：
    python retry_failed_searches.py 银行信用卡支付立减 银行活动羊毛攻略2026
    python retry_failed_searches.py --all-failed-marketing   # 便捷：整组重跑
"""
import sys
import os
import time
import random
import argparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_report as ur  # noqa: E402  （复用 MCP_URL / SEARCH_GROUPS / init_mcp_session）

import requests  # noqa: E402

RETRY_DELAY_MIN = 25   # 补跑间隔（秒）：比正式流程的 10-15s 更保守
RETRY_DELAY_MAX = 40

# 2026-09-28：MCP（rod 浏览器）抖动是随机偶发的，单次失败不代表关键词有问题。
# 实测同一 session 连搜可以成功、换 session 反而超时 → 判定为随机 flaky。
# 所以每个关键词最多尝试 MAX_ATTEMPTS 次，退避 30s / 60s，仍失败才计入 failed。
MAX_ATTEMPTS = 3
BACKOFFS = [30, 60]


def find_keyword(kw):
    for group, items in ur.SEARCH_GROUPS.items():
        for keyword, filename in items:
            if keyword == kw:
                return group, keyword, filename
    return None


def do_search(session, headers, keyword, filename, idx):
    outpath = os.path.join(ur.BASE_DIR, filename)
    print(f"\n  [{idx}] 重试搜索: {keyword}", flush=True)
    try:
        resp = session.post(ur.MCP_URL, json={
            "jsonrpc": "2.0", "id": idx + 100, "method": "tools/call",
            "params": {"name": "search_feeds", "arguments": {"keyword": keyword}},
        }, headers=headers, timeout=240)
        data = resp.json()
        if "result" in data:
            for item in data["result"].get("content", []):
                if item.get("type") == "text":
                    text = item["text"]
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
    args = [a for a in sys.argv[1:]]
    targets = []
    # 支持多个 --group（2026-09-28 起）：一次补齐多组失败搜索
    while "--group" in args:
        gi = args.index("--group")
        group = args[gi + 1]
        if group not in ur.SEARCH_GROUPS:
            print(f"❌ 未知分组: {group}（可选: {list(ur.SEARCH_GROUPS)}）", flush=True)
            return 2
        targets.extend(ur.SEARCH_GROUPS[group])
        args = args[:gi] + args[gi + 2:]
    for kw in args:
        found = find_keyword(kw)
        if not found:
            print(f"❌ 关键词不在 SEARCH_GROUPS 中，拒绝执行: {kw}", flush=True)
            return 2
        targets.append((found[1], found[2]))

    if not targets:
        print("用法: python retry_failed_searches.py <关键词1> [<关键词2> ...] 或 --group <marketing|product|sentiment>")
        return 2

    print(f"🚀 重试 {len(targets)} 组失败搜索（每组最多 {MAX_ATTEMPTS} 次）", flush=True)
    headers = ur.init_mcp_session()
    session = requests.Session()
    ok_count = 0
    failed = []
    for i, (keyword, filename) in enumerate(targets, 1):
        done = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                print(f"    🔁 第 {attempt} 次尝试: {keyword}", flush=True)
            if do_search(session, headers, keyword, filename, i):
                ok_count += 1
                done = True
                break
            if attempt <= len(BACKOFFS):
                backoff = BACKOFFS[attempt - 1] + random.uniform(0, 10)
                print(f"    ⏳ 失败，退避 {backoff:.1f} 秒后重试...", flush=True)
                time.sleep(backoff)
        if not done:
            failed.append(keyword)
        if i < len(targets):
            # 补跑时用更长间隔：实测 10-15s 间隔下 MCP（rod 浏览器）容易连续读超时
            delay = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX)
            print(f"    ⏳ 等待 {delay:.1f} 秒...", flush=True)
            time.sleep(delay)

    print(f"\n重试完成: 成功 {ok_count}/{len(targets)}", flush=True)
    if failed:
        print(f"仍失败: {failed}", flush=True)
    return 0 if not failed else 3


if __name__ == "__main__":
    sys.exit(main())
