"""独立内存fixtures；unittest无需pytest，不硬编码本期笔记数量。

python -m unittest discover -s tests -p test_report_v6.py -v
python tests/test_report_v6.py --integration-output 尚不存在的测试目录
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import report_v6 as build
import verify_report_v6 as verify

ZERO = {key: 0 for key in build.DEFAULT_MINIMUMS}


def fixture(root, as_of='2026-10-23'):
    """全部数据明确为合成测试数据，不作真实笔记或生产coverage。"""
    root = Path(root)
    (root / 'imgs').mkdir(exist_ok=True)
    Image.new('RGB', (24, 80), '#c9344d').save(root / 'imgs/fixture_0.png')
    Image.new('RGB', (40, 120), '#315a86').save(root / 'imgs/fixture_1.png')
    end = date.fromisoformat(as_of)
    notes, reviews = [], {}
    insights, overview = {}, []
    for index, section in enumerate(build.SECTIONS):
        nid = 'fixture_' + section
        imgs = ['imgs/fixture_0.png', 'imgs/fixture_1.png'] if index != 1 else []
        pub = f'{(end - timedelta(days=index)).isoformat()} 12:30:00'
        ev = {'t': '合成测试标题 ' + section, 'a': '合成测试作者',
              'url': 'https://www.xiaohongshu.com/explore/' + nid + '?xsec_token=fixture&xsec_source=pc_search',
              'desc': '</script><img onerror="alert(1)">\n合成测试正文，不是真实帖子。费用5元仅用于校验。',
              'imgs': imgs, 'tags': ['合成测试'], 'pub': pub, 'cmts': [],
              'likes': 0 if section == 'sentiment' else 10, 'collects': 0, 'comments': 0, 'shares': 0}
        metrics = {key: None if section == 'sentiment' else ev[key] for key in build.METRICS}
        metrics.update(captured_at=as_of + 'T13:00:00+08:00', source='search')
        notes.append({'id': nid, 'evidence': ev, 'display_metrics': metrics, 'image_count': len(imgs)})
        reviews[nid] = review_for(notes[-1], section, root)
        insight = {'title': '合成测试观察', 'body': '此项仅验证引用，不代表真实用户或规则。', 'action': '仅用于离线测试。',
                   'scope': '合成测试样本', 'refs': [nid], 'claims': []}
        insights[section] = [insight]
        overview.append({'tab': section, 'title': '合成测试重点', 'body': '仅用于离线测试。', 'refs': [nid], 'claims': []})
    manifest = {'schema_version': 1, 'run_id': 'fixture-' + as_of, 'as_of': as_of, 'data_kind': 'test_fixture',
                'required_groups': ['fixture-' + s for s in build.SECTIONS],
                'groups': [{'key': 'fixture-' + s, 'section': s, 'required': True, 'status': 'success',
                            'run_id': 'fixture-' + as_of, 'as_of': as_of} for s in build.SECTIONS]}
    editorial = {'schema_version': 1, 'as_of': as_of, 'run_id': manifest['run_id'], 'status': 'approved',
                 'reviewed_by': '合成测试审核员（非真实内容审核）', 'reviewed_at': as_of + 'T00:00:00+08:00',
                 'insights': insights, 'overview': overview, 'publication': {'status': 'draft'}}
    return {'notes': notes}, {'reviews': reviews}, editorial, manifest


def review_for(row, section, root):
    return {'primary_section': section, 'object': '合成测试对象', 'topics': ['合成测试主题'],
            'attitude': '疑问求助' if section == 'sentiment' else '中性分享', 'analyst_note': '合成测试，用户主张未核验。',
            'verification': '合成测试图文；未核验', 'event_key': row['id'], 'event_label': '', 'event_status': '',
            'evidence_sha256': build.digest(row['evidence']),
            'image_sha256': {p: hashlib.sha256((Path(root) / p).read_bytes()).hexdigest() for p in row['evidence']['imgs']}}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store, self.reviews, self.editorial, self.manifest = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def report(self):
        return build.build_report(self.store, self.reviews, self.editorial, self.manifest, self.root, ZERO)

    def approve(self, row, section='marketing'):
        self.reviews['reviews'][row['id']] = review_for(row, section, self.root)

    def extra(self, nid, pub, section='marketing'):
        row = copy.deepcopy(self.store['notes'][0])
        row['id'] = nid
        row['evidence']['url'] = 'https://www.xiaohongshu.com/explore/' + nid
        row['evidence']['pub'] = pub
        self.store['notes'].append(row)
        self.approve(row, section)
        return row

    def test_fixture_counts_fulltext_null_and_internal_immutability(self):
        original = copy.deepcopy(self.store)
        inputs_before = build.digest([self.store, self.reviews, self.editorial, self.manifest])
        raw_images = {p: (self.root / p).read_bytes() for row in self.store['notes'] for p in row['evidence']['imgs']}
        report, quality, internal = self.report()
        self.assertEqual(self.store, original)
        self.assertEqual(build.digest([self.store, self.reviews, self.editorial, self.manifest]), inputs_before)
        self.assertEqual(internal['all_image_sha256'], {p: hashlib.sha256(raw).hexdigest() for p, raw in raw_images.items()})
        self.assertEqual(report['provenance']['evidence_sha256'], build.digest(original['notes']))
        for n, row in zip(report['notes'], original['notes']):
            for key in ('t', 'a', 'desc', 'tags', 'pub', 'cmts') + build.METRICS:
                self.assertEqual(n['evidence'][key], row['evidence'][key])
            self.assertEqual([quality['image_sources'][p] for p in n['evidence']['imgs']], row['evidence']['imgs'])
        output = self.root / 'immutable_release'
        build.write_bundle(report, quality, self.root, output)
        self.assertEqual({p: (self.root / p).read_bytes() for p in raw_images}, raw_images)
        self.assertEqual({p.name for p in (output / 'imgs').iterdir()}, {Path(p).name for p in quality['images']})
        self.assertEqual(report['provenance']['image_sha256'], build.digest(quality['images']))
        self.assertEqual(report['source_count'], len(self.store['notes']))
        self.assertEqual(quality['metrics_null_fields'], len(build.METRICS))
        self.assertFalse(quality['production_data_passed'])
        self.assertEqual(internal['notes_total'], len(self.store['notes']))
        self.assertTrue(all(not n['evidence']['url'].split('?')[1:] for n in report['notes']))

    def test_unknown_missing_review_future_keep_internal(self):
        self.extra('unknown', None)
        self.extra('future', '2026-10-24 00:00')
        self.extra('unreviewed', '2026-10-22 01:00')
        del self.reviews['reviews']['unreviewed']
        report, _, internal = self.report()
        self.assertEqual(report['source_count'], len(self.store['notes']) - 3)
        reasons = {x['id']: x['reasons'] for x in internal['items']}
        self.assertEqual(reasons['unknown'], ['unknown_pub'])
        self.assertEqual(reasons['future'], ['future_pub'])
        self.assertEqual(reasons['unreviewed'], ['missing_review'])
        self.assertEqual(len(self.store['notes']), internal['notes_total'])

    def test_cst_seven_sixty_day_boundaries(self):
        end = date.fromisoformat(self.manifest['as_of'])
        for days in (0, 6, 7, 59, 60):
            self.extra('boundary_' + str(days), f'{end - timedelta(days=days)} 00:00:00')
        self.extra('utc_last_day', '2026-10-22T16:00:00Z')
        self.extra('utc_future', '2026-10-23T16:00:00Z')
        report, _, _ = self.report()
        byid = {n['id']: n for n in report['notes']}
        self.assertTrue(byid['boundary_6']['recent'])
        self.assertFalse(byid['boundary_7']['recent'])
        self.assertIn('boundary_59', byid)
        self.assertNotIn('boundary_60', byid)
        self.assertEqual(byid['utc_last_day']['date'], self.manifest['as_of'])
        self.assertNotIn('utc_future', byid)

    def test_malformed_or_search_only_pub_rejected(self):
        for pub in ('2026-10-22', '昨天', '2026-02-30 12:00'):
            with self.subTest(pub=pub):
                row = self.store['notes'][0]
                row['evidence']['pub'] = pub
                self.approve(row)
                with self.assertRaises(build.ValidationError):
                    self.report()

    def test_asof_runid_and_editorial_approval(self):
        for key, value in [('as_of', '2026-10-22'), ('run_id', 'old'), ('status', 'needs_review'), ('reviewed_by', None), ('reviewed_at', '2026-10-21T14:00:00+08:00')]:
            original = self.editorial[key]
            self.editorial[key] = value
            with self.subTest(key=key), self.assertRaises(build.ValidationError):
                self.report()
            self.editorial[key] = original

    def test_cross_section_duplicate_and_missing_refs_fail(self):
        item = self.editorial['insights']['product'][0]
        original = item['refs']
        for refs in (['fixture_marketing'], ['missing'], [], ['fixture_product', 'fixture_product']):
            item['refs'] = refs
            with self.subTest(refs=refs), self.assertRaises(build.ValidationError):
                self.report()
        item['refs'] = original
        self.editorial['insights']['product'].append(copy.deepcopy(item))
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_overview_once_per_section_and_max_insights(self):
        self.editorial['overview'].append(copy.deepcopy(self.editorial['overview'][0]))
        with self.assertRaises(build.ValidationError):
            self.report()
        self.editorial['overview'].pop()
        self.editorial['insights']['marketing'] *= 4
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_missing_metrics_in_evidence_and_snapshot_fail_closed(self):
        row = self.store['notes'][0]
        for kind in ('evidence', 'display_metrics'):
            for key in build.METRICS:
                value = row[kind].pop(key)
                self.approve(row)
                with self.subTest(kind=kind, key=key), self.assertRaises(build.ValidationError):
                    self.report()
                row[kind][key] = value
                self.approve(row)
        row['display_metrics'].pop('captured_at')
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_metrics_types_and_null_not_zero(self):
        row = self.store['notes'][0]
        for value in (True, -1, 1.2, '100', float('nan')):
            row['display_metrics']['likes'] = value
            with self.subTest(value=value), self.assertRaises(build.ValidationError):
                self.report()
        row['display_metrics']['likes'] = None
        report, _, _ = self.report()
        self.assertIsNone(report['notes'][0]['display_metrics']['likes'])
        self.assertEqual(report['notes'][0]['evidence']['likes'], 10)

    def test_snapshot_complete_no_old_field_blending(self):
        row = self.store['notes'][0]
        row['evidence']['likes'] = 999
        row['display_metrics'] = {k: None for k in build.METRICS}
        row['display_metrics'].update(captured_at='2026-10-23T15:00:00+08:00', source='search')
        self.approve(row)
        report, _, _ = self.report()
        self.assertTrue(all(report['notes'][0]['display_metrics'][k] is None for k in build.METRICS))
        self.assertEqual(report['notes'][0]['evidence']['likes'], 999)

    def test_low_like_question_is_not_filtered(self):
        row = self.store['notes'][2]
        row['display_metrics']['likes'] = 0
        report, _, _ = self.report()
        self.assertIn(row['id'], {n['id'] for n in report['notes']})
        self.assertEqual(report['counts']['direct'], 1)

    def test_no_images_and_missing_fulltext_image_fail(self):
        report, _, _ = self.report()
        n = next(n for n in report['notes'] if n['primary_section'] == 'product')
        self.assertEqual(n['evidence']['imgs'], [])
        row = self.store['notes'][1]
        row['evidence']['desc'] = ''
        self.approve(row, 'product')
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_missing_corrupt_unsafe_and_modified_images(self):
        row = self.store['notes'][0]
        for path in ('imgs/missing.png', '../outside.png', 'https://example.invalid/img.png'):
            row['evidence']['imgs'] = [path]
            row['image_count'] = 1
            with self.subTest(path=path), self.assertRaises(build.ValidationError):
                self.report()
        row['evidence']['imgs'] = ['imgs/fixture_0.png']
        (self.root / 'imgs/fixture_0.png').write_bytes(b'not image')
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_review_binds_images_and_full_evidence(self):
        row = self.store['notes'][0]
        row['evidence']['desc'] += ' changed'
        with self.assertRaises(build.ValidationError):
            self.report()
        self.approve(row)
        Image.new('RGB', (24, 80), 'white').save(self.root / 'imgs/fixture_0.png')
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_wrong_image_count_fail(self):
        self.store['notes'][0]['image_count'] += 1
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_url_allowlist_strip_token_preserve_original(self):
        row = self.store['notes'][0]
        original = row['evidence']['url']
        report, _, _ = self.report()
        self.assertNotIn('xsec_token', build.canonical(report).decode())
        self.assertEqual(row['evidence']['url'], original)
        for url in ('http://www.xiaohongshu.com/explore/fixture_marketing',
                    'https://www.xiaohongshu.com.evil.test/explore/fixture_marketing',
                    'https://name:pass@www.xiaohongshu.com/explore/fixture_marketing',
                    'https://www.xiaohongshu.com:443/explore/fixture_marketing',
                    'https://www.xiaohongshu.com/explore/other',
                    'https://www.xiaohongshu.com/explore/%2e%2e'):
            row['evidence']['url'] = url
            self.approve(row)
            with self.subTest(url=url), self.assertRaises(build.ValidationError):
                self.report()

    def test_coverage_failed_missing_stale_and_inconsistent_required(self):
        group = self.manifest['groups'][0]
        for key, value in [('status', 'timeout'), ('as_of', '2026-10-22'), ('run_id', 'previous'), ('required', False)]:
            old = group[key]
            group[key] = value
            with self.subTest(key=key), self.assertRaises(build.ValidationError):
                self.report()
            group[key] = old
        self.manifest['groups'].pop()
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_unknown_group_status_cannot_be_success(self):
        for status in ('ok', 'partial', 'failed', '', None):
            self.manifest['groups'][0]['status'] = status
            with self.subTest(status=status), self.assertRaises(build.ValidationError):
                self.report()

    def test_optional_failure_not_silently_full_coverage(self):
        self.manifest['groups'].append({'key': 'optional', 'section': 'marketing', 'required': False,
                                       'status': 'timeout', 'run_id': self.manifest['run_id'], 'as_of': self.manifest['as_of']})
        _, quality, _ = self.report()
        self.assertEqual(quality['coverage']['optional_failed'], 1)

    def test_nextweek_old_editorial_rejected_no_residual_refs(self):
        self.manifest['as_of'] = '2026-12-31'
        self.manifest['run_id'] = 'fixture-next'
        for group in self.manifest['groups']:
            group.update(as_of=self.manifest['as_of'], run_id=self.manifest['run_id'])
        with self.assertRaises(build.ValidationError):
            self.report()
        self.editorial.update(as_of=self.manifest['as_of'], run_id=self.manifest['run_id'], reviewed_at='2026-10-23T14:00:00+08:00')
        with self.assertRaises(build.ValidationError):
            self.report()
        # 下周截至日仍可读本月证据，但近期标识应动态清空，不复制旧recent。
        self.manifest['as_of'] = '2026-10-30'
        self.editorial.update(as_of='2026-10-30', reviewed_at='2026-10-30T00:00:00+08:00')
        for group in self.manifest['groups']:
            group['as_of'] = '2026-10-30'
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_rollweek_dynamic_groups_without_fixed_refs(self):
        old = (date.fromisoformat(self.manifest['as_of']) - timedelta(days=8)).isoformat()
        row = self.extra('historical', old + ' 14:00')
        row['evidence']['pub'] = '2026-09-30 23:59:00'
        self.approve(row)
        report, _, _ = self.report()
        self.assertFalse(next(n for n in report['notes'] if n['id'] == row['id'])['recent'])
        history = [g for g in report['reading_groups']['marketing'] if g['key'] == 'history'][0]
        self.assertTrue(history['open'])
        self.editorial['insights'] = {s: [] for s in build.SECTIONS}
        self.editorial['overview'] = []
        report, _, _ = self.report()
        self.assertTrue(all(not report['insights'][s] for s in build.SECTIONS))
        self.assertEqual(report['overview'], [])

    def test_next_week_rebuild_clear_old_status_and_stale_refs(self):
        self.store, self.reviews, self.editorial, self.manifest = fixture(self.root, '2026-10-16')
        self.reviews['reviews']['fixture_marketing']['event_status'] = '近期发现 · 用户自述'
        self.manifest.update(as_of='2026-10-23', run_id='fixture-2026-10-23')
        for g in self.manifest['groups']:
            g.update(as_of=self.manifest['as_of'], run_id=self.manifest['run_id'])
        with self.assertRaises(build.ValidationError):
            self.report()
        self.editorial.update(as_of=self.manifest['as_of'], run_id=self.manifest['run_id'], reviewed_at='2026-10-23T00:00:00+08:00')
        report, _, _ = self.report()
        self.assertFalse(any(n['recent'] for n in report['notes']))
        self.assertFalse(any(n['event_status'] for n in report['notes']))
        self.editorial['insights']['marketing'][0]['scope'] = '近期变化线索'
        with self.assertRaises(build.ValidationError):
            self.report()
        self.editorial['insights'] = {s: [] for s in build.SECTIONS}
        self.editorial['overview'] = []
        for row in self.store['notes']:
            row['evidence']['pub'] = '2026-08-01 12:00:00'
            self.approve(row, row['id'].removeprefix('fixture_'))
        report, _, _ = self.report()
        self.assertEqual(report['source_count'], 0)
        self.assertTrue(all(not groups for groups in report['reading_groups'].values()))

    def test_theme_cross_time_merge_one_event(self):
        row = self.extra('same_theme', '2026-09-30 01:00')
        self.reviews['reviews'][row['id']]['event_key'] = 'fixture_marketing'
        report, _, _ = self.report()
        groups = report['reading_groups']['marketing']
        events = [e for group in groups for e in group['events']]
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0]['ids']), {'fixture_marketing', 'same_theme'})

    def test_content_digits_metric_and_evidence_claims(self):
        item = self.editorial['insights']['marketing'][0]
        item['body'] = '合成测试点赞10，原文自述费用5元，未核验。'
        with self.assertRaises(build.ValidationError):
            self.report()
        item['claims'] = [{'field': 'body', 'token': '10', 'kind': 'metric', 'id': 'fixture_marketing', 'metric': 'likes', 'value': 10},
                          {'field': 'body', 'token': '5', 'kind': 'evidence', 'id': 'fixture_marketing', 'quote': '费用5元', 'value': 5}]
        self.report()
        self.store['notes'][0]['display_metrics']['likes'] = 11
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_image_numbers_only_after_human_quote(self):
        item = self.editorial['insights']['marketing'][0]
        item['body'] = '合成截图写5元，仅为测试。'
        item['claims'] = [{'field': 'body', 'token': '5', 'kind': 'image_quote', 'id': 'fixture_marketing',
                           'image': 'imgs/fixture_0.png', 'quote': '合成截图5元', 'value': 5}]
        with self.assertRaises(build.ValidationError):
            self.report()
        self.reviews['reviews']['fixture_marketing']['image_quotes'] = [{'image': 'imgs/fixture_0.png', 'quote': '合成截图5元'}]
        original = build.digest([self.reviews, self.editorial])
        report, quality, _ = self.report()
        public = report['notes'][0]['evidence']['imgs'][0]
        self.assertNotEqual(public, item['claims'][0]['image'])
        self.assertEqual(quality['image_sources'][public], item['claims'][0]['image'])
        self.assertEqual(build.digest([self.reviews, self.editorial]), original)
        self.assertNotIn('_image_quotes', report['notes'][0])
        item['claims'][0]['image'] = public
        with self.assertRaisesRegex(build.ValidationError, '图中数字'):
            self.report()

    def test_no_auto_confirmation_of_complaint_rules(self):
        item = self.editorial['insights']['marketing'][0]
        for text in ('已确认投诉', '官方规则保证', '人人可领', '保证收益'):
            item['body'] = text
            with self.subTest(text=text), self.assertRaises(build.ValidationError):
                self.report()
        item['body'] = '用户主张未核验，不代表已确认投诉。'
        self.report()

    def test_production_minimum_not_seed_count_or_fixture_flag(self):
        with self.assertRaises(build.ValidationError):
            build.build_report(self.store, self.reviews, self.editorial, self.manifest, self.root)
        self.manifest['data_kind'] = 'production'
        self.editorial['publication']['status'] = 'ready'
        with self.assertRaises(build.ValidationError):
            build.build_report(self.store, self.reviews, self.editorial, self.manifest, self.root)

    def test_duplicate_json_keys_rejected(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"notes": [], "notes": []}', encoding='utf-8')
        with self.assertRaises(build.ValidationError):
            build.read_json(path)

    def test_publication_not_fake_deployment(self):
        for status in ('ready', 'published', 'unknown'):
            self.editorial['publication']['status'] = status
            with self.subTest(status=status), self.assertRaises(build.ValidationError):
                self.report()
        self.manifest['data_kind'] = 'production'
        self.editorial['publication']['status'] = 'published'
        with self.assertRaises(build.ValidationError):
            self.report()

    def test_atomic_bundle_and_independent_json_verifier(self):
        report, quality, _ = self.report()
        output = self.root / 'release'
        build.write_bundle(report, quality, self.root, output)
        got, _ = verify.validate_bundle(output)
        self.assertEqual(got, report)
        with self.assertRaises(build.ValidationError):
            build.write_bundle(report, quality, self.root, output)
        self.assertNotIn('internal_audit', (output / 'public_report_v6.json').read_text(encoding='utf-8'))
        q = json.loads((output / 'report_quality_v6.json').read_text(encoding='utf-8'))
        del q['metrics_keys']
        (output / 'report_quality_v6.json').write_bytes(build.canonical(q))
        with self.assertRaises(ValueError):
            verify.validate_bundle(output)

    def test_publish_image_names_are_deterministic_and_byte_versioned(self):
        first, q1, _ = self.report()
        _, same, _ = self.report()
        self.assertEqual(q1['images'], same['images'])
        self.assertEqual(q1['image_sources'], same['image_sources'])
        for public, source in q1['image_sources'].items():
            path = Path(source)
            self.assertEqual(public, f'imgs/{path.stem}_v6_{q1["images"][public][:12]}{path.suffix}')
        release1 = self.root / 'release1'
        build.write_bundle(first, q1, self.root, release1)
        original = 'imgs/fixture_0.png'
        Image.new('RGB', (24, 80), 'white').save(self.root / original)
        for row in self.store['notes']:
            self.approve(row, row['id'].removeprefix('fixture_'))
        second, q2, _ = self.report()
        release2 = self.root / 'release2'
        build.write_bundle(second, q2, self.root, release2)
        old = next(p for p, s in q1['image_sources'].items() if s == original)
        new = next(p for p, s in q2['image_sources'].items() if s == original)
        self.assertNotEqual(old, new)
        self.assertNotEqual(q1['images'][old], q2['images'][new])
        self.assertEqual(hashlib.sha256((release1 / old).read_bytes()).hexdigest(), q1['images'][old])
        self.assertEqual(hashlib.sha256((release2 / new).read_bytes()).hexdigest(), q2['images'][new])
        verify.validate_bundle(release1)
        verify.validate_bundle(release2)

    def test_mapping_missing_incomplete_extra_unsafe_or_misnamed_fails_closed(self):
        report, quality, _ = self.report()
        output = self.root / 'mapping_release'
        quality = build.write_bundle(report, quality, self.root, output)
        public = next(iter(quality['images']))
        missing = dict(quality['image_sources'])
        missing.pop(public)
        wrong_name = dict(quality['image_sources'], **{public: 'imgs/other.png'})
        unsafe = dict(quality['image_sources'], **{public: '../outside.png'})
        extra = dict(quality['image_sources'], **{'imgs/extra.png': 'imgs/fixture_0.png'})
        for mapping in (None, [], {}, missing, wrong_name, unsafe, extra):
            broken = dict(quality, image_sources=mapping)
            with self.subTest(mapping=mapping):
                with self.assertRaises(build.ValidationError):
                    build.write_bundle(report, broken, self.root, self.root / 'invalid_release')
                (output / 'report_quality_v6.json').write_bytes(build.canonical(broken))
                with self.assertRaises(ValueError):
                    verify.validate_bundle(output)
                self.assertFalse((self.root / 'invalid_release').exists())
        del quality['image_sources']
        (output / 'report_quality_v6.json').write_bytes(build.canonical(quality))
        with self.assertRaisesRegex(ValueError, 'mandatory'):
            verify.validate_bundle(output)

    def test_internal_only_images_not_in_public_mapping_and_empty_mapping_required(self):
        self.editorial.update(insights={s: [] for s in build.SECTIONS}, overview=[])
        for review in self.reviews['reviews'].values():
            review['primary_section'] = 'internal'
        report, quality, internal = self.report()
        self.assertEqual(quality['images'], {})
        self.assertEqual(quality['image_sources'], {})
        self.assertTrue(internal['all_image_sha256'])
        output = self.root / 'empty_release'
        quality = build.write_bundle(report, quality, self.root, output)
        verify.validate_bundle(output)
        del quality['image_sources']
        (output / 'report_quality_v6.json').write_bytes(build.canonical(quality))
        with self.assertRaisesRegex(ValueError, 'mandatory'):
            verify.validate_bundle(output)

    def test_source_verifier_preserves_original_paths_order_and_rejects_forged_source(self):
        report, quality, _ = self.report()
        originals = [self.store, self.reviews, self.editorial, self.manifest]
        paths = {}
        for key, obj in zip(('store', 'reviews', 'editorial', 'manifest'), originals):
            path = self.root / (key + '.json')
            path.write_bytes(build.canonical(obj))
            paths[key] = path
        input_hashes = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}
        quality['input_file_sha256'] = input_hashes
        output = self.root / 'source_release'
        build.write_bundle(report, quality, self.root, output)
        self.assertEqual(verify.validate_bundle(output, paths)[0], report)
        public = next(iter(quality['images']))
        source = quality['image_sources'][public]
        alternative = 'imgs/alternative.png'
        (self.root / alternative).write_bytes((self.root / source).read_bytes())
        sha = quality['images'].pop(public)
        replacement = build.published_image_path(alternative, sha)
        quality['images'][replacement] = sha
        del quality['image_sources'][public]
        quality['image_sources'][replacement] = alternative
        for n in report['notes']:
            n['evidence']['imgs'] = [replacement if p == public else p for p in n['evidence']['imgs']]
        report['provenance']['image_sha256'] = build.digest(quality['images'])
        quality['report_sha256'] = build.digest(report)
        forged = self.root / 'forged_release'
        build.write_bundle(report, quality, self.root, forged)
        verify.validate_bundle(forged)
        with self.assertRaisesRegex(ValueError, '图源映射失配'):
            verify.validate_bundle(forged, paths)
        self.assertEqual({k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()}, input_hashes)

    def test_post_validation_image_change_no_output(self):
        report, quality, _ = self.report()
        Image.new('RGB', (24, 80), 'white').save(self.root / 'imgs/fixture_0.png')
        output = self.root / 'release'
        with self.assertRaises(build.ValidationError):
            build.write_bundle(report, quality, self.root, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.release-*')))

    def test_concurrent_input_change_before_atomic_publish(self):
        report, quality, _ = self.report()
        source = self.root / 'source.json'
        source.write_bytes(b'initial')
        initial_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        source.write_bytes(b'changed')
        output = self.root / 'release'
        with self.assertRaises(build.ValidationError):
            build.write_bundle(report, quality, self.root, output, {str(source): initial_sha})
        self.assertFalse(output.exists())

    def test_template_js_syntax_and_css_frozen(self):
        text = (ROOT / 'report_v6_template.html').read_text(encoding='utf-8')
        js = re.findall(r'<script>(.*?)</script>', text, re.S)[0]
        process = subprocess.run(['node', '--check', '-'], input=js, text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('#evidence .grid{grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;', text)
        self.assertEqual(hashlib.sha256(re.search(r'<style>(.*?)</style>', text, re.S)[1].encode()).hexdigest(), '20ba5c66f12b4d9043f3f886e7ef047b0d908f52fa92d98bc6f047529c28cae7')
        self.assertNotIn('V6.1', text)
        self.assertEqual(text.count('__REPORT_DATA__'), 1)

    def test_script_escape_and_data_digest(self):
        report, quality, _ = self.report()
        html = build.render_html(report)
        self.assertNotIn('</script><img', html)
        self.assertIn('\\u003c/script>', html)
        self.assertEqual(quality['report_sha256'], build.digest(report))


def integration(output):
    output = Path(output).resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError('集成测试目录必须未存在，父目录需已存在')
    output.mkdir()
    sources = output / 'fixtures'
    sources.mkdir()
    store, reviews, editorial, manifest = fixture(sources)
    for key, obj in zip(('store', 'reviews', 'editorial', 'manifest'), (store, reviews, editorial, manifest)):
        (sources / (key + '.json')).write_bytes(build.canonical(obj))
    command = [sys.executable, str(ROOT / 'report_v6.py'), 'build']
    for key in ('store', 'reviews', 'editorial', 'manifest'):
        command += ['--' + key, str(sources / (key + '.json'))]
    command += ['--asset-root', str(sources), '--output', str(output / 'fixture_release'), '--test-fixture']
    process = subprocess.run(command, capture_output=True, text=True)
    print(process.stdout)
    if process.returncode:
        raise ValueError(process.stderr)
    command = [sys.executable, str(ROOT / 'verify_report_v6.py'), '--report-dir', str(output / 'fixture_release'),
               '--output', str(output / 'fixture_verification.json'), '--screenshots', str(output)]
    for key in ('store', 'reviews', 'editorial', 'manifest'):
        command += ['--' + key, str(sources / (key + '.json'))]
    process = subprocess.run(command, capture_output=True, text=True)
    print(process.stdout)
    if process.returncode:
        raise ValueError(process.stderr or process.stdout)


if __name__ == '__main__':
    if '--integration-output' in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument('--integration-output', required=True)
        integration(parser.parse_args().integration_output)
    else:
        unittest.main()
