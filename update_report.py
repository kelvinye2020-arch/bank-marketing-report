"""
小红书银行营销看板 - 一站式更新脚本
=====================================
用法：python update_report.py [--search-only] [--report-only] [--no-push]

5个阶段：
  1. 检查 MCP 服务是否运行（端口 18060）
  2. 检查登录状态（调 check_login_status）
  3. 分批搜索（6组关键词，随机10-15秒间隔，两批之间等30秒）
  4. 生成看板 HTML（调 generate_report.py）
  5. Git 推送（master → merge main → push main）

设计原则：
  - 任何前置条件不满足，立刻停止并给出明确指引，不静默失败
  - 搜索降频：间隔10-15秒，分批+30秒冷却，降低被封风险
  - 支持跳过某些阶段（--search-only / --report-only / --no-push）
"""

import sys
import os
import json
import time
import random
import socket
import subprocess
import argparse
import base64
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path



# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# Configuration
# ============================================================
BASE_DIR = str(Path(__file__).resolve().parent)
LOCAL_CONFIG_PATH = Path(BASE_DIR) / "config.local.json"


def load_local_config():
    """Load local-only config. This file is ignored by Git and may contain machine paths or secrets."""
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️  读取本地配置失败: {LOCAL_CONFIG_PATH} ({e})", flush=True)
        return {}


LOCAL_CONFIG = load_local_config()
MCP_HOST = LOCAL_CONFIG.get("mcp_host") or os.environ.get("XHS_MCP_HOST", "localhost")
MCP_PORT = int(LOCAL_CONFIG.get("mcp_port") or os.environ.get("XHS_MCP_PORT", "18060"))
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
MCP_EXE = LOCAL_CONFIG.get("mcp_exe") or os.environ.get("XHS_MCP_EXE", "")
QR_PATH = os.path.join(BASE_DIR, "qr_code.png")
REPORT_URL = "https://kelvinye2020-arch.github.io/bank-marketing-report/"

GITHUB_REPO = "kelvinye2020-arch/bank-marketing-report"

# 企业微信机器人配置：优先环境变量，其次本项目本地配置，再读取本地配置声明的外部配置路径。
# 不把 webhook key 写进脚本，避免误提交密钥。
WECOM_CONFIG_PATHS = [os.path.join(BASE_DIR, "wecom_config.json")]
WECOM_CONFIG_PATHS.extend(LOCAL_CONFIG.get("wecom_config_paths", []))
WECOM_WEBHOOK_URL_TEMPLATE = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"



# Search config
SEARCHES = [
    ("银行满减优惠活动", "search_result_1.json"),
    ("银行信用卡支付立减", "search_result_2.json"),
    ("银行活动羊毛攻略2026", "search_result_3.json"),
    ("银行立减金活动汇总", "search_result_4.json"),
    ("中国银行立减金满减", "search_result_5.json"),
    ("工商银行立减金满减", "search_result_6.json"),
]
BATCH_SIZE = 3           # 每批搜索数量
SEARCH_DELAY_MIN = 10    # 单次搜索间隔最小（秒）
SEARCH_DELAY_MAX = 15    # 单次搜索间隔最大（秒）
BATCH_COOLDOWN = 30      # 批次间冷却（秒）


# ============================================================
# Helpers
# ============================================================
def banner(stage, msg):
    """Print a prominent stage banner."""
    print(f"\n{'='*60}", flush=True)
    print(f"  阶段 {stage}: {msg}", flush=True)
    print(f"{'='*60}", flush=True)






def ok(msg):
    print(f"✅ {msg}", flush=True)


def warn(msg):
    print(f"⚠️  {msg}", flush=True)


def get_wecom_webhook_key():
    """Get WeCom webhook key from env or local config files."""
    key = os.environ.get("WECOM_BOT_KEY", "").strip()
    if key:
        return key

    webhook_url = os.environ.get("WECOM_WEBHOOK_URL", "").strip()
    if "key=" in webhook_url:
        return webhook_url.split("key=", 1)[1].split("&", 1)[0].strip()

    key = str(LOCAL_CONFIG.get("wecom_webhook_key", "")).strip()
    if key:
        return key

    webhook_url = str(LOCAL_CONFIG.get("wecom_webhook_url", "")).strip()
    if "key=" in webhook_url:
        return webhook_url.split("key=", 1)[1].split("&", 1)[0].strip()

    for config_path in WECOM_CONFIG_PATHS:

        path = Path(config_path)
        if not path.exists():
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            key = config.get("webhook_key", "").strip()
            if key:
                return key
        except Exception as e:
            warn(f"读取企微配置失败: {path} ({e})")
    return ""


