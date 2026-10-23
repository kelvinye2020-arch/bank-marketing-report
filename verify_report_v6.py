"""独立V6.2验收：JSON/来源/图片字节/七屏宽三tab/全部全文图/键盘。

python verify_report_v6.py --report-dir 批次目录 --output 验收结果.json
可加 --store --reviews --editorial --manifest 验证原输入文件及公开证据映射。
不导入构建器，不采集；仅启动127.0.0.1临时HTTP服务，禁止外网请求。
"""
import argparse
import hashlib
import json
import re
import sys
import threading
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

SECTIONS = ('marketing', 'product', 'sentiment')
METRICS = ('likes', 'collects', 'comments', 'shares')
WIDTHS = (360, 390, 768, 1024, 1120, 1280, 1440)
CST = timezone(timedelta(hours=8))


def check(condition, text):
    if not condition:
        raise ValueError(text)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return sha(canonical(value))


def json_file(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def image_check(root, relative, expected):
    check(isinstance(relative, str) and re.fullmatch(r'imgs/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)', relative), '图片路径不安全')
    resolved = (root / relative).resolve()
    check(resolved.is_relative_to(root) and resolved.is_file(), f'图片缺失或越界：{relative}')
    check(sha(resolved.read_bytes()) == expected, f'图片字节哈希不符：{relative}')
    with Image.open(resolved) as im:
        check(im.format in ('JPEG', 'PNG', 'WEBP'), '图片格式不受支持')
        im.verify()
    with Image.open(resolved) as im:
        im.load()


def validate_image_sources(images, sources):
    check(isinstance(images, dict) and isinstance(sources, dict) and set(images) == set(sources),
          'quality.image_sources必须完整映射全部发布图片')
    reverse = {}
    for public, source in sources.items():
        check(isinstance(source, str) and re.fullmatch(r'imgs/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp)', source),
              '图源路径不安全')
        expected = images[public]
        check(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected), '发布图片哈希无效')
        path = Path(source)
        check(public == f'imgs/{path.stem}_v6_{expected[:12]}{path.suffix}', '发布图片名与图源/哈希映射不符')
        check(source not in reverse, '同一图源不能映射多个发布路径')
        reverse[source] = public
    return reverse


