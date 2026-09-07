"""Generate bank marketing report HTML with clickable xiaohongshu links.

Time filter: rolling 60-day window based on note publish date (extracted from note ID).
Date is dynamically set to today's date at runtime.

Features:
- Publish date extracted from note ID (hex timestamp in first 8 chars)
- Direct note links (xiaohongshu.com/explore/{id}) instead of search page
- Quality filter: minimum likes threshold (MIN_LIKES)
- New note highlight: notes published within rolling 7 days marked with 🆕
- Focus banks: spotlight notes about key banks (FOCUS_BANKS) with ⭐
- Note summary: REMOVED (xiaohongshu anti-scraping blocks get_feed_detail, 2026-04-12 decision)
"""
import json
import os
import random
import re
import sys
import io
import time
import base64
from pathlib import Path
from datetime import date, datetime, timedelta

# Fix Windows console encoding for emoji/CJK
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# QR code generation (for scan-to-view original article in 小红书 app)
try:
    import qrcode as _qrcode_lib
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False


def make_qr_data_uri(url, size=140):
    """Generate a QR code as a base64 PNG data URI for embedding in HTML.
    Returns "" if qrcode lib is not available or generation fails.
    """
    if not _HAS_QRCODE:
        return ""
    try:
        qr = _qrcode_lib.QRCode(
            version=None,
            error_correction=_qrcode_lib.constants.ERROR_CORRECT_M,
            box_size=4,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print(f"  QR 生成失败 ({url[:30]}...): {e}", flush=True)
        return ""

BASE_DIR = str(Path(__file__).resolve().parent)
XHS_BASE = "https://www.xiaohongshu.com/explore/"

def build_note_url(note_id, xsec_token=None):
    """Build a Xiaohongshu note URL. Use xsec_token when available so that
    PC web can open the note directly without the QR-code interstitial.

    Without xsec_token, xhs now serves the "scan with app" QR page to
    desktop browsers. With the token, the request mimics an authenticated
    internal navigation and loads the note UI on PC.
    """
    if not xsec_token:
        return XHS_BASE + note_id
    from urllib.parse import quote
    return XHS_BASE + note_id + "?xsec_token=" + quote(xsec_token) + "&xsec_source=pc_search"

SEARCH_FILES = 6  # search_result_1.json .. search_result_6.json (also reads search_result_new_*)

# --- Quality filter ---
MIN_LIKES = 30  # Minimum likes to include a note

# --- Focus banks (highlighted with ⭐) ---
FOCUS_BANKS = {
    "中国银行": ["中国银行", "中行", "中银"],
    "工商银行": ["工商银行", "工行", "宇宙行"],
}

# --- Time filter config (rolling 60 days from note ID timestamp) ---
TODAY = date.today()
CURRENT_YEAR = TODAY.year
LOOKBACK_DAYS = 60
# Rolling range: today - 60 days through today (e.g. 2026-06-01 => 2026-04-02 ~ 2026-06-01)
DATE_START = TODAY - timedelta(days=LOOKBACK_DAYS)
DATE_END = TODAY



def note_id_to_date(note_id):
    """Extract publish date from xiaohongshu note ID (first 8 hex chars = unix timestamp)."""
    try:
        ts = int(note_id[:8], 16)
        return datetime.fromtimestamp(ts).date()
    except (ValueError, OSError):
        return None


def note_id_to_datestr(note_id):
    """Return formatted date string like '2026-03-01' from note ID."""
    d = note_id_to_date(note_id)
    return d.strftime("%Y-%m-%d") if d else ""


def is_recent_by_id(note_id):
    """Check if note was published within the rolling 60-day window."""
    d = note_id_to_date(note_id)
    if d is None:
        return False
    return DATE_START <= d <= DATE_END

def to_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0


# --- "New" definition: published within rolling 7 days ---
NEW_WINDOW_DAYS = 7
NEW_CUTOFF = TODAY - timedelta(days=NEW_WINDOW_DAYS)

def is_new_note(note_id):
    """Check if note was published within the rolling 7-day window."""
    d = note_id_to_date(note_id)
    if d is None:
        return False
    return NEW_CUTOFF <= d <= TODAY

# --- Title dedup: strip leading date prefix to merge re-posted series ---
# 例如 "2026.07.17 招行喊你薅羊毛" 和 "2026.07.10: 招行喊你薅羊毛" 归一化后一样
def normalize_title(title):
    """Strip leading date prefix (2026.07.08 / 7月8日 / 07.08 / 2026-07-08 / 2026年7月8日 等格式)."""
    s = re.sub(r'^\s*(20\d{2})[.\-/年](\d{1,2})[.\-/月](\d{1,2})\s*日?[:：、\-\s]*', '', title)
    s = re.sub(r'^\s*\d{1,2}\s*月\s*\d{1,2}\s*日?[:：、\-\s]*', '', s)
    return s.strip()

# --- Determine focus bank for a note ---
def get_focus_bank(title):
    """Return focus bank name if title matches, else None."""
    t = title.lower()
    for bank_name, aliases in FOCUS_BANKS.items():
        if any(alias in t for alias in aliases):
            return bank_name
    return None

# --- Extract content tags from title ---
BANK_NAMES = [
    ("建设银行", ["建设银行", "建行", "龙支付"]),
    ("招商银行", ["招商银行", "招行", "招商"]),
    ("工商银行", ["工商银行", "工行", "宇宙行"]),
    ("中国银行", ["中国银行", "中行", "中银"]),
    ("农业银行", ["农业银行", "农行"]),
    ("交通银行", ["交通银行", "交行"]),
    ("中信银行", ["中信银行", "中信"]),
    ("浦发银行", ["浦发银行", "浦发"]),
    ("光大银行", ["光大银行", "光大"]),
    ("民生银行", ["民生银行", "民生"]),
    ("兴业银行", ["兴业银行", "兴业"]),
    ("平安银行", ["平安银行", "平安"]),
    ("华夏银行", ["华夏银行", "华夏"]),
    ("邮储银行", ["邮储银行", "邮储", "邮政储蓄"]),
    ("浙商银行", ["浙商银行", "浙商"]),
    ("广发银行", ["广发银行", "广发"]),
]
ACTIVITY_TYPES = [
    ("立减金", ["立减金", "立减"]),
    ("满减", ["满减"]),
    ("返现", ["返现"]),
    ("月月刷", ["月月刷"]),
    ("开卡礼", ["开卡"]),
    ("达标奖励", ["达标", "消费达标", "资产提升"]),
    ("信用卡", ["信用卡"]),
    ("充值优惠", ["充值"]),
    ("还款", ["还款"]),
    ("云闪付", ["云闪付"]),
    ("银联", ["银联"]),
    ("支付优惠", ["支付"]),
]

BANK_SHORT_NAMES = {
    "建设银行": "建行",
    "招商银行": "招行",
    "工商银行": "工行",
    "中国银行": "中行",
    "农业银行": "农行",
    "交通银行": "交行",
    "中信银行": "中信",
    "浦发银行": "浦发",
    "光大银行": "光大",
    "民生银行": "民生",
    "兴业银行": "兴业",
    "平安银行": "平安",
    "华夏银行": "华夏",
    "邮储银行": "邮储",
    "浙商银行": "浙商",
    "广发银行": "广发",
}

def extract_tags(title):
    """Extract bank names and activity types from note title."""
    t = title.lower()
    banks = []
    for name, aliases in BANK_NAMES:
        if any(a in t for a in aliases):
            banks.append(name)
    activities = []
    for name, aliases in ACTIVITY_TYPES:
        if any(a in t for a in aliases):
            activities.append(name)
    amounts = re.findall(r'(\d+(?:\.\d+)?)\s*元', title)
    return banks, activities, amounts


# =====================================================
# Load all search results
# =====================================================
all_notes = {}
_search_files = [f"search_result_{i}.json" for i in range(1, SEARCH_FILES + 1)]
_search_files += [f"search_result_new_{i}.json" for i in range(1, SEARCH_FILES + 1)]

for _sf in _search_files:
    path = os.path.join(BASE_DIR, _sf)
    if not os.path.exists(path):
        continue
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                continue
            data = json.loads(content)
    except (json.JSONDecodeError, IOError):
        continue
    feeds = data.get("feeds") or data.get("data", {}).get("feeds", [])
    if isinstance(data, list):
        feeds = data
    for feed in feeds:
        if feed.get("modelType") != "note":
            continue
        fid = feed["id"]
        if fid not in all_notes:
            nc = feed.get("noteCard", {})
            user = nc.get("user", {})
            interact = nc.get("interactInfo", {})
            title_raw = nc.get("displayTitle", "").replace("\u200b", "")
            likes = to_int(interact.get("likedCount", "0"))
            publish_date = note_id_to_datestr(fid)
            xsec_token = feed.get("xsecToken", "")
            note_url = build_note_url(fid, xsec_token)
            all_notes[fid] = {
                "id": fid,
                "title": title_raw,
                "author": user.get("nickname", "Unknown"),
                "likes": likes,
                "collects": to_int(interact.get("collectedCount", "0")),
                "comments": to_int(interact.get("commentCount", "0")),
                "shares": to_int(interact.get("sharedCount", "0")),
                "url": note_url,
                "xsec_token": xsec_token,
                "qr_data_uri": make_qr_data_uri(note_url),
                "type": nc.get("type", "normal"),
                "is_new": is_new_note(fid),
                "focus_bank": get_focus_bank(title_raw),
                "publish_date": publish_date,
                "tags": extract_tags(title_raw),
            }

# Sort by likes descending
notes = sorted(all_notes.values(), key=lambda x: x["likes"], reverse=True)

# Title dedup: 归一化（去日期前缀）后相同的标题只保留最新发布的一条
# 例: "2026.07.17 招行喊你薅羊毛" + "2026.07.10 招行喊你薅羊毛" → 保留 07.17
from collections import OrderedDict
_deduped = OrderedDict()
for _n in notes:
    _orig = _n["title"]
    _norm = normalize_title(_orig)
    _key = _norm if _norm != _orig else _orig  # 无日期前缀的不归一化
    if _key not in _deduped or _n["publish_date"] > _deduped[_key]["publish_date"]:
        _deduped[_key] = _n
notes = sorted(_deduped.values(), key=lambda x: x["likes"], reverse=True)

# Filter: bank-related AND recent (rolling 60 days) AND minimum likes
def is_bank_related(note):
    title = note["title"].lower()
    keywords = ["银行", "立减", "信用卡", "满减", "支付", "羊毛", "返现", "活动汇总", "月月刷", "充值",
                 "开卡", "优惠", "建行", "招行", "工行", "中行", "农行", "交行", "中信", "浦发",
                 "邮储", "光大", "浙商", "民生", "兴业", "华夏", "平安", "etc", "薅", "还款",
                 "银联", "资产提升", "消费达标", "龙支付", "云闪付"]
    return any(k in title for k in keywords)

bank_notes = [n for n in notes if is_bank_related(n) and is_recent_by_id(n["id"]) and n["likes"] >= MIN_LIKES]
filtered_low_likes = [n for n in notes if is_bank_related(n) and is_recent_by_id(n["id"]) and n["likes"] < MIN_LIKES]
filtered_out = [n for n in notes if is_bank_related(n) and not is_recent_by_id(n["id"])]

# 动态统计：银行频次（所有 bank_notes 里出现过的银行+次数）
_bank_counter = {}
for n in bank_notes:
    for b in n["tags"][0]:
        _bank_counter[b] = _bank_counter.get(b, 0) + 1
_top_banks = sorted(_bank_counter.items(), key=lambda x: -x[1])[:4]
top_banks_text = "、".join(BANK_SHORT_NAMES.get(b, b) for b, _ in _top_banks) if _top_banks else "（暂无数据）"

# 动态统计：玩法频次（从 ACTIVITY_TYPES 提取）
_activity_counter = {}
for n in bank_notes:
    for a in n["tags"][1]:
        _activity_counter[a] = _activity_counter.get(a, 0) + 1
_top_activities = sorted(_activity_counter.items(), key=lambda x: -x[1])[:3]
top_activities_text = "、".join(a for a, _ in _top_activities) if _top_activities else "（暂无数据）"




# Stats
new_count = sum(1 for n in bank_notes if n["is_new"])
focus_count = sum(1 for n in bank_notes if n["focus_bank"])
focus_bank_counts = {}
for n in bank_notes:
    if n["focus_bank"]:
        focus_bank_counts[n["focus_bank"]] = focus_bank_counts.get(n["focus_bank"], 0) + 1

# =====================================================
# Note detail fetching (embedded full-text modal, 2026-09-07)
# xhs now forces a login modal on PC web even with xsec_token.
# Workaround: fetch note detail anonymously via the token URL at
# generation time (server-side HTML contains full noteDetailMap),
# embed desc + images into the report, view in an in-page modal.
# Images use ci.xiaohongshu.com/{fileId} — long-lived & hotlinkable
# (sns-webpic-*.xhscdn.com URLs 403 after a few weeks).
# =====================================================
NO_DETAILS = "--no-details" in sys.argv
DETAILS_CACHE_PATH = os.path.join(BASE_DIR, "note_details.json")

def fetch_note_detail(note_id, xsec_token):
    """Fetch note detail anonymously. Returns dict or None."""
    import requests as _rq
    url = build_note_url(note_id, xsec_token)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    r = _rq.get(url, headers=headers, timeout=20)
    if r.status_code != 200 or "noteDetailMap" not in r.text:
        return None
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        return None
    st = json.loads(m.group(1).replace("undefined", "null"))
    nd = st.get("note", {}).get("noteDetailMap", {})
    entry = nd.get(note_id) or (next(iter(nd.values())) if nd else None)
    if not entry:
        return None
    note = entry.get("note", {})
    images = []
    for img in note.get("imageList", [])[:9]:
        fid_img = img.get("fileId")
        if fid_img:
            images.append("https://ci.xiaohongshu.com/" + fid_img)
    if not images:
        cov_fid = (note.get("cover") or {}).get("fileId")
        if cov_fid:
            images.append("https://ci.xiaohongshu.com/" + cov_fid)
    tags = [t.get("name", "") for t in note.get("tagList", []) if t.get("name")]
    ts = note.get("time")
    pub = ""
    if ts:
        try:
            pub = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            pub = ""
    return {
        "desc": note.get("desc", ""),
        "images": images,
        "tags": tags[:8],
        "ip": note.get("ipLocation", ""),
        "pub": pub,
        "type": note.get("type", "normal"),
    }

note_details = {}
if os.path.exists(DETAILS_CACHE_PATH):
    try:
        with open(DETAILS_CACHE_PATH, "r", encoding="utf-8") as f:
            note_details = json.load(f)
    except (json.JSONDecodeError, IOError):
        note_details = {}
# Prune cache to notes in the current report
_current_ids = {n["id"] for n in bank_notes}
note_details = {k: v for k, v in note_details.items() if k in _current_ids}

_fetch_ok = _fetch_fail = 0
if not NO_DETAILS:
    _todo = [n for n in bank_notes if n["id"] not in note_details and n.get("xsec_token")]
    if _todo:
        print(f"Fetching note details: {len(_todo)} to fetch ({len(note_details)} cached)...", flush=True)
    for n in _todo:
        try:
            d = fetch_note_detail(n["id"], n["xsec_token"])
        except Exception as e:
            d = None
            print(f"  detail fetch error {n['id'][:12]}: {e}", flush=True)
        if d:
            note_details[n["id"]] = d
            _fetch_ok += 1
        else:
            _fetch_fail += 1
            print(f"  detail fetch FAILED {n['id'][:12]} ({n['title'][:24]})", flush=True)
        time.sleep(random.uniform(1.2, 2.5))
    try:
        with open(DETAILS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(note_details, f, ensure_ascii=False, indent=1)
    except IOError as e:
        print(f"  警告: note_details.json 写入失败: {e}", flush=True)
print(f"Note details embedded: {len(note_details)}/{len(bank_notes)} "
      f"(fetched {_fetch_ok}, failed {_fetch_fail})")

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def fmt_num(n):
    if n >= 10000:
        return f"{n/10000:.1f}w"
    elif n >= 1000:
        return f"{n:,}"
    return str(n)

# Build embedded detail payload for the in-page modal (merge card meta + detail)
_embed = {}
for n in bank_notes:
    d = note_details.get(n["id"])
    if not d:
        continue
    _embed[n["id"]] = {
        "t": n["title"],
        "a": n["author"],
        "url": n["url"],
        "likes": n["likes"], "collects": n["collects"],
        "comments": n["comments"], "shares": n["shares"],
        "desc": d["desc"], "imgs": d["images"], "tags": d["tags"],
        "ip": d["ip"], "pub": d["pub"], "vtype": d["type"],
    }
_embed_json = json.dumps(_embed, ensure_ascii=False).replace("</", "<\\/")

# Generate HTML
html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<title>小红书 · 银行营销活动搜索报告</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f5f5; color: #333; line-height: 1.6; }
  .header { background: linear-gradient(135deg, #ff2442 0%, #ff6b81 100%); color: #fff; padding: 40px 20px; text-align: center; }
  .header h1 { font-size: 28px; margin-bottom: 8px; }
  .header p { opacity: 0.9; font-size: 14px; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .summary-card { background: #fff; border-radius: 12px; padding: 20px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .summary-card .num { font-size: 36px; font-weight: 700; color: #ff2442; }
  .summary-card .label { font-size: 13px; color: #999; margin-top: 4px; }
  .section { margin-bottom: 36px; }
  .section-title { font-size: 20px; font-weight: 700; margin-bottom: 16px; padding-left: 12px; border-left: 4px solid #ff2442; }
  .note-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
  .note-card { background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: transform .2s, box-shadow .2s; position: relative; }
  .note-card:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.1); }
  .note-card .title { font-size: 16px; font-weight: 600; margin-bottom: 8px; line-height: 1.4; }
  .note-card .title a { color: #333; text-decoration: none; }
  .note-card .title a:hover { color: #ff2442; text-decoration: underline; }
  .note-card .author { font-size: 13px; color: #666; margin-bottom: 8px; }
  .note-card .stats { display: flex; gap: 16px; flex-wrap: wrap; }
  .note-card .stat { font-size: 12px; color: #999; }
  .note-card .stat span { color: #ff2442; font-weight: 600; font-size: 14px; }
  .note-card .rank { position: absolute; top: 12px; right: 12px; background: #ff2442; color: #fff; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
  .note-card .rank.top3 { background: linear-gradient(135deg, #ff2442, #ff6b81); }
  .note-card .rank.top10 { background: #ff8c00; }
  .note-card .rank.normal { background: #ddd; color: #666; }
  .note-card .link-btn { display: inline-block; margin-top: 12px; padding: 4px 14px; background: #ff2442; color: #fff; border-radius: 20px; font-size: 12px; text-decoration: none; transition: background .2s; }
  .note-card .link-btn:hover { background: #e0203a; }
  .note-card .qr-wrap { position: absolute; top: 12px; left: 12px; padding: 4px; background: #fff; border: 1px solid #eee; border-radius: 6px; line-height: 0; cursor: help; }
  .note-card .qr-img { display: block; width: 56px; height: 56px; image-rendering: pixelated; }
  .note-card .qr-wrap:hover { transform: scale(1.5); transition: transform .2s; z-index: 10; box-shadow: 0 4px 16px rgba(0,0,0,0.2); border-color: #ff2442; }
  .content-tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .content-tag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
  .content-tag.bank { background: #e3f2fd; color: #1565c0; border: 1px solid #bbdefb; }
  .content-tag.activity { background: #fff3e0; color: #e65100; border: 1px solid #ffe0b2; }
  .content-tag.amount { background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
  .toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); color: #fff; padding: 10px 24px; border-radius: 8px; font-size: 14px; z-index: 9999; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: 1; }
  .xhs-tip { background: #fff3e0; border: 1px solid #ffe0b2; border-radius: 8px; padding: 10px 16px; margin-bottom: 16px; font-size: 13px; color: #e65100; }
  .xhs-tip strong { color: #bf360c; }
  .footer { text-align: center; padding: 24px; color: #999; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  th { background: #ff2442; color: #fff; padding: 12px 16px; text-align: left; font-size: 13px; }
  td { padding: 12px 16px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
  tr:hover td { background: #fff8f9; }
  td a { color: #ff2442; text-decoration: none; }
  td a:hover { text-decoration: underline; }
  .highlight { background: #fff9e6; border: 1px solid #ffe58f; border-radius: 8px; padding: 16px; margin-bottom: 24px; }
  .highlight h3 { color: #d48806; margin-bottom: 8px; }
  .bank-tag { display: inline-block; background: #fff0f2; color: #ff2442; padding: 2px 10px; border-radius: 20px; font-size: 12px; margin-right: 6px; margin-bottom: 6px; border: 1px solid #ffe0e5; }
  .bank-tags { margin-bottom: 10px; }
  .new-badge { display: inline-block; background: #ff2442; color: #fff; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-left: 6px; vertical-align: middle; animation: pulse 2s infinite; }
  .focus-badge { display: inline-block; background: linear-gradient(135deg, #ffd700, #ffb300); color: #7c5800; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; margin-left: 6px; vertical-align: middle; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }
  .focus-section { background: linear-gradient(135deg, #fffbeb 0%, #fff7d6 100%); border: 1px solid #fde68a; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
  .focus-section h3 { color: #92400e; margin-bottom: 12px; font-size: 18px; }
  .focus-section .note-grid { gap: 12px; }
  .focus-card { background: #fff; border-radius: 10px; padding: 16px; border-left: 4px solid #f59e0b; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
  .focus-card .title { font-size: 14px; font-weight: 600; margin-bottom: 6px; }
  .focus-card .title a { color: #333; text-decoration: none; }
  .focus-card .title a:hover { color: #ff2442; }
  .focus-card .stats { font-size: 12px; color: #999; }
  .focus-card .stats span { color: #f59e0b; font-weight: 600; }
  .stat-row { display: flex; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .stat-pill { background: #fff; border-radius: 20px; padding: 6px 16px; font-size: 13px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  .stat-pill strong { color: #ff2442; }
  .bank-tags { margin-bottom: 10px; }
  .show-more-btn { display: block; width: 100%; padding: 12px; background: #fff; border: 2px dashed #ff2442; border-radius: 8px; color: #ff2442; font-size: 14px; font-weight: 600; cursor: pointer; transition: all .2s; margin-top: 16px; }
  .show-more-btn:hover { background: #fff0f2; }
  .show-more-btn:disabled { display: none; }
  .hidden-row { display: none; }
  .td-summary { font-size: 12px; color: #888; margin-top: 4px; line-height: 1.4; }
  /* ---- 笔记详情弹窗（免登录全文） ---- */
  .link-btn.secondary { background: #fff; color: #ff2442; border: 1px solid #ff2442; margin-left: 8px; }
  .link-btn.secondary:hover { background: #fff0f2; }
  .modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.55); z-index: 10000; display: none; align-items: center; justify-content: center; padding: 20px; }
  .modal-mask.open { display: flex; }
  .modal-box { background: #fff; border-radius: 14px; max-width: 680px; width: 100%; max-height: 90vh; overflow-y: auto; padding: 24px 28px; position: relative; box-shadow: 0 12px 48px rgba(0,0,0,0.3); }
  .modal-close { position: absolute; top: 12px; right: 14px; width: 32px; height: 32px; border-radius: 50%; border: none; background: #f2f2f2; font-size: 16px; cursor: pointer; color: #666; z-index: 2; }
  .modal-close:hover { background: #ffe0e5; color: #ff2442; }
  .modal-title { font-size: 18px; font-weight: 700; margin-bottom: 6px; padding-right: 36px; line-height: 1.4; }
  .modal-meta { font-size: 12px; color: #999; margin-bottom: 14px; }
  .modal-imgs { position: relative; margin-bottom: 14px; text-align: center; background: #fafafa; border-radius: 10px; }
  .modal-imgs img { max-width: 100%; max-height: 52vh; border-radius: 10px; display: block; margin: 0 auto; }
  .carousel-btn { position: absolute; top: 50%; transform: translateY(-50%); width: 34px; height: 34px; border-radius: 50%; border: none; background: rgba(0,0,0,0.45); color: #fff; font-size: 16px; cursor: pointer; }
  .carousel-btn:hover { background: rgba(255,36,66,0.85); }
  .carousel-btn.prev { left: 8px; }
  .carousel-btn.next { right: 8px; }
  .carousel-idx { position: absolute; bottom: 8px; right: 10px; background: rgba(0,0,0,0.5); color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; }
  .modal-desc { font-size: 14px; line-height: 1.8; white-space: pre-wrap; word-break: break-word; margin-bottom: 14px; }
  .modal-tags { margin-bottom: 14px; }
  .modal-tags .htag { display: inline-block; color: #1565c0; font-size: 13px; margin-right: 10px; }
  .modal-stats { display: flex; gap: 18px; font-size: 12px; color: #999; border-top: 1px solid #f0f0f0; padding-top: 12px; flex-wrap: wrap; align-items: center; }
  .modal-stats span { color: #ff2442; font-weight: 600; }
  .modal-orig { margin-left: auto; font-size: 12px; color: #ff2442; text-decoration: none; }
  .modal-orig:hover { text-decoration: underline; }
</style>
</head>
<body>

<div class="header">
  <h1>小红书 · 银行营销活动搜索报告</h1>
  <p>搜索时间：""" + f"{TODAY.year}年{TODAY.month}月{TODAY.day}日" + """ | 数据来源：小红书 | 发帖时间：""" + f"{DATE_START.year}年{DATE_START.month}月{DATE_START.day}日 - {TODAY.year}年{TODAY.month}月{TODAY.day}日" + """ | 💡 原文链接需在小红书App或已登录浏览器中打开</p>
</div>

<div class="container">

  <div class="summary">
    <div class="summary-card">
      <div class="num">""" + str(len(bank_notes)) + """</div>
      <div class="label">近60天银行活动（≥""" + str(MIN_LIKES) + """赞）</div>
    </div>
    <div class="summary-card">
      <div class="num" style="color:#52c41a">""" + str(new_count) + """</div>
      <div class="label">🆕 近一周新发 """ + f"({new_count*100//len(bank_notes) if bank_notes else 0}%)" + """</div>
    </div>
    <div class="summary-card">
      <div class="num">""" + fmt_num(bank_notes[0]["likes"] if bank_notes else 0) + """</div>
      <div class="label">单篇最高点赞</div>
    </div>
  </div>

  <div class="highlight">
    <h3>核心发现</h3>
    <ul style="margin:0; padding-left:20px; line-height:2;">
      <li>📅 <strong>数据范围</strong>：""" + f"{DATE_START.year}年{DATE_START.month}月{DATE_START.day}日 — {DATE_END.year}年{DATE_END.month}月{DATE_END.day}日" + """（滚动60天），基于笔记实际发帖时间精确筛选（非标题推断）</li>
      <li>🆕 <strong>近一周新发</strong>：<strong>""" + str(new_count) + """</strong> 条笔记发帖于近7天内（""" + f"{NEW_CUTOFF.month}月{NEW_CUTOFF.day}日 - {TODAY.month}月{TODAY.day}日" + """），标记为 <span class="new-badge">NEW</span></li>
      <li>📊 <strong>质量筛选</strong>：仅展示 ≥""" + str(MIN_LIKES) + """ 赞的笔记，已过滤 """ + str(len(filtered_low_likes)) + """ 条低赞内容</li>
      <li>🏦 <strong>热门银行</strong>：<strong>""" + top_banks_text + """</strong>讨论度最高</li>
      <li>🎯 <strong>主流玩法</strong>：<strong>""" + top_activities_text + """</strong>为本周热门形式</li>
      <li>🔗 <strong>使用方式</strong>：卡片展示封面和互动数据，点击标题可跳转原文</li>
    </ul>
  </div>

  <div class="xhs-tip">
    💡 <strong>使用提示</strong>：点击「📖 查看全文」在报告内直接阅读完整笔记（正文+图片，<strong>免登录</strong>）；「原帖 ↗」跳转小红书（可能弹登录框）；卡片左上角二维码可用 App 扫码互动
  </div>

  <!-- ==================== TOP 热门笔记 ==================== -->
  <div class="section">
    <h2 class="section-title">TOP 热门笔记（按点赞排序）</h2>
    <div class="note-grid">
"""

top_notes = bank_notes[:15]
for i, note in enumerate(top_notes, 1):
    rank_class = "top3" if i <= 3 else ("top10" if i <= 10 else "normal")
    new_tag = ' <span class="new-badge">NEW</span>' if note["is_new"] else ""
    focus_tag = ""
    if note["focus_bank"]:
        short = BANK_SHORT_NAMES.get(note["focus_bank"], note["focus_bank"])
        focus_tag = f' <span class="focus-badge">🏦 {short}</span>'
    # Content tags
    banks, activities, amounts = note["tags"]
    tag_html = ""
    tag_items = []
    for b in banks[:2]:
        tag_items.append(f'<span class="content-tag bank">{esc(b)}</span>')
    for a in activities[:2]:
        tag_items.append(f'<span class="content-tag activity">{esc(a)}</span>')
    for amt in amounts[:2]:
        tag_items.append(f'<span class="content-tag amount">{esc(amt)}元</span>')
    if tag_items:
        tag_html = f'<div class="content-tags">{"".join(tag_items)}</div>'
    html += f"""
      <div class="note-card">
        <div class="rank {rank_class}">{i}</div>
        {f'<div class="qr-wrap" title="用小红书App扫码查看原文"><img class="qr-img" src="{esc(note["qr_data_uri"])}" alt="QR"></div>' if note.get("qr_data_uri") else ""}
        <div class="title"><a href="{esc(note['url'])}" target="_blank">{esc(note['title'])}</a>{new_tag}{focus_tag}</div>
        <div class="author">作者：{esc(note['author'])} | 📅 {note['publish_date']}</div>
        {tag_html}
        <div class="stats">
          <div class="stat">点赞 <span>{fmt_num(note['likes'])}</span></div>
          <div class="stat">收藏 <span>{fmt_num(note['collects'])}</span></div>
          <div class="stat">评论 <span>{fmt_num(note['comments'])}</span></div>
          <div class="stat">分享 <span>{fmt_num(note['shares'])}</span></div>
        </div>
        <a class="link-btn" href="{esc(note['url'])}" target="_blank">查看原文 &rarr;</a>
      </div>
"""

html += """
    </div>
  </div>

  <!-- ==================== 全部笔记列表 ==================== -->
  <div class="section">
    <h2 class="section-title">全部银行活动笔记列表（≥""" + str(MIN_LIKES) + """赞）</h2>
    <table>
      <thead>
        <tr>
          <th style="width:40px">#</th>
          <th>标题</th>
          <th style="width:90px">发帖日期</th>
          <th style="width:100px">标签</th>
          <th style="width:120px">作者</th>
          <th style="width:65px">点赞</th>
          <th style="width:65px">收藏</th>
          <th style="width:65px">评论</th>
          <th style="width:65px">分享</th>
        </tr>
      </thead>
      <tbody>
"""

VISIBLE_ROWS = 15
hidden_count = max(0, len(bank_notes) - VISIBLE_ROWS)

for i, note in enumerate(bank_notes, 1):
    row_class = ' class="hidden-row"' if i > VISIBLE_ROWS else ''
    tags = ""
    if note["is_new"]:
        tags += '<span class="new-badge">NEW</span> '
    note_banks = note["tags"][0]
    for bk in note_banks[:2]:
        short = BANK_SHORT_NAMES.get(bk, bk)
        tags += f'<span class="focus-badge">🏦 {short}</span> '
    # Title in table
    title_cell = f'<a href="javascript:void(0)" onclick="openNote(\'{note["id"]}\')">{esc(note["title"])}</a>'
    html += f"""        <tr{row_class}>
          <td>{i}</td>
          <td>{title_cell}</td>
          <td>{note['publish_date']}</td>
          <td>{tags}</td>
          <td>{esc(note['author'])}</td>
          <td>{fmt_num(note['likes'])}</td>
          <td>{fmt_num(note['collects'])}</td>
          <td>{fmt_num(note['comments'])}</td>
          <td>{fmt_num(note['shares'])}</td>
        </tr>
"""

html += """      </tbody>
    </table>
"""

if hidden_count > 0:
    html += f"""    <button class="show-more-btn" id="showMoreBtn" onclick="showAllRows()">展开更多（还有 {hidden_count} 条）</button>
"""

html += """  </div>

</div>

<div class="footer">
  <p>报告由 WorkBuddy 通过小红书 MCP 自动生成 | 数据仅供参考，具体活动以银行官方公告为准</p>
</div>

<!-- 笔记详情弹窗（免登录全文，数据在生成时匿名抓取嵌入） -->
<div class="modal-mask" id="noteModal" onclick="if(event.target===this)closeNote()">
  <div class="modal-box">
    <button class="modal-close" onclick="closeNote()">✕</button>
    <div class="modal-title" id="mTitle"></div>
    <div class="modal-meta" id="mMeta"></div>
    <div class="modal-imgs" id="mImgs" style="display:none">
      <img id="mImg" src="" alt="笔记图片">
      <button class="carousel-btn prev" id="mPrev" onclick="slideImg(-1)">‹</button>
      <button class="carousel-btn next" id="mNext" onclick="slideImg(1)">›</button>
      <div class="carousel-idx" id="mIdx"></div>
    </div>
    <div class="modal-desc" id="mDesc"></div>
    <div class="modal-tags" id="mTags"></div>
    <div class="modal-stats" id="mStats"></div>
  </div>
</div>

<script>
const NOTE_DETAILS = """ + _embed_json + """;
let _curNote = null, _curIdx = 0;

function openNote(id) {
    const d = NOTE_DETAILS[id];
    if (!d) { window.open('https://www.xiaohongshu.com/explore/' + id, '_blank'); return; }
    _curNote = d; _curIdx = 0;
    document.getElementById('mTitle').textContent = d.t;
    document.getElementById('mMeta').textContent =
        '作者：' + d.a + (d.pub ? ' | 📅 ' + d.pub : '') + (d.ip ? ' | 📍 ' + d.ip : '') + (d.vtype === 'video' ? ' | 🎬 视频笔记' : '');
    const imgsBox = document.getElementById('mImgs');
    if (d.imgs && d.imgs.length) {
        imgsBox.style.display = '';
        showImg();
    } else {
        imgsBox.style.display = 'none';
    }
    document.getElementById('mDesc').textContent = d.desc || '（无正文）';
    document.getElementById('mTags').innerHTML = (d.tags || []).map(t => '<span class="htag">#' + t + '</span>').join('');
    document.getElementById('mStats').innerHTML =
        '点赞 <span>' + d.likes + '</span>　收藏 <span>' + d.collects + '</span>　评论 <span>' + d.comments + '</span>　分享 <span>' + d.shares + '</span>' +
        '<a class="modal-orig" href="' + d.url + '" target="_blank">去小红书看原帖 ↗</a>';
    document.getElementById('noteModal').classList.add('open');
    document.body.style.overflow = 'hidden';
}
function showImg() {
    const imgs = _curNote.imgs;
    const img = document.getElementById('mImg');
    img.src = imgs[_curIdx];
    img.onerror = function() { img.onerror = null; img.alt = '图片加载失败，可点底部「去小红书看原帖」'; };
    document.getElementById('mIdx').textContent = (_curIdx + 1) + ' / ' + imgs.length;
    const multi = imgs.length > 1;
    document.getElementById('mPrev').style.display = multi ? '' : 'none';
    document.getElementById('mNext').style.display = multi ? '' : 'none';
}
function slideImg(step) {
    if (!_curNote) return;
    const n = _curNote.imgs.length;
    _curIdx = (_curIdx + step + n) % n;
    showImg();
}
function closeNote() {
    document.getElementById('noteModal').classList.remove('open');
    document.body.style.overflow = '';
    _curNote = null;
}
document.addEventListener('keydown', function(e) {
    if (!_curNote) return;
    if (e.key === 'Escape') closeNote();
    else if (e.key === 'ArrowLeft') slideImg(-1);
    else if (e.key === 'ArrowRight') slideImg(1);
});

function showAllRows() {
    const hiddenRows = document.querySelectorAll('.hidden-row');
    hiddenRows.forEach(row => row.classList.remove('hidden-row'));
    document.querySelector('.show-more-btn').style.display = 'none';
}
</script>

</body>
</html>
"""

output_path = os.path.join(BASE_DIR, "bank_marketing_report.html")
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
if _args:
    output_path = _args[0]
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nReport generated: {output_path}")
print(f"Total unique notes: {len(all_notes)}")
print(f"Bank-related & recent (>={MIN_LIKES} likes): {len(bank_notes)}")
print(f"Filtered out (low likes <{MIN_LIKES}): {len(filtered_low_likes)}")
print(f"Filtered out (outside rolling {LOOKBACK_DAYS}-day window): {len(filtered_out)}")
print(f"New notes (published {NEW_CUTOFF} ~ {TODAY}): {new_count}")
print(f"Focus bank notes: {focus_count} ({', '.join(f'{k}:{v}' for k,v in focus_bank_counts.items())})")
if bank_notes:
    print(f"Top note likes: {bank_notes[0]['likes']}")