def send_wecom_payload(payload, label="企微通知"):
    """Send a raw payload to WeCom. Notification failure must not break the update pipeline."""
    key = get_wecom_webhook_key()
    if not key:
        warn("未配置企微机器人，跳过通知")
        return False

    url = WECOM_WEBHOOK_URL_TEMPLATE.format(key=key)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("errcode") == 0:
            ok(f"{label}已发送")
            return True
        warn(f"{label}失败: {result}")
    except urllib.error.URLError as e:
        warn(f"{label}网络失败: {e}")
    except Exception as e:
        warn(f"{label}异常: {e}")
    return False


def send_wecom_markdown(content):
    """Send a markdown message to WeCom."""
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }
    return send_wecom_payload(payload, "企微通知")


def send_wecom_image(image_path):
    """Send a local image file to WeCom robot as an image message."""
    path = Path(image_path)
    if not path.exists():
        warn(f"二维码图片不存在，跳过企微图片发送: {path}")
        return False

    try:
        image_bytes = path.read_bytes()
    except Exception as e:
        warn(f"读取二维码图片失败，跳过企微图片发送: {e}")
        return False

    if len(image_bytes) > 2 * 1024 * 1024:
        warn(f"二维码图片超过企微机器人2MB限制，跳过图片发送: {path}")
        return False

    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(image_bytes).decode("ascii"),
            "md5": hashlib.md5(image_bytes).hexdigest(),
        },
    }
    return send_wecom_payload(payload, "企微二维码图片")


def notify_failure(reason, hint=None, qr_path=None):

    """Notify update failure through WeCom."""
    lines = [
        "# 小红书银行活动看板更新失败",
        f"> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 原因：<font color=\"warning\">{reason}</font>",
    ]
    if qr_path:
        lines.append(f"> 二维码：`{qr_path}`")
        lines.append("> 请用小红书小号扫码后，重新运行 `python update_report.py`")
    if hint:
        lines.append(f"> 处理建议：{hint}")
    lines.append(f"> 线上看板：{REPORT_URL}")
    send_wecom_markdown("\n".join(lines))
    if qr_path:
        send_wecom_image(qr_path)


