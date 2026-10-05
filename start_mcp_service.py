#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
start_mcp_service.py — 供 automation-3 在无人值守时自动拉起小红书 MCP。

设计要点：
- 用 -headless=true：自动化场景不需要弹窗，且能在无桌面环境下运行
  （有头模式在无 display 会话里拉不起 Chromium，headless 模式不依赖 display）
- 先 taskkill 旧进程 + 清端口，避免 bind 冲突
- 轮询 18060 最多 40s，起来返回 0，起不来返回 1

注意：如果拉起后 search_feeds 仍返回空（cookie 过期），由调用方负责发企微让用户手动扫码。
"""
import subprocess
import time
import socket
import sys
import os

# Windows 控制台默认 GBK。当 stdout 被重定向到管道/文件（如 `| Tee-Object`）时，
# print 含非 GBK 字符（✅ U+2705、⚠️ 等）会抛 UnicodeEncodeError，
# 导致「MCP 端口实际已就绪」却因打印崩溃返回 exit=1，被上层误判为「自启失败」。
# 这里强制 UTF-8 输出，errors=replace 兜底，避免打印问题污染退出码判定。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MCP_DIR = r"D:\AI agent\xiaohongshu-mcp-windows-amd64"
EXE = "xiaohongshu-mcp-windows-amd64.exe"
CHROMIUM = r"D:\AI agent\chromium\chrome-win\chrome.exe"
PORT = 18060


def kill_old():
    """清理旧 MCP 进程和占用端口的 PID。"""
    subprocess.run(
        ["taskkill", "/F", "/IM", EXE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # 找占用 18060 的 PID 并杀掉
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, encoding="gbk",
            errors="ignore", timeout=10
        ).stdout
        for line in out.splitlines():
            if f":{PORT}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
    except Exception:
        pass
    time.sleep(1)


def port_up():
    try:
        s = socket.create_connection(("localhost", PORT), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def main():
    print(f"[start_mcp_service] 清理旧进程 + 端口 {PORT}...")
    kill_old()

    if port_up():
        print("[start_mcp_service] 端口已就绪，无需重启")
        return 0

    print("[start_mcp_service] 启动 MCP (headless=true)...")
    exe_path = os.path.join(MCP_DIR, EXE)
    try:
        # DETACHED_PROCESS=0x00000008：脱离父进程独立运行，不被 WorkBuddy 会话结束杀掉
        subprocess.Popen(
            [exe_path, "-headless=true", "-bin", CHROMIUM],
            cwd=MCP_DIR,
            creationflags=0x00000008,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[start_mcp_service] 启动失败: {e}")
        return 1

    print("[start_mcp_service] 等待端口起来 (最多 40s)...")
    for i in range(40):
        time.sleep(1)
        if port_up():
            print(f"[start_mcp_service] ✅ MCP 已就绪 (用时 {i+1}s)")
            return 0
    print("[start_mcp_service] ❌ 40s 内端口未起来，启动失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
