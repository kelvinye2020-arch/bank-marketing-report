"""统一 pipeline 离线 integration；所有外网、采集和生产发布均 mock。"""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xhs_weekly_v6 as pipeline
import xhs_collect_v6 as collector_module
import report_v6
import verify_report_v6

spec = importlib.util.spec_from_file_location('report_fixture_tests', ROOT / 'tests/test_report_v6.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
WRAPPERS = tuple(n for n in pipeline.CODE_FILES if n.endswith('.py') and not n.startswith('tests/')
                 and n not in ('xhs_weekly_v6.py', 'xhs_collect_v6.py', 'report_v6.py', 'verify_report_v6.py', 'mcp_pool.py'))


def browser_result(report, widths=None):
    widths = list(verify_report_v6.WIDTHS if widths is None else widths)
    return {'widths': widths, 'measurements': [
        {'width': w, 'section': s, 'cards': report['counts']['sections'][s],
         'insights': len(report['insights'][s]), 'columns': 1 if w <= 760 else 2 if w < 1120 else 3}
        for w in widths for s in report_v6.SECTIONS], 'browser_errors': [], 'network_errors': [],
        'fulltext_checks': len(report['notes']) * len(widths),
        'image_checks': sum(n['image_count'] for n in report['notes']) * len(widths)}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        for name in pipeline.CODE_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        (self.root / 'outputs').mkdir()
        self.assets = self.root / 'fixture_assets'
        self.assets.mkdir()
        self.inputs = list(fixtures.fixture(self.assets))
        self.inputs[1].update(as_of=self.inputs[3]['as_of'], run_id=self.inputs[3]['run_id'])
        self.sources = {}
        for key, obj in zip(pipeline.SOURCES, self.inputs):
            p = self.root / ('input_' + key + '.json')
            p.write_bytes(pipeline.canonical(obj))
            self.sources[key] = p
        self.args = argparse.Namespace(**{k: str(p) for k, p in self.sources.items()},
            config=str(self.root / 'xhs_search_config.json'), asset_root=str(self.assets),
            output=str(self.root / 'outputs/bundle'), test_fixture=True)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(pipeline, 'ROOT', self.root))
        # 双保险：任意意外网络请求均立刻使测试失败。
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('禁止联网')))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def save(self, index):
        key = pipeline.SOURCES[index]
        self.sources[key].write_bytes(pipeline.canonical(self.inputs[index]))

    def bundle(self):
        pipeline.build(self.args)
        self.args.bundle = self.args.output
        self.args.config = None
        self.args.browser = None
        return Path(self.args.bundle)

    def ready_fixture(self):
        # 仅模拟生产边界，内容仍标为合成；保持正式 15/5/4/3 门槛。
        store, reviews, editorial, manifest = self.inputs
        originals = copy.deepcopy(store['notes'])
        for section, target in (('marketing', 15), ('product', 5), ('sentiment', 4)):
            base = next(n for n in originals if n['id'] == 'fixture_' + section)
            for i in range(1, target):
                row = copy.deepcopy(base)
                row['id'] = 'mock_' + section + '_' + str(i)
                row['evidence']['url'] = base['evidence']['url'].split('?')[0].rsplit('/', 1)[0] + '/' + row['id']
                store['notes'].append(row)
                reviews['reviews'][row['id']] = fixtures.review_for(row, section, self.assets)
        config = pipeline.read_json(self.args.config)
        manifest.update(data_kind='production', config_sha256=hashlib.sha256(pipeline.canonical(config)).hexdigest())
        groups = []
        for s, words in config['fixed_groups'].items():
            for word in words:
                groups.append({'key': 'f%02d' % (len(groups) + 1), 'section': s, 'keyword': word,
                    'required': True, 'mode': 'default', 'status': 'success', 'as_of': manifest['as_of'], 'run_id': manifest['run_id']})
        for i, item in enumerate(config['supplements'], 1):
            groups.append(dict(item, key='s%02d' % i, section=next(s for s, words in config['fixed_groups'].items() if item['keyword'] in words),
                required=False, mode='recent', status='success', as_of=manifest['as_of'], run_id=manifest['run_id']))
        manifest.update(groups=groups, required_groups=[g['key'] for g in groups if g['required']])
        editorial['publication']['status'] = 'ready'
        for i in range(4):
            self.save(i)
        self.args.test_fixture = False
        (self.root / 'imgs').mkdir()
        for name in pipeline.ARTIFACTS:
            (self.root / name).write_bytes('原生产占位，不是测试发布'.encode('utf-8'))
        return self.bundle()

    def verified(self):
        self.args.output = str(self.root / 'outputs/acceptance.json')
        report = pipeline.read_json(Path(self.args.bundle) / 'public_report_v6.json')
        with patch.object(verify_report_v6, 'browser_verify', return_value=browser_result(report)) as browser:
            result = pipeline.verification(self.args)
        browser.assert_called_once()
        return result

    def verified_parts(self):
        report = pipeline.read_json(Path(self.args.bundle) / 'public_report_v6.json')
        paths = []
        def single(root, data, executable=None, screenshots=None, widths=None):
            self.assertEqual(len(widths), 1)
            return browser_result(data, widths)
        with patch.object(verify_report_v6, 'browser_verify', side_effect=single) as browser:
            for width in verify_report_v6.WIDTHS:
                self.args.width = width
                self.args.output = str(self.root / f'outputs/part_{width}.json')
                with patch.object(pipeline, 'checked_bundle', wraps=pipeline.checked_bundle) as checks:
                    part = pipeline.verify_part(self.args)
                self.assertEqual(checks.call_count, 2)
                self.assertEqual(part['browser'], browser_result(report, [width]))
                self.assertEqual(part['width'], width)
                self.assertIn('checked_at', part)
                paths.append(self.args.output)
            self.assertEqual(browser.call_count, 7)
        self.args.parts = paths
        self.args.output = str(self.root / 'outputs/acceptance_merged.json')
        return paths

    def test_parts_merge_matches_full_and_binds_each_file_without_browser(self):
        self.ready_fixture()
        paths = self.verified_parts()
        with patch.object(verify_report_v6, 'browser_verify', side_effect=AssertionError('合并禁止浏览器')):
            merged = pipeline.verify_merge(self.args)
        bundle, meta, _, report, quality = pipeline.checked_bundle(self.args)
        self.assertEqual(merged['browser'], browser_result(report))
        self.assertEqual([r['width'] for r in merged['parts']], list(verify_report_v6.WIDTHS))
        self.assertEqual([r['sha256'] for r in merged['parts']], [pipeline.file_sha(p) for p in paths])
        pipeline.acceptance_check(merged, bundle, meta, report, quality)
        # 只追加空白也必须阻断：JSON 语义仍合法，但 merge 绑定的字节变化。
        Path(paths[0]).write_bytes(Path(paths[0]).read_bytes() + b' ')
        with self.assertRaisesRegex(pipeline.PipelineError, 'part.*变化'):
            pipeline.acceptance_check(merged, bundle, meta, report, quality)
        self.args.acceptance = self.args.output
        with patch.object(pipeline, 'staged_empty'), patch.object(pipeline, 'network_git') as network, patch.object(pipeline, 'promote') as promote:
            with self.assertRaises(pipeline.PipelineError):
                pipeline.publish(self.args)
            network.assert_not_called()
            promote.assert_not_called()

    def test_merge_missing_duplicate_width_and_invalid_part_refused(self):
        self.bundle()
        paths = self.verified_parts()
        originals = [Path(p).read_bytes() for p in paths]
        self.args.parts = paths[:-1]
        with self.assertRaisesRegex(pipeline.PipelineError, '漏 width'):
            pipeline.verify_merge(self.args)
        self.args.parts = paths[:-1] + [paths[0]]
        with self.assertRaisesRegex(pipeline.PipelineError, '路径重复'):
            pipeline.verify_merge(self.args)
        self.args.parts = paths
        first = pipeline.read_json(paths[0])
        Path(paths[-1]).write_bytes(pipeline.canonical(first))
        with self.assertRaisesRegex(pipeline.PipelineError, 'width.*重复'):
            pipeline.verify_merge(self.args)
        Path(paths[-1]).write_bytes(originals[-1])
        changes = [('run_id', '旧批'), ('source_sha256', {}), ('code_sha256', {}),
                   ('pipeline_seal_sha256', '0' * 64), ('artifact_sha256', {}), ('error', '')]
        for field, value in changes:
            with self.subTest(field=field):
                altered = dict(first, **{field: value})
                Path(paths[0]).write_bytes(pipeline.canonical(altered))
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.verify_merge(self.args)
        for field, value in (('fulltext_checks', 99), ('image_checks', 271), ('browser_errors', ['合成错误'])):
            with self.subTest(browser_field=field):
                altered = copy.deepcopy(first)
                altered['browser'][field] = value
                Path(paths[0]).write_bytes(pipeline.canonical(altered))
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.verify_merge(self.args)
        altered = copy.deepcopy(first)
        altered['browser']['measurements'][0]['columns'] = 3
        Path(paths[0]).write_bytes(pipeline.canonical(altered))
        with self.assertRaises(pipeline.PipelineError):
            pipeline.verify_merge(self.args)
        self.assertFalse(Path(self.args.output).exists())
        Path(paths[0]).write_bytes(originals[0])
        self.sources['store'].write_bytes(self.sources['store'].read_bytes() + b' ')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.verify_merge(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_part_source_change_during_browser_refused_without_receipt(self):
        self.bundle()
        self.args.width = 360
        self.args.output = str(self.root / 'outputs/part_failed.json')
        def changed(root, report, executable=None, widths=None):
            self.assertEqual(widths, [360])
            self.sources['reviews'].write_bytes(self.sources['reviews'].read_bytes() + b' ')
            return browser_result(report, widths)
        with patch.object(verify_report_v6, 'browser_verify', side_effect=changed):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.verify_part(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_acceptance_time_is_browser_time_not_merge_refresh(self):
        self.ready_fixture()
        paths = self.verified_parts()
        now = pipeline.datetime.now(pipeline.CST)
        first = pipeline.read_json(paths[0])
        first['checked_at'] = (now - pipeline.timedelta(minutes=30)).isoformat(timespec='seconds')
        Path(paths[0]).write_bytes(pipeline.canonical(first))
        merged = pipeline.verify_merge(self.args)
        self.assertEqual(merged['verified_at'], first['checked_at'])
        bundle, meta, _, report, quality = pipeline.checked_bundle(self.args)
        pipeline.acceptance_check(merged, bundle, meta, report, quality)
        for stamp in ((now - pipeline.timedelta(hours=3)).isoformat(),
                      (now + pipeline.timedelta(hours=1)).isoformat(), '2026-10-23T12:00:00', None):
            with self.subTest(stamp=stamp):
                altered = dict(merged, verified_at=stamp)
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.acceptance_check(altered, bundle, meta, report, quality)
        first['checked_at'] = (now - pipeline.timedelta(hours=3)).isoformat(timespec='seconds')
        Path(paths[0]).write_bytes(pipeline.canonical(first))
        self.args.output = str(self.root / 'outputs/expired_merge.json')
        with self.assertRaisesRegex(pipeline.PipelineError, '超过2小时'):
            pipeline.verify_merge(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_part_cli_and_merge_cli_strict_parameters(self):
        part = pipeline.parser().parse_args(['verify-part', '--width', '390', '--bundle', 'b', '--output', 'p', '--browser', 'edge'])
        self.assertEqual((part.width, part.bundle, part.output, part.browser), (390, 'b', 'p', 'edge'))
        paths = [str(w) for w in verify_report_v6.WIDTHS]
        merge = pipeline.parser().parse_args(['verify-merge', '--parts', *paths, '--bundle', 'b', '--output', 'a'])
        self.assertEqual(merge.parts, paths)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pipeline.parser().parse_args(['verify-merge', '--parts', *paths[:-1], '--bundle', 'b', '--output', 'a'])
        with self.assertRaises(ValueError):
            verify_report_v6.browser_verify(self.root, {}, widths=[])
        with self.assertRaises(ValueError):
            verify_report_v6.browser_verify(self.root, {}, widths=[360, 360])

    def test_build_sealed_no_overwrite_no_input_or_production_changes(self):
        before = pipeline.hashes(self.sources)
        bundle = self.bundle()
        self.assertEqual(pipeline.hashes(self.sources), before)
        self.assertFalse((self.root / 'bank_marketing_report.html').exists())
        self.assertTrue((bundle / 'pipeline_bundle_v6.json').exists())
        self.assertEqual(pipeline.checked_bundle(self.args)[3]['publication']['status'], 'draft')
        self.args.config = str(self.root / 'xhs_search_config.json')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.build(self.args)

    def test_pipeline_source_images_use_original_paths_and_mapping_is_mandatory(self):
        bundle = self.bundle()
        meta_path = bundle / 'pipeline_bundle_v6.json'
        meta = pipeline.read_json(meta_path)
        quality = pipeline.read_json(bundle / 'report_quality_v6.json')
        expected = {source: quality['images'][public] for public, source in quality['image_sources'].items()}
        self.assertEqual(meta['source_image_sha256'], expected)
        self.assertTrue(set(expected).isdisjoint(quality['images']))
        self.assertTrue(all((self.assets / source).is_file() for source in expected))
        self.assertTrue(all(not (self.assets / public).exists() for public in quality['images']))
        pipeline.checked_bundle(self.args)
        for wrong in ({}, quality['images'], dict(expected, **{'imgs/extra.png': '0' * 64})):
            with self.subTest(wrong=wrong):
                meta['source_image_sha256'] = wrong
                meta_path.write_bytes(pipeline.canonical(meta))
                with self.assertRaisesRegex(pipeline.PipelineError, 'pipeline源图哈希'):
                    pipeline.checked_bundle(self.args)

    def test_original_filename_collision_does_not_block_mapped_promote(self):
        (self.root / 'imgs').mkdir()
        old = self.root / 'imgs/fixture_0.png'
        old.write_bytes(b'original historical bytes')
        source_before = pipeline.hashes({p: self.assets / p for p in ('imgs/fixture_0.png', 'imgs/fixture_1.png')})
        bundle = self.bundle()
        expected = pipeline.read_json(bundle / 'pipeline_bundle_v6.json')['artifact_sha256']
        self.assertNotIn('imgs/fixture_0.png', expected)
        pipeline.promote(self.root, bundle, expected)
        self.assertEqual(old.read_bytes(), b'original historical bytes')
        self.assertEqual(pipeline.hashes({p: self.assets / p for p in source_before}), source_before)
        self.assertEqual(pipeline.hashes({p: self.root / p for p in expected}), expected)
        pipeline.checked_bundle(self.args)

    def test_review_current_period_and_published_fail_closed(self):
        self.inputs[1]['run_id'] = 'old'
        self.save(1)
        with self.assertRaises(pipeline.PipelineError):
            pipeline.build(self.args)
        self.inputs[1]['run_id'] = self.inputs[3]['run_id']
        self.save(1)
        self.inputs[2]['publication']['status'] = 'published'
        self.save(2)
        with self.assertRaises(pipeline.PipelineError):
            pipeline.build(self.args)
        self.assertFalse(Path(self.args.output).exists())

    def test_production_missing_fixed_word_and_lower_threshold_blocked(self):
        self.ready_fixture()
        manifest = self.inputs[3]
        manifest['groups'][0]['keyword'] = '其他词'
        with self.assertRaises(pipeline.PipelineError):
            pipeline.coverage_check(manifest, pipeline.read_json(self.root / 'xhs_search_config.json'))
        with self.assertRaises(report_v6.ValidationError):
            report_v6.build_report(*self.inputs, self.assets, minimums=fixtures.ZERO)

    def test_verify_real_bundle_binds_images_input_and_scripts(self):
        self.bundle()
        result = self.verified()
        self.assertTrue(result['passed'])
        self.assertTrue(result['source_inputs_verified'])
        self.assertEqual(result['artifact_sha256'], pipeline.read_json(Path(self.args.bundle) / 'pipeline_bundle_v6.json')['artifact_sha256'])
        self.sources['reviews'].write_bytes(self.sources['reviews'].read_bytes() + b' ')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.checked_bundle(self.args)

    def test_corrupt_bundle_and_changed_code_or_source_image_blocked(self):
        bundle = self.bundle()
        code = self.root / 'mcp_pool.py'
        initial = code.read_bytes()
        code.write_bytes(initial + b'\n')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.checked_bundle(self.args)
        code.write_bytes(initial)
        image = self.assets / 'imgs/fixture_0.png'
        original = image.read_bytes()
        image.write_bytes(original + b'changed')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.checked_bundle(self.args)
        image.write_bytes(original)
        (bundle / 'bank_marketing_report.html').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            pipeline.checked_bundle(self.args)

    def test_collect_calls_original_cli_only_and_only_refused(self):
        args = argparse.Namespace(config=str(self.root / 'xhs_search_config.json'), stage='search', only=None,
            seed=None, run_id='mock-run', detail_batch=12, reuse_dir=None)
        with patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=1)) as process:
            self.assertEqual(pipeline.collect(args), 1)
            command = process.call_args.args[0]
            self.assertEqual(command[:2], [sys.executable, '-B'])
            self.assertIn('xhs_collect_v6.py', command[2])
            self.assertIn('search', command)
            self.assertEqual(process.call_args.kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
        args.only = 'sentiment'
        with patch.object(pipeline.subprocess, 'run') as process, self.assertRaises(pipeline.PipelineError):
            pipeline.collect(args)
        process.assert_not_called()

    def test_promote_prechecks_collision_preserves_history_and_html(self):
        bundle = self.bundle()
        expected = pipeline.read_json(bundle / 'pipeline_bundle_v6.json')['artifact_sha256']
        (self.root / 'imgs').mkdir()
        history = self.root / 'imgs/historical.png'
        history.write_bytes(b'history')
        html = self.root / pipeline.ARTIFACTS[0]
        html.write_bytes(b'old HTML')
        quality = pipeline.read_json(bundle / 'report_quality_v6.json')
        public = next(p for p, source in quality['image_sources'].items() if source == 'imgs/fixture_0.png')
        original = self.root / 'imgs/fixture_0.png'
        original.write_bytes(b'different original bytes, never overwrite')
        collision = self.root / public
        collision.write_bytes(b'different published bytes')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.promote(self.root, bundle, expected)
        self.assertEqual(html.read_bytes(), b'old HTML')
        self.assertEqual(history.read_bytes(), b'history')
        self.assertEqual(collision.read_bytes(), b'different published bytes')
        collision.write_bytes((bundle / public).read_bytes())
        pipeline.promote(self.root, bundle, expected)
        pipeline.promote(self.root, bundle, expected)
        self.assertEqual(pipeline.hashes({n: self.root / n for n in expected}), expected)
        self.assertEqual(history.read_bytes(), b'history')
        self.assertEqual(original.read_bytes(), b'different original bytes, never overwrite')

    def test_existing_staging_first_failure_no_promote_network(self):
        args = argparse.Namespace()
        with patch.object(pipeline, 'git', return_value=b'unknown\0'), patch.object(pipeline, 'checked_bundle') as check, patch.object(pipeline, 'network_git') as net:
            with self.assertRaises(pipeline.PipelineError):
                pipeline.publish(args)
        check.assert_not_called()
        net.assert_not_called()

    def test_stage_explicit_files_test_force_only_no_add_all(self):
        expected = {'bank_marketing_report.html': 'a' * 64, 'mcp_pool.py': 'b' * 64,
                    'tests/test_report_v6.py': 'c' * 64, 'tests/test_xhs_weekly_v6.py': 'd' * 64}
        def fake_git(root, *args, **kwargs):
            if args[:3] == ('diff', '--cached', '--name-only'):
                return b''
            if args[0] == 'diff':
                return b''
            return ''
        with patch.object(pipeline, 'git', side_effect=fake_git) as g, patch.object(pipeline, 'unchanged'), patch.object(pipeline, 'check_index'):
            pipeline.stage_allowlist(self.root, expected)
        adds = [c.args[1:] for c in g.call_args_list if 'add' in c.args]
        self.assertIn(('-c', 'core.autocrlf=false', 'add', '-f', '--', 'tests/test_report_v6.py', 'tests/test_xhs_weekly_v6.py'), adds)
        self.assertTrue(any('mcp_pool.py' in c for c in adds))
        self.assertTrue(all('-A' not in c and '.' not in c and 'imgs' not in c for c in adds))
        self.assertFalse(any(n in pipeline.CODE_FILES for n in ('git_token.json', 'note_details.json', 'xhs_state/evidence_store.json')))

    def test_real_local_git_exact_stage_and_normal_commit_hook(self):
        # 仅临时本地仓库，不配置远端、不推送；验证真实 index 字节和正常 hook。
        for args in (('init', '-q'), ('config', 'user.name', '离线测试'),
                     ('config', 'user.email', 'fixture@example.invalid')):
            process = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, env=pipeline.git_env())
            self.assertEqual(process.returncode, 0)
        bundle = self.bundle()
        (self.root / 'imgs').mkdir()
        meta = pipeline.read_json(bundle / 'pipeline_bundle_v6.json')
        pipeline.promote(self.root, bundle, meta['artifact_sha256'])
        expected = dict(meta['artifact_sha256'], **meta['code_sha256'])
        self.assertTrue(pipeline.stage_allowlist(self.root, expected))
        staged = pipeline.git(self.root, 'diff', '--cached', '--name-only', '-z', binary=True)
        self.assertNotIn(b'input_store.json', staged)
        self.assertNotIn(b'outputs/', staged)
        pipeline.check_index(self.root, expected)
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
        with self.assertRaises(pipeline.PipelineError):
            pipeline.git(self.root, 'commit', '-m', '合成离线测试')
        self.assertTrue(pipeline.git(self.root, 'diff', '--cached', '--name-only'))

    def test_network_parent_never_reads_token_or_logs_child_error(self):
        failed = Mock(returncode=2, stdout=b'', stderr=b'https://SECRET@github.com/repo')
        with patch.object(pipeline.subprocess, 'run', return_value=failed) as process:
            with self.assertRaises(pipeline.PipelineError) as exc:
                pipeline.network_git(self.root, 'https://github.com' + pipeline.REMOTE_PATH, 'ls-remote')
        self.assertNotIn('SECRET', str(exc.exception))
        self.assertNotIn('SECRET', str(process.call_args))
        self.assertNotIn('git_token', str(process.call_args))

    def test_network_worker_credential_env_no_url_no_trace(self):
        token = 'ghp_ghp_mock_secret_PAT_not_real'
        url = 'https://github.com' + pipeline.REMOTE_PATH
        payload = pipeline.canonical({'root': str(self.root), 'url': url, 'args': ['ls-remote', url, 'refs/heads/master', 'refs/heads/main']})
        stream = Mock(buffer=io.BytesIO(payload))
        output = io.StringIO()
        response = Mock(returncode=0, stdout=('a' * 40 + '\trefs/heads/master\n' + 'a' * 40 + '\trefs/heads/main\n').encode(), stderr=b'')
        with patch.object(pipeline, 'remote_url', return_value=url), patch.object(pipeline, 'read_json', return_value={'github_pat': token}), patch.object(pipeline.sys, 'stdin', stream), patch.object(pipeline.subprocess, 'run', return_value=response) as process, patch.dict(os.environ, {'GIT_TRACE': '1', 'GIT_CURL_VERBOSE': '1'}), redirect_stdout(output):
            self.assertEqual(pipeline.git_network_worker(), 0)
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn(token, str(process.call_args.args))
        env = process.call_args.kwargs['env']
        self.assertNotIn('GIT_TRACE', env)
        self.assertNotIn('GIT_CURL_VERBOSE', env)
        self.assertIn('Authorization: Basic ', env['GIT_CONFIG_VALUE_1'])
        import base64
        actual = base64.b64decode(env['GIT_CONFIG_VALUE_1'].split('Basic ', 1)[1]).decode()
        self.assertEqual(actual, 'x-access-token:' + token[4:])

    def test_acceptance_requires_all_widths_fulltext_and_images(self):
        self.ready_fixture()
        receipt = self.verified()
        bundle, meta, _, report, quality = pipeline.checked_bundle(self.args)
        pipeline.acceptance_check(receipt, bundle, meta, report, quality)
        receipt['browser']['image_checks'] -= 1
        with self.assertRaises(pipeline.PipelineError):
            pipeline.acceptance_check(receipt, bundle, meta, report, quality)

    def mocked_publish(self, online_failure=False, remote_changed=False, post_push_failure=False, split=False):
        bundle = self.ready_fixture()
        if split:
            self.verified_parts()
            acceptance = pipeline.verify_merge(self.args)
        else:
            acceptance = self.verified()
        self.args.acceptance = self.args.output
        self.args.receipt = str(self.root / 'outputs/deployment.json')
        self.args.site_url = pipeline.SITE_URL
        expected = pipeline.read_json(bundle / 'pipeline_bundle_v6.json')['artifact_sha256']
        old, new = 'a' * 40, 'b' * 40
        calls = []
        def g(root, *args, **kwargs):
            calls.append(args)
            if args[:3] == ('diff', '--cached', '--name-only') or args[0] in ('diff', 'diff-tree'):
                return b''
            if args[0] == 'config':
                return 'https://github.com' + pipeline.REMOTE_PATH
            if args[0] == 'remote':
                return 'https://github.com' + pipeline.REMOTE_PATH
            if args[0] == 'symbolic-ref':
                return 'master'
            if args[0] == 'rev-parse':
                return new if args[1] == 'HEAD' else old
            if args[0] == 'merge-base':
                return old
            if args[0] == 'show':
                return (self.root / args[1].split(':', 1)[1]).read_bytes()
            return ''
        count = 0
        def network(root, url, *args):
            nonlocal count
            calls.append(args)
            if args[0] == 'ls-remote':
                count += 1
                if post_push_failure and count == 3:
                    raise pipeline.PipelineError('模拟已 push 后远端检查连接失败')
                value = new if count >= 3 or remote_changed and count == 2 else old
                return value + '\trefs/heads/master\n' + value + '\trefs/heads/main'
            return ''
        original = pipeline.file_sha(bundle / 'bank_marketing_report.html')
        report = pipeline.read_json(bundle / 'public_report_v6.json')
        online = Mock(side_effect=pipeline.PipelineError('线上尚未部署')) if online_failure else Mock(return_value=expected)
        with patch.object(pipeline, 'git', side_effect=g), patch.object(pipeline, 'network_git', side_effect=network), patch.object(pipeline, 'stage_allowlist', return_value=True), patch.object(pipeline, 'http_hashes', online), patch.object(verify_report_v6, 'browser_verify', return_value=browser_result(report)) as browser:
            if remote_changed:
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.publish(self.args)
                self.assertFalse(Path(self.args.receipt).exists())
                self.assertEqual(count, 2)
            elif post_push_failure:
                with self.assertRaises(pipeline.PushCompletedError) as exc:
                    pipeline.publish(self.args)
                self.assertTrue(exc.exception.result['push_succeeded'])
                self.assertTrue(exc.exception.result['do_not_republish'])
                self.assertEqual(exc.exception.result['next_command'], 'confirm-online')
                self.assertEqual(pipeline.read_json(self.args.receipt)['status'], 'pushed')
                self.assertFalse(pipeline.read_json(self.args.receipt)['verified'])
                online.assert_not_called()
            else:
                result = pipeline.publish(self.args)
                self.assertFalse(result['verified'])
                self.assertTrue(result['push_succeeded'])
                self.assertEqual(result['status'], 'pushed')
                self.assertIsNone(result['deployed_at'])
                self.assertIsNone(result['online_sha256'])
                self.assertEqual(result['expected_remote_refs'], {'master': new, 'main': new})
                pushed_path = Path(self.args.receipt)
                pushed_sha = pipeline.file_sha(pushed_path)
                online.assert_not_called()
                # 模拟主代理已查 Pages build：后续只有 confirm-online，不能写 git。
                start_confirm = len(calls)
                self.args.pushed_receipt = str(pushed_path)
                self.args.receipt = str(self.root / 'outputs/confirmed.json')
                def confirmed_git(root, *args, **kwargs):
                    if args[0] == 'rev-parse':
                        calls.append(args)
                        return new
                    return g(root, *args, **kwargs)
                with patch.object(pipeline, 'git', side_effect=confirmed_git), patch.object(pipeline, 'build', side_effect=AssertionError('禁止重建')):
                    if online_failure:
                        with self.assertRaises(pipeline.PushCompletedError) as exc:
                            pipeline.confirm_online(self.args)
                        self.assertTrue(exc.exception.result['push_succeeded'])
                        self.assertFalse(exc.exception.result['verified'])
                        self.assertEqual(exc.exception.result['next_command'], 'confirm-online')
                        self.assertFalse(Path(self.args.receipt).exists())
                        self.assertEqual(pipeline.file_sha(pushed_path), pushed_sha)
                        # 不睡眠；模拟稍后 Pages 就绪，重试确认无需重新 publish。
                        online.side_effect = None
                        online.return_value = expected
                    confirmed = pipeline.confirm_online(self.args)
                    self.assertTrue(confirmed['verified'])
                    self.assertEqual(confirmed['status'], 'confirmed_online')
                    self.assertEqual(confirmed['remote_refs'], {'master': new, 'main': new})
                    self.assertEqual(confirmed['online_sha256'], expected)
                    self.assertEqual(confirmed['page_publication_status'], 'ready')
                self.assertEqual(pipeline.file_sha(pushed_path), pushed_sha)
                self.assertFalse(any(c[0] in ('push', 'fetch', 'commit', 'update-ref', 'add') for c in calls[start_confirm:]))
                self.assertEqual(sum(c[0] == 'push' for c in calls), 1)
                self.assertEqual(sum(c[0] == 'commit' for c in calls), 1)
            browser.assert_not_called()
        self.assertEqual(pipeline.file_sha(bundle / 'bank_marketing_report.html'), original)
        self.assertEqual(pipeline.read_json(bundle / 'public_report_v6.json')['publication']['status'], 'ready')
        self.assertFalse(any('--no-verify' in c or '--force' in c for c in calls))
        if remote_changed:
            self.assertFalse(any(c[0] == 'push' for c in calls))
        return acceptance

    def test_mock_publish_only_ready_verified_bundle_independent_receipt(self):
        self.mocked_publish()

    def test_mock_publish_seven_parts_rechecked_without_browser(self):
        self.mocked_publish(split=True)

    def test_online_mismatch_no_success_receipt_no_rebuild(self):
        self.mocked_publish(online_failure=True)

    def test_remote_changed_no_push(self):
        self.mocked_publish(remote_changed=True)

    def test_post_push_failure_keeps_pushed_receipt_and_distinct_exit_code(self):
        self.mocked_publish(post_push_failure=True)
        with patch.object(pipeline, 'confirm_online', side_effect=pipeline.PushCompletedError(
                {'push_succeeded': True, 'status': 'pushed', 'git_commit': 'a' * 40}, 'push 已成功；线上未确认')), redirect_stderr(io.StringIO()) as output:
            code = pipeline.main(['confirm-online', '--bundle', 'unused', '--pushed-receipt', 'unused', '--receipt', 'unused'])
        self.assertEqual(code, 3)
        self.assertTrue(json.loads(output.getvalue())['push_succeeded'])
        self.assertEqual(json.loads(output.getvalue())['next_command'], 'confirm-online')

    def test_confirm_wrong_artifact_or_commit_no_online_receipt_or_git_write(self):
        bundle = self.ready_fixture()
        self.verified()
        meta = pipeline.read_json(bundle / 'pipeline_bundle_v6.json')
        report = pipeline.read_json(bundle / 'public_report_v6.json')
        commit = 'a' * 40
        pushed = {'schema_version': 1, 'status': 'pushed', 'push_succeeded': True, 'verified': False,
                  'bundle': str(bundle), 'artifact_sha256': meta['artifact_sha256'],
                  'pipeline_seal_sha256': pipeline.file_sha(bundle / 'pipeline_bundle_v6.json'),
                  'provenance': report['provenance'], 'run_id': report['provenance']['run_id'],
                  'as_of': report['data_as_of'], 'git_commit': commit,
                  'expected_remote_refs': {'master': commit, 'main': commit}, 'site_url': pipeline.SITE_URL,
                  'acceptance': str(self.root / 'outputs/acceptance.json'),
                  'acceptance_sha256': pipeline.file_sha(self.root / 'outputs/acceptance.json')}
        self.args.pushed_receipt = str(self.root / 'outputs/pushed.json')
        self.args.receipt = str(self.root / 'outputs/confirmed.json')
        for field, value in (('pipeline_seal_sha256', '0' * 64), ('git_commit', 'b' * 40), ('git_commit', commit)):
            altered = dict(pushed, **{field: value})
            Path(self.args.pushed_receipt).write_bytes(pipeline.canonical(altered))
            with self.subTest(field=field, value=value), patch.object(pipeline, 'network_git') as net, patch.object(pipeline, 'http_hashes') as http, patch.object(pipeline, 'git', return_value='b' * 40) as g:
                with self.assertRaises(pipeline.PushCompletedError):
                    pipeline.confirm_online(self.args)
                net.assert_not_called()
                http.assert_not_called()
                self.assertFalse(Path(self.args.receipt).exists())
                self.assertTrue(all(c.args[1] == 'rev-parse' for c in g.call_args_list))

    def test_fixture_draft_never_publish(self):
        self.bundle()
        with patch.object(pipeline, 'staged_empty'), patch.object(pipeline, 'network_git') as network:
            with self.assertRaises(pipeline.PipelineError):
                pipeline.publish(self.args)
        network.assert_not_called()

    def test_http_hash_each_artifact_redirects_off_and_credentials_off(self):
        payloads = {'bank_marketing_report.html': b'HTML', 'imgs/mock.png': b'image'}
        expected = {n: hashlib.sha256(b).hexdigest() for n, b in payloads.items()}
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        def response(url, **kwargs):
            r = Mock(status_code=200)
            r.iter_content.return_value = [payloads[url.removeprefix(pipeline.SITE_URL)]]
            r.__enter__ = Mock(return_value=r)
            r.__exit__ = Mock(return_value=False)
            self.assertFalse(kwargs['allow_redirects'])
            return r
        session.get.side_effect = response
        with patch('requests.Session', return_value=session):
            self.assertEqual(pipeline.http_hashes(pipeline.SITE_URL, expected), expected)
            wrong = dict(expected)
            wrong['imgs/mock.png'] = '0' * 64
            with self.assertRaises(pipeline.PipelineError):
                pipeline.http_hashes(pipeline.SITE_URL, wrong)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_count, len(expected) * 2)


