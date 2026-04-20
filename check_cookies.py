import json, time

d = json.load(open(r"c:\Users\kelvinyye\tools\xiaohongshu-mcp\cookies.json", "r"))
now = time.time()
print(f"Now: {now:.0f}")
for c in d:
    exp = c.get("expires", 0)
    if exp > 0:
        status = "EXPIRED" if exp < now else "ok"
        print(f"  {c['name']:20s} expires={exp:.0f}  {status}")
    else:
        print(f"  {c['name']:20s} session cookie")

# Check key cookies
key_names = ["web_session", "a1", "id_token"]
for name in key_names:
    for c in d:
        if c["name"] == name:
            exp = c.get("expires", 0)
            if 0 < exp < now:
                print(f"\nCRITICAL: {name} is EXPIRED!")
            else:
                print(f"\n{name}: valid")
