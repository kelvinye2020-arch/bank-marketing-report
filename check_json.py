import json, os

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"

for i in range(1, 7):
    f = os.path.join(BASE_DIR, f"search_result_{i}.json")
    if os.path.exists(f):
        size = os.path.getsize(f)
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            feeds = data.get("feeds", [])
            print(f"search_result_{i}.json: {len(feeds)} feeds, {size//1024}KB")
        except Exception as e:
            print(f"search_result_{i}.json: ERROR - {e}")
    else:
        print(f"search_result_{i}.json: NOT FOUND")
