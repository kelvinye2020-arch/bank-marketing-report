# -*- coding: utf-8 -*-
"""Repair degraded note details: re-fetch via MCP get_feed_detail.

Symptom: 55/62 entries in note_details.json have empty images (and often
empty tags) — popup shows text only, inconsistent with notes that have
images. Root cause of degradation unknown (fetched 2026-09-21 morning run);
re-fetch all broken entries with robust image extraction.

Run: python repair_details.py  (starts MCP headless itself, same process)
"""
import subprocess, time, json, re, os, sys, glob

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

MCP_EXE = r"D:\AI agent\xiaohongshu-mcp-windows-amd64\xiaohongshu-mcp-windows-amd64.exe"
MCP_CWD = r"D:\AI agent\xiaohongshu-mcp-windows-amd64"
MCP_URL = "http://localhost:18060/mcp"

# ---------- 1. start MCP (child of THIS process) ----------
log = open("mcp_headless.log", "w")
proc = subprocess.Popen([MCP_EXE, "-headless=true"], cwd=MCP_CWD,
                        stdout=log, stderr=subprocess.STDOUT)

import requests

h = {"Content-Type": "application/json",
     "Accept": "application/json, text/event-stream"}
resp = None
for _ in range(20):
    time.sleep(1)
    try:
        resp = requests.post(MCP_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "repair", "version": "1.0"}}},
            headers=h, timeout=3)
        if resp.status_code == 200:
            break
    except Exception:
        continue
if resp is None or resp.status_code != 200:
    print("FATAL: MCP did not start")
    sys.exit(1)
sid = resp.headers.get("Mcp-Session-Id")
if sid:
    h["Mcp-Session-Id"] = sid
requests.post(MCP_URL, json={"jsonrpc": "2.0",
                             "method": "notifications/initialized"},
              headers=h, timeout=10)
print("MCP ready")


def mcp_call(name, args, timeout=240):
    r = requests.post(MCP_URL, json={"jsonrpc": "2.0", "id": 99,
                                     "method": "tools/call",
                                     "params": {"name": name,
                                                "arguments": args}},
                      headers=h, timeout=timeout)
    body = r.text
    if body.lstrip().startswith(("event:", "data:")):
        m = re.search(r"data: (.*)", body)
        payload = json.loads(m.group(1))
    else:
        payload = r.json()
    result = payload.get("result", {})
    if result.get("isError"):
        text = next((c.get("text", "") for c in result.get("content", [])
                     if c.get("type") == "text"), "")
        raise RuntimeError(f"MCP {name} error: {text[:120]}")
    return next((c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"), "")


# ---------- 2. token lookup from search caches ----------
def build_token_index():
    idx = {}
    for f in glob.glob("search_result*.json") + glob.glob("search_product_*.json") \
            + glob.glob("search_sentiment_*.json"):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        def walk(o):
            if isinstance(o, dict):
                nid = o.get("id")
                if isinstance(nid, str) and re.fullmatch(r"[0-9a-f]{24}", nid) \
                        and o.get("xsecToken") and nid not in idx:
                    idx[nid] = o["xsecToken"]
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)
    return idx


TOKENS = build_token_index()
print("token index:", len(TOKENS))

# ---------- 3. robust image extraction ----------
def _img_url(o):
    if not isinstance(o, dict):
        return None
    fid = o.get("fileId")
    if fid:
        return "https://ci.xiaohongshu.com/" + fid
    for k in ("url", "urlDefault", "url_default", "original", "urlScoped"):
        v = o.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for k in ("infoList", "info_list"):
        for info in o.get(k) or []:
            if isinstance(info, dict):
                v = info.get("url")
                if isinstance(v, str) and v.startswith("http"):
                    return v
    return None


def extract_images(note):
    urls = []
    for img in (note.get("imageList") or [])[:9]:
        u = _img_url(img)
        if u:
            urls.append(u)
    if not urls:
        u = _img_url(note.get("cover") or {})
        if u:
            urls.append(u)
        vid = note.get("video") or {}
        for key in ("cover", "image", "videoCover"):
            u = _img_url(vid.get(key) or {})
            if u and u not in urls:
                urls.append(u)
    return urls


def parse_note(text):
    note = json.loads(text).get("data", {}).get("note", {})
    if not note:
        return None
    ts = note.get("time")
    pub = ""
    if ts:
        try:
            from datetime import datetime
            pub = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            pub = ""
    return {
        "desc": note.get("desc", ""),
        "images": extract_images(note),
        "tags": [t.get("name", "") for t in (note.get("tagList") or [])[:8]
                 if t.get("name")],
        "ip": note.get("ipLocation", ""),
        "pub": pub,
        "type": note.get("type", "normal"),
    }


# ---------- 4. determine broken set ----------
cache = json.load(open("note_details.json", encoding="utf-8"))
broken = [k for k, v in cache.items() if not v.get("images")]
print("broken entries (empty images):", len(broken))

results = {"fixed": 0, "still_empty": 0, "failed": 0}
for i, nid in enumerate(broken, 1):
    tok = TOKENS.get(nid, "")
    if not tok:
        print(f"[{i}/{len(broken)}] {nid}: NO TOKEN, skip")
        results["failed"] += 1
        continue
    try:
        text = mcp_call("get_feed_detail",
                        {"feed_id": nid, "xsec_token": tok})
        d = parse_note(text)
    except Exception as e:
        print(f"[{i}/{len(broken)}] {nid}: FAIL {str(e)[:80]}")
        results["failed"] += 1
        continue
    if d is None:
        print(f"[{i}/{len(broken)}] {nid}: no note in response")
        results["failed"] += 1
        continue
    d["fetched_at"] = time.strftime("%Y-%m-%d %H:%M")
    # 保留原 desc 若新抓为空
    if not d["desc"] and cache[nid].get("desc"):
        d["desc"] = cache[nid]["desc"]
    cache[nid] = d
    n_img = len(d["images"])
    if n_img:
        results["fixed"] += 1
    else:
        results["still_empty"] += 1
    print(f"[{i}/{len(broken)}] {nid}: imgs={n_img} tags={len(d['tags'])} "
          f"desc={len(d['desc'])} type={d['type']}")

# ---------- 5. missing product note 6aacda04 ----------
MISSING = "6aacda04000000002902e5ab"
if MISSING not in cache and MISSING in TOKENS:
    try:
        text = mcp_call("get_feed_detail",
                        {"feed_id": MISSING, "xsec_token": TOKENS[MISSING]})
        d = parse_note(text)
        if d:
            d["fetched_at"] = time.strftime("%Y-%m-%d %H:%M")
            cache[MISSING] = d
            print(f"missing {MISSING}: imgs={len(d['images'])} ADDED")
            results["fixed"] += 1
    except Exception as e:
        print(f"missing {MISSING}: FAIL {str(e)[:80]}")

with open("note_details.json", "w", encoding="utf-8") as f:
    json.dump(cache, f, ensure_ascii=False, indent=1)
print("saved. summary:", results)
proc.terminate()
