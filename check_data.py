import json, os

BASE = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"

# Check file sizes
for prefix in ["search_result_", "search_result_new_"]:
    for i in range(1, 7):
        fn = f"{prefix}{i}.json"
        fp = os.path.join(BASE, fn)
        if os.path.exists(fp):
            print(f"{fn}: {os.path.getsize(fp)} bytes")

# Find first valid feed and dump noteCard keys
for prefix in ["search_result_", "search_result_new_"]:
    for i in range(1, 7):
        fn = f"{prefix}{i}.json"
        fp = os.path.join(BASE, fn)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            continue
        feeds = data.get("feeds") or data.get("data", {}).get("feeds", [])
        if isinstance(data, list):
            feeds = data
        for feed in feeds:
            nc = feed.get("noteCard", {})
            if nc:
                print(f"\n=== noteCard keys from {fn} ===")
                for k, v in nc.items():
                    val_str = str(v)[:200]
                    print(f"  {k} ({type(v).__name__}): {val_str}")
                # Also check for desc in top-level feed
                print(f"\n=== feed top-level keys ===")
                for k, v in feed.items():
                    if k != "noteCard":
                        print(f"  {k}: {str(v)[:100]}")
                exit()
