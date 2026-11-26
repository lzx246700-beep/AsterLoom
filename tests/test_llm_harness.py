"""Behavioral checks for offline calls, provenance, interrupted work, and budgets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from importlib.util import find_spec
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from dataclasses import replace
from unittest.mock import patch

from r031_evidence import llm_harness as h, r44_packets


def envelope(answer, provider='openai'):
    content = json.dumps(answer)
    if provider == 'anthropic':
        return json.dumps({'content': [{'type': 'text', 'text': content}],
                           'stop_reason': 'end_turn', 'usage': {'input_tokens': 7}}).encode()
    return json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                       'usage': {'prompt_tokens': 7, 'completion_tokens': 2}}).encode()


class Gateway(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'sources').mkdir()
        (self.root / 'packets').mkdir()
        self.source = self.root / 'sources/paper.xml'
        self.source.write_text('<article><body><sec><title>Results</title><p>'
                               'Treatment increased ATP content relative to control cells.'
                               '</p></sec></body></article>')
        packet = r44_packets.create(dict(split='DEVELOPMENT', annotation_state='UNLABELED',
            paper='SYNTHETIC_ONLY', source_path=str(self.source),
            source_sha256=h.sha(self.source.read_bytes()), title='Synthetic'))
        self.packet = self.root / 'packets/input.json'
        self.packet.write_text(json.dumps(packet))
        self.answer = {'value': None}
        def parse(value):
            if set(value) != {'value'}:
                raise h.SchemaFailure('shape')
            return value
        def verify(packet, value):
            if value['value'] == 'invented':
                raise ValueError('secret should never be logged')
            return {'candidates': [value]}
        self.profile = h.TaskProfile('OTHER_DOMAIN', '1', {'type': 'object'}, '1', 'untrusted data',
                                      'v1', parse, verify)

    def transport(self, *raw, provider='openai'):
        raw = raw or (envelope(self.answer, provider),)
        return h.ReplayTransport(provider, 'fixture-model', 'v1', raw, tuple(map(h.sha, raw)))

    def run_job(self, transport=None, **kw):
        args = dict(packet_path=self.packet, packet_sha256=h.sha(self.packet.read_bytes()),
                    source_path=self.source, profile=self.profile,
                    transport=transport or self.transport(), cache=self.root / 'cache')
        args.update(kw)
        return h.run(**args)

    def test_two_provider_envelopes_without_network_or_credentials(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            for provider in ('openai', 'anthropic'):
                result = self.run_job(self.transport(provider=provider))
                self.assertEqual(result['status'], 'SOURCE_CHECKED_REVIEW_ONLY')
                self.assertEqual(result['new_api_cost_microdollars'], 0)
                self.assertEqual(result['training_approved'], 0)

    def test_unchanged_rerun_uses_verified_cache(self):
        first = self.run_job()
        with patch.object(h.ReplayTransport, 'respond', side_effect=AssertionError('repeat dispatch')):
            second = self.run_job()
        self.assertTrue(second['cache_hit'])
        self.assertEqual(first['run_id'], second['run_id'])

    def test_format_retry_is_bounded_and_keeps_all_raw_replies(self):
        transport = self.transport(b'not json', envelope(self.answer))
        result = self.run_job(transport)
        self.assertEqual(result['status'], 'SOURCE_CHECKED_REVIEW_ONLY')
        self.assertEqual(len(result['attempts']), 2)
        job = self.root / 'cache' / result['run_id']
        self.assertEqual((job / 'RAW_1.json').read_bytes(), b'not json')
        self.assertEqual((job / 'RAW_2.json').read_bytes(), transport.responses[1])

    def test_exhausted_schema_budget_does_not_make_third_call(self):
        result = self.run_job(self.transport(b'bad', b'bad', envelope(self.answer)))
        self.assertEqual(result['status'], 'SCHEMA_HOLD')
        self.assertEqual(len(result['attempts']), 2)

    def test_semantic_hold_is_not_retried_or_promoted(self):
        result = self.run_job(self.transport(envelope({'value': 'invented'}), envelope(self.answer)))
        self.assertEqual(result['status'], 'EVIDENCE_HOLD')
        self.assertEqual(len(result['attempts']), 1)
        self.assertNotIn('secret', json.dumps(result))
        self.assertIsNotNone(result['answer'])

    def test_unknown_transport_effect_is_not_retried(self):
        with patch.object(h.ReplayTransport, 'respond', side_effect=TimeoutError('secret')) as respond:
            result = self.run_job()
        self.assertEqual(respond.call_count, 1)
        self.assertEqual(result['status'], 'CALL_UNKNOWN_HOLD')
        self.assertNotIn('secret', json.dumps(result))

    def test_source_drift_blocks_even_cached_result(self):
        self.run_job()
        self.source.write_text('<article/>')
        with self.assertRaisesRegex(ValueError, 'SHA_MISMATCH'):
            self.run_job()

    def test_corrupted_and_incomplete_cache_are_holds(self):
        result = self.run_job()
        job = self.root / 'cache' / result['run_id']
        (job / 'RAW_1.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'CACHE_INCOMPLETE_OR_DRIFT'):
            self.run_job()
        (job / 'MANIFEST.json').unlink()
        with self.assertRaisesRegex(ValueError, 'CACHE_INCOMPLETE_OR_DRIFT'):
            self.run_job()

    def test_profile_prompt_schema_and_validator_versions_separate_cache(self):
        first = self.run_job()['run_id']
        for kw in ({'prompt': 'new'}, {'schema': {'type': 'array'}}, {'validator_version': 'v2'},
                   {'profile_id': 'THIRD_DOMAIN'}):
            self.assertNotEqual(first, self.run_job(profile=replace(self.profile, **kw))['run_id'])

    def test_capacity_and_output_limits(self):
        self.run_job(limits=h.Limits(max_cache_entries=1))
        with self.assertRaisesRegex(ValueError, 'CAPACITY'):
            self.run_job(profile=replace(self.profile, prompt='new'), limits=h.Limits(max_cache_entries=1))
        with self.assertRaisesRegex(ValueError, 'RESPONSE_TOO_LARGE'):
            self.run_job(limits=h.Limits(max_response_bytes=1))

    def test_frozen_holdout_and_unpinned_response_rejected(self):
        packet = json.loads(self.packet.read_text())
        packet['selection_scope'] = 'FROZEN_HOLDOUT_NO_GOLD'
        packet['packet_content_sha256'] = h.digest({k:v for k,v in packet.items() if k != 'packet_content_sha256'})
        self.packet.write_text(json.dumps(packet))
        with self.assertRaisesRegex(ValueError, 'ONLY_DEV'):
            self.run_job()

    def test_raw_sha_model_and_billing_boundary(self):
        with self.assertRaisesRegex(ValueError, 'RAW_RESPONSE_SHA'):
            self.run_job(replace(self.transport(), expected_sha256=('0' * 64,)))
        with self.assertRaisesRegex(ValueError, 'NOT_AUTHORIZED'):
            self.run_job(transport=object())

    def test_cache_cannot_write_beside_author_inputs(self):
        with self.assertRaisesRegex(ValueError, 'OVERLAPS_INPUT'):
            self.run_job(cache=self.source.parent / 'cache')

    def test_truncated_provider_output_is_not_success(self):
        raw = json.loads(envelope(self.answer))
        raw['choices'][0]['finish_reason'] = 'length'
        result = self.run_job(self.transport(json.dumps(raw).encode()), limits=h.Limits(max_attempts=1))
        self.assertEqual(result['status'], 'SCHEMA_HOLD')

    def test_concurrent_same_job_never_dispatches_twice(self):
        entered, release = Event(), Event()
        raw = envelope(self.answer)
        def blocked(transport, request, attempt):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError()
            return raw
        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(h.ReplayTransport, 'respond', blocked):
                future = pool.submit(self.run_job)
                self.assertTrue(entered.wait(timeout=5))
                try:
                    with self.assertRaisesRegex(ValueError, 'INCOMPLETE_OR_DRIFT'):
                        self.run_job()
                finally:
                    release.set()
                self.assertEqual(future.result()['status'], 'SOURCE_CHECKED_REVIEW_ONLY')
        self.assertTrue(self.run_job()['cache_hit'])

    def test_abandoned_admission_lock_requires_reconciliation(self):
        (self.root / 'cache/.admission').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'ADMISSION_BUSY'):
            self.run_job()


class ATPContract(unittest.TestCase):
    @unittest.skipUnless(find_spec('pydantic'), 'optional H0 contract dependency')
    def test_same_r44_shape_and_strict_types(self):
        from r031_evidence.llm_harness_profiles import atp_content_profile
        profile = atp_content_profile()
        answer = dict(paper='fixture', relations=[], reason_if_none='insufficient')
        self.assertEqual(profile.parse(answer), answer)
        for bad in (dict(answer, gold=True), dict(answer, paper=123)):
            with self.assertRaises(h.SchemaFailure):
                profile.parse(bad)