def notify_success(search_stats=None, report_stats=None, push_status=True):

    """Notify successful update through WeCom."""
    search_stats = search_stats or {}
    report_stats = report_stats or {}

    lines = [
        "# 小红书银行活动看板更新成功",
        f"> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if search_stats:
        lines.append(f"> 搜索：{search_stats.get('success', 0)}/{search_stats.get('total', 0)} 成功，失败 {search_stats.get('failed', 0)} 组")
    if report_stats:
        lines.append(f"> 唯一笔记：{report_stats.get('total_unique', '-') } 条")
        lines.append(f"> 入选看板：<font color=\"info\">{report_stats.get('bank_recent', '-') }</font> 条；近一周新增：{report_stats.get('new_notes', '-') } 条")
        lines.append(f"> 低赞过滤：{report_stats.get('filtered_low_likes', '-') } 条；超出60天过滤：{report_stats.get('filtered_out_window', '-') } 条")
    if push_status is True:
        push_text = "已推送"
    elif push_status is None:
        push_text = "无新变更，未重复推送"
    elif push_status == "skipped":
        push_text = "未推送（--no-push）"
    else:
        push_text = "推送未完成，请检查日志"
    lines.append(f"> GitHub Pages：{push_text}")
    lines.append(f"> 线上看板：{REPORT_URL}")
    send_wecom_markdown("\n".join(lines))



def fail(msg, hint=None, notify=True):
    """Print error, optionally notify, and exit with non-zero code."""
    print(f"\n❌ 错误: {msg}", flush=True)
    if hint:
        print(f"   👉 {hint}", flush=True)
    if notify:
        notify_failure(msg, hint)
    sys.exit(1)


def check_port(host, port, timeout=3):
    """Check if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def start_mcp_service():
    """Try to start Xiaohongshu MCP in the background."""
    if not MCP_EXE:
        warn("未配置 MCP 可执行文件路径，请在 config.local.json 设置 mcp_exe 或设置 XHS_MCP_EXE 环境变量")
        return False
    if not os.path.exists(MCP_EXE):
        warn(f"MCP 可执行文件不存在: {MCP_EXE}")
        return False


    try:
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": os.path.dirname(MCP_EXE),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([MCP_EXE], **kwargs)
        for _ in range(12):
            time.sleep(1)
            if check_port(MCP_HOST, MCP_PORT, timeout=1):
                return True
    except Exception as e:
        warn(f"自动拉起 MCP 失败: {e}")
    return False



# ============================================================
# Stage 1: Check MCP service
# ============================================================
def stage_check_mcp():
    banner(1, "检查 MCP 服务")
    if check_port(MCP_HOST, MCP_PORT):
        ok("MCP 服务已运行 (端口 18060)")
        return True

    warn("MCP 服务未运行，尝试自动拉起...")
    if start_mcp_service():
        ok("MCP 服务已自动拉起 (端口 18060)")
        return True

    fail(
        "MCP 服务未运行（端口 18060 无法连接）",
        f"已尝试自动拉起但失败，请手动启动 MCP 服务：\n       {MCP_EXE}"
    )



# ============================================================
# Stage 2: Check login status
# ============================================================
def init_mcp_session():
    """Initialize MCP session and return headers with Mcp-Session-Id."""
    import requests

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"
    }
    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "workbuddy", "version": "1.0"}
        }
    }, headers=headers, timeout=15)

    session_id = resp.headers.get("Mcp-Session-Id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    requests.post(MCP_URL, json={
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }, headers=headers, timeout=10)
    return headers


def call_mcp_tool(headers, tool_name, arguments=None, timeout=60, request_id=2):
    """Call an MCP tool and return parsed JSON response."""
    import requests

    resp = requests.post(MCP_URL, json={
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}}
    }, headers=headers, timeout=timeout)
    return resp.json()


def generate_login_qrcode(headers):
    """Generate Xiaohongshu login QR code through MCP and save it locally."""
    print("  登录已过期，正在生成二维码...", flush=True)
    data = call_mcp_tool(headers, "get_login_qrcode", {}, timeout=60, request_id=3)
    content = data.get("result", {}).get("content", [])
    text_lines = []

    for item in content:
        if item.get("type") == "text":
            text = item.get("text", "")
            if text:
                text_lines.append(text[:500])
            b64 = ""
            if text.startswith("data:image") and "," in text:
                b64 = text.split(",", 1)[1]
            elif "base64" in text.lower():
                b64 = text.split(",", 1)[-1]
            if b64:
                try:
                    with open(QR_PATH, "wb") as f:
                        f.write(base64.b64decode(b64))
                    ok(f"二维码已保存: {QR_PATH}")
                    return QR_PATH, "\n".join(text_lines)
                except Exception:
                    pass
        elif item.get("type") == "image" and item.get("data"):
            with open(QR_PATH, "wb") as f:
                f.write(base64.b64decode(item["data"]))
            ok(f"二维码已保存: {QR_PATH}")
            return QR_PATH, "\n".join(text_lines)

    fail(
        "登录已过期，且二维码生成失败",
        f"get_login_qrcode 返回异常: {json.dumps(data, ensure_ascii=False)[:300]}"
    )


def stage_check_login():
    banner(2, "检查小红书登录状态")

    print("  初始化 MCP 会话...", flush=True)
    try:
        headers = init_mcp_session()
    except Exception as e:
        fail(f"MCP 会话初始化失败: {e}", "检查 MCP 服务是否正常运行")

    print("  调用 check_login_status...", flush=True)
    try:
        data = call_mcp_tool(headers, "check_login_status", {}, timeout=30, request_id=2)

        if "result" in data:
            content = data["result"].get("content", [])
            text = ""
            for item in content:
                if item.get("type") == "text":
                    text = item["text"]
                    break

            # check_login_status 返回文本含 "logged in" 或中文"已登录"表示成功
            text_lower = text.lower()
            if "logged in" in text_lower or "已登录" in text_lower or "login status: true" in text_lower:
                ok("登录状态正常")
                print(f"  详情: {text[:200]}", flush=True)
                return headers  # Return headers with session for reuse

            qr_path, qr_text = generate_login_qrcode(headers)
            hint = (
                f"登录已过期，二维码已生成：{qr_path}\n"
                "       请用小红书小号扫码后，重新运行：python update_report.py"
            )
            if qr_text:
                print(f"  二维码提示: {qr_text[:300]}", flush=True)
            print(f"  二维码路径: {qr_path}", flush=True)
            notify_failure("小红书登录已过期，需要扫码", hint, qr_path=qr_path)
            fail("未登录或登录已过期", hint, notify=False)
        elif "error" in data:
            fail(f"check_login_status 返回错误: {data['error']}")
        else:
            fail(f"check_login_status 返回异常: {json.dumps(data, ensure_ascii=False)[:300]}")

    except SystemExit:
        raise
    except Exception as e:
        fail(f"检查登录状态失败: {e}")



# ============================================================
# Stage 3: Search (batched, with random delays)
# ============================================================
def stage_search(mcp_headers):
    banner(3, "分批搜索小红书")
    import requests

    session = requests.Session()
    success_count = 0
    fail_count = 0
    total = len(SEARCHES)

    # Split into batches
    batches = []
    for i in range(0, total, BATCH_SIZE):
        batches.append(SEARCHES[i:i + BATCH_SIZE])

    for batch_idx, batch in enumerate(batches):
        if batch_idx > 0:
            cooldown = BATCH_COOLDOWN + random.randint(0, 10)
            print(f"\n  ⏳ 批次间冷却 {cooldown} 秒...", flush=True)
            time.sleep(cooldown)

        print(f"\n  --- 第 {batch_idx+1}/{len(batches)} 批 ({len(batch)} 组) ---", flush=True)

        for j, (keyword, filename) in enumerate(batch):
            global_idx = batch_idx * BATCH_SIZE + j + 1
            outpath = os.path.join(BASE_DIR, filename)
            print(f"\n  [{global_idx}/{total}] 搜索: {keyword}", flush=True)

            try:
                resp = session.post(MCP_URL, json={
                    "jsonrpc": "2.0",
                    "id": global_idx + 10,
                    "method": "tools/call",
                    "params": {"name": "search_feeds", "arguments": {"keyword": keyword}}
                }, headers=mcp_headers, timeout=90)

                data = resp.json()

                if "result" in data:
                    content = data["result"].get("content", [])
                    for item in content:
                        if item.get("type") == "text":
                            text = item["text"]
                            with open(outpath, "w", encoding="utf-8") as f:
                                f.write(text)
                            print(f"    ✅ 成功, {len(text)} bytes → {filename}", flush=True)
                            success_count += 1
                            break
                    else:
                        print(f"    ⚠️ 响应中无 text 内容", flush=True)
                        fail_count += 1
                elif "error" in data:
                    err_msg = json.dumps(data["error"], ensure_ascii=False)[:200]
                    print(f"    ❌ 错误: {err_msg}", flush=True)
                    fail_count += 1
                else:
                    print(f"    ⚠️ 异常响应", flush=True)
                    fail_count += 1

            except Exception as e:
                print(f"    ❌ 异常: {e}", flush=True)
                fail_count += 1

            # Random delay between searches (except last one)
            if not (batch_idx == len(batches) - 1 and j == len(batch) - 1):
                delay = random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX)
                print(f"    ⏳ 等待 {delay:.1f} 秒...", flush=True)
                time.sleep(delay)

    print(f"\n  搜索完成: 成功 {success_count}/{total}, 失败 {fail_count}/{total}", flush=True)

    if fail_count > 0 and success_count == 0:
        fail("全部搜索失败", "可能登录已过期，请重新扫码登录后重试")
    elif fail_count > 0:
        warn(f"{fail_count} 组搜索失败，将使用已有数据继续生成报告")

    return {"success": success_count, "failed": fail_count, "total": total}



# ============================================================
# Stage 4: Generate report
# ============================================================
def parse_report_stats(output):
    """Parse key counters from generate_report.py stdout."""
    stats = {}
    patterns = {
        "total_unique": "Total unique notes:",
        "bank_recent": "Bank-related & recent",
        "filtered_low_likes": "Filtered out (low likes",
        "filtered_out_window": "Filtered out (outside rolling",
        "new_notes": "New notes (published",
        "top_note_likes": "Top note likes:",
    }

    for line in (output or "").splitlines():
        stripped = line.strip()
        for key, marker in patterns.items():
            if stripped.startswith(marker):
                raw_value = stripped.split(":", 1)[-1].strip().split()[0]
                try:
                    stats[key] = int(raw_value.replace(",", ""))
                except ValueError:
                    pass
    return stats


def stage_generate_report():

    banner(4, "生成看板 HTML")
    script = os.path.join(BASE_DIR, "generate_report.py")
    if not os.path.exists(script):
        fail(f"找不到 generate_report.py: {script}")

    result = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=BASE_DIR
    )

    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)

    if result.returncode != 0:
        fail("生成报告失败", "查看上方错误输出")

    report_stats = parse_report_stats(result.stdout)
    report_path = os.path.join(BASE_DIR, "bank_marketing_report.html")
    if os.path.exists(report_path):
        size = os.path.getsize(report_path)
        ok(f"报告已生成: {report_path} ({size:,} bytes)")
        return report_stats
    else:
        fail("报告文件未生成")



# ============================================================
# Stage 5: Git push
# ============================================================
def stage_git_push():
    banner(5, "推送到 GitHub")

    def run_git(*args, check=True):
        cmd = ["git"] + list(args)
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=BASE_DIR
        )
        if check and result.returncode != 0:
            print(f"  git {' '.join(args)} 失败:", flush=True)
            if result.stderr:
                print(f"  {result.stderr.strip()}", flush=True)
        return result

    # Add and commit on master
    print("  git add & commit (master)...", flush=True)
    run_git("add", "bank_marketing_report.html", check=False)
    for i in range(1, 7):
        run_git("add", f"search_result_{i}.json", check=False)

    commit_msg = f"update: bank marketing report {time.strftime('%Y-%m-%d %H:%M')}"
    commit_result = run_git("commit", "-m", commit_msg, check=False)

    if "nothing to commit" in (commit_result.stdout + commit_result.stderr):
        warn("没有新的变更需要提交")
        return None



    if commit_result.returncode != 0:
        fail("git commit 失败")

    ok("commit 完成")

    # Push master
    print("  推送 master...", flush=True)
    push_result = run_git("push", "origin", "master", check=False)
    if push_result.returncode == 0:
        ok("master 分支已推送")
    else:
        warn(f"master 推送失败: {push_result.stderr.strip()}")

    # Merge master → main, then push main (GitHub Pages deploys from main)
    print("  合并 master → main...", flush=True)
    run_git("checkout", "main", check=False)
    merge_result = run_git("merge", "master", "--no-edit", check=False)
    main_pushed = False
    if merge_result.returncode == 0:
        push_main = run_git("push", "origin", "main", check=False)
        if push_main.returncode == 0:
            ok("main 分支已推送（GitHub Pages 将自动部署）")
            main_pushed = True
        else:
            warn(f"main 推送失败: {push_main.stderr.strip()}")
    else:
        warn(f"合并到 main 失败: {merge_result.stderr.strip()}")

    # Switch back to master
    run_git("checkout", "master", check=False)

    print(f"\n  线上地址: {REPORT_URL}", flush=True)
    return main_pushed



# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="小红书银行营销看板一站式更新")
    parser.add_argument("--search-only", action="store_true", help="只执行搜索，不生成报告和推送")
    parser.add_argument("--report-only", action="store_true", help="跳过搜索，直接用现有数据生成报告并推送")
    parser.add_argument("--no-push", action="store_true", help="不执行 git 推送")
    args = parser.parse_args()

    print("🚀 小红书银行营销看板更新开始", flush=True)
    print(f"   工作目录: {BASE_DIR}", flush=True)
    print(f"   MCP 地址: {MCP_URL}", flush=True)

    search_stats = None
    report_stats = None
    pushed = "skipped" if args.no_push else None


    if args.report_only:
        print("   模式: --report-only（跳过搜索）", flush=True)
        report_stats = stage_generate_report()
        if not args.no_push:
            pushed = stage_git_push()
    elif args.search_only:
        print("   模式: --search-only（只搜索）", flush=True)
        stage_check_mcp()
        mcp_headers = stage_check_login()
        search_stats = stage_search(mcp_headers)
    else:
        # Full pipeline
        stage_check_mcp()
        mcp_headers = stage_check_login()
        search_stats = stage_search(mcp_headers)
        report_stats = stage_generate_report()
        if not args.no_push:
            pushed = stage_git_push()

    notify_success(search_stats, report_stats, push_status=pushed)


    print(f"\n{'='*60}", flush=True)
    print("🎉 全部完成！", flush=True)
    print(f"{'='*60}", flush=True)



if __name__ == "__main__":
    main()
