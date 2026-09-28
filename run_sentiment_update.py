#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_sentiment_update.py — 周四舆情专项一键跑（MCP 拉起 + 搜索 + 报告 + 推送，单进程内完成）。

为什么需要这个包装脚本（2026-09-17 教训）：
  WorkBuddy 的 Bash 工具会在**命令结束时回收该命令启动的所有子进程**（沙箱 Job Object）。
  所以「先跑 start_mcp_service.py 拉起 MCP，再另起一条命令跑 update_report.py」必然失败：
  MCP 在第一条命令结束时就被杀掉，第二条命令看到的 18060 是死的。
  实测 CREATE_BREAKAWAY_FROM_JOB（0x01000000）被拒绝（WinError 5 拒绝访问），无法逃逸。
  唯一可靠做法：MCP 进程与任务跑在**同一条命令/同一个进程树**里 —— 本脚本即为此存在。

用法：
  python run_sentiment_update.py [--only all|marketing|product|sentiment] [--no-run]
      --only    all = 全量 14 组（周一）；默认 sentiment（周四舆情专项）
      --no-run  只做「拉起 MCP + 握手校验」，不跑 update_report（用于快速自检）

退出码：0 成功；1 MCP 起不来或握手失败；其它为 update_report.py 的退出码
"""
import sys
import os
import subprocess
import argparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(cmd, desc):
    print(f"\n=== {desc} ===", flush=True)
    r = subprocess.run([PY] + cmd, cwd=BASE_DIR)
    print(f"[{desc}] exit={r.returncode}", flush=True)
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="小红书看板一键更新（含 MCP 自愈拉起）")
    ap.add_argument("--only", choices=["all", "marketing", "product", "sentiment"], default="sentiment",
                    help="只搜索指定分组（all = 全量三组，周一用；默认 sentiment，周四舆情专项）")
    ap.add_argument("--no-run", action="store_true", help="只拉起 MCP 并校验握手，不跑更新任务")
    args = ap.parse_args()

    # 1) 拉起 MCP（子进程，随本脚本同生共死，不会被提前回收）
    if run(["start_mcp_service.py"], "拉起 MCP") != 0:
        print("❌ MCP 自启失败：请手动双击 start_mcp.bat，看到「启动 HTTP 服务器 : 18060」后重跑", flush=True)
        return 1

    # 2) 握手校验（tools/list）
    if run(["probe_mcp_health.py"], "MCP 握手校验") != 0:
        print("❌ MCP 端口已监听但握手失败，停止后续流程", flush=True)
        return 1

    if args.no_run:
        print("✅ --no-run 模式：MCP 已就绪，未执行更新任务", flush=True)
        return 0

    # 3) 跑真正任务
    cmd = ["update_report.py"]
    if args.only and args.only != "all":
        cmd += ["--only", args.only]
    return run(cmd, f"更新任务 (--only {args.only})")


if __name__ == "__main__":
    sys.exit(main())
