import urllib.request
import re

data = urllib.request.urlopen('https://st.gtimg.com/quotes_center/assets/index.d2403baf.js').read().decode('utf-8')

# 搜索 hcenter 或 stockqt 相关
print("=== stockqt/hcenter ===")
for m in re.finditer(r'stockqt|hcenter', data):
    s = max(0, m.start()-200)
    e = min(len(data), m.end()+300)
    print(data[s:e])
    print("===")

# 搜索 GIDX 附近更大范围
print("\n\n=== GIDX module ===")
for m in re.finditer(r'GIDX', data):
    s = max(0, m.start()-500)
    e = min(len(data), m.end()+500)
    print(data[s:e])
    print("===")
