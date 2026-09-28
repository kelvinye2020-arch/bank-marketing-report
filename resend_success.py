# -*- coding: utf-8 -*-
"""补发 2026-09-28 更新成功通知（首轮因代理 502 未送达）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_report as ur
from datetime import datetime

lines = [
    "# 小红书声量监控看板更新成功（补发）",
    f"> 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（首轮通知因网络代理 502 未送达，现补发）",
    "> 搜索：14 组关键词，首轮 10 组成功，剩余 4 组已重试补齐",
    "> 营销 tab：入选 <font color=\"info\">37</font> 条 ｜ 产品 tab：8 条 ｜ 舆情 tab：9 条",
    "> 新增能力：舆情笔记嵌入前 10 条热评（试点），9 篇中 7 篇有评论",
    "> 图片修复：185 张全部本地化，<font color=\"info\">远程引用 0</font>（此前图片加载失败已根治）",
    "> GitHub Pages：已推送",
    f"> 线上看板：{ur.REPORT_URL}",
]
ur.send_wecom_markdown("\n".join(lines))