class CollectorRolloverTests(unittest.TestCase):
    OLD_TIME = '2026-10-19T09:00:00+08:00'
    NEW_TIME = '2026-10-26T09:00:00+08:00'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = collector_module.load(ROOT / 'xhs_search_config.json')
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(collector_module, 'ROOT', self.root))
        self.clock = self.stack.enter_context(patch.object(collector_module, 'now', return_value=self.OLD_TIME))
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('禁止联网')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('禁止MCP进程')))
        self.stack.enter_context(patch.object(collector_module.Collector, 'configure_pool', side_effect=AssertionError('禁止MCP')))
        self.old = self.make('old-week', stage='init')
        store, _, _, _ = fixtures.fixture(self.old.state, as_of='2026-10-19')
        self.old.store = {'schema_version': 6, 'notes': []}
        for index, row in enumerate(store['notes']):
            published = collector_module.datetime.fromisoformat(row['evidence']['pub']).replace(tzinfo=collector_module.CST)
            row['evidence']['pub'] = published.isoformat()
            original = copy.deepcopy(row)
            row.update(title=row['evidence']['t'], author=row['evidence']['a'], date=published.date().isoformat(), status='pending')
            assets = [{'local_path': (self.old.state / p).relative_to(self.root).as_posix(),
                       'sha256': collector_module.file_hash(self.old.state / p)} for p in row['evidence']['imgs']]
            row['_collector'] = {'origin': 'seed' if index == 0 else 'new_candidate',
                'evidence_sha256': collector_module.digest(row['evidence']), 'image_manifest': assets,
                'seed_original_note': original, 'seed_note_sha256': collector_module.digest(original),
                'review_status': 'seed_preserved' if index == 0 else 'pending',
                'detail_status': 'complete', 'comments_status': 'zero_reported',
                'metrics_history': [], 'sources_internal': []}
            if index:
                collector_module.atomic(self.old.raw / ('detail_' + row['id'] + '.json'), {
                    'status': 'complete', 'started_at': self.OLD_TIME, 'finished_at': self.OLD_TIME,
                    'payload': {'data': {'note': {'time': int(published.timestamp() * 1000),
                        'desc': row['evidence']['desc'], 'imageList': [{} for _ in row['evidence']['imgs']]}}}})
            self.old.store['notes'].append(row)
        self.old.catalog = {'old-search-id': {'title': '旧周仅检索候选'}}
        self.old.queue['items'] = [{'id': 'old-search-id', 'status': 'pending_detail'}]
        keyword = self.config['fixed_groups']['marketing'][0]
        self.old_response = self.old.raw / 'f01_attempt1.json'
        collector_module.atomic(self.old_response, {'started_at': self.OLD_TIME, 'finished_at': self.OLD_TIME,
            'http_status': 200, 'arguments': {'keyword': keyword}, 'response': {'result': {'content': [
                {'type': 'text', 'text': json.dumps({'feeds': []})}]}}})
        self.old.manifest.update(searches=[{'key': 'f01', 'mode': 'default', 'status': 'ok',
            'attempts': [{'status': 'ok', 'raw_private_path': self.old_response.relative_to(self.root).as_posix()}]}],
            search_finished_at=self.OLD_TIME, search_status='pass', details=[{'id': 'old-detail-id', 'status': 'complete'}])
        self.old.persist()
        for name in ('reviews_v6.json', 'editorial_v6.json', 'handoff_internal.json', '本期交接.txt'):
            (self.old.state / name).write_bytes(('旧周审核交接，不得改写：' + name).encode('utf-8'))
        (self.old.run / 'handoff_internal.txt').write_bytes(b'old private handoff')
        self.before = self.snapshot()
        self.clock.return_value = self.NEW_TIME

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def make(self, run_id, stage='search', config=None, seed=None):
        args = argparse.Namespace(run_id=run_id, stage=stage, seed=seed, reuse_dir=None, detail_batch=12)
        instance = collector_module.Collector(config or self.config, args)
        self.addCleanup(instance.download_executor.shutdown, wait=True)
        return instance

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def rejected_unchanged(self, run_id, stage, message, config=None):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, message):
            self.make(run_id, stage, config=config)
        self.assertEqual(self.snapshot(), before)

    def test_new_search_archives_old_inputs_and_hashes_before_reset(self):
        fresh = self.make('new-week')
        archive = self.old.run / 'archive'
        expected = {p.name: p.read_bytes() for p in self.old.state.iterdir() if p.is_file()}
        for name in ('search_manifest.json', 'candidate_queue.json'):
            expected[name] = self.before['xhs_state/' + name]
        expected.update({'run/' + name.removeprefix('xhs_state/runs/old-week/'): blob
                         for name, blob in self.before.items() if name.startswith('xhs_state/runs/old-week/')})
        seal = collector_module.load(archive / 'archive_hashes.json')
        self.assertTrue(seal['immutable'])
        self.assertEqual(seal['run_id'], 'old-week')
        self.assertEqual(seal['next_run_id'], 'new-week')
        self.assertEqual(seal['archived_at'], self.NEW_TIME)
        hashes = {name: hashlib.sha256(blob).hexdigest() for name, blob in expected.items()}
        self.assertEqual(seal['file_sha256'], hashes)
        self.assertEqual(seal['files_sha256'], collector_module.digest(hashes))
        asset_hashes = {name.removeprefix('xhs_state/'): hashlib.sha256(blob).hexdigest()
                        for name, blob in self.before.items() if '/imgs/' in name or '/seed_imgs/' in name}
        self.assertEqual(seal['preserved_asset_sha256'], asset_hashes)
        self.assertEqual(seal['assets_sha256'], collector_module.digest(asset_hashes))
        for name, blob in expected.items():
            self.assertEqual((archive / name).read_bytes(), blob)
        self.assertEqual(fresh.catalog, {})
        self.assertEqual(fresh.queue['items'], [])
        self.assertEqual(collector_module.load(fresh.catalog_path), {})
        self.assertEqual(collector_module.load(fresh.queue_path)['items'], [])
        self.assertEqual(list(fresh.raw.iterdir()), [])
        self.assertEqual(fresh.manifest['searches'], [])
        self.assertEqual(fresh.manifest['details'], [])
        self.assertEqual(fresh.manifest['status'], 'pending')
        self.assertEqual(fresh.manifest['cutoff_at'], self.NEW_TIME)
        self.assertEqual(fresh.manifest['data_as_of'], '2026-10-26')
        self.assertEqual(fresh.manifest['search_succeeded'], 0)
        self.assertTrue(all(g['status'] == 'pending' for g in fresh.manifest['groups']))
        self.assertNotIn('search_finished_at', fresh.manifest)
        self.assertEqual(fresh.manifest['previous_run_archive'], 'xhs_state/runs/old-week/archive')
        for name, blob in self.before.items():
            if name not in ('xhs_state/search_manifest.json', 'xhs_state/candidate_queue.json'):
                self.assertEqual((self.root / name).read_bytes(), blob)

    def test_rollover_store_and_images_resume_without_seed_or_outputs(self):
        fresh = self.make('new-week', seed=str(self.root / 'outputs/nonexistent_seed.json'))
        fresh.init_store()
        self.assertEqual(fresh.store, self.old.store)
        self.assertEqual(fresh.store_path.read_bytes(), self.before['xhs_state/evidence_store.json'])
        self.assertFalse((self.root / 'outputs').exists())
        fresh.prepare_queue()
        self.assertNotIn('old-search-id', {item['id'] for item in fresh.queue['items']})
        self.assertEqual({item['id'] for item in fresh.queue['items']},
                         {n['id'] for n in fresh.store['notes'] if n['_collector']['origin'] != 'seed'})
        self.assertTrue(all(i['status'] == 'pending_review' for i in fresh.queue['items']))
        for name, blob in self.before.items():
            if '/imgs/' in name:
                self.assertEqual((self.root / name).read_bytes(), blob)

    def test_same_run_cross_day_all_stages_refused_without_mutation(self):
        for stage in ('init', 'search', 'details', 'verify'):
            with self.subTest(stage=stage):
                self.rejected_unchanged('old-week', stage, '同run跨日')

    def test_new_run_details_verify_and_init_refused_without_mutation(self):
        for stage in ('details', 'verify', 'init'):
            with self.subTest(stage=stage):
                self.rejected_unchanged('new-week', stage, '新run-id只能从search')

    def test_same_day_same_run_resume_preserves_original_cutoff_and_search(self):
        self.clock.return_value = '2026-10-19T15:00:00+08:00'
        for stage in ('search', 'details', 'verify'):
            with self.subTest(stage=stage):
                resumed = self.make('old-week', stage)
                self.assertEqual(resumed.cutoff, self.OLD_TIME)
                self.assertEqual(resumed.manifest, self.old.manifest)
                self.assertEqual(resumed.catalog, self.old.catalog)
                self.assertEqual(resumed.queue, self.old.queue)
        self.assertEqual(self.snapshot(), self.before)

    def test_config_changes_only_new_search_allowed(self):
        changed = copy.deepcopy(self.config)
        changed['runtime']['detail_limit'] = 12
        collector_module.validate(changed)
        self.clock.return_value = self.OLD_TIME
        for stage in ('init', 'search', 'details', 'verify'):
            with self.subTest(stage=stage):
                self.rejected_unchanged('old-week', stage, '词源配置改变', config=changed)
        for stage in ('details', 'verify', 'init'):
            with self.subTest(stage=stage):
                self.rejected_unchanged('new-week', stage, '新run-id只能从search', config=changed)
        self.clock.return_value = self.NEW_TIME
        fresh = self.make('new-week', config=changed)
        self.assertEqual(fresh.manifest['config_sha256'], collector_module.digest(changed))
        seal = collector_module.load(self.old.run / 'archive/archive_hashes.json')
        self.assertEqual(seal['file_sha256']['search_manifest.json'],
                         hashlib.sha256(self.before['xhs_state/search_manifest.json']).hexdigest())

    def test_existing_new_run_directory_refused_before_archive(self):
        conflict = self.old.runs / 'new-week'
        conflict.mkdir()
        (conflict / 'sentinel').write_bytes(b'never overwrite')
        self.rejected_unchanged('new-week', 'search', '目录已存在')
        self.assertFalse((self.old.run / 'archive').exists())

    def test_archive_duplicate_never_overwritten(self):
        fresh = self.make('new-week')
        archive_before = {p.relative_to(self.old.run / 'archive').as_posix(): p.read_bytes()
                          for p in (self.old.run / 'archive').rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'archive已存在'):
            fresh.archive_previous(self.old.manifest)
        self.assertEqual({p.relative_to(self.old.run / 'archive').as_posix(): p.read_bytes()
                          for p in (self.old.run / 'archive').rglob('*') if p.is_file()}, archive_before)
        # 模拟切换前遗留archive：必须停止，不能覆盖或自动删除来继续。
        self.old.manifest_path.write_bytes(self.before['xhs_state/search_manifest.json'])
        self.rejected_unchanged('other-week', 'search', 'archive已存在')

    def test_missing_required_old_input_blocks_switch(self):
        self.old.queue_path.unlink()
        self.rejected_unchanged('new-week', 'search', '必要文件缺失')
        self.assertFalse((self.old.run / 'archive').exists())
        self.assertFalse((self.old.runs / 'new-week').exists())

    def test_copy_failure_has_no_partial_archive_or_new_period(self):
        with patch.object(collector_module.shutil, 'copyfileobj', side_effect=OSError('合成复制中断')):
            with self.assertRaisesRegex(OSError, '合成复制中断'):
                self.make('new-week')
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse((self.old.run / 'archive').exists())
        self.assertFalse((self.old.runs / 'new-week').exists())
        self.assertFalse(list(self.old.runs.glob('.archive-*')))

    def test_old_response_cannot_be_counted_as_new_day_success(self):
        fresh = self.make('new-week')
        fresh.manifest['searches'] = copy.deepcopy(self.old.manifest['searches'])
        with self.assertRaisesRegex(ValueError, '当前run'):
            fresh.sync_report_manifest()
        copied = fresh.raw / self.old_response.name
        copied.write_bytes(self.old_response.read_bytes())
        fresh.manifest['searches'][0]['attempts'][0]['raw_private_path'] = copied.relative_to(self.root).as_posix()
        with self.assertRaisesRegex(ValueError, '跨日原检索响应'):
            fresh.sync_report_manifest()
        self.assertEqual(collector_module.load(fresh.manifest_path)['search_succeeded'], 0)
        self.assertEqual(self.old_response.read_bytes(), self.before[self.old_response.relative_to(self.root).as_posix()])

    def test_new_search_schedules_all_slots_without_old_success_reuse(self):
        fresh = self.make('new-week')
        fresh.init_store()
        scheduled = []
        with patch.object(fresh, 'schedule', side_effect=lambda tasks, fn: scheduled.extend(tasks)):
            fresh.search()
        self.assertEqual([t['key'] for t in scheduled],
                         ['f%02d' % i for i in range(1, 16)] + ['s01', 's02'])
        self.assertEqual(fresh.manifest['search_succeeded'], 0)
        self.assertEqual(list(fresh.raw.iterdir()), [])

    def test_cli_new_search_completes_with_mocked_tools_only(self):
        config_path = self.root / 'xhs_search_config.json'
        collector_module.atomic(config_path, self.config)
        worker = Mock(port=18076)
        worker.needs_recycle.return_value = False
        def schedule(instance, tasks, fn):
            for task in tasks:
                fn(worker, task)
        def request(instance, worker, tool, arguments, timeout, row):
            self.assertEqual(tool, 'search_feeds')
            row.update(http_status=200, response={'result': {'content': [
                {'type': 'text', 'text': json.dumps({'feeds': []})}]}})
            return {'feeds': []}
        with patch.object(collector_module.sys, 'argv', ['xhs_collect_v6.py', 'search',
                '--config', str(config_path), '--run-id', 'new-week']), \
                patch.object(collector_module.Collector, 'configure_pool', return_value=None), \
                patch.object(collector_module.Collector, 'schedule', new=schedule), \
                patch.object(collector_module.Collector, 'request', autospec=True, side_effect=request) as tools, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(collector_module.main(), 0)
        self.assertEqual(tools.call_count, 17)
        manifest = collector_module.load(self.old.manifest_path)
        self.assertEqual(manifest['run_id'], 'new-week')
        self.assertEqual(manifest['search_succeeded'], 17)
        self.assertTrue((self.old.run / 'archive/archive_hashes.json').is_file())
        self.assertEqual(collector_module.load(self.old.store_path)['notes'], self.old.store['notes'])

    def test_archived_cumulative_detail_schema_verifies_without_current_raw(self):
        fresh = self.make('new-week')
        fresh.init_store()
        fresh.adapt_report_store()
        for note in fresh.store['notes'][1:]:
            source = self.root / note['_collector']['detail_raw_private_path']
            self.assertIn('archive/run/raw', source.as_posix())
            self.assertFalse((fresh.raw / source.name).exists())
            self.assertEqual(note['_collector']['detail_raw_sha256'], collector_module.file_hash(source))
        self.assertEqual(fresh.manifest['details'], [])
        self.assertEqual(fresh.stats()['new_complete_added_this_run'], 0)
        # 覆盖检查单独已有真实测试；此处只隔离跨周累积详情的schema验验路径。
        with patch.object(report_v6, 'validate_manifest', return_value=(fresh.end.date(), {'complete': True})):
            audit = fresh.verify_report_schema()
        self.assertTrue(audit['passed'])
        self.assertEqual(audit['store_notes'], 3)
        self.assertEqual(fresh.store_path.read_bytes(), self.before['xhs_state/evidence_store.json'])

    def test_bound_old_detail_follows_archive_and_detects_tampering(self):
        fresh = self.make('new-week')
        fresh.init_store()
        note = fresh.store['notes'][1]
        original = self.old.raw / ('detail_' + note['id'] + '.json')
        note['_collector'].update(detail_raw_private_path=original.relative_to(self.root).as_posix(),
                                  detail_raw_sha256=collector_module.file_hash(original))
        source = fresh.detail_evidence_path(note)
        self.assertEqual(source, self.old.run / 'archive/run/raw' / original.name)
        source.write_bytes(source.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, '哈希不符'):
            fresh.detail_evidence_path(note)

    def test_multiple_rollovers_keep_prior_archives_and_bound_detail_sources(self):
        fresh = self.make('new-week')
        fresh.init_store()
        fresh.adapt_report_store()
        fresh.persist()
        first_archive = {p.relative_to(self.old.run / 'archive').as_posix(): p.read_bytes()
                         for p in (self.old.run / 'archive').rglob('*') if p.is_file()}
        self.clock.return_value = '2026-11-02T09:00:00+08:00'
        third = self.make('third-week')
        third.init_store()
        third.adapt_report_store()
        self.assertEqual(third.manifest['search_succeeded'], 0)
        self.assertEqual(third.manifest['details'], [])
        self.assertTrue((fresh.run / 'archive/archive_hashes.json').is_file())
        for note in third.store['notes'][1:]:
            self.assertIn('old-week/archive/run/raw', note['_collector']['detail_raw_private_path'])
        self.assertEqual({p.relative_to(self.old.run / 'archive').as_posix(): p.read_bytes()
                          for p in (self.old.run / 'archive').rglob('*') if p.is_file()}, first_archive)

    def test_missing_or_ambiguous_historical_detail_fails_closed(self):
        fresh = self.make('new-week')
        fresh.init_store()
        note = fresh.store['notes'][1]
        archive_source = self.old.run / 'archive/run/raw' / ('detail_' + note['id'] + '.json')
        collector_module.atomic(fresh.raw / archive_source.name, collector_module.load(archive_source))
        with self.assertRaisesRegex(ValueError, '来源不唯一'):
            fresh.detail_evidence_path(note)
        missing = copy.deepcopy(note)
        missing['id'] = 'nonexistent'
        with self.assertRaisesRegex(ValueError, '原响应缺失'):
            fresh.detail_evidence_path(missing)

    def test_archive_inventory_change_stops_switch_before_rename(self):
        original_copy = collector_module.shutil.copyfileobj
        def add_input(inp, out):
            original_copy(inp, out)
            (self.old.state / 'handoff_late.json').write_bytes(b'late handoff')
        with patch.object(collector_module.shutil, 'copyfileobj', side_effect=add_input):
            with self.assertRaisesRegex(ValueError, '并发改写'):
                self.make('new-week')
        self.assertFalse((self.old.run / 'archive').exists())
        self.assertFalse((self.old.runs / 'new-week').exists())
        self.assertEqual(self.old.manifest_path.read_bytes(), self.before['xhs_state/search_manifest.json'])
        self.assertFalse(list(self.old.runs.glob('.archive-*')))

    def test_empty_state_details_verify_need_search_manifest(self):
        separate = self.root / 'empty-root'
        separate.mkdir()
        with patch.object(collector_module, 'ROOT', separate):
            for stage in ('details', 'verify'):
                with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, '必须已有同run同日搜索manifest'):
                    self.make('new-week', stage)
        self.assertEqual(list(separate.iterdir()), [])


