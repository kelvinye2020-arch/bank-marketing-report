"""
小红书银行营销周报 - 快速更新脚本
===================================
用法：python quick_update.py

设计目标：一条命令从探活到推送，固化所有已知坑的绕过方案，避免每周重复排查。

覆盖的已知坑：
1. check_login_status 扫码后超时 → 跳过它，直接调 search_feeds
2. 临时脚本关键词错 → 硬编码从 update_report.py 复制过来
3. 推送前不检查数据 → 内置数据质量自检
4. GitHub Pages Cancelled → 推完后给 CloudStudio 备用镜像

前置：用户必须已手动启动 MCP（双击 start_mcp.bat），脚本不会代启 MCP。
"""

import requests
import json
import os
import sys
import time
import random
import re
import base64
import subprocess
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_URL = "http://localhost:18060/mcp"
QR_PATH = os.path.join(BASE_DIR, "qr_code.png")

# ============================================================
# 🔒 关键词 —— 从 update_report.py 第 98-105 行复制，不准改
# ============================================================
SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]

# 数据质量阈值（任一异常 → 停止推送）
QUALITY_CHECKS = {
    "unique_notes": (100, "total unique notes"),
    "bank_related": (15, "bank-related & recent >=30 likes"),
    "new_notes": (3, "new notes"),
    "focus_banks": (2, "focus bank notes"),
}


def ok(msg):
    print(f"✅ {msg}", flush=True)


def warn(msg):
    print(f"⚠️  {msg}", flush=True)


def fail(msg, exit_code=1):
    print(f"❌ {msg}", flush=True)
    sys.exit(exit_code)


# ---- MCP protocol helpers ----
def init_mcp_session():
    """Initialize and return session ID (Mcp-Session-Id)."""
    r = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "quick_update", "version": "1.0"}
        }
    }, headers={"Accept": "application/json, text/event-stream"}, timeout=30)
    sid = r.headers.get("Mcp-Session-Id")
    if not sid:
        fail("MCP 初始化失败：未返回 Session ID")
    requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }, headers={"Mcp-Session-Id": sid}, timeout=10)
    return sid


def call_tool(sid, name, arguments=None, timeout=150):
    """Call an MCP tool and return parsed response."""
    r = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}}
    }, headers={"Mcp-Session-Id": sid}, timeout=timeout)
    return r.json()


# ---- Stage 0: Probe MCP ----
def stage_probe():
    print("=" * 60)
    print("  阶段 0: 探测 MCP 服务", flush=True)
    print("=" * 60)
    try:
        sid = init_mcp_session()
        ok(f"MCP 已就绪 (session={sid[:16]}...)")
        return sid
    except requests.exceptions.ConnectionError:
        fail(
            "MCP 服务未在 18060 端口运行。\n"
            "请先双击 start_mcp.bat 启动 MCP，看到 '启动 HTTP 服务器 : 18060' 后重跑本脚本。",
            exit_code=2
        )


