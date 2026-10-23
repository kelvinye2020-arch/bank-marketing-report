"""V6隔离采集；不调用生成器、git、自动化或通知。"""
import argparse
import copy
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.dont_write_bytecode = True
import requests
from PIL import Image
import mcp_pool as mp

ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))


def now():
    return datetime.now(CST).isoformat(timespec='seconds')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def mkdir(path):
    if not path.parent.is_dir():
        raise FileNotFoundError('父目录不存在: ' + str(path.parent))
    path.mkdir(exist_ok=True)
    return path


def atomic(path, value):
    if not path.parent.is_dir():
        raise FileNotFoundError('父目录不存在: ' + str(path.parent))
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('xb') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def image_info(blob):
    with Image.open(io.BytesIO(blob)) as im:
        fmt, size = im.format, im.size
        im.verify()
    if fmt not in ('JPEG', 'PNG', 'WEBP') or min(size) <= 0:
        raise ValueError('不是有效jpg/png/webp')
    with Image.open(io.BytesIO(blob)) as im:
        im.load()
        if min(im.size) <= 0:
            raise ValueError('图片不能完整解码')
    return {'format': fmt, 'width': size[0], 'height': size[1], 'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}


def versioned_image(directory, name, blob):
    info = image_info(blob)
    ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[info['format']]
    base = directory / (name + ext)
    if base.exists() and base.read_bytes() != blob:
        base = directory / (name + '_v' + info['sha256'][:16] + ext)
    if base.exists():
        if base.read_bytes() != blob:
            raise ValueError('版本名冲突，禁止覆盖')
    else:
        # 独占创建，所有采集写入由运行锁保护。
        with base.open('xb') as f:
            f.write(blob)
    return base, info


def parse_count(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '').replace('，', '')
    if not text:
        return None
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([万亿wWkK]?)\+?', text)
    if not m:
        return None
    return int(float(m[1]) * {'': 1, '万': 10000, '亿': 100000000, 'w': 10000, 'k': 1000}.get(m[2].lower(), 1))


def metrics(interact, captured_at, source):
    interact = interact or {}
    # likeCount与likedCount是两个实际返回形态；空值不借用历史字段。
    likes = interact['likeCount'] if 'likeCount' in interact else interact.get('likedCount')
    return {'captured_at': captured_at, 'source': source, 'likes': parse_count(likes),
            'collects': parse_count(interact.get('collectedCount')),
            'comments': parse_count(interact.get('commentCount')),
            'shares': parse_count(interact.get('sharedCount'))}


def unpack(response):
    if not isinstance(response, dict) or response.get('error'):
        raise ValueError('JSON-RPC失败或非对象')
    result = response.get('result')
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError('工具失败或result缺失')
    for part in result.get('content') or []:
        if part.get('type') == 'text':
            payload = json.loads(part.get('text', ''))
            if not isinstance(payload, dict) or payload.get('isError') or payload.get('error') or payload.get('success') is False:
                raise ValueError('工具内容失败或格式异常')
            return payload
    raise ValueError('缺少有效JSON工具正文，不当零成功')


def note_time(note):
    value = note.get('time')
    if value is None or value == '':
        return None
    try:
        stamp = float(value)
        if stamp > 100000000000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, CST)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def id_time(nid):
    try:
        if not re.fullmatch(r'[a-fA-F0-9]{24}', nid):
            return None
        return datetime.fromtimestamp(int(nid[:8], 16), CST)
    except (ValueError, OverflowError, OSError):
        return None


def safe_url(value):
    p = urlsplit(value or '')
    if p.scheme not in ('https', 'http'):
        return ''
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


def safe_text(value):
    return re.sub(r'[\U00010000-\U0010ffff\u2600-\u27ff\ufe0f]', '', str(value or ''))


def safe_evidence(ev):
    result = copy.deepcopy(ev)
    result['url'] = safe_url(result.get('url'))
    for key in ('t', 'a', 'desc'):
        if key in result:
            result[key] = safe_text(result[key])
    for c in result.get('cmts') or []:
        for key in ('u', 'c'):
            if key in c:
                c[key] = safe_text(c[key])
    return result


