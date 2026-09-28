# -*- coding: utf-8 -*-
import json, os, re, collections

BASE = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(BASE,"bank_marketing_report.html"), encoding="utf-8").read()
line = [l for l in html.splitlines() if l.startswith("const NOTE_DETAILS = ")][0]
used = json.loads(line[len("const NOTE_DETAILS = "):].rstrip().rstrip(";"))
print("报告内嵌详情条目:", len(used))

remote, missing, total = [], [], 0
for nid, d in used.items():
    for img in (d.get("imgs") or []):
        total += 1
        if str(img).startswith("http"):
            remote.append((nid, img))
        elif not os.path.exists(os.path.join(BASE, str(img))):
            missing.append((nid, img))

print("引用图总数:", total, "| 远程:", len(remote), "| 本地缺失:", len(missing))
print("远程域名:", collections.Counter(u.split("/")[2] for _,u in remote))
print("远程涉及笔记:", len(set(n for n,_ in remote)))
if missing:
    print("!! 本地缺失:", len(missing), missing[:5])
rn = collections.Counter(n for n,_ in remote)
json.dump(sorted(rn), open(os.path.join(BASE,"refetch_ids.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("待重抓笔记数:", len(rn), "-> refetch_ids.json")
for n,c in rn.most_common(): print("   ", n, c)
