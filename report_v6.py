"""V6.2 离线构建；不采集、不部署、不修改原始证据。

build --store evidence_store.json --reviews review.json --editorial editorial.json
      --manifest search_manifest.json --output 一个尚不存在的发布目录

证据哈希：UTF-8 JSON，sort_keys=True，separators=(',', ':'), ensure_ascii=False。
review.reviews 按ID映射，每条包含人工/agent审核字段与 evidence_sha256。
editorial 每期必须更新 as_of/run_id/status/reviewed_by/reviewed_at；每个摘要
必须有 refs 与 claims。claims 将数字文字逐项绑定 evidence/display_metrics/pub/
image_quote/count；image_quote 必须有已审核图中文字，不能自动确认官方规则。
输出目录为不可变批次，全部校验完成后一次目录 rename；已有目录绝不覆盖。
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from PIL import Image

VERSION = 'V6.2'
CST = timezone(timedelta(hours=8))
SECTIONS = ('marketing', 'product', 'sentiment')
METRICS = ('likes', 'collects', 'comments', 'shares')
ISSUE_SUBJECTS = ('理财通', '零钱通')
ISSUE_TOPICS = ('服务投诉', '还款手续费', '资金转入转出', '到账时间', '收益与持有期')
REVIEW_FIELDS = ('primary_section', 'object', 'topics', 'attitude', 'analyst_note',
                 'verification', 'event_key', 'event_label', 'event_status', 'evidence_sha256')
IMAGE_RE = re.compile(r'imgs/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)')
NUMBER_RE = re.compile(r'\d+(?:,\d{3})*(?:\.\d+)?')
DEFAULT_MINIMUMS = {'marketing': 15, 'product': 5, 'sentiment': 4, 'recent': 3}


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    raw = Path(path).read_bytes()
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'JSON重复键：{key}')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValidationError(f'非法数字：{value}')))


def strict_date(value, label):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), f'{label}必须为YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f'{label}日期无效') from exc


def timestamp(value, label, allow_unknown=False):
    if allow_unknown and value in (None, ''):
        return None
    require(isinstance(value, str), f'{label}缺少时点')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValidationError(f'{label}时点无效') from exc
    require(result.tzinfo is not None, f'{label}必须有时区')
    return result.astimezone(CST)


def publication_time(value):
    if value in (None, ''):
        return None
    require(isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?', value),
        '详情pub必须含完整日期与时间；不能用搜索日期或ID推算')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=CST) if result.tzinfo is None else result.astimezone(CST)
    except ValueError as exc:
        raise ValidationError('详情pub无效') from exc


def safe_origin(value):
    require(isinstance(value, str) and not re.search(r'[\s\\\x00-\x1f]', value), '原帖URL无效')
    u = urlsplit(value)
    require(u.scheme == 'https' and u.hostname in ('xiaohongshu.com', 'www.xiaohongshu.com')
            and u.username is None and u.password is None and u.netloc in ('xiaohongshu.com', 'www.xiaohongshu.com'),
            '原帖只允许https小红书URL，禁止账户信息或端口')
    require(re.fullmatch(r'/(?:explore|discovery/item)/[A-Za-z0-9_-]+/?', unquote(u.path)), '原帖URL路径无效')
    # 公开报告不保留任何查询参数；原始带token证据不改写。
    return urlunsplit((u.scheme, u.netloc, u.path, '', ''))


def validate_metrics(metrics, label, snapshot=False):
    require(isinstance(metrics, dict), f'{label}必须是完整指标对象')
    for key in METRICS:
        require(key in metrics, f'{label}缺少mandatory指标 {key}')
        value = metrics[key]
        require(value is None or (type(value) is int and value >= 0), f'{label}.{key}必须是非负整数或null')
    if snapshot:
        require('captured_at' in metrics and 'source' in metrics, f'{label}缺少captured_at/source')
        require(metrics['source'] in ('saved', 'search', 'detail'), f'{label}.source必须为saved/search/detail')
        timestamp(metrics['captured_at'], label + '.captured_at', allow_unknown=metrics['source'] == 'saved')


def validated_image(root, relative):
    require(isinstance(relative, str) and IMAGE_RE.fullmatch(relative), f'图片不是本地safe路径：{relative}')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    require(path.is_relative_to(root) and path.is_file(), f'图片缺失或越界：{relative}')
    raw = path.read_bytes()
    try:
        with Image.open(path) as image:
            require(image.format in ('JPEG', 'PNG', 'WEBP'), f'图片字节格式不支持：{relative}')
            image.verify()
        with Image.open(path) as image:
            image.load()
            require(image.width > 0 and image.height > 0, f'图片尺寸无效：{relative}')
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValidationError(f'图片不能解码：{relative}') from exc
    return hashlib.sha256(raw).hexdigest()


def published_image_path(relative, sha):
    require(isinstance(relative, str) and IMAGE_RE.fullmatch(relative), '图源不是本地safe路径')
    require(isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{64}', sha), '发布图片哈希无效')
    path = Path(relative)
    return f'imgs/{path.stem}_v6_{sha[:12]}{path.suffix}'


def validate_image_sources(images, sources):
    require(isinstance(images, dict) and isinstance(sources, dict) and set(images) == set(sources),
            'quality.image_sources必须完整映射全部发布图片')
    reverse = {}
    for public, source in sources.items():
        require(public == published_image_path(source, images[public]), '发布图片名与图源/哈希映射不符')
        require(source not in reverse, '同一图源不能映射多个发布路径')
        reverse[source] = public
    return reverse


def validate_manifest(manifest):
    require(isinstance(manifest, dict), 'manifest必须为对象')
    require(strict_date(manifest.get('as_of'), 'manifest.as_of') <= datetime.now(CST).date(), 'manifest.as_of不能来自未来')
    end = strict_date(manifest.get('as_of'), 'manifest.as_of')
    require(isinstance(manifest.get('run_id'), str) and bool(manifest['run_id'].strip()), 'manifest.run_id缺失')
    require(manifest.get('data_kind') in ('production', 'test_fixture'), 'manifest.data_kind必须显式声明production或test_fixture')
    groups = manifest.get('groups')
    required = manifest.get('required_groups')
    require(isinstance(groups, list) and groups and isinstance(required, list) and required,
            'manifest必须列明groups和required_groups，不能静默忽略失败组')
    require(all(isinstance(key, str) and key for key in required) and len(required) == len(set(required)), 'required_groups重复或无效')
    require(all(isinstance(g, dict) for g in groups), 'groups条目必须为对象')
    keys = [g.get('key') for g in groups]
    require(all(isinstance(key, str) and key for key in keys) and len(keys) == len(set(keys)), 'manifest组key重复或无效')
    require(set(required) <= set(keys), 'manifest缺少required组')
    sections = set()
    for g in groups:
        require(g.get('section') in SECTIONS, 'manifest仅允许三主栏')
        require(g.get('run_id') == manifest['run_id'] and g.get('as_of') == manifest['as_of'], f'组 {g["key"]} 跨批次或日期不匹配')
        require(type(g.get('required')) is bool, f'组 {g["key"]} 缺少required布尔值')
        require(g['required'] == (g['key'] in required), 'required_groups与group.required不一致')
        require(isinstance(g.get('status'), str), 'group.status缺失')
        if g['required']:
            sections.add(g['section'])
            require(g['status'] == 'success', f'coverage阻断：{g["key"]}={g["status"]}')
    require(sections == set(SECTIONS), 'coverage必须含全部三主栏required组')
    return end, {'required': len(required), 'succeeded': len(required), 'complete': True,
                 'optional_failed': sum(not g['required'] and g['status'] != 'success' for g in groups)}


def reading_groups(notes, end):
    """复用冻结build_v6.py的主题合并和分桶；时点由as_of派生。"""
    result = {}
    def likes(n):
        value = n['display_metrics']['likes']
        return value if type(value) is int else -1
    def timekey(n):
        return n['published_at_cst']
    for section in SECTIONS:
        grouped = {}
        for n in notes:
            if n['primary_section'] == section:
                # 产品栏样本少、每篇内容差异大，按用户要求逐篇平铺不合并；
                # 营销栏保持同主题合并。
                key = n['id'] if section == 'product' else n['event_key']
                grouped.setdefault(key, []).append(n)
        events = []
        for key, members in grouped.items():
            members.sort(key=lambda n: (likes(n), timekey(n)), reverse=True)
            events.append({'key': key, 'ids': [n['id'] for n in members], 'likes': max(likes(n) for n in members),
                           'time': max(timekey(n) for n in members),
                           'month': any(n['date'] >= end.replace(day=1).isoformat() for n in members),
                           'recent': any(n['recent'] for n in members), 'reserved': any(n['event_status'] for n in members),
                           'label': members[0]['event_label']})
        events.sort(key=lambda e: (e['likes'], e['time']), reverse=True)
        if section == 'sentiment':
            top = sorted([e for e in events if e['month']], key=lambda e: e['time'], reverse=True)
            buckets = [('current', '当月具体问题', top, True, '按发布时间排列；低赞的具体问题同样保留。'),
                       ('history', '历史关注问题', [e for e in events if not e['month']], True, '按已保存点赞排列；不是本周新增故障。')]
        else:
            top = [e for e in events if e['month'] and not e['reserved']][:6]
            topkeys = {e['key'] for e in top}
            recent = sorted([e for e in events if e['recent'] and e['key'] not in topkeys], key=lambda e: e['time'], reverse=True)
            usedkeys = topkeys | {e['key'] for e in recent}
            rest = [e for e in events if e['month'] and e['key'] not in usedkeys]
            history = [e for e in events if not e['month']]
            if section == 'marketing':
                buckets = [('popular', '热门活动案例', top, True, '按样本内点赞排列，同主题合并；并非平台热榜。'),
                           ('recent', '近期新发现', recent, True, '近7天发布，按日期排列；新帖子不等于新活动，最多直接展示前9篇，其余可展开。'),
                           ('month', '当月其他活动', rest, len(recent) < 3, '当月其余资料按点赞排列；活动是否仍有效需另行核验。'),
                           ('history', '历史参考', history, len(recent) < 3, '前60天中当月以前的资料，不混算本期新增。')]
            else:
                buckets = [('popular', '当月重点使用案例', top, True, '优先较高赞且细节具体的案例；样本低赞时不强凑热门。'),
                           ('recent', '近期新发现', recent, True, '近7天发布，按日期排列；新帖子不等于新活动，具体变化另行标注。'),
                           ('month', '当月其他证据', rest, len(recent) < 3, '当月其余资料按点赞排列；活动是否仍有效需另行核验。'),
                           ('history', '历史参考', history, len(recent) < 3, '前60天中当月以前的资料，不混算本期新增。')]
        result[section] = [{'key': key, 'title': title, 'events': es, 'open': opened, 'note': text}
                           for key, title, es, opened, text in buckets if es]
    return result


def assert_not_confirmed(text):
    stripped = re.sub(r'(?:未|未经|不能|不应|不代表|并非|非已|不当作|不将|不是)(?:独立)?(?:已)?(?:确认|核验|认定)[^，。；\n]*', '', text)
    require(not re.search(r'已确认(?:投诉|故障|新上线)|官方(?:确认|规则|规定|保证)|已证实|平台确实|人人可领|保证收益', stripped),
            '不可自动确认投诉/故障/官方规则或普遍保证；请改为明确归因、待核验表述')


def validate_issue_scope(review, evidence, image_quotes):
    require(review.get('object') in ISSUE_SUBJECTS, 'sentiment对象准入仅允许理财通/零钱通')
    scope = review.get('issue_scope')
    require(isinstance(scope, dict), 'sentiment缺少issue_scope对象')
    require(scope.get('subject') in ISSUE_SUBJECTS and scope['subject'] == review['object'],
            'issue_scope.subject必须与object一致且为理财通/零钱通')
    require(scope.get('topic') in ISSUE_TOPICS, 'issue_scope.topic不在问题枚举内')
    require(scope.get('source') in ('desc', 'image'), 'issue_scope.source必须为desc/image')
    quote = scope.get('quote')
    require(isinstance(quote, str) and bool(quote.strip()), 'issue_scope.quote必须为非空原文')
    if scope['source'] == 'desc':
        require(quote in evidence['desc'], 'issue_scope.quote不是evidence.desc逐字子串')
    else:
        image = scope.get('image')
        require(isinstance(image, str) and image in evidence['imgs'], 'issue_scope.image不在evidence.imgs')
        require(isinstance(image_quotes, list) and any(isinstance(item, dict) and item.get('image') == image
                and item.get('quote') == quote for item in image_quotes), 'issue_scope图引文未绑定review.image_quotes')


def validate_claims(item, byid, allnotes):
    claims = item.get('claims')
    require(isinstance(claims, list), '摘要缺少claims数字校验清单（无数字也须[]）')
    checked_tokens = set()
    for claim in claims:
        require(isinstance(claim, dict), 'claim必须为对象')
        field, token = claim.get('field'), claim.get('token')
        require(field in ('title', 'body', 'action', 'scope') and field in item and isinstance(token, str)
                and token in item[field] and NUMBER_RE.search(token), 'claim.field/token无效')
        kind = claim.get('kind')
        if kind == 'window':
            require(claim.get('measure') in ('recent_days', 'history_days'), 'window claim无效')
            expected = 7 if claim['measure'] == 'recent_days' else 60
        elif kind == 'count':
            require(claim.get('section') in SECTIONS and claim.get('measure') in ('recent', 'total'), 'count claim无效')
            selection = [n for n in allnotes if n['primary_section'] == claim['section']]
            expected = sum(n['recent'] for n in selection) if claim['measure'] == 'recent' else len(selection)
        else:
            nid = claim.get('id')
            require(nid in item['refs'] and nid in byid, 'claim必须绑定本摘要refs')
            n = byid[nid]
            if kind == 'metric':
                require(claim.get('metric') in METRICS, 'metric claim字段无效')
                expected = n['display_metrics'][claim['metric']]
                require(expected is not None, '不能引用未知互动数字')
            elif kind == 'pub':
                require(claim.get('part') in ('year', 'month', 'day'), 'pub claim字段无效')
                expected = getattr(date.fromisoformat(n['date']), claim['part'])
            elif kind == 'evidence':
                quote = claim.get('quote')
                require(isinstance(quote, str) and quote and quote in n['evidence']['desc'] and token in quote, '正文数字必须有原文逐字引文')
                expected = claim.get('value')
            elif kind == 'image_quote':
                image = claim.get('image')
                quote = claim.get('quote')
                approved = n.get('_image_quotes', [])
                require(image in n['evidence']['imgs'] and isinstance(quote, str) and token in quote
                        and {'image': image, 'quote': quote} in approved, '图中数字需review.image_quotes人工绑定')
                expected = claim.get('value')
            else:
                raise ValidationError('未知claim.kind')
        require(type(expected) in (int, float) and math.isfinite(expected)
                and float(token.replace(',', '')) == expected and claim.get('value') == expected, '摘要数字与来源不一致')
        checked_tokens.add((field, token))
    for field in ('title', 'body', 'action', 'scope'):
        for match in NUMBER_RE.finditer(item.get(field, '')):
            require((field, match.group()) in checked_tokens, f'未校验摘要数字：{field}={match.group()}')


def validate_editorial(editorial, manifest, notes):
    require(isinstance(editorial, dict) and editorial.get('as_of') == manifest['as_of']
            and editorial.get('run_id') == manifest['run_id'], 'editorial与manifest的as_of/run_id必须匹配，禁止沿用上期摘要')
    require(editorial.get('status') == 'approved' and isinstance(editorial.get('reviewed_by'), str)
            and bool(editorial['reviewed_by'].strip()), 'editorial需本期人工/agent审核approved')
    reviewed = timestamp(editorial.get('reviewed_at'), 'editorial.reviewed_at')
    require(reviewed.date() >= strict_date(manifest['as_of'], 'as_of'), 'editorial审核早于本期as_of')
    require(reviewed <= datetime.now(CST) + timedelta(minutes=5), 'editorial审核时点不能来自未来')
    insights, overview = editorial.get('insights'), editorial.get('overview')
    require(isinstance(insights, dict) and set(insights) == set(SECTIONS), 'editorial.insights必须含且只含三栏')
    require(isinstance(overview, list) and len(overview) <= 3, 'overview最多三条且每栏最多一条')
    byid = {n['id']: n for n in notes}
    used, overview_sections = set(), set()
    def item_check(item, section, unique):
        require(isinstance(item, dict), '摘要必须为对象')
        fields = ('title', 'body', 'action', 'scope') if unique else ('title', 'body')
        for key in fields:
            require(isinstance(item.get(key), str) and item[key].strip(), f'摘要缺少{key}')
            assert_not_confirmed(item[key])
        refs = item.get('refs')
        require(isinstance(refs, list) and refs and all(isinstance(nid, str) for nid in refs)
                and len(refs) == len(set(refs)), '摘要必须有不重复refs，不自动填充')
        for nid in refs:
            require(nid in byid and byid[nid]['primary_section'] == section, '摘要引用未发布/窗口外/跨栏证据')
            if section == 'sentiment':
                validate_issue_scope(byid[nid], byid[nid]['evidence'], byid[nid].get('_image_quotes', []))
            if unique:
                require(nid not in used, 'insights全局重复refs')
                used.add(nid)
        if re.search(r'近期|近7天|本周', item.get('scope', '') + ' ' + item.get('title', '')):
            require(any(byid[nid]['recent'] for nid in refs), '近期摘要不能引用只有旧日期的证据')
        validate_claims(item, byid, notes)
        return {key: copy.deepcopy(item[key]) for key in fields + ('refs',)}
    cleaned_insights = {}
    for section in SECTIONS:
        require(isinstance(insights[section], list) and len(insights[section]) <= 3, '每栏insights最多三条，不凑数')
        cleaned_insights[section] = [item_check(item, section, True) for item in insights[section]]
    cleaned_overview = []
    for item in overview:
        require(isinstance(item, dict) and item.get('tab') in SECTIONS and item['tab'] not in overview_sections, 'overview跨栏或重复栏目')
        overview_sections.add(item['tab'])
        cleaned_overview.append({'tab': item['tab'], **item_check(item, item['tab'], False)})
    return cleaned_insights, cleaned_overview


def build_report(store, reviews, editorial, manifest, asset_root, minimums=None):
    end, coverage = validate_manifest(manifest)
    require(isinstance(store, dict) and isinstance(store.get('notes'), list), 'store必须包含notes完整列表')
    review_map = reviews.get('reviews') if isinstance(reviews, dict) else None
    require(isinstance(review_map, dict), 'reviews必须包含按ID映射的reviews对象')
    rawnotes = store['notes']
    notes, ids, image_hashes, audit = [], set(), {}, []
    for row in rawnotes:
        require(isinstance(row, dict) and isinstance(row.get('id'), str)
                and re.fullmatch(r'[A-Za-z0-9_-]+', row['id']), 'note.id无效')
        nid = row['id']
        require(nid not in ids, f'证据ID重复：{nid}')
        ids.add(nid)
        ev = row.get('evidence')
        require(isinstance(ev, dict), f'{nid}缺少全文evidence对象')
        for key in ('t', 'a', 'url', 'desc', 'imgs', 'tags', 'pub', 'cmts'):
            require(key in ev, f'{nid}缺少全文字段 {key}')
        require(all(isinstance(ev[key], str) for key in ('t', 'a', 'desc')), f'{nid}原文标题/作者/正文类型错误')
        origin = safe_origin(ev['url'])
        require(unquote(urlsplit(origin).path).rstrip('/').split('/')[-1] == nid, f'{nid}原帖URL ID不符')
        validate_metrics(ev, nid + '.evidence')
        if 'captured_at' in ev:
            timestamp(ev['captured_at'], nid + '.evidence.captured_at', allow_unknown=True)
        require(isinstance(ev['imgs'], list) and len(ev['imgs']) == len(set(ev['imgs'])), f'{nid}图片列表重复或缺失')
        require(isinstance(ev['tags'], list) and all(isinstance(t, str) for t in ev['tags']), f'{nid}tags类型错误')
        require(isinstance(ev['cmts'], list), f'{nid}cmts必须存在（无采样用[]）')
        require(bool(ev['desc'].strip()) or bool(ev['imgs']), f'{nid}既无全文也无图片，不是完整证据')
        public_cmts = []
        for c in ev['cmts']:
            require(isinstance(c, dict) and isinstance(c.get('c'), str), f'{nid}评论缺少原文')
            require(all(key not in c or isinstance(c[key], str) for key in ('u', 'd')), f'{nid}评论字段错误')
            require('likes' in c and (c['likes'] is None or type(c['likes']) is int and c['likes'] >= 0), f'{nid}评论点赞无效')
            public_cmts.append({key: c[key] for key in ('u', 'd', 'c', 'likes') if key in c})
        for relative in ev['imgs']:
            if relative not in image_hashes:
                image_hashes[relative] = validated_image(asset_root, relative)
        require(type(row.get('image_count')) is int and row['image_count'] == len(ev['imgs']), f'{nid}image_count必填且须匹配完整图片列表')
        pub = publication_time(ev['pub'])
        metrics = copy.deepcopy(row.get('display_metrics')) if 'display_metrics' in row else {
            **{key: ev[key] for key in METRICS}, 'captured_at': ev.get('captured_at') or None, 'source': 'saved'}
        validate_metrics(metrics, nid + '.display_metrics', snapshot=True)
        metrics['captured_at'] = metrics['captured_at'] or None
        review = review_map.get(nid)
        reasons = []
        if review is None:
            reasons.append('missing_review')
        else:
            require(isinstance(review, dict) and all(key in review for key in REVIEW_FIELDS), f'{nid}review审核字段缺失')
            require(review['evidence_sha256'] == digest(ev), f'{nid}evidence_sha256不匹配，需重新审核')
            require(review['primary_section'] in SECTIONS + ('internal', 'archive'), f'{nid}primary_section不允许')
            require(all(isinstance(review[key], str) for key in REVIEW_FIELDS if key not in ('topics',)), f'{nid}review字段类型错误')
            require(all(review[key].strip() for key in ('object', 'attitude', 'verification', 'event_key')), f'{nid}review核心字段为空')
            require(isinstance(review['topics'], list) and all(isinstance(t, str) and t.strip() for t in review['topics']), f'{nid}topics错误')
            require(review['attitude'] in ('正向体验', '中性分享', '负面体验', '疑问求助', '混合/待判'), f'{nid}attitude不在审核枚举内')
            assert_not_confirmed(review['analyst_note'] + '。' + review['verification'] + '。' + review['event_status'])
            require(isinstance(review.get('image_sha256'), dict)
                    and review['image_sha256'] == {p: image_hashes[p] for p in ev['imgs']}, f'{nid}缺少审核图片哈希或审核后图片字节变化')
            if review['primary_section'] not in SECTIONS:
                reasons.append('internal_section')
        if pub is None:
            reasons.append('unknown_pub')
        elif pub.date() > end:
            reasons.append('future_pub')
        elif pub.date() < end - timedelta(days=59):
            reasons.append('outside_window')
        audit.append({'id': nid, 'evidence_sha256': digest(ev), 'publication': 'internal' if reasons else 'public', 'reasons': reasons})
        if reasons:
            continue
        if review['primary_section'] == 'sentiment':
            validate_issue_scope(review, ev, review.get('image_quotes', []))
        n = {key: copy.deepcopy(review[key]) for key in REVIEW_FIELDS if key != 'evidence_sha256'}
        if review['primary_section'] == 'sentiment':
            n['issue_scope'] = copy.deepcopy(review['issue_scope'])
        public_ev = {key: copy.deepcopy(ev[key]) for key in ('t', 'a', 'desc', 'imgs', 'tags', 'pub')}
        public_ev.update(url=origin, cmts=public_cmts, **{key: ev[key] for key in METRICS})
        if 'captured_at' in ev:
            public_ev['captured_at'] = ev['captured_at']
        n.update(id=nid, title=ev['t'].strip() or re.sub(r'#[^#\n]+#', '', ev['desc']).strip().split('\n')[0][:48] or '无文字标题 · 查看原始图片',
                 author=ev['a'], date=pub.date().isoformat(), date_basis='详情发布时间 · CST', published_at_cst=pub.isoformat(timespec='seconds'),
                 recent=pub.date() >= end - timedelta(days=6), outside_window=False, sections=[review['primary_section']],
                 sentiment_kind='direct' if review['primary_section'] == 'sentiment' else 'none', evidence=public_ev,
                 display_metrics={key: metrics[key] for key in METRICS + ('captured_at', 'source')},
                 metrics_refreshed=metrics['source'] != 'saved', image_count=len(ev['imgs']),
                 _image_quotes=copy.deepcopy(review.get('image_quotes', [])))
        if not n['recent']:
            n['event_status'] = ''
        notes.append(n)
    insights, overview = validate_editorial(editorial, manifest, notes)
    # 数字引文仍用原图路径审核；仅公开转换阶段替换图片引用，不改store/review。
    public_images, image_sources = {}, {}
    for n in notes:
        n.pop('_image_quotes')
        published = []
        for source in n['evidence']['imgs']:
            sha = image_hashes[source]
            public = published_image_path(source, sha)
            require(public not in public_images or (public_images[public] == sha and image_sources[public] == source),
                    '发布图片路径碰撞，拒绝不同图源或字节共用路径')
            public_images[public] = sha
            image_sources[public] = source
            published.append(public)
        if n['primary_section'] == 'sentiment' and n['issue_scope']['source'] == 'image':
            n['issue_scope']['image'] = published[n['evidence']['imgs'].index(n['issue_scope']['image'])]
        n['evidence']['imgs'] = published
    validate_image_sources(public_images, image_sources)
    counts = {'unique': len(notes), 'recent_unique': sum(n['recent'] for n in notes),
              'direct': sum(n['primary_section'] == 'sentiment' for n in notes),
              'sections': {s: sum(n['primary_section'] == s for n in notes) for s in SECTIONS}}
    if minimums is None:
        minimums = DEFAULT_MINIMUMS
    require(set(minimums) == set(DEFAULT_MINIMUMS) and all(type(v) is int and v >= 0 for v in minimums.values()), 'minimums结构错误')
    if manifest['data_kind'] == 'production':
        require(all(minimums[k] >= v for k, v in DEFAULT_MINIMUMS.items()), 'production不得降低数量门槛')
    for key, limit in minimums.items():
        actual = counts['recent_unique'] if key == 'recent' else counts['sections'][key]
        require(actual >= limit, f'质量阻断：{key}={actual} < {limit}，不得凑数')
    state = editorial.get('publication')
    require(isinstance(state, dict) and state.get('status') in ('draft', 'ready', 'published'), 'publication.status必填')
    require(not (manifest['data_kind'] == 'test_fixture' and state['status'] != 'draft'), 'test_fixture只能draft，不可伪造正式ready')
    publication = {'status': state['status'], 'deployed_at': None, 'deployment_receipt_sha256': None}
    if state['status'] == 'published':
        receipt = state.get('deployment_receipt')
        require(isinstance(receipt, dict) and receipt.get('run_id') == manifest['run_id']
                and receipt.get('as_of') == manifest['as_of'] and receipt.get('verified') is True,
                'published需主代理提供匹配批次的已验证部署回执；构建器不部署')
        require(receipt.get('manifest_sha256') == digest(manifest) and receipt.get('editorial_content_sha256') == digest({k: v for k, v in editorial.items() if k != 'publication'})
                and receipt.get('evidence_sha256') == digest(rawnotes), '部署回执未绑定当前证据/编辑/manifest')
        timestamp(receipt.get('deployed_at'), 'deployment_receipt.deployed_at')
        require(isinstance(receipt.get('artifact_sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', receipt['artifact_sha256']), '部署回执缺少artifact_sha256')
        publication.update(deployed_at=receipt['deployed_at'], deployment_receipt_sha256=digest(receipt))
    provenance = {'run_id': manifest['run_id'], 'as_of': manifest['as_of'], 'data_kind': manifest['data_kind'],
                  'manifest_sha256': digest(manifest), 'editorial_sha256': digest(editorial), 'reviews_sha256': digest(review_map),
                  'evidence_sha256': digest(rawnotes), 'store_sha256': digest(store), 'image_sha256': digest(public_images)}
    report = {'version': VERSION, 'data_as_of': end.isoformat(), 'built_at': datetime.now(CST).isoformat(timespec='seconds'),
              'recent_start': (end - timedelta(days=6)).isoformat(), 'month_start': end.replace(day=1).isoformat(),
              'window_start': (end - timedelta(days=59)).isoformat(), 'source_count': len(notes), 'image_count': len(public_images),
              'counts': counts, 'visible_counts': copy.deepcopy(counts), 'notes': notes, 'insights': insights, 'overview': overview,
              'reading_groups': reading_groups(notes, end), 'publication': publication, 'provenance': provenance,
              'coverage_note': f'搜索覆盖：本期计划搜索 {coverage["required"]} 组关键词，成功 {coverage["succeeded"]} 组，补充词失败 {coverage["optional_failed"]} 组。这只是“搜过哪些词”，不代表覆盖全平台或热门榜。',
              'publication_notice': ('合成测试fixture，仅验证程序与版式，不代表真实采集或本期审核。' if manifest['data_kind'] == 'test_fixture' else '本报告为本期已审核样本观察，不代表全平台声量、活动有效性或已确认投诉。用户自述与业务判断分开保留。')}
    quality = {'schema_version': 1, 'version': VERSION, 'passed': True,
               'production_data_passed': manifest['data_kind'] == 'production', 'publication': publication,
               'provenance': provenance, 'coverage': coverage, 'counts': counts, 'minimums': minimums,
               'image_count': len(public_images), 'fulltext_count': len(notes), 'metrics_keys': list(METRICS),
               'metrics_null_fields': sum(n['display_metrics'][k] is None for n in notes for k in METRICS),
               'report_sha256': digest(report), 'images': public_images, 'image_sources': image_sources}
    internal = {'notes_total': len(rawnotes), 'items': audit, 'all_image_sha256': image_hashes}
    return report, quality, internal


def render_html(report, template_path=None):
    template = Path(template_path or Path(__file__).with_name('report_v6_template.html')).read_text(encoding='utf-8')
    require(template.count('__REPORT_DATA__') == 1, '模板占位符必须恰好一个')
    serialized = canonical(report).decode('utf-8').replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return template.replace('__REPORT_DATA__', serialized)


def write_bundle(report, quality, asset_root, output, source_snapshot=None):
    output = Path(output).resolve()
    require(output.parent.is_dir(), 'output父目录必须已存在')
    require(not output.exists(), 'output必须为新批次目录；不覆盖已有报告')
    validate_image_sources(quality.get('images'), quality.get('image_sources'))
    require(set(quality['images']) == {p for n in report['notes'] for p in n['evidence']['imgs']},
            '公开图片引用与quality映射不一致')
    require(quality['report_sha256'] == digest(report)
            and report['provenance']['image_sha256'] == digest(quality['images']), '公开内容或图片摘要与quality不一致')
    html = render_html(report)
    quality = copy.deepcopy(quality)
    quality['html_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
    staging = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        (staging / 'imgs').mkdir()
        for relative, sha in quality['images'].items():
            source_relative = quality['image_sources'][relative]
            source = Path(asset_root) / source_relative
            require(validated_image(asset_root, source_relative) == sha, f'校验后图片字节变化：{source_relative}')
            shutil.copyfile(source, staging / relative)
            require(validated_image(staging, relative) == sha, f'图片复制字节不一致：{relative}')
        for name, obj in [('public_report_v6.json', report), ('report_quality_v6.json', quality)]:
            (staging / name).write_bytes(canonical(obj))
        (staging / 'bank_marketing_report.html').write_text(html, encoding='utf-8')
        if source_snapshot:
            require(all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == expected for p, expected in source_snapshot.items()), '原子发布前输入文件被并发改写')
        require(not output.exists(), 'output出现并发写入，停止')
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return quality


def migrate_seed(seed_path, destination):
    """一次迁移；全文不改写，种子编辑待审，不作为任何一期默认值。"""
    seed = read_json(seed_path)
    destination = Path(destination)
    require(destination.is_dir(), '迁移父目录必须存在')
    paths = [destination / 'review_seed.json', destination / 'editorial_seed.json']
    require(not any(p.exists() for p in paths), '迁移seed已存在，拒绝覆盖')
    review_map = {}
    seed_notes = []
    for n in seed['notes']:
        ev = copy.deepcopy(n['evidence'])
        review = {key: copy.deepcopy(n[key]) for key in REVIEW_FIELDS if key != 'evidence_sha256'}
        review.update(evidence_sha256=digest(ev),
                      image_sha256={p: validated_image(Path(seed_path).parent, p) for p in ev['imgs']},
                      migrated_from='V6已审metadata；迁移不代表本期新核验')
        review_map[n['id']] = review
        seed_notes.append({'id': n['id'], 'evidence': ev, 'display_metrics': copy.deepcopy(n['display_metrics']), 'image_count': len(ev['imgs'])})
    reviewed = {'schema_version': 1, 'reviews': review_map,
                'migration_seed_store': {'schema_version': 1, 'notes': seed_notes},
                'migration': {'seed_sha256': digest(seed), 'as_of': seed['data_as_of'], 'note_count': len(seed_notes),
                              'note': '主代理一次合入evidence_store并复制原图；周更不依赖outputs。'}}
    editorial = {'schema_version': 1, 'as_of': seed['data_as_of'], 'run_id': None, 'status': 'needs_review',
                 'reviewed_by': None, 'reviewed_at': None, 'publication': {'status': 'draft'},
                 'insights': copy.deepcopy(seed['insights']), 'overview': copy.deepcopy(seed['overview'])}
    for items in editorial['insights'].values():
        for item in items:
            item['claims'] = []
    for item in editorial['overview']:
        item['refs'] = []
        item['claims'] = []
    paths[0].write_bytes(canonical(reviewed))
    paths[1].write_bytes(canonical(editorial))
    return {'migrated': len(seed_notes), 'editorial_status': 'needs_review', 'seed_sha256': digest(seed)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='V6.2离线构建，失败不产出或覆盖发布目录')
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    for flag in ('store', 'reviews', 'editorial', 'manifest', 'output'):
        b.add_argument('--' + flag, required=True)
    b.add_argument('--asset-root', help='本地图根目录；默认生产脚本所在目录')
    b.add_argument('--test-fixture', action='store_true', help='仅测试降低数量阈值，要求manifest为test_fixture且只能draft')
    m = sub.add_parser('migrate-seed')
    m.add_argument('--seed', required=True)
    m.add_argument('--destination', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'migrate-seed':
            result = migrate_seed(args.seed, args.destination)
        else:
            source_paths = [args.store, args.reviews, args.editorial, args.manifest]
            source_hashes = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths]
            inputs = [read_json(p) for p in source_paths]
            require(source_hashes == [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths], '读取期间输入文件被并发改写')
            if args.test_fixture:
                require(inputs[3].get('data_kind') == 'test_fixture', '--test-fixture不能绕过production阈值')
            root = Path(args.asset_root) if args.asset_root else Path(__file__).resolve().parent
            report, quality, _ = build_report(*inputs, root, minimums={k: 0 for k in DEFAULT_MINIMUMS} if args.test_fixture else None)
            require(source_hashes == [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths], '构建期间输入文件被并发改写')
            quality['input_file_sha256'] = dict(zip(('store', 'reviews', 'editorial', 'manifest'), source_hashes))
            quality = write_bundle(report, quality, root, args.output, dict(zip(source_paths, source_hashes)))
            result = {'output': str(Path(args.output).resolve()), 'counts': quality['counts'],
                      'data_kind': report['provenance']['data_kind'], 'publication': report['publication']['status'],
                      'production_data_passed': quality['production_data_passed']}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
