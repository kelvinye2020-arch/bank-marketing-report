# 自动化执行记忆 - 小红书银行营销数据更新

## 2026-03-23 (周一 10:00) — 部分成功

**执行结果**: 报告已更新并推送到 GitHub，但数据未更新（使用上次数据）

**问题**: 小红书 MCP 服务器未登录（`is_logged_in: false`），无法获取新的搜索数据。
- 已下载并安装 MCP 服务器至 `C:\Users\kelvinyye\tools\xiaohongshu-mcp\`
- 服务器能正常启动监听 18060 端口，但需要先运行 login 工具扫码登录
- 扫码登录需要用户手动操作，自动化任务无法完成

**处理方式**: 
- 从 git HEAD 恢复了之前的有效 JSON 数据（search_result_2/3/4 各 22 条）
- search_result_1.json 在 git 历史中始终为空（0 feeds）
- 用现有数据重新生成了报告（日期更新为 2026-03-23）
- 报告数据：59 条去重笔记，41 条银行相关近 3 月，最高点赞 4,732

**Git 推送**: ✅ 成功
- commit: `880fffa` "update-2026-03-23" 
- 已推送到 master 和 main

**⚠️ 用户需要操作**:
1. 运行 `C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-login-windows-amd64.exe` 扫码登录
2. 登录成功后，启动 `C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe`
3. 然后重新触发自动化任务，即可获取最新数据

**踩坑记录**:
- PowerShell `>` 重定向会将 stdout 转为 UTF-16 LE BOM 编码，破坏 JSON 文件。应使用 Python subprocess 写文件或 cmd /c 重定向
- MCP 服务器首次使用需要先运行 login 工具扫码，headless 模式无法自动完成登录

---

## 2026-03-30 (周一 10:49) — ✅ 完全成功（手动触发）

**执行结果**: 新数据搜索成功，报告已更新并推送到 GitHub。

**数据获取**: 4 组关键词全部搜索成功（每个约 56KB）
- 去重笔记：145 条（上次 59 条，增长 146%）
- 银行相关近 3 月（1-3月）：112 条（上次 41 条）
- 过滤掉旧月份：16 条
- 最高点赞：4,779

**前提条件**: 用户手动扫码登录后触发，cookie 有效。自动化任务（10:42）因 cookie 过期失败。

**Git 推送**: ✅ 成功
- commit: `22f8be3` "update-2026-03-30"
- 已推送到 master 和 main（main 需先 merge master 再 push）

---

## 2026-05-11 (周一 10:20) — 部分成功（Fallback 数据）

**执行结果**: 报告已更新日期并推送到 GitHub，但搜索数据未更新（使用上次 5/7 的数据）。

**问题**: MCP 服务器登录状态检查通过（is_logged_in: true），但搜索接口 60 秒超时无响应。可能原因：cookie 实际已过期但 status 接口未正确反映，或小红书后端风控限流。

**处理方式**: 
- 使用 git HEAD 中已有的 6 个 JSON 文件（每个 22 feeds, 27KB）作为 fallback
- 用现有数据重新生成了报告（日期更新为 2026-05-11）
- 报告数据：165 条去重笔记，44 条银行相关近 3 月（3-5月，≥30赞），最高点赞 2,154
- 新笔记 0 条（因为用的旧数据）

**Git 推送**: ✅ 成功
- commit: `a2cbda0` "update-2026-05-11"
- 已推送到 master 和 main（fast-forward merge）

**⚠️ 用户需要操作**:
1. 需重新扫码登录小红书 MCP 以刷新 cookie
2. 运行 `C:\Users\kelvinyye\tools\xiaohongshu-mcp\xiaohongshu-login-windows-amd64.exe` 扫码
3. 登录后手动触发自动化可获取最新数据

**踩坑记录**:
- MCP status 接口返回 is_logged_in=true 不代表搜索接口能正常工作。cookie 过期后 status 仍可能返回 true，但 search 会 hang 住直到超时。判断 cookie 是否有效需要实际执行一次搜索。

---

## 2026-06-01 (周一 10:00) — ✅ 完全成功

**执行结果**: 6 组关键词全部搜索成功，报告已更新并推送到 GitHub。

**数据获取**:
- 6 组搜索全部成功（每个约 26KB）
- 去重笔记：182 条
- 银行相关近 3 月（≥30赞）：38 条
- 过滤掉旧月份：35 条，低赞过滤：50 条
- 新笔记（5/18~5/25）：5 条
- 重点银行：中国银行 8 条、工商银行 4 条
- 最高点赞：1,571

**Git 推送**: ✅ 成功
- 已推送到 master 和 main（fast-forward merge）
- 线上地址：https://kelvinye2020-arch.github.io/bank-marketing-report/

---

## 2026-06-08 (周一 10:00) — ✅ 完全成功

**执行结果**: 6 组关键词全部搜索成功，报告已更新并推送到 GitHub。

**数据获取**:
- 6 组搜索全部成功（每个约 26KB）
- 去重笔记：191 条
- 银行相关近 60 天（≥30赞）：20 条
- 过滤掉旧月份：75 条，低赞过滤：45 条
- 新笔记（6/1~6/8）：13 条
- 重点银行：中国银行 2 条、工商银行 6 条
- 最高点赞：2,080

**Git 推送**: ✅ 成功（脚本内 push 因 ca-bundle.crt 证书问题失败，手动切换 sslBackend=schannel 后重推成功）
- commit: `e3d9c28`
- 已推送到 master 和 main
- 线上地址：https://kelvinye2020-arch.github.io/bank-marketing-report/

**踩坑记录**:
- Git push 报 `error adding trust anchors from file: C:/Program Files/Git/mingw64/ssl/certs/ca-bundle.crt`，解决方案：`git config --local http.sslBackend schannel` 切换为 Windows 原生 SSL 后端。已设置为 local config，后续自动化不会再遇此问题。

---

## 2026-06-15 (周一 09:58) — ✅ 完全成功

**执行结果**: 6 组关键词全部搜索成功，报告已更新并推送到 GitHub。

**数据获取**:
- MCP 服务未运行，脚本自动拉起成功（端口 18060）
- 登录状态正常（xiaohongshu-mcp 账号）
- 6 组搜索全部成功（每个约 26KB）
- 去重笔记：186 条
- 银行相关近 60 天（≥30赞）：22 条
- 过滤掉旧月份：72 条，低赞过滤：43 条
- 新笔记（6/8~6/15）：8 条
- 重点银行：中国银行 5 条、工商银行 3 条
- 最高点赞：628

**Git 推送**: ✅ 成功
- 已推送到 master 和 main
- 线上地址：https://kelvinye2020-arch.github.io/bank-marketing-report/
- 企微通知已发送

**备注**: 本次 MCP 服务未运行，脚本自动拉起逻辑生效，无需手动干预。
