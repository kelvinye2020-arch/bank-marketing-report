#!/usr/bin/env python3
"""
export_browser_cookies.py — 把 MCP 的登录态导出到浏览器可导入的格式

用途：
  小红书 MCP 搜索用的登录态（cookies.json）转换成 Cookie-Editor 扩展
  的导入格式，导入浏览器后，从报告点「查看原文」跳转 xhs 不会再弹登录框。

用法：
  python export_browser_cookies.py
  → 生成 xhs_cookies_for_browser.json（放桌面或任意位置）

浏览器导入步骤（一次性，cookie 有效期内 ~2-3 周不用重复）：
  1. Chrome/Edge 应用商店安装扩展：Cookie-Editor
  2. 打开 https://www.xiaohongshu.com 任意页面
  3. 点浏览器右上角 Cookie-Editor 图标 → 右下角 Import
  4. 粘贴 xhs_cookies_for_browser.json 的全部内容 → 回车
  5. 刷新页面 → 已登录（右上角显示头像）

注意：
  - MCP 每次搜索后会自动刷新 cookies.json，重新导出+导入即可拿到新 cookie
  - cookie 过期重新扫码后，需要重跑本脚本再导入一次
"""
import json
import os
import sys

MCP_COOKIES = r"D:\AI agent\xiaohongshu-mcp-windows-amd64\cookies.json"
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "xhs_cookies_for_browser.json")

# Cookie-Editor sameSite 取值映射（None 需配 Secure，跨站跳转最稳）
def to_cookie_editor(c):
    out = {
        "name": c.get("name", ""),
        "value": c.get("value", ""),
        "domain": c.get("domain", ".xiaohongshu.com"),
        "path": c.get("path", "/"),
        "secure": True,           # SameSite=None 必须 Secure
        "httpOnly": bool(c.get("httpOnly", False)),
        "sameSite": "no_restriction",  # 跨站顶级跳转也带 cookie
    }
    exp = c.get("expires")
    if exp and exp > 0:
        out["expirationDate"] = int(exp)
    else:
        out["session"] = True
    return out

def main():
    if not os.path.exists(MCP_COOKIES):
        print(f"[ERROR] 找不到 MCP cookies: {MCP_COOKIES}")
        print("请确认 MCP 已启动并登录过至少一次。")
        sys.exit(1)

    with open(MCP_COOKIES, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list) or not raw:
        print("[ERROR] cookies.json 格式异常（空或非 list）")
        sys.exit(1)

    # 去重（rod 偶尔存两条同名 acw_tc）
    seen = {}
    for c in raw:
        if not c.get("name"):
            continue
        seen[c["name"]] = c  # 后者覆盖前者，保留最新

    editor_cookies = [to_cookie_editor(c) for c in seen.values()]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(editor_cookies, f, ensure_ascii=False, indent=2)

    has_session = "web_session" in seen
    print(f"[OK] 已导出 {len(editor_cookies)} 条 cookie → {OUTPUT}")
    print(f"     关键登录态 web_session: {'✅ 存在' if has_session else '❌ 缺失（MCP 可能未登录）'}")
    if not has_session:
        print("     请先用 MCP 扫码登录后再重跑本脚本。")
        sys.exit(2)
    print()
    print("下一步（浏览器一次性操作）：")
    print("  1. Chrome/Edge 装 Cookie-Editor 扩展")
    print("  2. 打开 www.xiaohongshu.com")
    print("  3. Cookie-Editor 图标 → Import → 粘贴文件内容 → 回车")
    print("  4. 刷新页面，右上角出现头像 = 登录态生效")

if __name__ == "__main__":
    main()