class WrapperTests(unittest.TestCase):
    def test_every_wrapper_exits_before_old_top_level(self):
        for entry in WRAPPERS + ('mcp_pool.py',):
            with self.subTest(entry=entry), patch.object(pipeline, 'legacy_main', return_value=9) as route, patch('subprocess.Popen', side_effect=AssertionError('禁止 MCP')), patch('subprocess.run', side_effect=AssertionError('禁止旧 subprocess')):
                with self.assertRaises(SystemExit) as exc:
                    runpy.run_path(str(ROOT / entry), run_name='__main__')
                self.assertEqual(exc.exception.code, 9)
                route.assert_called_once_with(entry)

    def test_generate_import_never_runs_old_top_level(self):
        with patch('subprocess.Popen', side_effect=AssertionError('禁止启动')), self.assertRaises(ImportError):
            runpy.run_path(str(ROOT / 'generate_report.py'), run_name='old_generate_import')

    def test_report_only_is_offline_build_no_default_push(self):
        with patch.object(pipeline, 'main', return_value=0) as main:
            self.assertEqual(pipeline.legacy_main('update_report.py', ['--report-only', '--no-push', '--output', 'outputs/new']), 0)
        main.assert_called_once_with(['build', '--output', 'outputs/new'])
        for argv in ([], ['--only', 'sentiment'], ['--search-only'], ['银行立减金'], ['publish']):
            with self.subTest(argv=argv), patch.object(pipeline, 'main') as main, redirect_stderr(io.StringIO()):
                self.assertEqual(pipeline.legacy_main('quick_update.py', argv), 2)
                main.assert_not_called()


if __name__ == '__main__':
    unittest.main()