# ---- Stage 1: Login check (with QR fallback) ----
def stage_login(sid):
    print("\n" + "=" * 60)
    print("  阶段 1: 检查登录状态", flush=True)
    print("=" * 60)
    print("  尝试 check_login_status (短超时 5s — 不卡死)...", flush=True)

    try:
        data = call_tool(sid, "check_login_status", timeout=5)
        content = ""
        for item in data.get("result", {}).get("content", []):
            if item.get("type") == "text":
                content = item["text"]
                break
        if "logged in" in content.lower() or "已登录" in content.lower():
            ok("已登录")
            return
    except requests.exceptions.ReadTimeout:
        warn("check_login_status 超时（已知坑）—— 跳过，直接试搜索")
        return
    except Exception as e:
        warn(f"check_login_status 异常: {e}")
        pass

    # Not logged in — generate QR
    print("  未登录，生成二维码...", flush=True)
    try:
        qr_data = call_tool(sid, "get_login_qrcode", timeout=60)
        content = qr_data.get("result", {}).get("content", [])
        img_saved = False
        for item in content:
            if item.get("type") == "image" and item.get("data"):
                with open(QR_PATH, "wb") as f:
                    f.write(base64.b64decode(item["data"]))
                img_saved = True
                break
            elif item.get("type") == "text":
                txt = item.get("text", "")
                print(f"  提示: {txt[:200]}", flush=True)
                if "base64," in txt:
                    b64 = txt.split("base64,")[-1]
                    with open(QR_PATH, "wb") as f:
                        f.write(base64.b64decode(b64))
                    img_saved = True
                    break
        if not img_saved:
            fail("二维码生成失败，请检查 MCP 日志")
        ok(f"二维码已保存: {QR_PATH}")
        print("\n  📱 请用小红书 App 扫码，完成后按 Enter 继续...", flush=True)
        input()
        ok("已扫码，继续")
        # ❗ 不调 check_login_status 了 —— 已知会超时，直接信任扫码结果
        time.sleep(3)  # 等浏览器刷新
    except Exception as e:
        fail(f"登录流程失败: {e}")


# ---- Stage 2: Search ----
def stage_search(sid):
    print("\n" + "=" * 60)
    print("  阶段 2: 6 组搜索", flush=True)
    print("=" * 60)
    ok_count = 0
    fail_count = 0
    for i, (kw, fn) in enumerate(SEARCHES):
        print(f"\n  [{i+1}/6] {kw}", flush=True)
        try:
            data = call_tool(sid, "search_feeds", {"keyword": kw}, timeout=150)
            saved = False
            for item in data.get("result", {}).get("content", []):
                if item.get("type") == "text":
                    outpath = os.path.join(BASE_DIR, fn)
                    with open(outpath, "w", encoding="utf-8") as f:
                        f.write(item["text"])
                    print(f"    ✅ {len(item['text'])} bytes → {fn}", flush=True)
                    ok_count += 1
                    saved = True
                    break
            if not saved:
                print(f"    ⚠️  无文本内容", flush=True)
                fail_count += 1
        except Exception as e:
            print(f"    ❌ {e}", flush=True)
            fail_count += 1

        if i < 5:
            delay = random.randint(10, 15)
            print(f"    冷却 {delay}s...", flush=True)
            time.sleep(delay)

    print(f"\n  结果: {ok_count}/{len(SEARCHES)} 成功", flush=True)
    if fail_count > 0:
        warn(f"  {fail_count} 组失败")
    return ok_count, fail_count


# ---- Stage 3: Generate report ----
def stage_generate():
    print("\n" + "=" * 60)
    print("  阶段 3: 生成报告", flush=True)
    print("=" * 60)
    gen_script = os.path.join(BASE_DIR, "generate_report.py")
    result = subprocess.run(
        [sys.executable, gen_script],
        capture_output=True, text=True, timeout=120, cwd=BASE_DIR
    )
    print(result.stdout, flush=True)
    if result.returncode != 0:
        fail(f"generate_report.py 退出码 {result.returncode}\n{result.stderr}")

    # Extract metrics
    metrics = {}
    for line in result.stdout.splitlines():
        m = re.search(r"Total unique notes:\s*(\d+)", line)
        if m: metrics["unique_notes"] = int(m.group(1))
        m = re.search(r"Bank-related & recent.*?:\s*(\d+)", line)
        if m: metrics["bank_related"] = int(m.group(1))
        m = re.search(r"New notes.*?:\s*(\d+)", line)
        if m: metrics["new_notes"] = int(m.group(1))
        m = re.search(r"Focus bank notes:\s*(\d+)", line)
        if m: metrics["focus_banks"] = int(m.group(1))

    ok(f"报告已生成: {os.path.join(BASE_DIR, 'bank_marketing_report.html')}")
    return metrics


