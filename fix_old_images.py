# -*- coding: utf-8 -*-
"""一次性修复：缓存里 1040g3k 老图床 ID 已 404 的笔记，走 MCP 重抓详情
拿新鲜签名 URL 后立即下载到 imgs/ 本地化。同进程拉起 MCP（沙箱约束）。
每条增量保存。"""
import json, os, re, subprocess, sys, time
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "note_details.json")
IMGS = os.path.join(BASE, "imgs")
MCP = "http://localhost:18060/mcp"

nd = json.load(open(CACHE, encoding="utf-8"))
import os as _os
if _os.path.exists(_os.path.join(BASE, "refetch_ids.json")):
    targets = json.load(open(_os.path.join(BASE, "refetch_ids.json")))
    targets = [k for k in targets if k in nd]
else:
    targets = [k for k, v in nd.items()
               if any(u.startswith("http") for u in v.get("images", []))]
print(f"待修复笔记: {len(targets)}", flush=True)
if not targets:
    sys.exit(0)

# token 来源：报告 HTML NOTE_DETAILS（最可靠）→ 搜索缓存
_HTML_USED = {}
def _html_used():
    if _HTML_USED:
        return _HTML_USED
    try:
        html = open(os.path.join(BASE, "bank_marketing_report.html"), encoding="utf-8").read()
        line = [l for l in html.splitlines() if l.startswith("const NOTE_DETAILS = ")][0]
        _HTML_USED.update(json.loads(line[len("const NOTE_DETAILS = "):].rstrip().rstrip(";")))
    except Exception:
        pass
    return _HTML_USED

def find_token(nid):
    u = _html_used().get(nid, {}).get("url", "")
    m = re.search(r"xsec_token=([^&]+)", u)
    if m:
        return m.group(1)
    import glob
    for f in glob.glob(os.path.join(BASE, "search_*.json")):
        txt = open(f, encoding="utf-8").read()
        if nid in txt:
            m = re.search(r'"id"\s*:\s*"' + nid + r'".{0,500}?"xsecToken"\s*:\s*"([^"]+)"', txt, re.S)
            if m:
                return m.group(1)
    return ""

def img_url(o):
    if not isinstance(o, dict):
        return None
    if o.get("fileId"):
        return "https://ci.xiaohongshu.com/" + o["fileId"]
    for k in ("url", "urlDefault", "urlPre", "original", "urlScoped"):
        v = o.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for k in ("infoList", "info_list"):
        for info in o.get(k) or []:
            if isinstance(info, dict) and isinstance(info.get("url"), str):
                return info["url"]
    return None

def download(nid, idx, url):
    os.makedirs(IMGS, exist_ok=True)
    fname = f"{nid}_{idx}.jpg"
    fpath = os.path.join(IMGS, fname)
    dl = url
    if "ci.xiaohongshu.com" in url and "imageView2" not in url:
        dl = url + "?imageView2/2/w/1080/format/jpg"
    if dl.startswith("http://sns-webpic"):  # 沙箱拦 :80，走 https
        dl = "https://" + dl[len("http://"):]
    try:
        r = requests.get(dl, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}, timeout=25)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(fpath, "wb") as f:
                f.write(r.content)
            return f"imgs/{fname}"
    except Exception:
        pass
    return None

# 拉起 MCP（同进程）
log = open(os.path.join(BASE, "mcp_headless.log"), "w")
p = subprocess.Popen([r"D:\AI agent\xiaohongshu-mcp-windows-amd64\xiaohongshu-mcp-windows-amd64.exe", "-headless=true"],
                     cwd=r"D:\AI agent\xiaohongshu-mcp-windows-amd64", stdout=log, stderr=subprocess.STDOUT)
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ready = False
for i in range(25):
    time.sleep(1)
    try:
        r = requests.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                "clientInfo": {"name": "fix", "version": "1.0"}}},
                          headers=h, timeout=3)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        pass
if not ready:
    print("MCP 启动失败", flush=True)
    p.terminate()
    sys.exit(1)
sid = r.headers.get("Mcp-Session-Id")
if sid:
    h["Mcp-Session-Id"] = sid
requests.post(MCP, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h, timeout=10)
print("MCP ready", flush=True)

ok = fail = 0
for ti, nid in enumerate(targets, 1):
    tok = find_token(nid)
    if not tok:
        print(f"[{ti}/{len(targets)}] {nid[:12]} 无 token，跳过", flush=True)
        fail += 1
        continue
    try:
        resp = requests.post(MCP, json={"jsonrpc": "2.0", "id": 60, "method": "tools/call",
                                        "params": {"name": "get_feed_detail",
                                                   "arguments": {"feed_id": nid, "xsec_token": tok}}},
                             headers=h, timeout=240)
        body = resp.text
        payload = json.loads(re.search(r"data: (.*)", body).group(1)) if body.lstrip().startswith(("event:", "data:")) else resp.json()
        text = next((i.get("text", "") for i in payload.get("result", {}).get("content", []) if i.get("type") == "text"), "")
        note = json.loads(text).get("data", {}).get("note", {})
        urls = []
        for img in (note.get("imageList") or [])[:9]:
            u = img_url(img)
            if u:
                urls.append(u)
        if not urls:
            print(f"[{ti}/{len(targets)}] {nid[:12]} 重抓无图", flush=True)
            fail += 1
            continue
        local = []
        for i, u in enumerate(urls):
            lp = download(nid, i, u)
            local.append(lp if lp else u)
        nd[nid]["images"] = local
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(nd, f, ensure_ascii=False, indent=1)
        n_loc = sum(1 for x in local if x.startswith("imgs/"))
        print(f"[{ti}/{len(targets)}] {nid[:12]} 重抓 {len(urls)} 图，本地化 {n_loc}", flush=True)
        ok += 1
    except Exception as e:
        print(f"[{ti}/{len(targets)}] {nid[:12]} ERR {str(e)[:60]}", flush=True)
        fail += 1
    time.sleep(2)

p.terminate()
print(f"完成: 成功 {ok} / 失败 {fail} / 共 {len(targets)}", flush=True)
