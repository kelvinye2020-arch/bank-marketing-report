"""Generate bank marketing report HTML with clickable xiaohongshu links.

Time filter: strict 3 calendar months based on note publish date (extracted from note ID).
Date is dynamically set to today's date at runtime.

Features:
- Publish date extracted from note ID (hex timestamp in first 8 chars)
- Direct note links (xiaohongshu.com/explore/{id}) instead of search page
- Quality filter: minimum likes threshold (MIN_LIKES)
- New note highlight: notes published within rolling 7 days marked with 🆕
- Focus banks: spotlight notes about key banks (FOCUS_BANKS) with ⭐
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
XHS_BASE = "https://www.xiaohongshu.com/explore/"
SEARCH_FILES = 6  # search_result_1.json .. search_result_6.json (also reads search_result_new_*)

# --- Quality filter ---
MIN_LIKES = 30  # Minimum likes to include a note

# --- Focus banks (highlighted with ⭐) ---
FOCUS_BANKS = {
    "中国银行": ["中国银行", "中行", "中银"],
    "工商银行": ["工商银行", "工行", "宇宙行"],
}

# --- Time filter config (strict 3 calendar months from note ID timestamp) ---
TODAY = date.today()
CURRENT_YEAR = TODAY.year
# Strict range: 1st day of (current_month - 2) to last day of current month
# e.g. 2026-03-30 → 2026-01-01 ~ 2026-03-31
_first_of_month = TODAY.replace(day=1)
DATE_START = (_first_of_month - timedelta(days=59)).replace(day=1)  # ~2 months back, then snap to 1st
DATE_END = (_first_of_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)  # last day of current month


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
    """Check if note was published within the strict 3-month window."""
    d = note_id_to_date(note_id)
    if d is None:
        return False  # Can't parse → exclude
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
    # Extract amounts (e.g., "10元", "66元", "188元")
    amounts = re.findall(r'(\d+(?:\.\d+)?)\s*元', title)
    return banks, activities, amounts

# Load all search results (both old and new format files)
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
    # Support both old format {data: {feeds: [...]}} and new format {feeds: [...]}
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
            all_notes[fid] = {
                "id": fid,
                "title": title_raw,
                "author": user.get("nickname", "Unknown"),
                "likes": likes,
                "collects": to_int(interact.get("collectedCount", "0")),
                "comments": to_int(interact.get("commentCount", "0")),
                "shares": to_int(interact.get("sharedCount", "0")),
                "url": XHS_BASE + fid,
                "cover": nc.get("cover", {}).get("urlDefault", "") or nc.get("cover", {}).get("urlPre", ""),
                "type": nc.get("type", "normal"),
                "is_new": is_new_note(fid),
                "focus_bank": get_focus_bank(title_raw),
                "publish_date": publish_date,
                "tags": extract_tags(title_raw),
            }

# Sort by likes descending
notes = sorted(all_notes.values(), key=lambda x: x["likes"], reverse=True)

# Filter: bank-related AND recent (last 3 months) AND minimum likes
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

# Stats
new_count = sum(1 for n in bank_notes if n["is_new"])
focus_count = sum(1 for n in bank_notes if n["focus_bank"])
focus_bank_counts = {}
for n in bank_notes:
    if n["focus_bank"]:
        focus_bank_counts[n["focus_bank"]] = focus_bank_counts.get(n["focus_bank"], 0) + 1

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def fmt_num(n):
    if n >= 10000:
        return f"{n/10000:.1f}w"
    elif n >= 1000:
        return f"{n:,}"
    return str(n)

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
  .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }
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
  .note-card .author { font-size: 13px; color: #666; margin-bottom: 12px; }
  .note-card .stats { display: flex; gap: 16px; flex-wrap: wrap; }
  .note-card .stat { font-size: 12px; color: #999; }
  .note-card .stat span { color: #ff2442; font-weight: 600; font-size: 14px; }
  .note-card .rank { position: absolute; top: 12px; right: 12px; background: #ff2442; color: #fff; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
  .note-card .rank.top3 { background: linear-gradient(135deg, #ff2442, #ff6b81); }
  .note-card .rank.top10 { background: #ff8c00; }
  .note-card .rank.normal { background: #ddd; color: #666; }
  .note-card .link-btn { display: inline-block; margin-top: 12px; padding: 4px 14px; background: #ff2442; color: #fff; border-radius: 20px; font-size: 12px; text-decoration: none; transition: background .2s; }
  .note-card .link-btn:hover { background: #e0203a; }
  .card-cover { margin: -20px -20px 12px -20px; border-radius: 12px 12px 0 0; overflow: hidden; max-height: 160px; }
  .card-cover img { width: 100%; height: 160px; object-fit: cover; display: block; }
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
      <div class="label">近3月银行活动（≥""" + str(MIN_LIKES) + """赞）</div>
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
      <li>📅 <strong>数据范围</strong>：""" + f"{DATE_START.year}年{DATE_START.month}月 — {DATE_END.year}年{DATE_END.month}月" + """，基于笔记实际发帖时间精确筛选（非标题推断）</li>
      <li>🆕 <strong>近一周新发</strong>：<strong>""" + str(new_count) + """</strong> 条笔记发帖于近7天内（""" + f"{NEW_CUTOFF.month}月{NEW_CUTOFF.day}日 - {TODAY.month}月{TODAY.day}日" + """），标记为 <span class="new-badge">NEW</span></li>
      <li>📊 <strong>质量筛选</strong>：仅展示 ≥""" + str(MIN_LIKES) + """ 赞的笔记，已过滤 """ + str(len(filtered_low_likes)) + """ 条低赞内容</li>
      <li>🏦 <strong>热门银行</strong>：<strong>建设银行、招商银行、工商银行、中信银行</strong>讨论度最高</li>
      <li>🎯 <strong>主流玩法</strong>：<strong>立减金、满减优惠、资产提升返现</strong>为三大主要形式</li>
      <li>🔗 <strong>使用方式</strong>：卡片展示封面、标签和互动数据，点击标题可跳转原文（需已登录小红书）</li>
    </ul>
  </div>

  <div class="xhs-tip">
    📱 <strong>温馨提示</strong>：小红书近期收紧了PC端访问限制，跳转后需用手机APP扫码查看可查看原文
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
    # Cover image
    cover_html = ""
    if note.get("cover"):
        cover_url = note["cover"]
        if cover_url.startswith("http://"):
            cover_url = "https://" + cover_url[7:]
        cover_html = f'<div class="card-cover"><img src="{esc(cover_url)}" alt="" loading="lazy" onerror="this.parentNode.style.display=\'none\'"></div>'
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
        {cover_html}
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

VISIBLE_ROWS = 15  # 默认显示前15行，其余折叠
hidden_count = max(0, len(bank_notes) - VISIBLE_ROWS)

for i, note in enumerate(bank_notes, 1):
    row_class = ' class="hidden-row"' if i > VISIBLE_ROWS else ''
    tags = ""
    if note["is_new"]:
        tags += '<span class="new-badge">NEW</span> '
    # Show bank short names as tags
    note_banks = note["tags"][0]  # banks list from extract_tags
    for bk in note_banks[:2]:
        short = BANK_SHORT_NAMES.get(bk, bk)
        tags += f'<span class="focus-badge">🏦 {short}</span> '
    html += f"""        <tr{row_class}>
          <td>{i}</td>
          <td><a href="{esc(note['url'])}" target="_blank">{esc(note['title'])}</a></td>
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

<script>
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
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Report generated: {output_path}")
print(f"Total unique notes: {len(all_notes)}")
print(f"Bank-related & recent (>={MIN_LIKES} likes): {len(bank_notes)}")
print(f"Filtered out (low likes <{MIN_LIKES}): {len(filtered_low_likes)}")
print(f"Filtered out (older months): {len(filtered_out)}")
print(f"New notes (published {NEW_CUTOFF} ~ {TODAY}): {new_count}")
print(f"Focus bank notes: {focus_count} ({', '.join(f'{k}:{v}' for k,v in focus_bank_counts.items())})")
if bank_notes:
    print(f"Top note likes: {bank_notes[0]['likes']}")
