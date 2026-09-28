# -*- coding: utf-8 -*-
import update_report as u

MSG = """## ⚠️ 小红书周报：MCP 未启动（本次跳过）

**10:00 检测到 MCP 未在 18060 运行**（09:40 计划任务 XHS_MCP_Weekly 未生效，可能电脑没开机/未登录 Windows）。

> 端口探活：WinError 10061 拒绝连接
> netstat 无 18060 监听、tasklist 无 xiaohongshu-mcp 进程

**请双击启动：**
`D:\\AI agent\\xiaohongshu-mcp-windows-amd64\\start_mcp.bat`

看到「启动 HTTP 服务器 : 18060」后回复我重跑。**本次已跳过，未生成/推送任何报告。**

<font color="warning">连续第 4 次因 MCP 未启动跳过：07-20 / 07-27 / 08-03 / 08-10</font>
建议排查 XHS_MCP_Weekly 计划任务，或改为开机常驻方案。"""

print("SEND_RESULT", u.send_wecom_markdown(MSG))
