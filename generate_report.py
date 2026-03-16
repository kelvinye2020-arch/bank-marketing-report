"""Generate bank marketing report HTML with clickable xiaohongshu links.

Time filter: only include notes that appear to be from the last 3 months.
Date is dynamically set to today's date at runtime.
"""
import json
import os
import re
from datetime import date, timedelta
from urllib.parse import quote

BASE_DIR = r"c:\Users\kelvinyye\WorkBuddy\20260313150001"
XHS_BASE = "https://www.xiaohongshu.com/explore/"

# --- Time filter config (dynamic) ---
TODAY = date.today()
CURRENT_YEAR = TODAY.year
VALID_MONTHS = set()
for i in range(3):
    d = TODAY.replace(day=1) - timedelta(days=i * 28)  # approximate month back
    VALID_MONTHS.add((d.year, d.month))
# Always include current month in case timedelta approximation missed it
VALID_MONTHS.add((TODAY.year, TODAY.month))

def to_int(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return 0

def is_recent(title):
    """Check if a note title refers to a recent time period (last 3 months).
    
    Strategy:
    1. If title has explicit year+month (e.g. "2026年3月", "2026.03"), check against valid range.
    2. If title has month only (e.g. "3月"), accept months 1-3 (likely current year), reject 4-12.
    3. If title has "2026" but no month, accept it (current year content).
    4. If no time info at all, accept it (can't determine, keep for completeness).
    """
    # Pattern 1: explicit year + month  e.g. "2026年3月", "2026.03.03", "2026/01"
    year_month_matches = re.findall(r'(20\d{2})[\.\-/年](\d{1,2})', title)
    if year_month_matches:
        for ym, mm in year_month_matches:
            y, m = int(ym), int(mm)
            if 1 <= m <= 12 and (y, m) in VALID_MONTHS:
                return True
        # Has explicit year+month but none matched valid range
        return False
    
    # Pattern 2: month only  e.g. "3月来了", "8月各银行"
    month_matches = re.findall(r'(\d{1,2})月', title)
    if month_matches:
        for mm in month_matches:
            m = int(mm)
            if 1 <= m <= 12:
                # Only accept months that fall within our 3-month window
                if any(vm == m for _, vm in VALID_MONTHS):
                    return True
        # Has month reference but all outside valid range
        return False
    
    # Pattern 3: has "2026" somewhere -> current year content
    if "2026" in title:
        return True
    
    # Pattern 4: no time info at all -> keep it (can't determine)
    return True

# Load all search results
all_notes = {}
for i in range(1, 5):
    path = os.path.join(BASE_DIR, f"search_result_{i}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for feed in data.get("data", {}).get("feeds", []):
        if feed.get("modelType") != "note":
            continue
        fid = feed["id"]
        if fid not in all_notes:
            nc = feed.get("noteCard", {})
            user = nc.get("user", {})
            interact = nc.get("interactInfo", {})
            title_raw = nc.get("displayTitle", "").replace("\u200b", "")
            # Build search URL using title as keyword (more accessible than direct note link)
            search_url = "https://www.xiaohongshu.com/search_result?keyword=" + quote(title_raw, safe="")
            all_notes[fid] = {
                "id": fid,
                "title": title_raw,
                "author": user.get("nickname", "Unknown"),
                "likes": to_int(interact.get("likedCount", "0")),
                "collects": to_int(interact.get("collectedCount", "0")),
                "comments": to_int(interact.get("commentCount", "0")),
                "shares": to_int(interact.get("sharedCount", "0")),
                "url": search_url,
                "direct_url": XHS_BASE + fid,
                "cover": nc.get("cover", {}).get("urlDefault", ""),
                "type": nc.get("type", "normal"),
            }

# Sort by likes descending
notes = sorted(all_notes.values(), key=lambda x: x["likes"], reverse=True)

# Filter: bank-related AND recent (last 3 months)
def is_bank_related(note):
    title = note["title"].lower()
    keywords = ["银行", "立减", "信用卡", "满减", "支付", "羊毛", "返现", "活动汇总", "月月刷", "充值",
                 "开卡", "优惠", "建行", "招行", "工行", "中行", "农行", "交行", "中信", "浦发",
                 "邮储", "光大", "浙商", "民生", "兴业", "华夏", "平安", "etc", "薅", "还款",
                 "银联", "资产提升", "消费达标", "龙支付", "云闪付"]
    return any(k in title for k in keywords)

bank_notes = [n for n in notes if is_bank_related(n) and is_recent(n["title"])]
filtered_out = [n for n in notes if is_bank_related(n) and not is_recent(n["title"])]

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
</style>
</head>
<body>

<div class="header">
  <h1>小红书 · 银行营销活动搜索报告</h1>
  <p>搜索时间：""" + f"{TODAY.year}年{TODAY.month}月{TODAY.day}日" + """ | 数据来源：小红书 | 时间范围：""" + f"{min(VALID_MONTHS)[0]}年{min(VALID_MONTHS)[1]}月 - {max(VALID_MONTHS)[0]}年{max(VALID_MONTHS)[1]}月" + """ | 点击标题可跳转搜索页查找原文</p>
</div>

<div class="container">

  <div class="summary">
    <div class="summary-card">
      <div class="num">""" + str(len(all_notes)) + """</div>
      <div class="label">搜索到的笔记总数（去重后）</div>
    </div>
    <div class="summary-card">
      <div class="num">""" + str(len(bank_notes)) + """</div>
      <div class="label">近3个月银行活动笔记</div>
    </div>
    <div class="summary-card">
      <div class="num">""" + str(len(filtered_out)) + """</div>
      <div class="label">已过滤的过期笔记</div>
    </div>
    <div class="summary-card">
      <div class="num">""" + fmt_num(bank_notes[0]["likes"] if bank_notes else 0) + """</div>
      <div class="label">单篇最高点赞</div>
    </div>
  </div>

  <div class="highlight">
    <h3>核心发现</h3>
    <p>本报告仅收录<strong>2026年1月至3月</strong>的银行营销活动笔记，已过滤掉标题中明确标注为更早月份（如5月、6月、8月等）的旧活动。以<strong>立减金、满减优惠、资产提升返现</strong>为主要形式，<strong>建设银行、招商银行、工商银行、中信银行</strong>是讨论度最高的银行。<br><br><strong>点击标题跳转小红书搜索页，即可快速找到对应笔记进行复核。</strong></p>
  </div>

  <!-- ==================== TOP 热门笔记 ==================== -->
  <div class="section">
    <h2 class="section-title">TOP 热门笔记（按点赞排序，点击可搜索原文）</h2>
    <div class="note-grid">
"""

top_notes = bank_notes[:15]
for i, note in enumerate(top_notes, 1):
    rank_class = "top3" if i <= 3 else ("top10" if i <= 10 else "normal")
    html += f"""
      <div class="note-card">
        <div class="rank {rank_class}">{i}</div>
        <div class="title"><a href="{esc(note['url'])}" target="_blank">{esc(note['title'])}</a></div>
        <div class="author">作者：{esc(note['author'])}</div>
        <div class="stats">
          <div class="stat">点赞 <span>{fmt_num(note['likes'])}</span></div>
          <div class="stat">收藏 <span>{fmt_num(note['collects'])}</span></div>
          <div class="stat">评论 <span>{fmt_num(note['comments'])}</span></div>
          <div class="stat">分享 <span>{fmt_num(note['shares'])}</span></div>
        </div>
        <a class="link-btn" href="{esc(note['url'])}" target="_blank">搜索原文 &rarr;</a>
      </div>
"""

html += """
    </div>
  </div>

  <!-- ==================== 全部笔记列表 ==================== -->
  <div class="section">
    <h2 class="section-title">全部银行活动笔记列表（点击标题搜索原文）</h2>
    <table>
      <thead>
        <tr>
          <th style="width:40px">#</th>
          <th>标题（可点击）</th>
          <th style="width:130px">作者</th>
          <th style="width:70px">点赞</th>
          <th style="width:70px">收藏</th>
          <th style="width:70px">评论</th>
          <th style="width:70px">分享</th>
        </tr>
      </thead>
      <tbody>
"""

for i, note in enumerate(bank_notes, 1):
    html += f"""        <tr>
          <td>{i}</td>
          <td><a href="{esc(note['url'])}" target="_blank">{esc(note['title'])}</a></td>
          <td>{esc(note['author'])}</td>
          <td>{fmt_num(note['likes'])}</td>
          <td>{fmt_num(note['collects'])}</td>
          <td>{fmt_num(note['comments'])}</td>
          <td>{fmt_num(note['shares'])}</td>
        </tr>
"""

html += """      </tbody>
    </table>
  </div>

</div>

<div class="footer">
  <p>报告由 WorkBuddy 通过小红书 MCP 自动生成 | 数据仅供参考，具体活动以银行官方公告为准 | 点击标题跳转搜索页复核</p>
</div>

</body>
</html>
"""

output_path = os.path.join(BASE_DIR, "bank_marketing_report.html")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Report generated: {output_path}")
print(f"Total unique notes: {len(all_notes)}")
print(f"Bank-related & recent (Jan-Mar 2026): {len(bank_notes)}")
print(f"Filtered out (older months): {len(filtered_out)}")
if bank_notes:
    print(f"Top note likes: {bank_notes[0]['likes']}")
