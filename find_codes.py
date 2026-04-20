import urllib.request
import re

# 下载腾讯证券JS文件
data = urllib.request.urlopen('https://st.gtimg.com/quotes_center/assets/index.d2403baf.js').read().decode('utf-8')

# 找所有 gtimg.cn 相关 URL
urls = re.findall(r'https?://[a-z0-9.]*gtimg\.cn[^\s"\'`\\]*', data)
print("=== gtimg.cn URLs ===")
for u in set(urls):
    print(u[:200])

# 搜索 mstats 相关代码
print("\n=== mstats contexts ===")
for m in re.finditer(r'mstats', data):
    start = max(0, m.start()-100)
    end = min(len(data), m.end()+200)
    print(data[start:end])
    print("---")

# 搜索 GIDX 相关代码
print("\n=== GIDX data loading ===")
for m in re.finditer(r'GIDX', data):
    start = max(0, m.start()-200)
    end = min(len(data), m.end()+300)
    ctx = data[start:end]
    if 'function' in ctx or 'fetch' in ctx or 'ajax' in ctx or 'url' in ctx or 'http' in ctx or 'load' in ctx:
        print(ctx)
        print("---")

# 搜索 proxy.finance 相关
print("\n=== proxy/finance URLs ===")
proxy_urls = re.findall(r'proxy[.\w]*finance[.\w]*\.qq\.com[^\s"\'`\\]*', data)
for u in set(proxy_urls):
    print(u[:200])

# 搜索 ifzq 相关
print("\n=== ifzq URLs ===")
ifzq = re.findall(r'ifzq[.\w]*gtimg[.\w]*\.cn[^\s"\'`\\]*', data)
for u in set(ifzq):
    print(u[:200])

# 搜索 push 相关
print("\n=== push URLs ===")
push_urls = re.findall(r'push\d*\.gtimg\.cn[^\s"\'`\\]*', data)
for u in set(push_urls):
    print(u[:200])

# 找数据请求函数
print("\n=== fnTable/load contexts ===")
for m in re.finditer(r'fnTable', data):
    start = max(0, m.start()-50)
    end = min(len(data), m.end()+300)
    print(data[start:end])
    print("---")
