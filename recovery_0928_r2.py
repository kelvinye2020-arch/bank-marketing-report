# -*- coding: utf-8 -*-
"""2026-09-28 恢复第二轮：同进程杀旧 MCP → 重拉 → 补 4 组缺失搜索 → --report-only。"""
import subprocess, sys, time, os, requests

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

subprocess.run(["taskkill", "/F", "/IM", "xiaohongshu-mcp-windows-amd64.exe"], capture_output=True)
time.sleep(2)
print("old MCP killed", flush=True)

log = open("mcp_headless.log", "w")
p = subprocess.Popen([r"D://AI agent//xiaohongshu-mcp-windows-amd64//xiaohongshu-mcp-windows-amd64.exe", "-headless=true"],
                     cwd=r"D://AI agent//xiaohongshu-mcp-windows-amd64",
                     stdout=log, stderr=subprocess.STDOUT)
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ready = False
for i in range(25):
    time.sleep(1)
    try:
        r = requests.post("http://localhost:18060/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "recovery2", "version": "1.0"}}}, headers=h, timeout=3)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        pass
if not ready:
    print("MCP 启动失败", flush=True)
    p.terminate()
    sys.exit(1)
print("MCP ready", flush=True)

rc1 = subprocess.call([sys.executable, "retry_failed_searches.py",
                       "零钱通收益", "理财通亏钱", "零钱通冻结", "腾讯理财通投诉"])
print(f"retry done rc={rc1}", flush=True)

rc2 = subprocess.call([sys.executable, "update_report.py", "--report-only"])
print(f"report-only done rc={rc2}", flush=True)

p.terminate()
sys.exit(rc2 or 0)
