# -*- coding: utf-8 -*-
"""补齐报告里「有卡片但缺详情」的笔记：走 MCP get_feed_detail 抓全文+图+热评，
图片立即下载本地化，逐条增量写入 note_details.json。同进程拉起 MCP。"""
import json, os, re, subprocess, sys, time
from datetime import datetime
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "note_details.json")
IMGS = os.path.join(BASE, "imgs")
MCP = "http://localhost:18060/mcp"

targets = json.load(open(os.path.join(BASE, "missing_tokens.json"), encoding="utf-8"))
nd = json.load(open(CACHE, encoding="utf-8"))
print(f"待补详情: {len(targets)}", flush=True)


def _extract_images(note):
    urls = []
    for img in (note.get("imageList") or [])[:9]:
        if not isinstance(img, dict):
            continue
        u = None
        if img.get("fileId"):
            u = "https://ci.xiaohongshu.com/" + img["fileId"]
        else:
            for k in ("url", "urlDefault", "urlPre", "original", "urlScoped"):
                v = img.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    u = v
                    break
            if not u:
                for info in (img.get("infoList") or []):
                    if isinstance(info, dict) and isinstance(info.get("url"), str):
                        u = info["url"]
                        break
        if u:
            urls.append(u)
    return urls


def _extract_comments(data, limit=10):
    raw = (data.get("comments") or {}).get("list") or []
    out = []
    for c in raw[:limit]:
        content = (c.get("content") or "").strip()
        if not content:
            continue
        ts = c.get("createTime")
        date = ""
        if ts:
            try:
                date = datetime.fromtimestamp(int(ts) / 1000).strftime("%m-%d")
            except (ValueError, OSError):
                date = ""
        out.append({
            "u": (c.get("userInfo") or {}).get("nickname") or "匿名",
            "c": content[:200],
            "likes": int(str(c.get("likeCount", "0") or 0) or 0),
            "ip": c.get("ipLocation", ""),
            "d": date,
        })
    return out


def download(nid, idx, url):
    os.makedirs(IMGS, exist_ok=True)
    fname = f"{nid}_{idx}.jpg"
    fpath = os.path.join(IMGS, fname)
    dl = url
    if "ci.xiaohongshu.com" in url and "imageView2" not in url:
        dl = url + "?imageView2/2/w/1080/format/jpg"
    if dl.startswith("http://sns-webpic"):
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


# 同进程拉起 MCP
log = open(os.path.join(BASE, "mcp_headless.log"), "w")
p = subprocess.Popen([r"D:\AI agent\xiaohongshu-mcp-windows-amd64\xiaohongshu-mcp-windows-amd64.exe", "-headless=true", "-bin", "D:/AI agent/chromium/chrome-win/chrome.exe"],
                     cwd=r"D:\AI agent\xiaohongshu-mcp-windows-amd64", stdout=log, stderr=subprocess.STDOUT)
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
ready = False
for _ in range(25):
    time.sleep(1)
    try:
        r = requests.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                "clientInfo": {"name": "fixdetail", "version": "1.0"}}},
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
for ti, (nid, tok) in enumerate(targets.items(), 1):
    try:
        resp = requests.post(MCP, json={"jsonrpc": "2.0", "id": 50, "method": "tools/call",
                                        "params": {"name": "get_feed_detail",
                                                   "arguments": {"feed_id": nid, "xsec_token": tok}}},
                             headers=h, timeout=240)
        payload = resp.json().get("result", {})
        if payload.get("isError"):
            raise RuntimeError("MCP isError")
        text = next((i.get("text", "") for i in payload.get("content", []) if i.get("type") == "text"), "")
        if not text:
            raise RuntimeError("empty text")
        pl = json.loads(text).get("data", {})
        note = pl.get("note", {})
        if not note:
            raise RuntimeError("no note")
        urls = _extract_images(note)
        local = []
        for i, u in enumerate(urls):
            lp = download(nid, i, u)
            local.append(lp if lp else u)
        tags = [t.get("name", "") for t in note.get("tagList", []) if t.get("name")]
        ts = note.get("time")
        pub = ""
        if ts:
            try:
                pub = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                pub = ""
        if os.environ.get("CMTS_ONLY") == "1" and nid in nd:
            # 只补评论：保留已本地化的图片，避免重新下载
            nd[nid]["cmts"] = _extract_comments(pl)
            local = nd[nid].get("images", [])
        else:
            nd[nid] = {
                "desc": note.get("desc", ""),
                "images": local,
                "tags": tags[:8],
                "ip": note.get("ipLocation", ""),
                "pub": pub,
                "type": note.get("type", "normal"),
                "cmts": _extract_comments(pl),
            }
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(nd, f, ensure_ascii=False, indent=1)
        n_loc = sum(1 for x in local if x.startswith("imgs/"))
        print(f"[{ti}/{len(targets)}] {nid[:12]} OK 图{len(urls)}/本地{n_loc} 评{len(nd[nid]['cmts'])}", flush=True)
        ok += 1
    except Exception as e:
        print(f"[{ti}/{len(targets)}] {nid[:12]} ERR {str(e)[:60]}", flush=True)
        fail += 1
    time.sleep(2)

p.terminate()
print(f"完成: 成功 {ok} / 失败 {fail} / 共 {len(targets)}", flush=True)
