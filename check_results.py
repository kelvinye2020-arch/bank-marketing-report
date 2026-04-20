import json, os, glob

files = sorted(glob.glob("search_result_new_*.json"))
for f in files:
    size = os.path.getsize(f)
    with open(f, encoding="utf-8") as fp:
        text = fp.read()
    # Try parse as JSON
    try:
        data = json.loads(text)
        if isinstance(data, list):
            print(f"{f}: {size}B, {len(data)} items")
            if data:
                item = data[0]
                keys = list(item.keys())[:6] if isinstance(item, dict) else "?"
                print(f"  sample keys: {keys}")
        elif isinstance(data, dict):
            print(f"{f}: {size}B, dict with keys={list(data.keys())[:6]}")
        else:
            print(f"{f}: {size}B, type={type(data).__name__}")
    except:
        print(f"{f}: {size}B, not JSON, first 200 chars: {text[:200]}")
