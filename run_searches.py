"""Run all 6 keyword searches via xhs_client.py and save results as JSON files."""
import subprocess
import sys
import time
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
CLIENT = os.path.join(BASE_DIR, "skills", "xiaohongshu-mcp", "scripts", "xhs_client.py")

SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

success_count = 0
fail_count = 0

for keyword, filename in SEARCHES:
    outpath = os.path.join(BASE_DIR, filename)
    print(f"[搜索] {keyword} -> {filename} ...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, CLIENT, "search", keyword, "--sort", "最多点赞", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        stdout = result.stdout.strip()
        if stdout and len(stdout) > 100:
            with open(outpath, "w", encoding="utf-8") as f:
                f.write(stdout)
            print(f"  ✅ 成功，{len(stdout)} 字节", flush=True)
            success_count += 1
        else:
            print(f"  ❌ 返回数据过短 ({len(stdout)} 字节)，可能 cookie 过期", flush=True)
            if result.stderr:
                print(f"  STDERR: {result.stderr[:300]}", flush=True)
            fail_count += 1
    except subprocess.TimeoutExpired:
        print(f"  ❌ 超时 (60s)", flush=True)
        fail_count += 1
    except Exception as e:
        print(f"  ❌ 异常: {e}", flush=True)
        fail_count += 1
    
    # 间隔 3 秒，避免打挂 MCP 服务器
    if keyword != SEARCHES[-1][0]:
        time.sleep(3)

print(f"\n[完成] 成功: {success_count}, 失败: {fail_count}")
if fail_count > 0:
    print("[提示] 部分搜索失败，可能需要重新登录小红书")
    sys.exit(1)