class Collector:
    def __init__(self, config, args):
        self.config, self.args = config, args
        self.rt = config['runtime']
        state = ROOT / self.rt['state_dir']
        if state.resolve().parent != ROOT:
            raise ValueError('状态目录必须直属工作库')
        self.store_path = state / 'evidence_store.json'
        self.queue_path = state / 'candidate_queue.json'
        self.manifest_path = state / 'search_manifest.json'
        self.cutoff = now()
        self.end = datetime.fromisoformat(self.cutoff)
        self.lower = datetime.combine(self.end.date() - timedelta(days=self.rt['window_days'] - 1), datetime.min.time(), CST)
        previous = load(self.manifest_path) if self.manifest_path.exists() else None
        if previous is not None and not args.run_id:
            raise ValueError('已有采集manifest，必须显式run-id接续或以新run-id执行search')
        self.runid = args.run_id or self.end.strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:6]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.runid):
            raise ValueError('runid非法')
        rollover = previous is not None and previous.get('run_id') != self.runid
        if rollover and args.stage != 'search':
            raise ValueError('新run-id只能从search创建；details/verify必须同run同日接续')
        if previous is None and args.stage in ('details', 'verify'):
            raise ValueError('details/verify必须已有同run同日搜索manifest')
        if previous is not None and not rollover:
            if previous.get('data_as_of') != self.end.date().isoformat():
                raise ValueError('同run跨日禁止resume；请以新run-id执行search')
            if previous.get('config_sha256') != digest(config):
                raise ValueError('词源配置改变，不得续用旧搜索；仅新run search允许')
        run_path = state / 'runs' / self.runid
        if (rollover or previous is None) and run_path.exists():
            raise ValueError('新run-id目录已存在，禁止覆盖或复用历史批次')
        self.state = mkdir(state)
        self.runs = mkdir(state / 'runs')
        if rollover:
            self.archive_previous(previous)
        self.reuse = mkdir(state / 'reuse_details')
        self.imgs = mkdir(state / 'imgs')
        self.seed_imgs = mkdir(state / 'seed_imgs')
        self.run = mkdir(run_path)
        self.raw = mkdir(self.run / 'raw')
        self.catalog_path = self.run / 'catalog_internal.json'
        self.mutex = threading.RLock()
        self.store = None
        self.catalog = {}
        self.workers = []
        self.handles = []
        self.queue = {'schema_version': 6, 'run_id': self.runid, 'data_as_of': self.end.date().isoformat(),
                      'cutoff_at': self.cutoff, 'window_start': self.lower.isoformat(), 'items': [],
                      'scope': '仅有界详情候选，未完成全搜索结果正文审核；全部禁止自动发布'}
        if previous is not None and not rollover:
            self.manifest = previous
            self.cutoff = previous['cutoff_at']
            self.end = datetime.fromisoformat(self.cutoff)
            self.lower = datetime.fromisoformat(previous['window_start'])
            self.catalog = load(self.catalog_path) if self.catalog_path.exists() else {}
            self.queue = load(self.queue_path) if self.queue_path.exists() else self.queue
            if self.queue.get('run_id') != self.runid or self.queue.get('data_as_of') != self.end.date().isoformat():
                raise ValueError('候选队列与同run同日manifest不一致')
        else:
            self.manifest = {'schema_version': 6, 'run_id': self.runid, 'started_at': self.cutoff,
                'data_as_of': self.end.date().isoformat(), 'cutoff_at': self.cutoff,
                'window_start': self.lower.isoformat(), 'config_sha256': digest(config),
                'fixed_required': 15, 'supplement_required': len(config['supplements']),
                'searches': [], 'details': [], 'status': 'pending', 'search_status': 'pending',
                'blockers': [], 'raw_private': True, 'all_results_reviewed': False}
            if rollover:
                self.manifest['previous_run_archive'] = (self.runs / previous['run_id'] / 'archive').relative_to(ROOT).as_posix()
                # CLI运行锁内：旧批完整留档后，先落新pending周期，失败不会回写旧manifest。
                # store保持原字节；reviews/editorial留原位，由主代理另行本期绑定。
                self.persist()
        self.download_executor = ThreadPoolExecutor(max_workers=self.rt['download_workers'])

    def archive_previous(self, previous):
        oldid = previous.get('run_id')
        if not isinstance(oldid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', oldid):
            raise ValueError('旧run-id非法，不能留档')
        oldrun = self.runs / oldid
        if not oldrun.is_dir() or oldrun.is_symlink() or oldrun.resolve().parent != self.runs.resolve():
            raise ValueError('旧run目录缺失或越界，不能切换周期')
        archive = oldrun / 'archive'
        if archive.exists():
            raise ValueError('旧批archive已存在，拒绝重复留档或覆盖')
        # 私有交接可能由主代理另命名；只复制state直属文件和旧run文件，不读取outputs。
        for name in ('search_manifest.json', 'candidate_queue.json', 'evidence_store.json'):
            if not (self.state / name).is_file():
                raise ValueError('旧批留档必要文件缺失：' + name)
        def inventory():
            names = {'search_manifest.json', 'candidate_queue.json', 'evidence_store.json'}
            for pattern in ('*review*', '*editorial*', '*handoff*', '*交接*'):
                names.update(p.name for p in self.state.glob(pattern) if p.is_file())
            files = {name: self.state / name for name in names}
            files.update({'run/' + p.relative_to(oldrun).as_posix(): p for p in oldrun.rglob('*') if p.is_file()})
            for path in files.values():
                if path.is_symlink() or not path.resolve().is_relative_to(self.state.resolve()):
                    raise ValueError('旧批留档文件越界')
            return files
        def asset_hashes():
            assets = {}
            for folder in ('imgs', 'seed_imgs'):
                directory = self.state / folder
                for path in directory.rglob('*'):
                    if not path.is_file():
                        continue
                    if path.is_symlink() or not path.resolve().is_relative_to(self.state.resolve()):
                        raise ValueError('累积证据图片越界')
                    assets[path.relative_to(self.state).as_posix()] = file_hash(path)
            return assets
        files = inventory()
        initial = {name: file_hash(p) for name, p in files.items()}
        preserved_assets = asset_hashes()
        staging = oldrun.with_name('.archive-' + oldid + '-' + uuid.uuid4().hex)
        staging.mkdir()
        try:
            for name, source in files.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open('rb') as inp, target.open('xb') as out:
                    shutil.copyfileobj(inp, out)
                    out.flush()
                    os.fsync(out.fileno())
                if file_hash(target) != initial[name]:
                    raise ValueError('旧批留档复制哈希不符')
            seal = {'schema_version': 1, 'run_id': oldid, 'data_as_of': previous.get('data_as_of'),
                    'archived_at': self.cutoff, 'next_run_id': self.runid, 'immutable': True,
                    'file_sha256': initial, 'files_sha256': digest(initial),
                    'preserved_asset_sha256': preserved_assets, 'assets_sha256': digest(preserved_assets),
                    'assets_preserved_in_state': True, 'reviews_editorial_not_modified': True}
            atomic(staging / 'archive_hashes.json', seal)
            if {name: file_hash(p) for name, p in inventory().items()} != initial or asset_hashes() != preserved_assets:
                raise ValueError('旧批留档期间输入被并发改写')
            if archive.exists():
                raise ValueError('旧批archive并发出现，禁止覆盖')
            os.rename(staging, archive)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def sync_report_manifest(self):
        rows = {r['key']: r for r in self.manifest['searches']}
        tasks = [{'key': 'f%02d' % i, 'section': g, 'keyword': kw, 'required': True, 'mode': 'default'}
                 for i, (g, kw) in enumerate(((g, kw) for g, kws in self.config['fixed_groups'].items() for kw in kws), 1)]
        for i, item in enumerate(self.config['supplements'], 1):
            section = next(g for g, words in self.config['fixed_groups'].items() if item['keyword'] in words)
            tasks.append(dict(item, key='s%02d' % i, section=section, required=False, mode='recent'))
        groups = []
        for task in tasks:
            row = rows.get(task['key'], {})
            group = dict(task, run_id=self.runid, as_of=self.end.date().isoformat(),
                         status='pending' if not row else row.get('status', 'failed'),
                         captured_at=row.get('captured_at'), exhaustive=False,
                         returned=row.get('returned'), note_entries=row.get('note_entries'))
            if row.get('status') == 'ok':
                attempts = [a for a in row.get('attempts', []) if a.get('status') == 'ok']
                if not attempts:
                    raise ValueError('成功检索缺少原响应路径')
                path = (ROOT / attempts[-1]['raw_private_path']).resolve()
                if not path.is_relative_to(self.raw.resolve()):
                    raise ValueError('原检索响应必须属于当前run，禁止跨批复用')
                raw = load(path)
                expected = {'keyword': task['keyword']}
                if task.get('filters'):
                    expected['filters'] = task['filters']
                payload = unpack(raw.get('response'))
                feeds = payload.get('feeds')
                if feeds is None:
                    feeds = (payload.get('data') or {}).get('feeds')
                captured = raw.get('finished_at')
                if raw.get('http_status') != 200 or raw.get('arguments') != expected or not isinstance(feeds, list) or not captured:
                    raise ValueError('原检索响应与固定词/模式不匹配')
                if datetime.fromisoformat(captured).astimezone(CST).date() != self.end.date():
                    raise ValueError('不能把跨日原检索响应当本日成功')
                group.update(status='success', captured_at=captured, original_request_at=raw['started_at'],
                             source='cached_exactdate' if raw.get('reused') else 'original_request',
                             cached_exactdate=self.end.date().isoformat() if raw.get('reused') else None,
                             raw_private_path=path.relative_to(ROOT).as_posix(), raw_sha256=file_hash(path),
                             returned=len(feeds), note_entries=sum(f.get('modelType') == 'note' and bool(f.get('id')) for f in feeds))
                if self.args.stage == 'verify':
                    group.update(consumed_as='cached_exactdate', cached_exactdate=self.end.date().isoformat(),
                                 cached_original_time=captured, no_new_request_in_handoff=True)
            groups.append(group)
        self.manifest.update(as_of=self.end.date().isoformat(), data_kind='production', groups=groups,
                             required_groups=[g['key'] for g in groups if g['required']],
                             captured_at=max((g['captured_at'] for g in groups if g.get('captured_at')), default=self.cutoff),
                             search_total=len(tasks), search_succeeded=sum(g['status'] == 'success' for g in groups),
                             required_search_succeeded=sum(g['required'] and g['status'] == 'success' for g in groups),
                             exhaustive=False, quota={'detail_limit': self.rt['detail_limit'],
                             'selected': len(self.manifest.get('detail_selected_ids', [])),
                             'attempted': len(self.manifest['details']), 'all_results_reviewed': False,
                             'notice': '只采前页及有界详情；ID时间仅粗排，未抓全文/未看图/未分类均不算已审核'},
                             asset_root=self.state.relative_to(ROOT).as_posix())

    def adapt_report_store(self):
        import report_v6 as report
        seed_url = next(n['_collector']['seed_original_note']['evidence']['url']
                        for n in self.store['notes'] if n['_collector']['origin'] == 'seed')
        origin = urlsplit(report.safe_origin(seed_url))
        for n in self.store['notes']:
            meta = n['_collector']
            ev = n['evidence']
            if meta['origin'] == 'seed':
                for img, asset in zip(ev['imgs'], meta['image_manifest']):
                    source = ROOT / asset['local_path']
                    target = self.state / img
                    if not target.parent.is_dir():
                        raise FileNotFoundError('证据图父目录缺失')
                    if target.exists() and target.read_bytes() != source.read_bytes():
                        raise ValueError('种子证据图冲突，保留原文路径并阻断；不得覆盖')
                    blob = source.read_bytes()
                    image_info(blob)
                    if not target.exists():
                        with target.open('xb') as f:
                            f.write(blob)
            else:
                meta.setdefault('pre_schema_evidence', copy.deepcopy(ev))
                meta.setdefault('pre_schema_evidence_sha256', digest(meta['pre_schema_evidence']))
                ev['imgs'] = [(ROOT / a['local_path']).relative_to(self.state).as_posix() for a in meta['image_manifest']]
                if not ev.get('url'):
                    path = origin.path.rsplit('/', 1)[0] + '/' + n['id']
                    ev['url'] = urlunsplit((origin.scheme, origin.netloc, path, '', ''))
                    meta['url_basis'] = 'known_platform_note_id_route;不是工具返回URL，不含访问token'
                ev['captured_at'] = meta.get('captured_at')
                meta['evidence_sha256'] = digest(ev)
                if meta.get('detail_metrics', {}).get('source') == 'cached_detail':
                    meta['detail_metrics']['source'] = 'detail'
                n['status'] = 'pending'
                n['review_status'] = 'pending'
            n['image_count'] = len(ev['imgs'])
            for img in ev['imgs']:
                report.validated_image(self.state, img)
            report.safe_origin(ev['url'])
            report.publication_time(ev['pub'])
            report.validate_metrics(ev, n['id'] + '.evidence')
            report.validate_metrics(n['display_metrics'], n['id'] + '.display_metrics', snapshot=True)
        self.store['asset_root'] = self.state.relative_to(ROOT).as_posix()
        self.store['schema_adapter'] = {'target': 'report_v6', 'adapted_at': now(),
                                      'raw_preserved': True, 'seed_notes_full_structure_preserved': True,
                                      'new_images_prefix_only': True, 'no_content_review_performed': True}
        self.prepare_queue()
        byid = {n['id']: n for n in self.store['notes']}
        for item in self.queue['items']:
            item['image_review_status'] = 'not_reviewed'
            if item.get('reason') == 'id_estimate_outside_not_publish_filter' and not item.get('published_at_verified'):
                item['status'] = 'skipped_quota'
                item['reason'] = 'bounded_detail_limit_coarse_priority_only'
                item['date_verified'] = False
            if item['id'] not in byid or byid[item['id']]['_collector']['origin'] == 'seed':
                continue
            n = byid[item['id']]
            item['date'] = n['date']
            item['date_basis'] = '详情note.time · CST'
            item['asset_root'] = self.store['asset_root']
            item['image_paths'] = list(n['evidence']['imgs'])
            item['image_sha256'] = {img: a['sha256'] for img, a in zip(n['evidence']['imgs'], n['_collector']['image_manifest'])}
            source = self.detail_evidence_path(n)
            item['raw_private_path'] = source.relative_to(ROOT).as_posix()
            item['raw_sha256'] = file_hash(source)
            item['detail_captured_at'] = n['_collector'].get('captured_at')
            item['detail_source'] = n['_collector'].get('detail_source')
            text = n['title'] + n['evidence']['desc']
            item['review_priority'] = 0 if re.search('中行|中国银行', text) else 1 if re.search('建行|建设银行|还款', text) else 2
        self.queue['items'].sort(key=lambda i: (i.get('status') != 'pending_review', i.get('review_priority', 3), i['id']))
        self.queue.update(asset_root=self.store['asset_root'], exhaustive=False,
                          image_review_notice='图片仅下载并PIL完整解码，未做视觉或业务审核，不得自动发布')

    def persist(self):
        with self.mutex:
            self.sync_report_manifest()
            if self.store is not None:
                self.store['updated_at'] = now()
                atomic(self.store_path, self.store)
                self.queue['evidence_store_sha256'] = file_hash(self.store_path)
            self.queue['updated_at'] = now()
            atomic(self.queue_path, self.queue)
            atomic(self.catalog_path, self.catalog)
            self.manifest['collector_sha256'] = file_hash(Path(__file__))
            self.manifest['evidence_store_sha256'] = self.queue.get('evidence_store_sha256')
            self.manifest['candidate_queue_sha256'] = file_hash(self.queue_path)
            atomic(self.manifest_path, self.manifest)
            atomic(self.run / 'manifest.json', self.manifest)

    def init_store(self):
        if self.store_path.exists():
            self.store = load(self.store_path)
            if self.store.get('schema_version') != 6:
                raise ValueError('store schema不匹配')
            for n in self.store['notes']:
                meta = n['_collector']
                if meta.get('origin') == 'seed' and digest(meta['seed_original_note']) != meta['seed_note_sha256']:
                    raise ValueError('种子原文哈希不符: ' + n['id'])
                if digest(n['evidence']) != meta['evidence_sha256']:
                    raise ValueError('原证据哈希不符: ' + n['id'])
                for asset in meta.get('image_manifest') or []:
                    p = ROOT / asset['local_path']
                    if not p.is_file() or file_hash(p) != asset['sha256']:
                        raise ValueError('原证据图片缺失或字节变化: ' + n['id'])
                    image_info(p.read_bytes())
            return
        if not self.args.seed:
            raise ValueError('首次migration必须显式--seed；之后复跑不依赖种子目录')
        seed_path = Path(self.args.seed).resolve()
        seed = load(seed_path)
        notes = seed.get('notes')
        if not isinstance(notes, list) or len(notes) != 80 or len({n['id'] for n in notes}) != 80:
            raise ValueError('种子不是完整80篇')
        if sum(len(n['evidence']['imgs']) for n in notes) != 226:
            raise ValueError('种子不是226张原图')
        migrated = []
        for original in notes:
            n = copy.deepcopy(original)
            assets = []
            for img in n['evidence']['imgs']:
                p = (seed_path.parent / img).resolve()
                if seed_path.parent not in p.parents or not p.is_file():
                    raise ValueError('seed图片不存在或路径非法')
                copied, info = versioned_image(self.seed_imgs, p.stem, p.read_bytes())
                assets.append(dict(info, original_ref=img, local_path=copied.relative_to(ROOT).as_posix()))
            n['_collector'] = {'origin': 'seed', 'seed_note_sha256': digest(original), 'seed_original_note': copy.deepcopy(original),
                'evidence_sha256': digest(n['evidence']), 'image_manifest': assets,
                'detail_status': 'complete', 'review_status': 'seed_preserved',
                'auto_publish': False, 'sources_internal': [], 'metrics_history': [],
                'comments_status': self.comments_status(n['evidence'].get('comments'), n['evidence'].get('cmts', []))}
            migrated.append(n)
        self.store = {'schema_version': 6, 'created_at': now(), 'migration': {
            'seed_sha256': file_hash(seed_path), 'seed_count': 80, 'seed_images': 226,
            'seed_original_readonly': True, 'asset_resolution': '_collector.image_manifest[].local_path',
            'runtime_seed_path_required': False}, 'notes': migrated}
        self.persist()

    @staticmethod
    def comments_status(count, samples):
        if samples:
            return 'sampled_top10'
        if count is not None and count > 0:
            return 'missing_sample'
        return 'zero_reported' if count == 0 else 'unknown_count_empty_sample'

    def record_queue(self, item):
        with self.mutex:
            items = self.queue['items']
            for i, old in enumerate(items):
                if old['id'] == item['id']:
                    items[i] = item
                    break
            else:
                items.append(item)

    def configure_pool(self):
        mp.BASE_DIR = str(self.run)
        mp.MCP_EXE = self.rt['mcp_exe']
        mp.CHROMIUM = self.rt['chromium']
        mp.MCP_HOST = '127.0.0.1'
        mp.RECYCLE_AFTER_CALLS = self.rt['recycle_after_calls']
        mp.log = lambda message: print(safe_text(message), flush=True)
        if not Path(mp.MCP_EXE).is_file() or not Path(mp.CHROMIUM).is_file():
            raise FileNotFoundError('explicit-bin或MCP不存在，禁止自动浏览器回落')
        dirs = [Path(d) if Path(d).is_absolute() else ROOT / d for d in self.rt['cookie_dirs']]
        dirs.append(Path(mp.MCP_EXE).parent)
        cookies = [d / 'cookies.json' for d in dirs if (d / 'cookies.json').is_file()]
        if not cookies:
            raise FileNotFoundError('没有可复用cookie；不将HTTP探活当登录成功')
        cookie = max(cookies, key=lambda p: p.stat().st_mtime_ns)
        mp.MCP_DIR = str(cookie.parent)
        atomic(self.run / 'environment_internal.json', {'ports': self.rt['ports'], 'explicit_bin': mp.CHROMIUM,
            'cookie_directory': str(cookie.parent), 'cookie_mtime_ns': cookie.stat().st_mtime_ns,
            'recycle_after_calls': 1, 'mp_base_dir': mp.BASE_DIR, 'service_same_process_tree': True,
            'login_evidence': '只有非错误search响应才成立，HTTP监听不作登录证明'})
        if any(mp.check_port(mp.MCP_HOST, port) for port in self.rt['ports']):
            raise RuntimeError('保留端口被占用；不复用或终止未知进程')
        for idx, port in enumerate(self.rt['ports']):
            proc, fh = mp.spawn_instance(port, idx, headless=True)
            self.handles.append(fh)
            w = mp.MCPWorker(idx, port, proc=proc, logfile=fh)
            self.workers.append(w)
            w.init_session(retries=1)
            r = w.session.post(w.url, json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list', 'params': {}}, headers=w.headers, timeout=20)
            r.raise_for_status()
            schema = r.json()
            atomic(self.raw / ('schema_' + str(port) + '.json'), schema)
            if schema.get('error'):
                raise ValueError('tools/list失败')
            tools = {t['name']: t['inputSchema'] for t in schema['result']['tools']}
            props = tools['search_feeds']['properties']
            if 'keyword' not in props or not {'sort_by', 'publish_time'} <= set(props['filters']['properties']):
                raise ValueError('search schema不匹配')
            if not {'feed_id', 'xsec_token', 'load_all_comments'} <= set(tools['get_feed_detail']['properties']):
                raise ValueError('detail schema不匹配')

    def close_pool(self):
        for w in self.workers:
            w.close()
        for fh in self.handles:
            fh.close()
        self.workers, self.handles = [], []

    def request(self, worker, tool, arguments, timeout, row):
        def capture(response, *args, **kwargs):
            row['http_status'] = response.status_code
            row['response_text'] = response.text
        worker.session.hooks['response'] = [capture]
        try:
            response = worker.call(tool, arguments, timeout=timeout)
            row['response'] = response
            if row.get('http_status') != 200:
                raise ValueError('非200工具响应')
            return unpack(response)
        finally:
            worker.session.hooks['response'] = []

    def schedule(self, tasks, fn):
        todo = deque(tasks)
        qlock = threading.Lock()
        def consume(w):
            while True:
                with qlock:
                    if not todo:
                        return
                    task = todo.popleft()
                fn(w, task)
        with ThreadPoolExecutor(max_workers=len(self.workers)) as ex:
            list(ex.map(consume, self.workers))

    def search(self):
        if self.manifest.get('search_finished_at'):
            print('同run搜索已收尾，保留首次响应和失败，不追加重试', flush=True)
            return
        fixed = [{'key': 'f%02d' % i, 'mode': 'default', 'group': g, 'keyword': kw}
                 for i, (g, kw) in enumerate(((g, kw) for g, kws in self.config['fixed_groups'].items() for kw in kws), 1)]
        supplements = [dict(p, key='s%02d' % i, mode='recent', group='supplement') for i, p in enumerate(self.config['supplements'], 1)]
        done = {r['key']: r for r in self.manifest['searches']}
        for mode, tasks in [('default', fixed), ('recent', supplements)]:
            stop = threading.Event()
            tasks = [t for t in tasks if done.get(t['key'], {}).get('status') != 'ok']
            def run(w, task):
                summary = dict(task, status='pending', attempts=[], returned=None, note_entries=None)
                if stop.is_set():
                    summary['status'] = 'skipped_mode_stop'
                else:
                    for attempt in range(1, self.rt['search_cold_retries'] + 2):
                        row = dict(task, attempt=attempt, started_at=now(), port=w.port, status='pending')
                        try:
                            if (attempt > 1 or w.needs_recycle()) and not w.restart():
                                raise RuntimeError('冷启动失败')
                            args = {'keyword': task['keyword']}
                            if task.get('filters'):
                                args['filters'] = task['filters']
                            row['arguments'] = args
                            started = time.monotonic()
                            payload = self.request(w, 'search_feeds', args, self.rt['search_timeout_seconds'], row)
                            row['call_seconds'] = round(time.monotonic() - started, 3)
                            feeds = payload.get('feeds')
                            if feeds is None:
                                feeds = (payload.get('data') or {}).get('feeds')
                            if not isinstance(feeds, list) or any(not isinstance(f, dict) for f in feeds):
                                raise ValueError('feeds缺失或格式错误，不能记零')
                            row['payload'] = dict(payload, feeds=feeds)
                            row['status'] = 'ok'
                            summary.update(status='ok', returned=len(feeds), note_entries=sum(f.get('modelType') == 'note' and bool(f.get('id')) for f in feeds), captured_at=now())
                            with self.mutex:
                                for feed in feeds:
                                    if feed.get('modelType') != 'note' or not re.fullmatch(r'[a-fA-F0-9]{24}', str(feed.get('id', ''))):
                                        continue
                                    nid = feed['id']
                                    card = feed.get('noteCard') or {}
                                    item = self.catalog.setdefault(nid, {'id': nid, 'sources_internal': []})
                                    item.update(feed=feed, title=card.get('displayTitle') or '', author=(card.get('user') or {}).get('nickname') or (card.get('user') or {}).get('nickName') or '',
                                        display_metrics=metrics(card.get('interactInfo'), summary['captured_at'], 'search'), captured_at=summary['captured_at'])
                                    source = {k: task[k] for k in ('key', 'group', 'mode', 'keyword')}
                                    if source not in item['sources_internal']:
                                        item['sources_internal'].append(source)
                                self.refresh_seed_metrics()
                        except Exception as exc:
                            row.update(status='failed', error_type=type(exc).__name__, error_internal=str(exc))
                            summary['status'] = 'failed'
                        finally:
                            row['finished_at'] = now()
                            raw_path = self.raw / (task['key'] + '_attempt%d.json' % attempt)
                            atomic(raw_path, row)
                            summary['attempts'].append({'attempt': attempt, 'status': row['status'], 'error_type': row.get('error_type'),
                                'raw_private_path': raw_path.relative_to(ROOT).as_posix(), 'started_at': row['started_at'], 'finished_at': row['finished_at']})
                        if row['status'] == 'ok':
                            break
                        if attempt == self.rt['consecutive_failure_stop']:
                            stop.set()
                            summary['stop_reason'] = '该词连续两次失败，当前模式止损；未取到不是无内容'
                with self.mutex:
                    self.manifest['searches'] = [r for r in self.manifest['searches'] if r['key'] != task['key']] + [summary]
                    self.persist()
                print(task['key'], task['keyword'], summary['status'], '笔记数', summary['note_entries'], flush=True)
            self.schedule(tasks, run)
        rows = self.manifest['searches']
        fixed_ok = sum(r['mode'] == 'default' and r['status'] == 'ok' for r in rows)
        supplement_ok = sum(r['mode'] == 'recent' and r['status'] == 'ok' for r in rows)
        self.manifest.update(fixed_success=fixed_ok, supplement_success=supplement_ok,
            search_status='pass' if fixed_ok == 15 and supplement_ok == len(supplements) else 'fail',
            status='pass' if fixed_ok == 15 and supplement_ok == len(supplements) else 'fail',
            search_finished_at=now(), login_validated_by_nonerror_search=any(r['status'] == 'ok' for r in rows),
            unique_search_note_ids=len(self.catalog))
        self.prepare_queue()
        self.persist()

    def refresh_seed_metrics(self):
        for n in self.store['notes']:
            if n['id'] not in self.catalog:
                continue
            item = self.catalog[n['id']]
            meta = n['_collector']
            previous = n.get('display_metrics')
            if previous and previous != item['display_metrics'] and previous not in meta['metrics_history']:
                meta['metrics_history'].append(previous)
            n['display_metrics'] = copy.deepcopy(item['display_metrics'])
            meta['sources_internal'] = copy.deepcopy(item['sources_internal'])

    def prepare_queue(self):
        for n in self.store['notes']:
            meta = n['_collector']
            if meta['origin'] != 'seed' and meta['review_status'] == 'pending':
                self.record_queue({'id': n['id'], 'title': safe_text(n['title']), 'author': safe_text(n['author']),
                    'status': 'pending_review', 'detail_status': meta['detail_status'], 'review_status': 'pending',
                    'primary_section': None, 'auto_publish': False, 'published_at_verified': n.get('published_at_verified'),
                    'evidence': safe_evidence(n['evidence']), 'display_metrics': n['display_metrics'],
                    'evidence_sha256': meta['evidence_sha256'], 'comments_status': meta['comments_status'],
                    'image_sha256': [a['sha256'] for a in meta['image_manifest']]})
        existing = {n['id'] for n in self.store['notes']}
        queued = {i['id'] for i in self.queue['items']}
        for nid, item in self.catalog.items():
            if nid in existing or nid in queued:
                continue
            guess = id_time(nid)
            outside = guess is None or not self.lower <= guess <= self.end
            self.record_queue({'id': nid, 'title': safe_text(item['title']), 'author': safe_text(item['author']),
                'display_metrics': item['display_metrics'], 'status': 'pending_detail',
                'reason': 'id_date_unknown' if guess is None else 'id_estimate_outside_not_publish_filter' if outside else 'id_estimate_priority_only',
                'id_time_estimate': guess.isoformat() if guess else None, 'published_at_verified': None,
                'auto_publish': False, 'review_status': 'pending', 'primary_section': None})

    def import_reuse(self):
        for directory in self.args.reuse_dir or []:
            d = Path(directory)
            if not d.is_dir():
                raise FileNotFoundError('reuse目录不存在')
            for path in d.glob('detail*.json'):
                try:
                    row = load(path)
                    if not isinstance(row, dict) or row.get('status') != 'ok' or not row.get('response'):
                        continue
                    payload = unpack(row['response'])
                    note = (payload.get('data') or {}).get('note') or {}
                    nid = note.get('noteId') or payload.get('feed_id')
                    if not nid or not re.fullmatch(r'[a-fA-F0-9]{24}', nid) or not note_time(note):
                        continue
                    target = self.reuse / (nid + '.json')
                    if not target.exists() or (row.get('finished_at') or row.get('started_at', '')) > (load(target).get('finished_at') or load(target).get('started_at', '')):
                        atomic(target, row)
                except (ValueError, KeyError, TypeError):
                    continue
        # 旧production缓存仅在带实际time和原imageList时复用，不把字符串pub或文件mtime当发布时间。
        cache_path = ROOT / 'note_details.json'
        if cache_path.is_file():
            cache = load(cache_path)
            for nid, obj in cache.items():
                if not isinstance(obj, dict) or not note_time(obj) or 'imageList' not in obj:
                    continue
                target = self.reuse / (nid + '.json')
                if not target.exists():
                    atomic(target, {'payload': {'feed_id': nid, 'data': {'note': dict(obj, noteId=nid), 'comments': {'list': obj.get('cmts') or []}}}, 'source': 'production_cache', 'captured_at': obj.get('captured_at')})

    def cached(self, nid):
        p = self.reuse / (nid + '.json')
        if not p.exists():
            return None
        row = load(p)
        payload = unpack(row['response']) if row.get('response') else row.get('payload')
        if not payload or not note_time((payload.get('data') or {}).get('note') or {}):
            return None
        return row, payload

    def image_urls(self, img):
        urls = []
        for key in ('urlDefault', 'url'):
            if img.get(key):
                urls.append(img[key])
        for info in img.get('infoList') or []:
            if info.get('url'):
                urls.append(info['url'])
        # 仅真实fileId允许ci；绝不从SNS签名路径伪造ID。不使用urlPre缩略图。
        if img.get('fileId'):
            urls.append('https://ci.xiaohongshu.com/' + img['fileId'] + '?imageView2/2/w/1080/format/jpg')
        return list(dict.fromkeys(urls))

    def download(self, nid, idx, img):
        urls = self.image_urls(img)
        started = time.monotonic()
        failures = []
        for original in urls:
            url = 'https://' + original[7:] if original.startswith('http://') else original
            host = (urlsplit(url).hostname or '').lower()
            if not (host.endswith('.xhscdn.com') or host == 'ci.xiaohongshu.com') or urlsplit(url).scheme != 'https':
                failures.append('不允许的远图host或协议')
                continue
            remain = self.rt['download_timeout_seconds'] - (time.monotonic() - started)
            if remain <= 0:
                break
            try:
                with requests.get(url, stream=True, timeout=(min(5, remain), min(5, remain)), allow_redirects=False) as r:
                    r.raise_for_status()
                    chunks, size = [], 0
                    for chunk in r.iter_content(65536):
                        if time.monotonic() - started > self.rt['download_timeout_seconds']:
                            raise TimeoutError('图片总下载预算25s')
                        size += len(chunk)
                        if size > 30 * 1024 * 1024:
                            raise ValueError('原图超过安全预算')
                        chunks.append(chunk)
                    path, info = versioned_image(self.imgs, nid + '_' + str(idx), b''.join(chunks))
                    return dict(info, index=idx, local_path=path.relative_to(ROOT).as_posix(), original_url=original, fetched_url=url)
            except Exception as exc:
                failures.append(type(exc).__name__)
        return {'index': idx, 'status': 'failed', 'original_urls': urls, 'errors': failures or ['无原图URL']}

    def selected_details(self):
        existing = {n['id'] for n in self.store['notes']}
        buckets = {g: [] for g in self.config['fixed_groups']}
        for nid, item in self.catalog.items():
            if nid in existing:
                continue
            cached = self.cached(nid)
            reliable = note_time(cached[1]['data']['note']) if cached else None
            guess = id_time(nid)
            if reliable is not None and not self.lower <= reliable <= self.end:
                self.record_queue(dict(self.queue_item(nid), status='outside_or_unknown', reason='cached_detail_outside_window', published_at_verified=reliable.isoformat()))
                continue
            groups = {s['group'] for s in item['sources_internal']}
            group = next((g for g in ('sentiment', 'product', 'marketing') if g in groups), 'marketing')
            title = item['title']
            relevant_hint = bool(re.search('银行|工行|建行|中行|立减|绑卡|信用卡|零钱通|理财通', title)) or not title.strip()
            stamp = (reliable or guess).timestamp() if reliable or guess else 0
            priority = 0 if re.search('中行|中国银行', title) else 1 if re.search('建行|建设银行|还款', title) else 2
            coarse_outside = reliable is None and (guess is None or not self.lower <= guess <= self.end)
            buckets[group].append((priority, not bool(cached), coarse_outside, not relevant_hint, -stamp, nid))
        for values in buckets.values():
            values.sort()
        selected = []
        while any(buckets.values()) and len(selected) < self.rt['detail_limit']:
            for group in ('sentiment', 'marketing', 'product'):
                if buckets[group] and len(selected) < self.rt['detail_limit']:
                    selected.append(buckets[group].pop(0)[-1])
        for values in buckets.values():
            for *_, nid in values:
                self.record_queue(dict(self.queue_item(nid), status='skipped_quota', reason='bounded_detail_limit', auto_publish=False))
        return selected

    def queue_item(self, nid):
        return next(i for i in self.queue['items'] if i['id'] == nid)

    def details(self):
        self.import_reuse()
        self.prepare_queue()
        if 'detail_selected_ids' not in self.manifest:
            self.manifest['detail_selected_ids'] = self.selected_details()
        attempted = {r['id'] for r in self.manifest['details']}
        selected = [nid for nid in self.manifest['detail_selected_ids'] if nid not in attempted][:self.args.detail_batch]
        self.manifest['detail_limit'] = self.rt['detail_limit']
        self.persist()
        print('本阶段有界详情', len(selected), '篇；整轮最多', len(self.manifest['detail_selected_ids']), '篇；非全结果审阅', flush=True)
        def run(w, nid):
            row = {'id': nid, 'started_at': now(), 'status': 'pending', 'port': w.port, 'attempt': 1}
            payload = None
            try:
                cached = self.cached(nid)
                if cached:
                    cached_row, payload = cached
                    captured = cached_row.get('finished_at') or cached_row.get('captured_at') or cached_row.get('started_at')
                    source = 'cached_detail'
                    row['reused'] = True
                else:
                    feed = self.catalog[nid]['feed']
                    token = feed.get('xsecToken') or feed.get('xsec_token')
                    if not token:
                        raise ValueError('缺少详情token；不重试')
                    if w.needs_recycle() and not w.restart():
                        raise RuntimeError('详情前回收失败')
                    args = {'feed_id': nid, 'xsec_token': token, 'load_all_comments': False, 'click_more_replies': False}
                    row['arguments'] = args
                    payload = self.request(w, 'get_feed_detail', args, self.rt['detail_timeout_seconds'], row)
                    captured, source = now(), 'detail'
                    row['reused'] = False
                row['evidence_captured_at'] = captured
                data = payload.get('data') or {}
                note = data.get('note') or {}
                if not note or (note.get('noteId') and note['noteId'] != nid):
                    raise ValueError('详情为空或ID不一致')
                published = note_time(note)
                if published is None:
                    self.record_queue(dict(self.queue_item(nid), status='outside_or_unknown', reason='detail_time_missing', detail_status='unknown_date'))
                    row['status'] = 'unknown_date'
                    return
                if not self.lower <= published <= self.end:
                    self.record_queue(dict(self.queue_item(nid), status='outside_or_unknown', reason='detail_verified_outside_window', published_at_verified=published.isoformat(), detail_status='outside_window'))
                    row['status'] = 'outside_window'
                    return
                if 'desc' not in note or not isinstance(note['desc'], str) or not isinstance(note.get('imageList'), list):
                    raise ValueError('正文或imageList缺失，不能假装无图')
                images = note['imageList']
                assets = list(self.download_executor.map(lambda pair: self.download(nid, pair[0], pair[1]), enumerate(images)))
                row['image_assets_internal'] = assets
                if any(a.get('status') == 'failed' for a in assets):
                    self.record_queue(dict(self.queue_item(nid), status='incomplete', reason='image_download_failed', detail_status='incomplete', published_at_verified=published.isoformat(), expected_images=len(images), valid_local_images=sum(a.get('status') != 'failed' for a in assets)))
                    row['status'] = 'incomplete'
                    return
                author = (note.get('user') or {}).get('nickname') or (note.get('user') or {}).get('nickName') or ''
                snap = metrics(note.get('interactInfo'), captured, 'detail')
                comments = data.get('comments') or {}
                if not isinstance(comments, dict):
                    raise ValueError('评论采样结构异常')
                cmts = []
                for c in (comments.get('list') or [])[:10]:
                    user = c.get('userInfo') or {}
                    ct = c.get('createTime')
                    cmts.append({'u': user.get('nickname') or user.get('nickName') or '', 'c': c.get('content') or '',
                        'likes': parse_count(c.get('likeCount')), 'ip': c.get('ipLocation') or '',
                        **({'d': note_time({'time': ct}).isoformat()} if note_time({'time': ct}) else {})})
                feed_url = self.catalog[nid]['feed'].get('url') or note.get('url') or ''
                # 未返回原URL时保留空字段和真实note ID，不伪造原链接。
                ev = {'t': note.get('title') or '', 'a': author, 'url': feed_url,
                    'likes': snap['likes'], 'collects': snap['collects'], 'comments': snap['comments'], 'shares': snap['shares'],
                    'desc': note['desc'], 'imgs': [a['local_path'] for a in assets], 'cmts': cmts,
                    'tags': [t if isinstance(t, str) else t['name'] for t in (note.get('tagList') or [])],
                    'captured_at': captured, 'ip': note.get('ipLocation') or '',
                    'pub': published.isoformat(), 'vtype': note.get('type') or 'normal'}
                # 评论量搜索与详情不拼接成一个快照；仅用于空采样缺口判定。
                search_comments = self.catalog[nid]['display_metrics']['comments']
                status = self.comments_status(snap['comments'], cmts)
                if not cmts and search_comments is not None and search_comments > 0:
                    status = 'missing_sample'
                n = {'id': nid, 'title': ev['t'], 'author': author, 'date': published.date().isoformat(),
                    'date_basis': '详情note.time', 'published_at_verified': published.isoformat(), 'evidence': ev,
                    'display_metrics': copy.deepcopy(self.catalog[nid]['display_metrics']), 'primary_section': None,
                    'sections': [], 'status': 'pending', 'review_status': 'pending', 'auto_publish': False,
                    'image_count': len(images),
                    '_collector': {'origin': 'new_candidate', 'detail_status': 'complete', 'review_status': 'pending',
                    'auto_publish': False, 'evidence_sha256': digest(ev), 'image_manifest': assets,
                    'sources_internal': self.catalog[nid]['sources_internal'], 'metrics_history': [],
                    'detail_metrics': snap, 'comments_status': status, 'comments_limit': 10,
                    'comments_has_more': comments.get('hasMore'), 'captured_at': captured, 'detail_source': source}}
                with self.mutex:
                    if not any(old['id'] == nid for old in self.store['notes']):
                        self.store['notes'].append(n)
                    self.record_queue({'id': nid, 'title': safe_text(n['title']), 'author': safe_text(author),
                        'status': 'pending_review', 'detail_status': 'complete', 'review_status': 'pending',
                        'primary_section': None, 'auto_publish': False, 'published_at_verified': published.isoformat(),
                        'evidence': safe_evidence(ev), 'display_metrics': n['display_metrics'],
                        'evidence_sha256': digest(ev), 'comments_status': status,
                        'image_sha256': [a['sha256'] for a in assets]})
                row['status'] = 'complete'
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__, error_internal=str(exc))
                self.record_queue(dict(self.queue_item(nid), status='detail_failed', detail_status='incomplete', reason=type(exc).__name__))
            finally:
                row['finished_at'] = now()
                if payload is not None:
                    row['payload'] = payload
                path = self.raw / ('detail_' + nid + '.json')
                atomic(path, row)
                if row['status'] == 'complete':
                    with self.mutex:
                        meta = next(n['_collector'] for n in self.store['notes'] if n['id'] == nid)
                        meta['detail_raw_private_path'] = path.relative_to(ROOT).as_posix()
                        meta['detail_raw_sha256'] = file_hash(path)
                if payload is not None and note_time((payload.get('data') or {}).get('note') or {}):
                    if not row.get('reused'):
                        atomic(self.reuse / (nid + '.json'), row)
                with self.mutex:
                    summary = {k: row.get(k) for k in ('id', 'status', 'attempt', 'reused', 'error_type', 'started_at', 'finished_at')}
                    summary['raw_private_path'] = path.relative_to(ROOT).as_posix()
                    self.manifest['details'] = [r for r in self.manifest['details'] if r['id'] != nid] + [summary]
                    self.persist()
                print('详情', nid, row['status'], '复用', row.get('reused', False), flush=True)
        self.schedule(selected, run)
        self.manifest['details_last_batch_finished_at'] = now()
        remaining = set(self.manifest['detail_selected_ids']) - {r['id'] for r in self.manifest['details']}
        self.manifest['details_remaining'] = len(remaining)
        self.manifest['detail_status'] = 'pending' if remaining else 'bounded_complete'
        if not remaining:
            self.manifest['details_finished_at'] = now()
        self.adapt_report_store()
        self.verify_report_schema()
        self.finalize_status()
        self.manifest['safe_stats'] = self.stats()
        self.persist()

    def detail_evidence_path(self, note):
        meta = note['_collector']
        bound = meta.get('detail_raw_private_path')
        if bound:
            source = (ROOT / bound).resolve()
            if source.is_relative_to(self.runs.resolve()):
                relative = source.relative_to(self.runs)
                if len(relative.parts) == 3 and relative.parts[1] == 'raw':
                    archived = self.runs / relative.parts[0] / 'archive/run/raw' / relative.name
                    if archived.is_file():
                        source = archived.resolve()
            paths = [source]
        else:
            # 旧store没有逐篇来源字段：只按原发布时间、正文和采集时点匹配旧run证据。
            paths = []
            for run in self.runs.iterdir():
                if not run.is_dir() or run.name.startswith('.'):
                    continue
                source = run / 'archive/run/raw' if (run / 'archive').is_dir() else run / 'raw'
                candidate = source / ('detail_' + note['id'] + '.json')
                if candidate.is_file():
                    paths.append(candidate.resolve())
        matches = []
        for path in paths:
            if not path.is_relative_to(self.runs.resolve()) or not path.is_file():
                raise ValueError('累积详情原响应缺失或越界')
            row = load(path)
            raw_note = ((row.get('payload') or {}).get('data') or {}).get('note') or {}
            published = note_time(raw_note)
            if published is None or published.isoformat() != note['evidence']['pub'] or raw_note.get('desc') != note['evidence']['desc']:
                if bound:
                    raise ValueError('累积详情原响应与证据不一致')
                continue
            captured = meta.get('captured_at')
            if captured:
                if row.get('evidence_captured_at'):
                    same_capture = captured == row['evidence_captured_at']
                elif row.get('reused'):
                    same_capture = True  # 旧缓存响应保留原采集时点，详情阶段结束时间不是证据时点。
                else:
                    stamp = datetime.fromisoformat(captured)
                    same_capture = datetime.fromisoformat(row['started_at']) <= stamp <= datetime.fromisoformat(row['finished_at'])
                if not same_capture:
                    if bound:
                        raise ValueError('累积详情原响应采集时点不一致')
                    continue
            sha = file_hash(path)
            if bound and sha != meta.get('detail_raw_sha256'):
                raise ValueError('累积详情原响应哈希不符')
            relative = path.relative_to(self.runs)
            if len(relative.parts) == 5 and relative.parts[1:4] == ('archive', 'run', 'raw'):
                archive = path.parents[2]
                seal = load(archive / 'archive_hashes.json')
                if seal['file_sha256'].get(path.relative_to(archive).as_posix()) != sha:
                    raise ValueError('累积详情归档哈希不符')
            matches.append((path, sha))
        if len(matches) != 1:
            raise ValueError('累积详情原响应缺失或来源不唯一：' + note['id'])
        source, sha = matches[0]
        meta['detail_raw_private_path'] = source.relative_to(ROOT).as_posix()
        meta['detail_raw_sha256'] = sha
        return source

    def verify_report_schema(self):
        import report_v6 as report
        self.sync_report_manifest()
        _, coverage = report.validate_manifest(self.manifest)
        identifiers = set()
        seed_count = new_count = images = 0
        for n in self.store['notes']:
            nid, ev, meta = n['id'], n['evidence'], n['_collector']
            if nid in identifiers or not re.fullmatch(r'[A-Za-z0-9_-]+', nid):
                raise ValueError('证据ID重复或无效')
            identifiers.add(nid)
            if not all(k in ev for k in ('t', 'a', 'url', 'desc', 'imgs', 'tags', 'pub', 'cmts')):
                raise ValueError('全文schema缺字段')
            if not all(isinstance(ev[k], str) for k in ('t', 'a', 'desc')):
                raise ValueError('全文schema文字字段异常')
            if not isinstance(ev['imgs'], list) or len(ev['imgs']) != len(set(ev['imgs'])) or n['image_count'] != len(ev['imgs']):
                raise ValueError('全文schema图片数量错误')
            if not isinstance(ev['tags'], list) or any(not isinstance(t, str) for t in ev['tags']):
                raise ValueError('全文schema标签错误')
            if not isinstance(ev['cmts'], list) or (not ev['desc'].strip() and not ev['imgs']):
                raise ValueError('全文schema评论或正文缺失')
            for comment in ev['cmts']:
                if not isinstance(comment, dict) or not isinstance(comment.get('c'), str):
                    raise ValueError('评论原文缺失')
                if any(k in comment and not isinstance(comment[k], str) for k in ('u', 'd')):
                    raise ValueError('未知评论时点应缺省而非伪造')
                if 'likes' not in comment or not (comment['likes'] is None or type(comment['likes']) is int and comment['likes'] >= 0):
                    raise ValueError('评论指标无效')
            if report.safe_origin(ev['url']).rstrip('/').split('/')[-1] != nid:
                raise ValueError('原帖URL与ID不匹配')
            report.publication_time(ev['pub'])
            report.validate_metrics(ev, nid + '.evidence')
            report.validate_metrics(n['display_metrics'], nid + '.display_metrics', snapshot=True)
            if digest(ev) != meta['evidence_sha256']:
                raise ValueError('原文hash不匹配')
            for relative in ev['imgs']:
                report.validated_image(self.state, relative)
                images += 1
            if meta['origin'] == 'seed':
                original = meta['seed_original_note']
                if digest(original) != meta['seed_note_sha256'] or ev != original['evidence']:
                    raise ValueError('完整seed原文被改写')
                for key in ('object', 'topics', 'attitude', 'verification', 'analyst_note', 'primary_section', 'event_key', 'event_label', 'event_status'):
                    if n.get(key) != original.get(key):
                        raise ValueError('seed审核metadata被改写')
                seed_count += 1
            else:
                raw = load(self.detail_evidence_path(n))
                note = raw['payload']['data']['note']
                published = note_time(note)
                current = nid in {r['id'] for r in self.manifest['details']}
                if published is None or (current and not self.lower <= published <= self.end) or ev['pub'] != published.isoformat() or ev['desc'] != note['desc']:
                    raise ValueError('候选日期或原文与原detail不一致')
                if len(note['imageList']) != n['image_count'] or meta['detail_status'] != 'complete' or n['status'] != 'pending':
                    raise ValueError('不完整或未待审候选')
                new_count += 1
        if re.search(r'xsec[_-]?token|xsecToken|cookie|authorization', json.dumps(self.queue, ensure_ascii=False), re.I):
            raise ValueError('安全队列出现敏感token字段')
        audit = {'passed': True, 'checked_at': now(), 'coverage': coverage,
                 'store_notes': len(identifiers), 'seed_notes': seed_count, 'pending_new_notes': new_count,
                 'decoded_image_refs': images, 'schema': 'report_v6', 'asset_root': self.store['asset_root'],
                 'original_responses_preserved': True, 'no_network_requests_in_verify': True,
                 'content_review_performed': False, 'publication_allowed': False}
        self.manifest['schema_validation'] = audit
        for blocker in self.manifest['blockers']:
            if blocker.get('stage') == 'verify' and not blocker.get('resolved_at'):
                blocker.update(resolved_at=now(), resolution='保留seed原路径扩展名并完整解码；report_v6 schema离线复验通过')
        return audit

    def finalize_status(self):
        for blocker in self.manifest['blockers']:
            if blocker.get('stage') == 'init' and self.store and self.store['migration']['seed_count'] == 80:
                blocker.setdefault('resolved_at', now())
                blocker['resolution'] = '修复复用目录混入列表型索引，初始化已复验80篇226图'
        detail_gaps = [r for r in self.manifest['details'] if r['status'] != 'complete']
        self.manifest['detail_failure_count'] = len(detail_gaps)
        self.manifest['collection_status'] = 'fail' if self.manifest['search_status'] != 'pass' else 'partial_with_gaps' if detail_gaps else 'bounded_pass'
        if self.manifest.get('schema_validation', {}).get('passed') and not any(not b.get('resolved_at') for b in self.manifest['blockers']):
            self.manifest['status'] = self.manifest['collection_status']
        self.manifest['pass_scope'] = '固定15词必需组及两补充槽位按同日原响应核验；保留原请求时点，不冒充续接时点。只证明检索覆盖，不代表全文/图片内容/分类/评论完整审核'
        self.manifest['publish_status'] = 'blocked_pending_human_review'

    def stats(self):
        seeds = [n for n in self.store['notes'] if n['_collector']['origin'] == 'seed']
        new = [n for n in self.store['notes'] if n['_collector']['origin'] != 'seed']
        return {'seed_notes_preserved': len(seeds), 'seed_images_preserved': sum(len(n['_collector']['image_manifest']) for n in seeds),
            'store_notes': len(self.store['notes']), 'new_complete_pending_review': len(new),
            'new_valid_local_images': sum(len(n['evidence']['imgs']) for n in new),
            'new_remote_image_refs': sum(i.startswith(('http:', 'https:')) for n in new for i in n['evidence']['imgs']),
            'queue_status_counts': dict(Counter(i['status'] for i in self.queue['items'])),
            'skip_reason_counts': dict(Counter(i.get('reason') for i in self.queue['items'] if i.get('reason'))),
            'new_missing_comment_samples': sum(n['_collector']['comments_status'] == 'missing_sample' for n in new),
            'new_unknown_comment_count_empty_samples': sum(n['_collector']['comments_status'] == 'unknown_count_empty_sample' for n in new),
            'seed_missing_comment_samples_preserved': sum(n['_collector']['comments_status'] == 'missing_sample' for n in seeds),
            'seed_metrics_refreshed_this_run': sum(n['id'] in self.catalog for n in seeds),
            'new_complete_added_this_run': sum(r['status'] == 'complete' for r in self.manifest['details']),
            'detail_attempted': len(self.manifest['details']),
            'detail_reused': sum(bool(r.get('reused')) for r in self.manifest['details']),
            'detail_failure_count': sum(r['status'] == 'failed' for r in self.manifest['details']),
            'new_comment_samples': sum(len(n['evidence']['cmts']) for n in new),
            'search_snapshot_null_counts': {k: sum(c['display_metrics'][k] is None for c in self.catalog.values()) for k in ('likes', 'collects', 'comments', 'shares')},
            'detail_snapshot_null_counts': {k: sum(n['evidence'][k] is None for n in new) for k in ('likes', 'collects', 'comments', 'shares')},
            'all_results_reviewed': False, 'auto_published': 0}


def validate(config):
    if config.get('schema_version') != 6:
        raise ValueError('配置schema错误')
    groups = config.get('fixed_groups') or {}
    if set(groups) != {'marketing', 'product', 'sentiment'} or [len(groups[g]) for g in ('marketing', 'product', 'sentiment')] != [7, 4, 4]:
        raise ValueError('固定词必须15=7+4+4')
    expected = {
        'marketing': ['银行立减金', '银行满减优惠活动', '信用卡微信支付优惠', '银行绑卡优惠', '工行立减金', '建行立减金', '中行立减金'],
        'product': ['理财通转账', '理财通会员权益', '零钱通余额宝', '理财通信用卡还款'],
        'sentiment': ['理财通投诉', '理财通还款手续费', '零钱通转出失败', '零钱通到账时间']}
    if groups != expected:
        raise ValueError('固定15词必须逐字一致，不得临时换词')
    words = [kw for kws in groups.values() for kw in kws]
    if len(set(words)) != 15 or any(not isinstance(w, str) or not w.strip() for w in words):
        raise ValueError('关键词重复或为空')
    if config['default_mode'] != {'arguments': 'keyword-only', 'sort_by': '综合', 'publish_time': '不限'}:
        raise ValueError('固定词必须keyword-only默认综合不限')
    if [i['keyword'] for i in config['supplements']] != ['银行立减金', '理财通会员权益']:
        raise ValueError('补充槽位必须为指定两词')
    for item in config['supplements']:
        if item['keyword'] not in words or item['filters'] != {'sort_by': '最新', 'publish_time': '一周内'}:
            raise ValueError('补充词与显式filters不合规')
    rt = config['runtime']
    required = {'ports': [18076, 18077], 'recycle_after_calls': 1, 'search_timeout_seconds': 65,
        'search_cold_retries': 1, 'consecutive_failure_stop': 2, 'detail_timeout_seconds': 55,
        'detail_retries': 0, 'window_days': 60, 'download_workers': 4, 'load_all_comments': False}
    if any(rt.get(k) != v for k, v in required.items()) or not 0 <= rt['detail_limit'] <= 40 or not 0 < rt['download_timeout_seconds'] <= 25:
        raise ValueError('运行预算不符授权')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['init', 'search', 'details', 'verify'])
    parser.add_argument('--config', default=str(ROOT / 'xhs_search_config.json'))
    parser.add_argument('--seed')
    parser.add_argument('--reuse-dir', action='append')
    parser.add_argument('--run-id')
    parser.add_argument('--detail-batch', type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.detail_batch <= 12:
        parser.error('每阶段detail-batch必须1至12；整轮总上限40')
    config = load(Path(args.config))
    validate(config)
    if args.stage in ('details', 'verify') and not args.run_id:
        parser.error('details/verify必须显式--run-id续用同日search')
    state = ROOT / config['runtime']['state_dir']
    mkdir(state)
    # Windows内核锁：崩溃自动释放，不删除历史锁文件，不误杀其他进程。
    import msvcrt
    lock = (state / 'collector.lock').open('a+b')
    lock.seek(0, 2)
    if lock.tell() == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        raise RuntimeError('collector运行锁已占用，停止并发')
    collector = None
    code = 0
    try:
        collector = Collector(config, args)
        collector.init_store()
        if args.stage == 'search' and not collector.manifest.get('search_finished_at'):
            collector.configure_pool()
        elif args.stage == 'details' and (set(collector.manifest.get('detail_selected_ids', [])) - {r['id'] for r in collector.manifest['details']} or 'detail_selected_ids' not in collector.manifest):
            collector.configure_pool()
        if args.stage == 'search':
            collector.search()
        elif args.stage == 'details':
            if not collector.catalog:
                raise ValueError('没有同run真实搜索目录')
            collector.details()
        elif args.stage == 'init':
            collector.import_reuse()
            collector.persist()
        elif args.stage == 'verify':
            collector.adapt_report_store()
            collector.verify_report_schema()
            collector.finalize_status()
            collector.manifest['safe_stats'] = collector.stats()
            collector.persist()
        if collector.manifest.get('search_status') == 'fail':
            code = 1
        print(json.dumps({'run_id': collector.runid, 'stage': args.stage,
            'data_as_of': collector.end.date().isoformat(), 'search_status': collector.manifest['search_status'],
            'safe_stats': collector.stats()}, ensure_ascii=False), flush=True)
    except Exception as exc:
        code = 2
        if collector:
            collector.manifest['status'] = 'fail'
            collector.manifest['blockers'].append({'at': now(), 'stage': args.stage, 'error_type': type(exc).__name__, 'message_internal': str(exc)})
            collector.persist()
        print(type(exc).__name__ + '：采集停止，原始缺口记录内部manifest', flush=True)
    finally:
        if collector:
            collector.close_pool()
            collector.download_executor.shutdown(wait=True)
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
