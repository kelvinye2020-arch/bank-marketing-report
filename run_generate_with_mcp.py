# -*- coding: utf-8 -*-
"""拉起 headless MCP 后跑 generate_report.py（同进程生命周期，防沙箱杀子进程）。"""
import subprocess, sys, time
import requests

log = open("mcp_headless.log", "w")
mcp = subprocess.Popen(
    [r"D:\AI agent\xiaohongshu-mcp-windows-amd64\xiaohongshu-mcp-windows-amd64.exe", "-headless=true"],
    cwd=r"D:\AI agent\xiaohongshu-mcp-windows-amd64",
    stdout=log, stderr=subprocess.STDOUT)

MCP = "http://localhost:18060/mcp"
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ready = False
for i in range(25):
    time.sleep(1)
    try:
        r = requests.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                "clientInfo": {"name": "runner", "version": "1.0"}}},
                          headers=h, timeout=3)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        pass
if not ready:
    print("MCP failed to start", flush=True)
    mcp.terminate()
    sys.exit(1)
print("MCP ready, running generate_report.py ...", flush=True)

extra = [a for a in sys.argv[1:]]
rc = subprocess.call([sys.executable, "generate_report.py"] + extra)
mcp.terminate()
print(f"generate_report exit={rc}", flush=True)
sys.exit(rc)
