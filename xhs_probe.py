import http.client

c = http.client.HTTPConnection('localhost', 18060, timeout=3)
paths = ['/api/search', '/api/v1/search', '/tools/search', '/mcp', '/api', '/health', '/status', '/v1', '/tools', '/call', '/rpc']
for p in paths:
    try:
        c.request('GET', p)
        r = c.getresponse()
        print(f"GET {p}: {r.status} {r.reason}")
        body = r.read(200).decode()
        if r.status != 404:
            print(f"  Body: {body}")
    except Exception as e:
        print(f"GET {p}: ERROR {e}")
        c = http.client.HTTPConnection('localhost', 18060, timeout=3)
