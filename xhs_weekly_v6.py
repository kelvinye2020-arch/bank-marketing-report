"""V6 三栏周一统一入口；构建/验收离线，只有显式 publish 可以部署。"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
SOURCES = ('store', 'reviews', 'editorial', 'manifest')
ARTIFACTS = ('bank_marketing_report.html', 'public_report_v6.json', 'report_quality_v6.json')
# 只能显式暂存这些实现文件和本 bundle 全部引用的图片，不扫描目录加入文件。
CODE_FILES = (
    '.gitignore', 'xhs_weekly_v6.py', 'xhs_collect_v6.py', 'xhs_search_config.json',
    'report_v6.py', 'report_v6_template.html', 'verify_report_v6.py', 'mcp_pool.py',
    'tests/test_report_v6.py', 'tests/test_xhs_weekly_v6.py',
    'update_report.py', 'generate_report.py', 'quick_update.py', 'run_sentiment_update.py',
    'run_generate_with_mcp.py', 'retry_failed_searches.py', 'generate_report_0412.py',
    'run_all_searches.py', 'run_search.py', 'run_searches.py', 'run_searches_mcp.py',
    'run_xhs_search.py', 'run_xhs_search_direct.py', 'xhs_full_search.py',
    'xhs_http_batch.py', 'xhs_http_search.py', 'xhs_search_http.py', 'xhs_search_one.py',
    'xhs_single_search.py', 'recovery_0928.py', 'recovery_0928_r2.py',
    'retry_with_restart.py', 'fix_old_images.py', 'fix_missing_details.py', 'repair_details.py',
)
REMOTE_PATH = '/kelvinye2020-arch/bank-marketing-report.git'
SITE_URL = 'https://kelvinye2020-arch.github.io/bank-marketing-report/'


class PipelineError(ValueError):
    pass


class PushCompletedError(PipelineError):
    def __init__(self, pushed, message):
        super().__init__(message)
        self.result = dict(pushed, passed=False, verified=False, error=message,
                           next_command='confirm-online', do_not_republish=True)


def need(condition, message):
    if not condition:
        raise PipelineError(message)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            need(key not in obj, 'JSON 重复键，拒绝继续')
            obj[key] = value
        return obj
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(PipelineError('JSON 非有限数字')))


def fresh_json(path, obj):
    path = Path(path)
    need(path.parent.is_dir() and not path.exists(), '输出父目录须存在且文件不能已存在')
    with path.open('xb') as stream:
        stream.write(canonical(obj))
        stream.flush()
        os.fsync(stream.fileno())


def python_env():
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    return env


def git_env():
    env = python_env()
    # 禁止继承凭据调试、外部 config 注入或交互鉴权；不跳过正常仓库 hook。
    for key in list(env):
        if key.startswith('GIT_') or key in ('GH_TOKEN', 'GITHUB_TOKEN'):
            env.pop(key)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'Never'
    return env


def git(root, *args, binary=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            env=git_env(), timeout=180)
    need(result.returncode == 0, 'git 本地操作失败：' + args[0] + '（输出已屏蔽，不绕过 hook）')
    return result.stdout if binary else result.stdout.decode('utf-8', errors='strict').strip()


def remote_url(root):
    value = git(root, 'config', '--get', 'remote.origin.url')
    u = urlsplit(value)
    need(u.scheme == 'https' and u.netloc == 'github.com' and u.path == REMOTE_PATH
         and not u.query and not u.fragment, 'origin 必须是已授权仓库的无凭据 HTTPS URL')
    pushes = git(root, 'remote', 'get-url', '--push', '--all', 'origin').splitlines()
    need(pushes == [value], 'origin push URL 与授权 URL 不一致')
    return value


def network_git(root, url, *args):
    # PAT 仅在这个子进程内读取和使用，不进入父进程、参数 URL 或日志。
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'xhs_weekly_v6.py'), '--_git-network'],
                            input=canonical({'root': str(root), 'url': url, 'args': list(args)}),
                            capture_output=True, env=git_env(), timeout=240)
    need(result.returncode == 0, 'git 网络操作失败（凭据及子进程日志已屏蔽）')
    payload = json.loads(result.stdout)
    need(payload.get('passed') is True, 'git 网络操作失败：' + args[0] + '（凭据及输出已屏蔽）')
    return payload.get('refs', '')


def git_network_worker():
    result = {'passed': False}
    try:
        request = json.loads(sys.stdin.buffer.read())
        root, url, args = Path(request['root']).resolve(), request['url'], request['args']
        need(root == ROOT and url == remote_url(root), '网络 git 仅支持生产授权 origin')
        need(args and args[0] in ('fetch', 'ls-remote', 'push'), '网络 git 操作不允许')
        allowed = {
            ('fetch', '--no-tags', url, 'refs/heads/master:refs/remotes/origin/master', 'refs/heads/main:refs/remotes/origin/main'),
            ('ls-remote', url, 'refs/heads/master', 'refs/heads/main'),
        }
        push_ok = (len(args) == 5 and args[:3] == ['push', '--atomic', url]
                   and all(re.fullmatch(r'[0-9a-f]{40,64}:refs/heads/' + b, a)
                           for b, a in zip(('master', 'main'), args[3:])))
        need(tuple(args) in allowed or push_ok, '网络 git 参数不在白名单')
        token = read_json(root / 'git_token.json').get('github_pat', '').strip()
        if token.startswith('ghp_ghp_'):
            token = token[4:]
        need(bool(token) and not re.search(r'\s', token), '已配置 PAT 缺失或格式无效')
        authorization = base64.b64encode(('x-access-token:' + token).encode()).decode()
        env = git_env()
        env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='credential.helper', GIT_CONFIG_VALUE_0='',
                   GIT_CONFIG_KEY_1='http.' + url + '.extraHeader',
                   GIT_CONFIG_VALUE_1='Authorization: Basic ' + authorization)
        process = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env, timeout=180)
        result = {'passed': process.returncode == 0}
        if process.returncode == 0 and args[0] == 'ls-remote':
            refs = process.stdout.decode('ascii').strip()
            need(all(re.fullmatch(r'[0-9a-f]{40,64}\s+refs/heads/(?:master|main)', line)
                     for line in refs.splitlines()), '远端响应不合规')
            result['refs'] = refs
    except Exception:
        result = {'passed': False}
    print(json.dumps(result))
    return 0 if result['passed'] else 2


def config_check(path):
    # collector import 不执行采集，但配置验证仅在需要时加载。
    import xhs_collect_v6
    config = read_json(path)
    xhs_collect_v6.validate(config)
    need(len(config['supplements']) <= 2, '补充词不能超过两个')
    return config


def coverage_check(manifest, config):
    import report_v6
    report_v6.validate_manifest(manifest)
    if manifest['data_kind'] == 'test_fixture':
        return
    need(manifest.get('config_sha256') == hashlib.sha256(canonical(config)).hexdigest(), 'manifest 未绑定当前词源配置')
    expected = []
    for section, words in config['fixed_groups'].items():
        for word in words:
            expected.append({'key': 'f%02d' % (len(expected) + 1), 'section': section,
                             'keyword': word, 'required': True, 'mode': 'default'})
    for index, item in enumerate(config['supplements'], 1):
        section = next(s for s, words in config['fixed_groups'].items() if item['keyword'] in words)
        expected.append(dict(item, key='s%02d' % index, section=section, required=False, mode='recent'))
    actual = {g['key']: g for g in manifest['groups']}
    need(set(actual) == {g['key'] for g in expected}, 'coverage 必须是 config 完整 15 词及至多两个补充，不能仅单栏')
    for group in expected:
        row = actual[group['key']]
        need(all(row.get(k) == v for k, v in group.items()), '检索词、栏目或模式与 config 不一致')
        if group['required']:
            need(not row.get('filters'), '固定词禁止额外 filters')


def source_paths(args, metadata=None):
    result = {}
    defaults = {'store': 'evidence_store.json', 'reviews': 'reviews.json',
                'editorial': 'editorial.json', 'manifest': 'search_manifest.json'}
    for key in SOURCES:
        value = getattr(args, key, None) or (metadata or {}).get('sources', {}).get(key)
        result[key] = Path(value).resolve() if value else ROOT / 'xhs_state' / defaults[key]
    return result


def hashes(paths):
    return {key: file_sha(path) for key, path in paths.items()}


def unchanged(paths, expected):
    need(hashes(paths) == expected, '输入、脚本、配置或图片已变化，必须重新审核/构建/验收')


def code_paths(root):
    paths = {name: root / name for name in CODE_FILES}
    need(all(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root) for p in paths.values()),
         '必要脚本/配置/测试不存在或路径不安全')
    return paths


def artifact_hashes(bundle, quality):
    names = list(ARTIFACTS) + sorted(quality['images'])
    paths = {name: bundle / name for name in names}
    need(all(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(bundle) for p in paths.values()), 'bundle 文件缺失或越界')
    return hashes(paths)


def collect(args):
    config_check(args.config)
    need(args.only in (None, 'all'), '原 collector 不支持 --only 单栏；拒绝扩为三栏或自动发布')
    command = [sys.executable, '-B', str(ROOT / 'xhs_collect_v6.py'), args.stage, '--config', str(Path(args.config).resolve())]
    for key in ('seed', 'run_id', 'detail_batch'):
        value = getattr(args, key, None)
        if value is not None:
            command += ['--' + key.replace('_', '-'), str(value)]
    for value in args.reuse_dir or []:
        command += ['--reuse-dir', value]
    return subprocess.run(command, cwd=ROOT, env=python_env()).returncode


def build(args):
    import report_v6
    config = config_check(args.config)
    sources = source_paths(args)
    initial = hashes(sources)
    inputs = [read_json(sources[k]) for k in SOURCES]
    store, reviews, editorial, manifest = inputs
    unchanged(sources, initial)
    coverage_check(manifest, config)
    need(reviews.get('as_of') == manifest['as_of'] and reviews.get('run_id') == manifest['run_id'], 'reviews 须显式绑定本期 as_of/run_id')
    need(editorial.get('publication', {}).get('status') in ('draft', 'ready'), '统一构建禁止 published；部署回执独立，不二次构建伪造状态')
    if args.test_fixture:
        need(manifest['data_kind'] == 'test_fixture', 'test-fixture 不能降低生产阈值')
    assets = Path(args.asset_root).resolve() if args.asset_root else (ROOT / store.get('asset_root', 'xhs_state')).resolve()
    output = Path(args.output).resolve()
    output_root = ROOT / 'outputs'
    if not output_root.exists():
        need(ROOT.is_dir(), '生产父目录缺失')
        output_root.mkdir()
    need(output.parent == output_root.resolve() and not output.exists(), 'bundle 必须是生产 outputs 下未存在的直属目录')
    codes = code_paths(ROOT)
    code_snapshot = hashes(codes)
    config_path = Path(args.config).resolve()
    config_snapshot = file_sha(config_path)
    report, quality, _ = report_v6.build_report(*inputs, assets,
        minimums={k: 0 for k in report_v6.DEFAULT_MINIMUMS} if args.test_fixture else None)
    quality['input_file_sha256'] = initial
    unchanged(sources, initial)
    staging = Path(tempfile.mkdtemp(prefix='.v6-build-', dir=output_root))
    try:
        work = staging / 'bundle'
        quality = report_v6.write_bundle(report, quality, assets, work, {str(p): initial[k] for k, p in sources.items()})
        source_images = {source: quality['images'][public] for public, source in quality['image_sources'].items()}
        unchanged({p: assets / p for p in source_images}, source_images)
        metadata = {'schema_version': 1, 'sources': {k: str(p) for k, p in sources.items()}, 'source_sha256': initial,
                    'config': str(config_path), 'config_sha256': config_snapshot,
                    'code_sha256': code_snapshot, 'assets': str(assets),
                    'source_image_sha256': source_images,
                    'artifact_sha256': artifact_hashes(work, quality), 'provenance': report['provenance']}
        fresh_json(work / 'pipeline_bundle_v6.json', metadata)
        unchanged(sources, initial)
        unchanged(codes, code_snapshot)
        need(file_sha(config_path) == config_snapshot, '构建时配置被并发改写')
        need(not output.exists(), '输出出现并发写入，拒绝覆盖')
        os.rename(work, output)
    finally:
        # 仅清理本函数创建的临时目录，不动历史 bundle 或历史图。
        shutil.rmtree(staging)
    return {'passed': True, 'bundle': str(output), 'publication': report['publication']['status'],
            'counts': quality['counts'], 'production_data_passed': quality['production_data_passed']}


def checked_bundle(args):
    import report_v6
    import verify_report_v6
    bundle = Path(args.bundle).resolve()
    need(bundle.is_dir() and not Path(args.bundle).is_symlink(), 'bundle 不存在或为符号链接')
    metadata = read_json(bundle / 'pipeline_bundle_v6.json')
    need(metadata.get('schema_version') == 1, 'pipeline bundle schema 缺失')
    sources = source_paths(args, metadata)
    unchanged(sources, metadata['source_sha256'])
    codes = code_paths(ROOT)
    unchanged(codes, metadata['code_sha256'])
    config_path = Path(args.config).resolve() if args.config else Path(metadata['config']).resolve()
    need(file_sha(config_path) == metadata['config_sha256'], '词源 config 已变化')
    config = config_check(config_path)
    inputs = [read_json(sources[k]) for k in SOURCES]
    store, reviews, editorial, manifest = inputs
    coverage_check(manifest, config)
    need(reviews.get('as_of') == manifest['as_of'] and reviews.get('run_id') == manifest['run_id'], 'reviews 非本期审核')
    need(editorial.get('publication', {}).get('status') in ('draft', 'ready'), 'published 输入不能用于首次部署')
    report, quality = verify_report_v6.validate_bundle(bundle, sources)
    need(artifact_hashes(bundle, quality) == metadata['artifact_sha256'] and report['provenance'] == metadata['provenance'], 'bundle seal 或 provenance 变化')
    assets = Path(metadata['assets']).resolve()
    source_images = {source: quality['images'][public] for public, source in quality['image_sources'].items()}
    need(metadata.get('source_image_sha256') == source_images, 'pipeline源图哈希必须与发布→原路径映射完全一致')
    unchanged({p: assets / p for p in source_images}, source_images)
    # 严格重跑所有内容、review、数字 claims 门槛，但不写文件、不重新渲染新报告。
    minimums = quality['minimums']
    rebuilt, rebuilt_quality, _ = report_v6.build_report(*inputs, assets, minimums=minimums)
    rebuilt['built_at'] = report['built_at']
    need(rebuilt == report and all(rebuilt_quality[k] == quality[k] for k in ('counts', 'images', 'image_sources')),
         '公开内容或图源映射不等于当前已审输入')
    unchanged(sources, metadata['source_sha256'])
    return bundle, metadata, sources, report, quality


def verification_binding(bundle, metadata, report, quality):
    return {'schema_version': 1, 'passed': True, 'production_data_passed': quality['production_data_passed'],
            'source_inputs_verified': True, 'publication': report['publication']['status'],
            'bundle': str(bundle), 'run_id': report['provenance']['run_id'], 'as_of': report['data_as_of'],
            'provenance': report['provenance'], 'artifact_sha256': metadata['artifact_sha256'],
            'source_sha256': metadata['source_sha256'], 'code_sha256': metadata['code_sha256'],
            'config_sha256': metadata['config_sha256'], 'source_image_sha256': metadata['source_image_sha256'],
            'pipeline_seal_sha256': file_sha(bundle / 'pipeline_bundle_v6.json')}


def binding_check(receipt, binding):
    need(isinstance(receipt, dict) and 'error' not in receipt
         and receipt.get('passed') is True and receipt.get('source_inputs_verified') is True
         and type(receipt.get('production_data_passed')) is bool
         and type(receipt.get('schema_version')) is int
         and all(receipt.get(k) == v for k, v in binding.items()), '验收 source/code/artifact/seal/期次未绑定当前真实 bundle')


def verification_time(value, require_fresh=True):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise PipelineError('验收时间缺失或无效') from None
    need(stamp.tzinfo is not None, '验收时间必须带时区')
    age = datetime.now(CST) - stamp
    need(age >= timedelta(0) and (not require_fresh or age <= timedelta(hours=2)), '验收时间在未来或超过2小时，须重新验收')
    return stamp


def browser_check(browser, report, widths):
    import verify_report_v6
    need(isinstance(browser, dict) and 'error' not in browser
         and browser.get('widths') == list(widths)
         and all(type(w) is int for w in browser['widths'])
         and browser.get('browser_errors') == [] and browser.get('network_errors') == [], '必须通过指定全部宽度浏览器验收且无 error')
    expected = {(w, s) for w in widths for s in verify_report_v6.SECTIONS}
    rows = browser.get('measurements')
    need(isinstance(rows, list) and len(rows) == len(expected)
         and all(isinstance(r, dict) and set(r) == {'width', 'section', 'cards', 'insights', 'columns'}
                 and all(type(r[k]) is int for k in ('width', 'cards', 'insights', 'columns')) for r in rows)
         and {(r['width'], r['section']) for r in rows} == expected, '浏览器三栏逐宽度测量缺失、重复或结构错误')
    need(all(r['cards'] == report['counts']['sections'][r['section']]
             and r['insights'] == len(report['insights'][r['section']])
             and r['columns'] == (1 if r['width'] <= 760 else 2 if r['width'] < 1120 else 3) for r in rows), '浏览器数量或布局验收不符')
    need(type(browser.get('fulltext_checks')) is int and type(browser.get('image_checks')) is int
         and browser['fulltext_checks'] == len(report['notes']) * len(widths)
         and browser['image_checks'] == sum(len(n['evidence']['imgs']) for n in report['notes']) * len(widths), '全文/全部原图验收缺失')


def verification(args):
    import verify_report_v6
    bundle, metadata, sources, report, quality = checked_bundle(args)
    need(Path(args.output).parent.is_dir() and not Path(args.output).exists(), '验收输出不能覆盖')
    binding = verification_binding(bundle, metadata, report, quality)
    browser = verify_report_v6.browser_verify(bundle, report, args.browser)
    verified_at = datetime.now(CST).isoformat(timespec='seconds')
    after_bundle, after_meta, _, after_report, after_quality = checked_bundle(args)
    need(verification_binding(after_bundle, after_meta, after_report, after_quality) == binding, '验收期间 bundle seal 或来源变化')
    browser_check(browser, report, verify_report_v6.WIDTHS)
    result = dict(binding, verification_kind='full', browser=browser, verified_at=verified_at)
    fresh_json(args.output, result)
    return result


def verify_part(args):
    import verify_report_v6
    need(type(args.width) is int and args.width in verify_report_v6.WIDTHS, 'part 必须指定一个支持的 width')
    bundle, metadata, sources, report, quality = checked_bundle(args)
    need(Path(args.output).parent.is_dir() and not Path(args.output).exists(), '验收输出不能覆盖')
    binding = verification_binding(bundle, metadata, report, quality)
    browser = verify_report_v6.browser_verify(bundle, report, getattr(args, 'browser', None), widths=[args.width])
    checked_at = datetime.now(CST).isoformat(timespec='seconds')
    after_bundle, after_meta, _, after_report, after_quality = checked_bundle(args)
    need(verification_binding(after_bundle, after_meta, after_report, after_quality) == binding, 'part 验收期间 bundle seal 或来源变化')
    browser_check(browser, report, [args.width])
    result = dict(binding, verification_kind='part', width=args.width, checked_at=checked_at, browser=browser)
    fresh_json(args.output, result)
    return result


def checked_parts(paths, binding, report, require_fresh=True, expected_records=None):
    import verify_report_v6
    need(isinstance(paths, (list, tuple)) and len(paths) == len(verify_report_v6.WIDTHS), '必须提供七份唯一 width part，不能漏 width')
    resolved = [Path(p).resolve() for p in paths]
    need(len(set(resolved)) == len(resolved), 'part 路径重复')
    records, browsers, stamps = [], {}, []
    for original, path in zip(paths, resolved):
        need(path.is_file() and not Path(original).is_symlink(), 'part 缺失或为符号链接')
        snapshot = file_sha(path)
        part = read_json(path)
        need(file_sha(path) == snapshot, '读取时 part 变化')
        binding_check(part, binding)
        width = part.get('width')
        need(part.get('verification_kind') == 'part' and type(width) is int
             and width in verify_report_v6.WIDTHS and width not in browsers, 'part width 不支持或重复')
        stamps.append(verification_time(part.get('checked_at'), require_fresh))
        browser_check(part.get('browser'), report, [width])
        browsers[width] = part['browser']
        records.append({'path': str(path), 'sha256': snapshot, 'width': width, 'checked_at': part['checked_at']})
    need(set(browsers) == set(verify_report_v6.WIDTHS), '七份 part 漏 width')
    records.sort(key=lambda r: r['width'])
    if expected_records is not None:
        need(records == expected_records, '合并后 part 路径、哈希、width 或验收时间变化')
    aggregate = {'widths': list(verify_report_v6.WIDTHS),
                 'measurements': [r for w in verify_report_v6.WIDTHS for r in browsers[w]['measurements']],
                 'browser_errors': [], 'network_errors': [],
                 'fulltext_checks': sum(b['fulltext_checks'] for b in browsers.values()),
                 'image_checks': sum(b['image_checks'] for b in browsers.values())}
    need(all(file_sha(r['path']) == r['sha256'] for r in records), '核对期间 part 哈希变化')
    return records, aggregate, min(stamps).astimezone(CST).isoformat(timespec='seconds')


def verify_merge(args):
    bundle, metadata, sources, report, quality = checked_bundle(args)
    need(Path(args.output).parent.is_dir() and not Path(args.output).exists(), '验收输出不能覆盖')
    binding = verification_binding(bundle, metadata, report, quality)
    records, browser, verified_at = checked_parts(args.parts, binding, report)
    after_bundle, after_meta, _, after_report, after_quality = checked_bundle(args)
    need(verification_binding(after_bundle, after_meta, after_report, after_quality) == binding, 'merge 期间 bundle seal 或来源变化')
    # 再核对七份文件字节，避免合并期间意外改批。
    unchanged({r['path']: Path(r['path']) for r in records}, {r['path']: r['sha256'] for r in records})
    result = dict(binding, verification_kind='merged', browser=browser, parts=records,
                  verified_at=verified_at, merged_at=datetime.now(CST).isoformat(timespec='seconds'))
    fresh_json(args.output, result)
    return result


def acceptance_check(receipt, bundle, metadata, report, quality, require_fresh=True):
    import verify_report_v6
    need(receipt.get('passed') is True and receipt.get('production_data_passed') is True
         and receipt.get('source_inputs_verified') is True and receipt.get('publication') == 'ready', '必须提供 ready 生产且完整源验收通过的验收文件')
    binding = verification_binding(bundle, metadata, report, quality)
    binding_check(receipt, binding)
    verification_time(receipt.get('verified_at'), require_fresh)
    browser_check(receipt.get('browser'), report, verify_report_v6.WIDTHS)
    kind = receipt.get('verification_kind')
    need(kind in ('full', 'merged'), '必须提供完整或七 part 合并验收')
    if kind == 'merged':
        records = receipt.get('parts')
        need(isinstance(records, list) and len(records) == len(verify_report_v6.WIDTHS)
             and all(isinstance(r, dict) and set(r) == {'path', 'sha256', 'width', 'checked_at'} for r in records), '合并验收缺七份 part 哈希')
        _, browser, verified_at = checked_parts([r['path'] for r in records], binding, report,
                                               require_fresh, expected_records=records)
        need(browser == receipt['browser'] and verified_at == receipt['verified_at'], '合并验收与七份逐视口记录不符')
        merged_at = verification_time(receipt.get('merged_at'), require_fresh)
        need(all(verification_time(r['checked_at'], require_fresh) <= merged_at for r in records), '合并时间早于真实验收')
    else:
        need('parts' not in receipt, '完整验收不应包含分段记录')


@contextmanager
def publish_lock(root):
    # 独占锁不删除未知锁；若异常崩溃留下锁，需人工确认后处置。
    path = root / 'outputs' / '.v6-publish.lock'
    need(path.parent.is_dir(), 'outputs 缺失')
    try:
        stream = path.open('x')
    except FileExistsError:
        raise PipelineError('已有发布锁，拒绝并发或自动删除锁')
    try:
        stream.close()
        yield
    finally:
        path.unlink()


def promote(root, bundle, expected):
    # 在任何 HTML 替换前预检全部图片，历史同名不同字节即阻断。
    for name in expected:
        target = root / name
        need(target.parent.is_dir() and not target.is_symlink() and target.resolve().is_relative_to(root), '生产目标父目录缺失或越界')
        if name.startswith('imgs/') and target.exists():
            need(target.is_file() and file_sha(target) == expected[name], '生产图片同名不同哈希，拒绝覆盖：' + name)
    for name in sorted(expected, key=lambda n: (n == ARTIFACTS[0], n)):
        source, target = bundle / name, root / name
        need(file_sha(source) == expected[name], '拷贝前 bundle 字节变化')
        if name.startswith('imgs/') and target.exists():
            need(file_sha(target) == expected[name], '图片并发碰撞')
            continue
        temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            if target.exists() and not name.startswith('imgs/'):
                need(target.is_file(), '生产目标不是常规文件')
            with temp.open('xb') as out, source.open('rb') as inp:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            need(file_sha(temp) == expected[name], '原子拷贝哈希不符')
            if name.startswith('imgs/'):
                # 同卷硬链接是原子且不覆盖已有文件；竞争出现同名时立即拒绝。
                os.link(temp, target)
            else:
                os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
    unchanged({n: root / n for n in expected}, expected)


def staged_empty(root):
    need(not git(root, 'diff', '--cached', '--name-only', '-z', binary=True), '已有暂存内容（包括不明暂存），拒绝发布；不自动 reset')


def worktree_allowlist(root, expected):
    changed = set(filter(None, git(root, 'diff', '--name-only', '-z', binary=True).decode('utf-8').split('\0')))
    need(changed <= set(expected), '存在白名单外已跟踪改动，拒绝混入发布')
    need(not git(root, 'diff', '--diff-filter=DR', '--name-only', '-z', binary=True), '禁止提交删除或重命名（含历史图）')


def stage_allowlist(root, expected):
    staged_empty(root)
    worktree_allowlist(root, expected)
    unchanged({n: root / n for n in expected}, expected)
    # 测试目录被 test_*.py 忽略，只对两个精确测试文件 force add。
    tests = [n for n in expected if n in ('tests/test_report_v6.py', 'tests/test_xhs_weekly_v6.py')]
    regular = [n for n in expected if n not in tests]
    for offset in range(0, len(regular), 80):
        git(root, '-c', 'core.autocrlf=false', 'add', '--', *regular[offset:offset + 80])
    if tests:
        git(root, '-c', 'core.autocrlf=false', 'add', '-f', '--', *tests)
    staged = set(filter(None, git(root, 'diff', '--cached', '--name-only', '-z', binary=True).decode('utf-8').split('\0')))
    need(staged <= set(expected), '暂存区出现白名单外文件，停止，不清除用户暂存')
    need(not git(root, 'diff', '--cached', '--diff-filter=DR', '--name-only', '-z', binary=True), '暂存删除/重命名被禁止')
    check_index(root, expected)
    return bool(staged)


def check_index(root, expected):
    for name, sha in expected.items():
        need(hashlib.sha256(git(root, 'show', ':' + name, binary=True)).hexdigest() == sha, 'Git index 字节与白名单快照不同：' + name)


def ref_hashes(raw):
    result = {}
    for line in raw.splitlines():
        sha, ref = line.split()
        need(re.fullmatch(r'[0-9a-f]{40,64}', sha) and ref in ('refs/heads/master', 'refs/heads/main') and ref not in result, '远端分支响应错误')
        result[ref.rsplit('/', 1)[1]] = sha
    need(set(result) == {'master', 'main'}, '远端 master/main 缺失')
    return result


def http_hashes(site_url, expected):
    import requests
    need(site_url == SITE_URL, '线上验收 URL 必须是现有已授权站点')
    result = {}
    with requests.Session() as session:
        session.trust_env = False
        for name, sha in expected.items():
            try:
                with session.get(site_url + name, stream=True, allow_redirects=False, timeout=(10, 30),
                                 headers={'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'}) as response:
                    need(response.status_code == 200, '线上 HTTP 未就绪：' + name)
                    size, digest = 0, hashlib.sha256()
                    for part in response.iter_content(65536):
                        size += len(part)
                        need(size <= 40 * 1024 * 1024, '线上文件超过验收预算')
                        digest.update(part)
                    need(digest.hexdigest() == sha, '线上文件哈希不匹配：' + name)
                    result[name] = sha
            except requests.RequestException:
                raise PipelineError('线上 HTTP 验收失败：' + name + '（请求日志已屏蔽）')
    return result


def publish(args):
    root = ROOT.resolve()
    # 第一项即检查原暂存，不能先 add 或替换生产文件。
    staged_empty(root)
    bundle, metadata, sources, report, quality = checked_bundle(args)
    need(bundle.parent == (root / 'outputs').resolve(), '只允许生产 outputs 的 bundle')
    need(quality['production_data_passed'] is True and report['provenance']['data_kind'] == 'production'
         and report['publication']['status'] == 'ready', '只有 ready 生产数据允许 publish')
    acceptance_sha = file_sha(args.acceptance)
    acceptance = read_json(args.acceptance)
    acceptance_check(acceptance, bundle, metadata, report, quality)
    # 防意外错批，不承诺防恶意伪造 JSON；不重复启动长浏览器。
    checked_bundle(args)
    acceptance_check(acceptance, bundle, metadata, report, quality)
    need(file_sha(args.acceptance) == acceptance_sha, '验收文件发生变化')
    receipt = Path(args.receipt).resolve()
    need(receipt.parent == (root / 'outputs').resolve() and not receipt.exists(), '部署回执必须在 outputs 中新建，不覆盖')
    need(args.site_url == SITE_URL, '线上 URL 不在授权范围')
    with publish_lock(root):
        staged_empty(root)
        branch = git(root, 'symbolic-ref', '--short', 'HEAD')
        need(branch in ('master', 'main'), '生产 HEAD 须为 master 或 main')
        local = {b: git(root, 'rev-parse', 'refs/heads/' + b) for b in ('master', 'main')}
        need(local['master'] == local['main'], '本地 master/main 不一致，禁止覆盖分支，请先人工合并')
        url = remote_url(root)
        network_git(root, url, 'fetch', '--no-tags', url,
                    'refs/heads/master:refs/remotes/origin/master', 'refs/heads/main:refs/remotes/origin/main')
        remote = ref_hashes(network_git(root, url, 'ls-remote', url, 'refs/heads/master', 'refs/heads/main'))
        for b in ('master', 'main'):
            need(remote[b] == local[b], '本地/远端 master/main 不一致；禁止顺带推送未审历史提交，需先人工同步')
        checked_bundle(args)
        expected = dict(metadata['artifact_sha256'], **metadata['code_sha256'])
        worktree_allowlist(root, expected)
        need(file_sha(args.acceptance) == acceptance_sha, '验收文件发生变化')
        acceptance_check(acceptance, bundle, metadata, report, quality)
        promote(root, bundle, metadata['artifact_sha256'])
        has_changes = stage_allowlist(root, expected)
        checked_bundle(args)
        if has_changes:
            git(root, 'commit', '-m', '发布 V6 已审周报 ' + report['provenance']['run_id'])
        head = git(root, 'rev-parse', 'HEAD')
        # hook 可以正常拒绝提交，但不能悄悄加入额外文件或改报告内容。
        if has_changes:
            committed = set(filter(None, git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', '-z', head, binary=True).decode('utf-8').split('\0')))
            need(committed <= set(expected), 'hook 提交包含白名单外文件，停止推送')
        for name, sha in expected.items():
            need(hashlib.sha256(git(root, 'show', head + ':' + name, binary=True)).hexdigest() == sha, '提交内容与验收快照不同')
        staged_empty(root)
        unchanged({n: root / n for n in expected}, expected)
        checked_bundle(args)
        need(ref_hashes(network_git(root, url, 'ls-remote', url, 'refs/heads/master', 'refs/heads/main')) == remote, '提交期间远端发生变化，停止推送')
        need(file_sha(args.acceptance) == acceptance_sha, '验收文件发生变化')
        acceptance_check(acceptance, bundle, metadata, report, quality)
        network_git(root, url, 'push', '--atomic', url, head + ':refs/heads/master', head + ':refs/heads/main')
        pushed = {'schema_version': 1, 'passed': True, 'status': 'pushed', 'push_succeeded': True,
                  'verified': False, 'run_id': report['provenance']['run_id'], 'as_of': report['data_as_of'],
                  'pushed_at': datetime.now(CST).isoformat(timespec='seconds'), 'deployed_at': None,
                  'git_commit': head, 'expected_remote_refs': {'master': head, 'main': head},
                  'provenance': report['provenance'], 'artifact_sha256': metadata['artifact_sha256'],
                  'pipeline_seal_sha256': file_sha(bundle / 'pipeline_bundle_v6.json'),
                  'bundle': str(bundle), 'acceptance': str(Path(args.acceptance).resolve()),
                  'acceptance_sha256': acceptance_sha, 'site_url': args.site_url,
                  'online_sha256': None, 'page_publication_status': 'ready', 'receipt_independent': True,
                  'pushed_receipt': str(receipt), 'next_command': 'confirm-online', 'do_not_republish': True}
        try:
            # push 成功即留档，Pages 是否完成属于下一阶段，不再构建/commit/push。
            fresh_json(receipt, pushed)
            need(ref_hashes(network_git(root, url, 'ls-remote', url, 'refs/heads/master', 'refs/heads/main')) == {'master': head, 'main': head}, 'push 已成功，但远端 master/main 核对未通过')
            other = 'main' if branch == 'master' else 'master'
            git(root, 'update-ref', 'refs/heads/' + other, head, local[other])
        except Exception as exc:
            detail = str(exc) if isinstance(exc, PipelineError) else type(exc).__name__ + '（异常正文已屏蔽）'
            raise PushCompletedError(pushed, 'push 已成功；后置记录/分支核对失败：' + detail + '。不要重新 publish；保留同一 commit/artifact 后执行 confirm-online。') from None
        return pushed


def confirm_online(args):
    root = ROOT.resolve()
    pushed_path = Path(args.pushed_receipt).resolve()
    need(pushed_path.parent == (root / 'outputs').resolve(), 'pushed 回执必须位于生产 outputs')
    pushed_sha = file_sha(pushed_path)
    pushed = read_json(pushed_path)
    need(pushed.get('schema_version') == 1 and pushed.get('status') == 'pushed'
         and pushed.get('push_succeeded') is True and pushed.get('verified') is False,
         'confirm-online 只接受已 push、尚未线上验证的回执')
    try:
        bundle, metadata, sources, report, quality = checked_bundle(args)
        need(bundle.parent == (root / 'outputs').resolve() and str(bundle) == pushed.get('bundle'), 'pushed 回执不属于当前生产 bundle')
        need(quality['production_data_passed'] is True and report['provenance']['data_kind'] == 'production'
             and report['publication']['status'] == 'ready', '只可确认同一 ready 生产 artifact')
        need(pushed.get('artifact_sha256') == metadata['artifact_sha256']
             and pushed.get('pipeline_seal_sha256') == file_sha(bundle / 'pipeline_bundle_v6.json')
             and pushed.get('provenance') == report['provenance']
             and pushed.get('run_id') == report['provenance']['run_id'] and pushed.get('as_of') == report['data_as_of'],
             'pushed 回执的 artifact/seal/本期来源不匹配')
        acceptance_path = Path(pushed['acceptance'])
        need(file_sha(acceptance_path) == pushed['acceptance_sha256'], '原验收文件已变化')
        acceptance_check(read_json(acceptance_path), bundle, metadata, report, quality, require_fresh=False)
        head = pushed.get('git_commit')
        need(isinstance(head, str) and re.fullmatch(r'[0-9a-f]{40,64}', head), 'pushed commit 格式无效')
        refs = {'master': head, 'main': head}
        need(pushed.get('expected_remote_refs') == refs and pushed.get('site_url') == SITE_URL, 'pushed 目标分支/站点不匹配')
        receipt = Path(args.receipt).resolve()
        need(receipt.parent == (root / 'outputs').resolve() and not receipt.exists() and receipt != pushed_path,
             '线上回执必须是 outputs 中另一新文件，不覆盖 pushed 回执')
        with publish_lock(root):
            # 只有只读 git 命令；不能同步分支、commit 或再次 push。
            need({b: git(root, 'rev-parse', 'refs/heads/' + b) for b in refs} == refs,
                 '本地 master/main 不等于已 push commit；请人工核对，不重新发布')
            expected = dict(metadata['artifact_sha256'], **metadata['code_sha256'])
            for name, sha in expected.items():
                need(hashlib.sha256(git(root, 'show', head + ':' + name, binary=True)).hexdigest() == sha,
                     '已 push commit 文件不等于已审 artifact：' + name)
            url = remote_url(root)
            need(ref_hashes(network_git(root, url, 'ls-remote', url, 'refs/heads/master', 'refs/heads/main')) == refs,
                 '远端 master/main 不等于已 push commit')
            online = http_hashes(pushed['site_url'], metadata['artifact_sha256'])
            need(ref_hashes(network_git(root, url, 'ls-remote', url, 'refs/heads/master', 'refs/heads/main')) == refs,
                 '线上核对期间远端分支变化，不能签发回执')
            checked_bundle(args)
            need(file_sha(pushed_path) == pushed_sha and file_sha(acceptance_path) == pushed['acceptance_sha256'],
                 '线上核对期间原回执/验收文件变化')
            result = dict(pushed, status='confirmed_online', passed=True, verified=True,
                          deployed_at=datetime.now(CST).isoformat(timespec='seconds'),
                          remote_refs=refs, online_sha256=online, pushed_receipt_sha256=pushed_sha,
                          next_command=None)
            fresh_json(receipt, result)
            return result
    except Exception as exc:
        detail = str(exc) if isinstance(exc, PipelineError) else type(exc).__name__ + '（异常正文已屏蔽）'
        raise PushCompletedError(pushed, 'push 已成功；线上尚未确认：' + detail + '。确认 Pages 对应 build 完成后，只重跑 confirm-online，不要重新 publish。') from None


def parser():
    ap = argparse.ArgumentParser(description='V6 三栏统一周一；离线 build/verify，无默认 push 或通知')
    sub = ap.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect', help='仅调用原 collector CLI，不生成、不发布')
    c.add_argument('stage', choices=('init', 'search', 'details', 'verify'))
    c.add_argument('--config', default=str(ROOT / 'xhs_search_config.json'))
    c.add_argument('--seed')
    c.add_argument('--run-id')
    c.add_argument('--reuse-dir', action='append')
    c.add_argument('--detail-batch', type=int, default=12)
    c.add_argument('--only', choices=('all', 'marketing', 'product', 'sentiment'))
    b = sub.add_parser('build', help='本期审核 strict fail closed，不修改生产 HTML')
    b.add_argument('--output', required=True, help='生产 outputs 下尚不存在的批次目录')
    b.add_argument('--asset-root')
    b.add_argument('--test-fixture', action='store_true')
    v = sub.add_parser('verify', help='真实 bundle 来源、原图、七宽度三 tab 和全文验收')
    v.add_argument('--bundle', required=True)
    v.add_argument('--output', required=True)
    v.add_argument('--browser')
    vp = sub.add_parser('verify-part', help='单 width 三栏/全文/全部原图/模态键盘验收；不采集不部署')
    vp.add_argument('--width', required=True, type=int, choices=(360, 390, 768, 1024, 1120, 1280, 1440))
    vp.add_argument('--bundle', required=True)
    vp.add_argument('--output', required=True)
    vp.add_argument('--browser', help='可选本地 Chromium/Edge 可执行文件')
    vm = sub.add_parser('verify-merge', help='严格合并七份唯一 width part；只核对文件，不运行浏览器')
    vm.add_argument('--parts', required=True, nargs=7, metavar='PART')
    vm.add_argument('--bundle', required=True)
    vm.add_argument('--output', required=True)
    p = sub.add_parser('publish', help='显式部署；只接受2小时内完整验收，重验源/hash及七part，不重跑浏览器')
    p.add_argument('--bundle', required=True)
    p.add_argument('--acceptance', required=True)
    p.add_argument('--receipt', required=True, help='新建 pushed 未线上验证回执；不要因 Pages 延迟重新 publish')
    p.add_argument('--site-url', default=SITE_URL)
    cfo = sub.add_parser('confirm-online', help='已 push 后只读核对同一 commit/artifact，不重新构建/commit/push')
    cfo.add_argument('--bundle', required=True)
    cfo.add_argument('--pushed-receipt', required=True)
    cfo.add_argument('--receipt', required=True, help='另一新建的线上已验证回执')
    for command in (b, v, vp, vm, p, cfo):
        command.add_argument('--config', default=str(ROOT / 'xhs_search_config.json') if command is b else None)
        for key in SOURCES:
            command.add_argument('--' + key)
    return ap


def legacy_main(entry, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 旧参数只允许明确离线构建；默认全流程、任意词重试、单栏、旧详情均拒绝。
    if '--only' in argv or 'publish' in argv or '--push' in argv:
        print('旧入口拒绝单栏/发布旧模式；请显式使用 xhs_weekly_v6.py，绝不自动发布三栏。', file=sys.stderr)
        return 2
    if '--report-only' in argv:
        argv.remove('--report-only')
        if '--no-push' in argv:
            argv.remove('--no-push')
        argv.insert(0, 'build')
    elif argv and argv[0] in ('collect', 'build', 'verify'):
        if '--no-push' in argv:
            argv.remove('--no-push')
    elif argv == ['--help']:
        return main(argv)
    else:
        print(entry + '：旧流程已停用。使用显式 V6 collect/build/verify；--report-only 仅离线 build 且必须指定 --output。', file=sys.stderr)
        return 2
    return main(argv)


def main(argv=None):
    if argv is None and sys.argv[1:] == ['--_git-network']:
        return git_network_worker()
    args = parser().parse_args(argv)
    try:
        if args.command == 'collect':
            return collect(args)
        result = {'build': build, 'verify': verification, 'verify-part': verify_part,
                  'verify-merge': verify_merge, 'publish': publish, 'confirm-online': confirm_online}[args.command](args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except PushCompletedError as exc:
        print(json.dumps(exc.result, ensure_ascii=False), file=sys.stderr)
        return 3
    except Exception as exc:
        # 不回显外部库异常正文，避免凭据 URL、原响应和私有内容泄漏。
        message = str(exc) if isinstance(exc, PipelineError) else '操作失败：' + type(exc).__name__ + '（内部异常正文已屏蔽）'
        print(json.dumps({'passed': False, 'error': message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
