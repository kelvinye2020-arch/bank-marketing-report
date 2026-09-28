# -*- coding: utf-8 -*-
"""2026-09-28 恢复：同进程杀旧 MCP → 重拉 → 补跑 11 组缺失搜索 → --report-only 成型。"""
import subprocess, sys, time, os, requests

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# 1) 清掉 automation 残留的 MCP（状态不明）
subprocess.run(["taskkill", "/F", "/IM", "xiaohongshu-mcp-windows-amd64.exe"],
               capture_output=True)
time.sleep(2)
print("old MCP killed", flush=True)

# 2) 同进程重拉
log = open("mcp_headless.log", "w")
p = subprocess.Popen([r"D:\AI agent\xiaohongshu-mcp-windows-amd64\xiaohongshu-mcp-windows-amd64.exe", "-headless=true"],
                     cwd=r"D:\AI agent\xiaohongshu-mcp-windows-amd64",
                     stdout=log, stderr=subprocess.STDOUT)
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ready = False
for i in range(25):
    time.sleep(1)
    try:
        r = requests.post("http://localhost:18060/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "recovery", "version": "1.0"}}}, headers=h, timeout=3)
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

# 3) 补跑缺失的 11 组（retry 脚本复用 SEARCH_GROUPS，25-40s 间隔 + 3 次退避）
rc1 = subprocess.call([sys.executable, "retry_failed_searches.py",
                       "银行活动羊毛攻略2026", "中国银行立减金满减", "工行立减金",
                       "理财通转账", "零钱通收益", "零钱通转出", "理财通会员",
                       "零钱通安全吗", "理财通亏钱", "零钱通冻结", "腾讯理财通投诉"])
print(f"retry done rc={rc1}", flush=True)

# 4) 生成 + 详情 + 图片本地化 + 闸检 + 推送（MCP 还在本进程内，详情抓取可用）
rc2 = subprocess.call([sys.executable, "update_report.py", "--report-only"])
print(f"report-only done rc={rc2}", flush=True)

p.terminate()
sys.exit(rc2 or rc1)