def validate_bundle(report_dir, source_paths=None):
    root = Path(report_dir).resolve()
    d = json_file(root / 'public_report_v6.json')
    q = json_file(root / 'report_quality_v6.json')
    html = (root / 'bank_marketing_report.html').read_text(encoding='utf-8')
    required = {'schema_version', 'version', 'passed', 'production_data_passed', 'publication', 'provenance',
                'coverage', 'counts', 'minimums', 'image_count', 'fulltext_count', 'metrics_keys',
                'metrics_null_fields', 'report_sha256', 'html_sha256', 'images', 'image_sources'}
    check(isinstance(q, dict) and required <= q.keys(), 'quality mandatory字段缺失；fail closed')
    public_by_source = validate_image_sources(q['images'], q['image_sources'])
    check(q['schema_version'] == 1 and q['version'] == d.get('version') == 'V6.2', 'schema/version错误')
    check(q['passed'] is True and type(q['production_data_passed']) is bool, '质量状态错误')
    check(q['report_sha256'] == digest(d) and q['html_sha256'] == sha(html.encode('utf-8')), '公开JSON/HTML哈希不符')
    check(sha(re.search(r'<style>(.*?)</style>', html, re.S)[1].encode()) == '20ba5c66f12b4d9043f3f886e7ef047b0d908f52fa92d98bc6f047529c28cae7', '冻结CSS被修改')
    check(sha(''.join(re.findall(r'<[^>]+>', html.split('<script type=')[0])).encode()) == 'db10027ef10d465a6831b05b59096e5ca484be0d5e9ba8a188c43259ed90231c', '冻结静态DOM被修改')
    match = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', html, re.S)
    check(match is not None and json.loads(match.group(1)) == d, '内嵌数据与公开JSON不一致')
    provenance = d.get('provenance')
    check(isinstance(provenance, dict) and provenance == q['provenance'], 'provenance不一致')
    check(set(('run_id', 'as_of', 'data_kind', 'manifest_sha256', 'editorial_sha256', 'reviews_sha256',
               'evidence_sha256', 'store_sha256', 'image_sha256')) <= provenance.keys(), '来源摘要缺失')
    check(provenance['data_kind'] in ('production', 'test_fixture')
          and q['production_data_passed'] == (provenance['data_kind'] == 'production'), '测试数据伪装生产通过')
    check(all(re.fullmatch(r'[0-9a-f]{64}', provenance[k]) for k in provenance if k.endswith('_sha256')), '来源哈希无效')
    check(d.get('publication') == q['publication'] and d['publication']['status'] in ('draft', 'ready', 'published'), 'publication无效')
    check(provenance['data_kind'] != 'test_fixture' or d['publication']['status'] == 'draft', 'fixture只能draft')
    check(d['publication']['status'] != 'published' or d['publication'].get('deployment_receipt_sha256'), 'published缺回执')
    coverage = q['coverage']
    check(isinstance(coverage, dict) and set(('required', 'succeeded', 'complete', 'optional_failed')) <= coverage.keys()
          and type(coverage['required']) is int and coverage['required'] >= 3
          and coverage['succeeded'] == coverage['required'] and coverage['complete'] is True, 'coverage未完成')
    check(d['data_as_of'] == provenance['as_of'], '来源日期不符')
    end = date.fromisoformat(d['data_as_of'])
    check(d['recent_start'] == (end - timedelta(days=6)).isoformat()
          and d['window_start'] == (end - timedelta(days=59)).isoformat()
          and d['month_start'] == end.replace(day=1).isoformat(), '7/60/当月日期边界错误')
    check(isinstance(d.get('notes'), list) and isinstance(d.get('insights'), dict)
          and set(d['insights']) == set(SECTIONS) and isinstance(d.get('overview'), list), '数据结构错误')
    ids = [n['id'] for n in d['notes']]
    check(len(ids) == len(set(ids)), '公开ID重复')
    byid = {n['id']: n for n in d['notes']}
    nulls, image_set = 0, set()
    for n in d['notes']:
        check(n['primary_section'] in SECTIONS and n['sections'] == [n['primary_section']], '非主栏或跨栏重复')
        check(n['title'].strip() and n['outside_window'] is False and n['date_basis'] == '详情发布时间 · CST', '标题/日期依据错误')
        check(not set(('search_keywords', 'source', 'archive_reason', 'evidence_sha256', '_image_quotes')) & n.keys(), '内部审计泄漏')
        ev = n['evidence']
        check(set(ev) <= set(('t', 'a', 'desc', 'imgs', 'tags', 'pub', 'url', 'cmts', 'likes', 'collects', 'comments', 'shares', 'captured_at')), '原证据带内部字段')
        check(set(('t', 'a', 'desc', 'imgs', 'tags', 'pub', 'url', 'cmts')) <= ev.keys(), '全文mandatory字段缺失')
        check(isinstance(ev['desc'], str) and (ev['desc'].strip() or ev['imgs']), '全文与图均缺失')
        pub = datetime.fromisoformat(ev['pub'].replace('Z', '+00:00'))
        pub = pub.replace(tzinfo=CST) if pub.tzinfo is None else pub.astimezone(CST)
        check(end - timedelta(days=59) <= pub.date() <= end and n['date'] == pub.date().isoformat()
              and n['published_at_cst'] == pub.isoformat(timespec='seconds'), '未知/future/窗口外详情日期公开')
        check(n['recent'] == (pub.date() >= end - timedelta(days=6)), 'recent错误')
        check(n['recent'] or not n['event_status'], '旧日期残留近期状态')
        url = urlsplit(ev['url'])
        check(url.scheme == 'https' and url.netloc in ('www.xiaohongshu.com', 'xiaohongshu.com')
              and not url.query and not url.fragment and url.path.rstrip('/').split('/')[-1] == n['id'], '公开URL未去token或越界')
        for obj in (ev, n['display_metrics']):
            check(all(k in obj and (obj[k] is None or type(obj[k]) is int and obj[k] >= 0) for k in METRICS), 'mandatory四指标缺失或不是整数/null')
        check(set(METRICS + ('captured_at', 'source')) == set(n['display_metrics'])
              and n['display_metrics']['source'] in ('saved', 'search', 'detail'), 'display_metrics结构错误')
        capture = n['display_metrics']['captured_at']
        check(capture or n['display_metrics']['source'] == 'saved', '新快照缺少采集时间')
        if capture:
            check(datetime.fromisoformat(capture.replace('Z', '+00:00')).tzinfo is not None, '快照时间缺少时区')
        nulls += sum(n['display_metrics'][k] is None for k in METRICS)
        check(n['image_count'] == len(ev['imgs']) and len(ev['imgs']) == len(set(ev['imgs'])), 'note图计数错误')
        image_set.update(ev['imgs'])
        check(all(set(c) <= {'u', 'd', 'c', 'likes'} for c in ev['cmts']), '评论内部字段泄漏')
    counts = {'unique': len(ids), 'recent_unique': sum(n['recent'] for n in d['notes']),
              'direct': sum(n['primary_section'] == 'sentiment' for n in d['notes']),
              'sections': {s: sum(n['primary_section'] == s for n in d['notes']) for s in SECTIONS}}
    check(d['counts'] == d['visible_counts'] == q['counts'] == counts and d['source_count'] == len(ids), '主栏唯一计数不一致')
    check(q['metrics_keys'] == list(METRICS) and q['metrics_null_fields'] == nulls and q['fulltext_count'] == len(ids), '指标/全文质量数字不符')
    check(d['image_count'] == q['image_count'] == len(image_set) and set(q['images']) == image_set
          and provenance['image_sha256'] == digest(q['images']), '图片唯一计数/摘要错误')
    check(set(q['minimums']) == set(SECTIONS + ('recent',)) and all(type(v) is int and v >= 0 for v in q['minimums'].values()), '数量阈值结构错误')
    for key, value in q['minimums'].items():
        actual = counts['recent_unique'] if key == 'recent' else counts['sections'][key]
        check(actual >= value, '数量闸门失败')
    if provenance['data_kind'] == 'production':
        check(all(q['minimums'][k] >= v for k, v in {'marketing': 15, 'product': 5, 'sentiment': 4, 'recent': 3}.items()), '生产阈值被降低')
    for relative, expected in q['images'].items():
        image_check(root, relative, expected)
    refs = set()
    for s, items in d['insights'].items():
        check(len(items) <= 3, '摘要超三条')
        for item in items:
            check(item['refs'] and len(item['refs']) == len(set(item['refs'])), '摘要refs缺失或重复')
            for nid in item['refs']:
                check(nid in byid and byid[nid]['primary_section'] == s and nid not in refs, '摘要跨栏/重复/残留refs')
                refs.add(nid)
    tabs = [item['tab'] for item in d['overview']]
    check(len(tabs) <= 3 and len(tabs) == len(set(tabs)) and set(tabs) <= set(SECTIONS), 'overview重复栏目')
    for item in d['overview']:
        check(item['refs'] and all(nid in byid and byid[nid]['primary_section'] == item['tab'] for nid in item['refs']), 'overview引用失效')
    check(set(d['reading_groups']) == set(SECTIONS), 'reading_groups非三栏')
    for s, groups in d['reading_groups'].items():
        grouped_ids, event_keys = [], []
        for group in groups:
            evs = group['events']
            event_keys.extend(e['key'] for e in evs)
            grouped_ids.extend(nid for e in evs for nid in e['ids'])
            for event in evs:
                check(event['ids'] and all(nid in byid and byid[nid]['event_key'] == event['key'] for nid in event['ids']), '主题归组错误')
                members = [byid[nid] for nid in event['ids']]
                like = lambda n: n['display_metrics']['likes'] if n['display_metrics']['likes'] is not None else -1
                check(members == sorted(members, key=lambda n: (like(n), n['published_at_cst']), reverse=True), '主题代表排序错误')
                check(event['time'] == max(n['published_at_cst'] for n in members) and event['likes'] == max(like(n) for n in members), '主题时点/点赞错误')
            order = 'time' if group['key'] in ('recent', 'current') else 'likes'
            check([e[order] for e in evs] == sorted([e[order] for e in evs], reverse=True), '分组排序错误')
        check(len(grouped_ids) == len(set(grouped_ids)) and len(event_keys) == len(set(event_keys))
              and set(grouped_ids) == {n['id'] for n in d['notes'] if n['primary_section'] == s}, '阅读组漏证据/重复证据')
    check(not set(('search_audit', 'trial_summary', 'internal_audit', 'original_count', 'new_evidence_count', 'method_notes')) & d.keys(), '内部审计嵌入报告')
    if source_paths:
        check(set(source_paths) == {'store', 'reviews', 'editorial', 'manifest'}, '输入源验收必须四份齐全')
        check(q.get('input_file_sha256') == {k: sha(Path(p).read_bytes()) for k, p in source_paths.items()}, '输入文件字节哈希变化')
        sources = {key: json_file(path) for key, path in source_paths.items()}
        store, reviews, editorial, manifest = (sources[k] for k in ('store', 'reviews', 'editorial', 'manifest'))
        check(provenance['store_sha256'] == digest(store) and provenance['evidence_sha256'] == digest(store['notes'])
              and provenance['reviews_sha256'] == digest(reviews['reviews']) and provenance['editorial_sha256'] == digest(editorial)
              and provenance['manifest_sha256'] == digest(manifest), '原来源语义摘要不符')
        check(manifest['run_id'] == editorial['run_id'] == provenance['run_id']
              and manifest['as_of'] == editorial['as_of'] == provenance['as_of'], '当前周期不匹配')
        check(all(g['status'] == 'success' and g['run_id'] == manifest['run_id'] and g['as_of'] == manifest['as_of']
                  for g in manifest['groups'] if g['required']), 'required覆盖失败')
        originals = {n['id']: n for n in store['notes']}
        eligible = set()
        for nid, row in originals.items():
            review = reviews['reviews'].get(nid)
            if not review or review['primary_section'] not in SECTIONS or not row['evidence']['pub']:
                continue
            pub = datetime.fromisoformat(row['evidence']['pub'].replace('Z', '+00:00'))
            pub = pub.replace(tzinfo=CST) if pub.tzinfo is None else pub.astimezone(CST)
            if end - timedelta(days=59) <= pub.date() <= end:
                eligible.add(nid)
        check(eligible == set(ids), '公开ID与源证据可发布集合不匹配')
        for n in d['notes']:
            ev = originals[n['id']]['evidence']
            review = reviews['reviews'][n['id']]
            check(review['evidence_sha256'] == digest(ev), 'review未绑定源证据')
            check(all(n['evidence'][key] == ev[key] for key in ('t', 'a', 'desc', 'tags', 'pub') + METRICS), '原文/原指标被改写')
            check(all(p in public_by_source for p in ev['imgs'])
                  and n['evidence']['imgs'] == [public_by_source[p] for p in ev['imgs']], '原图顺序/图源映射失配')
            check(review['image_sha256'] == {p: q['images'][public_by_source[p]] for p in ev['imgs']}, '源图审核字节不符')
    return d, q