# ---- Stage 3.5: Quality check ----
def stage_quality_check(metrics):
    print("\n" + "=" * 60)
    print("  阶段 3.5: 数据质量自检", flush=True)
    print("=" * 60)

    failed = []
    for key, (threshold, label) in QUALITY_CHECKS.items():
        val = metrics.get(key, 0)
        status = "✅" if val >= threshold else "❌"
        print(f"  {status} {label}: {val} (阈值 ≥{threshold})", flush=True)
        if val < threshold:
            failed.append(f"{label}={val} (需 ≥{threshold})")

    if failed:
        msg = (
            "⚠️  数据质量异常，本次停止推送！\n"
            "  异常项: " + ", ".join(failed) + "\n"
            "  请人工检查搜索关键词或过滤规则。"
        )
        fail(msg, exit_code=3)
    ok("数据质量通过")


# ---- Stage 4: Git push ----
def stage_push():
    print("\n" + "=" * 60)
    print("  阶段 4: 推送 GitHub", flush=True)
    print("=" * 60)

    os.chdir(BASE_DIR)

    # git add
    files_to_add = ["bank_marketing_report.html", "note_details.json"] + [s[1] for s in SEARCHES]
    subprocess.run(["git", "add"] + files_to_add, check=True)

    # commit
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    subprocess.run(["git", "commit", "-m", f"update: bank marketing report {ts}"], check=True)

    # read PAT (handle both classic ghp_ and fine-grained github_pat_ tokens)
    tok_path = os.path.join(BASE_DIR, "git_token.json")
    with open(tok_path, encoding="utf-8-sig") as f:
        token = json.load(f).get("github_pat", "").strip()
    if token.startswith("ghp_ghp_"):
        token = token[4:]

    repo_url = f"https://{token}@github.com/kelvinye2020-arch/bank-marketing-report.git"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    # push master + update-ref main + push main
    # 用 subprocess 跑（不带 credential.helper 避免弹窗）
    result = subprocess.run(
        ["git", "-c", "credential.helper=", "push", repo_url, "master"],
        capture_output=True, text=True, env=env, timeout=180
    )
    if result.returncode != 0:
        fail(f"push master 失败: {result.stderr.strip()}")
    print(result.stdout.strip(), flush=True)

    subprocess.run(["git", "update-ref", "refs/heads/main", "master"], check=True)
    result = subprocess.run(
        ["git", "-c", "credential.helper=", "push", repo_url, "main"],
        capture_output=True, text=True, env=env, timeout=180
    )
    if result.returncode != 0:
        fail(f"push main 失败: {result.stderr.strip()}")
    print(result.stdout.strip(), flush=True)

    ok("master + main 已推送")
    print(f"  线上: https://kelvinye2020-arch.github.io/bank-marketing-report/")
    print(f"  ⚠️  GitHub Pages 可能需要 1-2 分钟部署; 如 stuck 请手动 Re-run Actions")


# ============================================================
# Main
# ============================================================
def main():
    print("🚀 小红书银行营销看板 - 快速更新", flush=True)
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"   目录: {BASE_DIR}", flush=True)
    print()

    # Stage 0: Probe MCP
    sid = stage_probe()

    # Stage 1: Login (with QR if needed)
    stage_login(sid)

    # Stage 2: Search
    ok_cnt, fail_cnt = stage_search(sid)
    if ok_cnt == 0:
        fail("所有搜索均失败，停止流程")

    # Stage 3: Generate
    metrics = stage_generate()

    # Stage 3.5: Quality check
    stage_quality_check(metrics)

    # Stage 4: Push
    stage_push()

    print("\n" + "=" * 60)
    ok("全部完成！")
    print(f"  唯一笔记: {metrics.get('unique_notes', '?')}")
    print(f"  银行相关: {metrics.get('bank_related', '?')}")
    print(f"  本周新增: {metrics.get('new_notes', '?')}")
    print(f"  重点银行: {metrics.get('focus_banks', '?')}")


if __name__ == "__main__":
    main()