def browser_verify(root, d, executable=None, screenshots=None, widths=None):
    widths = tuple(WIDTHS if widths is None else widths)
    check(widths and all(type(w) is int and w in WIDTHS for w in widths)
          and len(widths) == len(set(widths)), '浏览器验收宽度为空、重复或不受支持')
    from playwright.sync_api import sync_playwright
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors, network_errors, measurements = [], [], []
    origin = f'http://127.0.0.1:{server.server_port}/'
    try:
        with sync_playwright() as pw:
            launch = {'headless': True}
            if executable:
                launch['executable_path'] = executable
            else:
                edge = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
                if edge.is_file():
                    launch['executable_path'] = str(edge)
            browser = pw.chromium.launch(**launch)
            try:
                page = browser.new_page(viewport={'width': widths[0], 'height': 920})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.on('requestfailed', lambda req: network_errors.append(req.url))
                page.on('response', lambda resp: network_errors.append(f'{resp.status} {resp.url}') if resp.status >= 400 else None)
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin) else route.abort())
                page.goto(origin + 'bank_marketing_report.html', wait_until='networkidle')
                check(page.evaluate('DATA.source_count') == len(d['notes']), '浏览器动态计数不一致')
                check(page.locator('.version').inner_text().startswith(d['version']), '动态版本未呈现')
                check(d['data_as_of'] in page.locator('#date-line').inner_text(), '动态日期未呈现')
                check(page.locator('#metrics .metric').count() == 4, '四指标结构变化')
                top_values = [d['source_count'], d['counts']['recent_unique'], d['counts']['direct'], d['image_count']]
                check(page.locator('#metrics b').all_text_contents() == [str(v) for v in top_values], '前台唯一数字不一致')
                for width in widths:
                    print(f'[browser_verify] width={width} 开始', file=sys.stderr, flush=True)
                    page.set_viewport_size({'width': width, 'height': 920})
                    seen = set()
                    for section in SECTIONS:
                        page.locator('#tab-' + section).click()
                        expected_ids = {n['id'] for n in d['notes'] if n['primary_section'] == section}
                        card_ids = page.locator('#evidence .note').evaluate_all('ns=>ns.map(n=>n.dataset.noteId)')
                        check(len(card_ids) == len(set(card_ids)) and set(card_ids) == expected_ids and not seen.intersection(card_ids), '全部三tab卡片数量/唯一归属错误')
                        seen.update(card_ids)
                        check(page.locator('#tab-' + section + ' .tab-count').inner_text() == str(len(expected_ids)), 'tab动态数量错误')
                        check(page.locator('.insight').count() == len(d['insights'][section]), '摘要动态数量错误')
                        check(page.locator('.insight-number').all_text_contents() == [str(i + 1).zfill(2) for i in range(len(d['insights'][section]))], '摘要纵向编号错误')
                        check(page.evaluate('document.documentElement.scrollWidth<=innerWidth'), f'{width}/{section}页面横溢')
                        # 折叠 details 的后代不参与布局，computed tracks 未解析为像素；先展开再验证全部证据网格。
                        page.locator('#evidence details').evaluate_all('ns=>ns.forEach(n=>n.open=true)')
                        grids = page.locator('#evidence .grid').evaluate_all('ns=>ns.map(n=>{const s=getComputedStyle(n);return {columns:s.gridTemplateColumns.split(" ").length,gap:s.gap}})')
                        expected_columns = 1 if width <= 760 else 2 if width < 1120 else 3
                        check(all(g['columns'] == expected_columns for g in grids), f'{width}/{section}列数错误')
                        check(all(g['gap'] == '16px' for g in grids), '冻结卡片间距变化')
                        boxes = page.locator('.insight').evaluate_all('ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})')
                        check(all(boxes[i + 1]['y'] >= boxes[i]['y'] + boxes[i]['h'] - 1 for i in range(len(boxes) - 1)), '摘要不是纵向排列')
                        page.locator('#evidence details').evaluate_all('ns=>ns.forEach(n=>n.open=true)')
                        check(page.locator('#evidence .note').evaluate_all('ns=>ns.every(n=>n.scrollWidth<=n.clientWidth+1)'), '卡片横溢')
                        for n in (n for n in d['notes'] if n['primary_section'] == section):
                            card = page.locator('#evidence .note[data-note-id="' + n['id'] + '"]')
                            rendered = card.evaluate('n=>({padding:getComputedStyle(n).padding,labels:[...n.querySelectorAll(".stats>span")].map(s=>s.firstChild.textContent),stats:[...n.querySelectorAll(".stats b")].map(s=>s.textContent),coverage:n.querySelectorAll(".coverage").length})')
                            check(rendered['padding'] == '16px', '冻结卡片padding变化')
                            check(rendered['labels'] == ['点赞', '收藏', '评论', '分享'], '互动标签改变')
                            expected_stats = [format(n['display_metrics'][k], ',') if n['display_metrics'][k] is not None else '未取到' for k in METRICS]
                            check(rendered['stats'] == expected_stats, 'null被显示为zero或指标不符')
                            check(rendered['coverage'] == bool(n['evidence']['cmts']), '零评论采样不应显示标签')
                            card.locator('.primary').click()
                            check(page.locator('#note-text').inner_text() == (n['evidence']['desc'] or '（原始笔记无正文，请查看图片）'), '全文非原文')
                            check(page.locator('#note-text img,#note-text script').count() == 0, '原文被解释为HTML')
                            imgs = n['evidence']['imgs']
                            if not imgs:
                                check(page.locator('#carousel').is_hidden(), '无图没有隐藏轮播')
                                page.keyboard.press('ArrowRight')
                            else:
                                for index, img in enumerate(imgs):
                                    page.wait_for_function('document.getElementById("note-image").complete && document.getElementById("note-image").naturalWidth>0')
                                    check(page.locator('#note-image').get_attribute('src') == img, '轮播图片顺序/路径错误')
                                    # 每篇首图保持真实鼠标点击；后续图用按钮 Enter，避免反复滚动/稳定性等待。
                                    if index == 0:
                                        page.locator('#enlarge').click()
                                    else:
                                        page.locator('#enlarge').focus()
                                        page.keyboard.press('Enter')
                                    check(page.locator('#zoom-dialog').evaluate('n=>n.open'), '放大未打开')
                                    page.wait_for_function('document.getElementById("zoom-image").complete && document.getElementById("zoom-image").naturalWidth>0')
                                    check(page.locator('#zoom-image').get_attribute('src') == img, '放大不是原图')
                                    check(page.locator('#zoom-dialog').evaluate('n=>n.scrollWidth<=n.clientWidth+1'), 'zoom横溢')
                                    page.keyboard.press('Tab')
                                    check(page.locator('#zoom-dialog').evaluate('n=>n.contains(document.activeElement)'), 'zoom焦点未约束')
                                    page.keyboard.press('Escape')
                                    check(not page.locator('#zoom-dialog').evaluate('n=>n.open') and page.locator('#note-dialog').evaluate('n=>n.open'), '双层Escape错误')
                                    if len(imgs) > 1:
                                        page.keyboard.press('ArrowRight')
                                        page.wait_for_function('document.getElementById("note-image").complete && document.getElementById("note-image").naturalWidth>0')
                                if len(imgs) > 1:
                                    page.keyboard.press('ArrowLeft')
                                    page.wait_for_function('document.getElementById("note-image").complete && document.getElementById("note-image").naturalWidth>0')
                                    check(page.locator('#note-image').get_attribute('src') == imgs[-1], '上一张键盘失败')
                            check(page.locator('#note-dialog').evaluate('n=>n.scrollWidth<=n.clientWidth+1'), '全文弹窗横溢')
                            page.locator('#original').focus()
                            page.keyboard.press('Tab')
                            check(page.locator('#note-dialog').evaluate('n=>n.contains(document.activeElement)'), '全文焦点未约束')
                            page.keyboard.press('Escape')
                            check(not page.locator('#note-dialog').evaluate('n=>n.open'), '全文Escape失败')
                            check(card.locator('.primary').evaluate('n=>n===document.activeElement'), '关闭未还原卡片焦点')
                        measurements.append({'width': width, 'section': section, 'cards': len(expected_ids),
                                             'insights': len(d['insights'][section]), 'columns': expected_columns})
                    check(seen == {n['id'] for n in d['notes']}, '三tab漏证据')
                    if screenshots and width in (390, 1280):
                        page.locator('#tab-marketing').click()
                        page.evaluate('window.scrollTo(0,0)')
                        page.screenshot(path=str(Path(screenshots) / f'fixture_v6_{width}.png'), full_page=True)
                    page.locator('#tab-marketing').click()
                    page.locator('#tab-marketing').focus()
                    for key, expected in [('ArrowRight', 'product'), ('End', 'sentiment'), ('Home', 'marketing'), ('ArrowLeft', 'sentiment')]:
                        page.keyboard.press(key)
                        check(page.locator('#tab-' + expected).get_attribute('aria-selected') == 'true', 'tab键盘导航失败')
                    check(page.locator('#archive,#archive-evidence,#search,#sort,#trial-table').count() == 0, '内部附区泄漏')
                    check(not errors and not network_errors, f'浏览器错误：{errors}；请求错误：{network_errors}')
                    print(f'[browser_verify] width={width} 完成', file=sys.stderr, flush=True)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    return {'widths': list(widths), 'measurements': measurements, 'browser_errors': errors,
            'network_errors': network_errors, 'fulltext_checks': len(d['notes']) * len(widths),
            'image_checks': sum(len(n['evidence']['imgs']) for n in d['notes']) * len(widths)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立验收；不采集、不部署，任意失败退出码2')
    parser.add_argument('--report-dir', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--browser', help='可选本地Chromium/Edge可执行文件')
    parser.add_argument('--screenshots', help='可选已存在的截图目录')
    for key in ('store', 'reviews', 'editorial', 'manifest'):
        parser.add_argument('--' + key)
    args = parser.parse_args(argv)
    output = Path(args.output)
    result = {'passed': False}
    try:
        check(output.parent.is_dir() and not output.exists(), '验收输出父目录不存在或文件已存在，拒绝覆盖')
        if args.screenshots:
            check(Path(args.screenshots).is_dir(), '截图目录不存在')
        sources = {k: getattr(args, k) for k in ('store', 'reviews', 'editorial', 'manifest') if getattr(args, k)}
        d, q = validate_bundle(args.report_dir, sources or None)
        browser = browser_verify(Path(args.report_dir).resolve(), d, args.browser, args.screenshots)
        result = {'passed': True, 'production_data_passed': q['production_data_passed'],
                  'source_inputs_verified': bool(sources), 'provenance': d['provenance'], 'counts': d['counts'],
                  'publication': d['publication']['status'], 'browser': browser}
        output.write_bytes(canonical(result))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        result['error'] = str(exc)
        if output.parent.is_dir() and not output.exists():
            output.write_bytes(canonical(result))
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
