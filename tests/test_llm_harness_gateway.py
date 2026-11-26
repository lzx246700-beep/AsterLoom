from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.util import find_spec
import io
import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from r031_evidence.llm_harness import sha
from r031_evidence import r44_deepseek as ds
from r031_evidence.llm_harness_gateway import ModelRequest,Authorization,ModelGateway


def raw(answer=None,**kw):
    return json.dumps(dict(model='deepseek-flash',system_fingerprint='synthetic-fingerprint',
        choices=[dict(message=dict(content=json.dumps(answer if answer is not None else {'ok':True})),finish_reason='stop')],
        usage=dict(prompt_tokens=100,completion_tokens=50,total_tokens=150),**kw)).encode()


class GatewaySafety(unittest.TestCase):
    def setUp(self):
        # Synthetic HTTP/ledger fixtures replay an audited price date. Production
        # date revalidation remains active; no real ledger or credential used.
        class PriceDate(datetime.date):
            @classmethod
            def today(cls):return cls(2026,11,12)
        from r031_evidence import deepseek_budget_reconcile as reconcile
        for module in (ds,reconcile):
            price_clock=patch.object(module,'dt',SimpleNamespace(date=PriceDate))
            price_clock.start();self.addCleanup(price_clock.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'inputs').mkdir();(self.root/'ledger').mkdir()
        self.source=self.root/'inputs/source.xml';self.source.write_text('<article>synthetic source fixture</article>')
        self.req=ModelRequest('deepseek','deepseek-flash','SYNTHETIC',str(self.source),sha(self.source.read_bytes()),
                              'untrusted scientific data','read source',{},dict(profile_id='FIXTURE'),'fixture','CC BY')
        self.auth=self.grant([self.req])

    def grant(self, requests, **kw):
        fields=dict(grant_id='synthetic-test-grant',provider=requests[0].provider,model=requests[0].model,
                    source_sha256s=tuple({r.source_sha256 for r in requests}),request_sha256s=tuple(r.request_sha256() for r in requests),
                    cap_microdollars=1_000_000,max_calls=10,expires_at=int(time.time())+600,
                    approval_reference='SYNTHETIC TEST ONLY, NOT OWNER AUTHORIZATION',approved=True,allow_source_disclosure=True,
                    ledger_path=str((self.root/'ledger/deepseek_experiment_budget.sqlite3').resolve()))
        fields.update(kw);return Authorization(**fields)

    def gateway(self,sender=None,cache='cache'):
        return ModelGateway(self.root/cache,simulation_sender=sender or (lambda req,body:raw()),simulation_ledger=self.root/'ledger')

    def ledger_rows(self):
        with sqlite3.connect(self.root/'ledger/deepseek_experiment_budget.sqlite3') as db:
            return db.execute('SELECT id,microdollars FROM reservations').fetchall()

    def test_default_off_and_unapproved_expired_or_wrong_input_never_reach_keychain(self):
        g=ModelGateway(self.root/'live')
        with patch.object(ds,'_raw_http') as network:
            with self.assertRaisesRegex(ValueError,'EXPLICIT_APPROVAL'):g.call(self.req)
            with self.assertRaisesRegex(ValueError,'DEFAULT_OFF'):g.call(self.req,authorization=self.auth)
            for auth in (replace(self.auth,approved=False),replace(self.auth,expires_at=0),replace(self.auth,request_sha256s=('0'*64,))):
                with self.assertRaises(ValueError):g.call(self.req,authorization=auth,allow_network=True)
            network.assert_not_called()
        self.assertFalse((self.root/'ledger/deepseek_experiment_budget.sqlite3').exists())

    def test_deepseek_and_second_provider_mock_payloads_and_synthetic_usage(self):
        for provider,model in (('deepseek','deepseek-flash'),('openai','fixture-openai-model')):
            req=replace(self.req,provider=provider,model=model)
            auth=self.grant([req],grant_id='synthetic-'+provider)
            result=self.gateway().call(req,authorization=auth)
            self.assertEqual(result['status'],'STRUCTURED_RESPONSE_REVIEW_ONLY')
            self.assertEqual(result['billing_mode'],'SIMULATION')
            self.assertEqual(result['actual_cost_microdollars'],0)
            self.assertEqual(result['new_api_calls'],0)
            self.assertIn('response_format',json.loads(req.body()))

    def test_received_response_cached_across_different_cache_directories_no_reserve_again(self):
        g=self.gateway();first=g.call(self.req,authorization=self.auth);rows=self.ledger_rows()
        second=self.gateway(lambda *a: self.fail('repeat HTTP'),cache='other-cache').call(self.req,authorization=self.auth)
        self.assertTrue(second['cache_hit']);self.assertEqual(first['answer'],second['answer'])
        self.assertEqual(rows,self.ledger_rows())

    def test_schema_errors_keep_raw_and_do_not_retry(self):
        count=[]
        def sender(*a):count.append(1);return b'not-json'
        result=self.gateway(sender).call(self.req,authorization=self.auth)
        self.assertEqual(result['status'],'SCHEMA_HOLD_RAW_RETAINED')
        self.assertEqual(len(count),1)
        self.assertEqual(next((self.root/'cache').glob('*/RAW_HTTP_RESPONSE.json')).read_bytes(),b'not-json')

    def test_legacy_twelve_relation_cap_is_explicit_hold_not_complete_discovery(self):
        req=replace(self.req,strategy='A_R44')
        result=self.gateway(lambda *a:raw(dict(relations=[{} for _ in range(12)]))).call(req,authorization=self.grant([req]))
        self.assertEqual(result['status'],'LEGACY_RELATION_CAP_SATURATION_HOLD')
        self.assertEqual(len(result['answer']['relations']),12)

    @unittest.skipUnless(find_spec('pydantic_ai'),'optional typed dependency')
    def test_pydantic_ai_schema_rejects_extra_without_corrective_paid_call(self):
        from r031_evidence.llm_harness_profiles import ATPResponse
        req=replace(self.req,output_schema=ATPResponse.model_json_schema())
        auth=self.grant([req]);calls=[]
        def sender(*a):calls.append(1);return raw(dict(paper='SYNTHETIC',relations=[],reason_if_none='none',Gold=1))
        result=self.gateway(sender).call(req,authorization=auth,output_type=ATPResponse)
        self.assertEqual(result['status'],'SCHEMA_HOLD_RAW_RETAINED')
        self.assertEqual(len(calls),1);self.assertEqual(result['answer']['Gold'],1)
        self.assertEqual(result['independent_gold'],0)

    def test_timeout_and_429_and_5xx_different_holds_never_retry(self):
        for label,exc in (('NETWORK_UNKNOWN_HOLD',TimeoutError('secret')),
                          ('RATE_LIMIT_HOLD',urllib.error.HTTPError(ds.URL,429,'secret',{},io.BytesIO(b'{"error":"rate limit"}'))),
                          ('HTTP_5XX_HOLD',urllib.error.HTTPError(ds.URL,503,'secret',{},io.BytesIO(b'{"error":"unavailable"}')))):
            req=replace(self.req,prompt=label);auth=self.grant([req],grant_id='test-'+label)
            calls=[]
            def sender(*a):calls.append(1);raise exc
            g=self.gateway(sender)
            result=g.call(req,authorization=auth)
            self.assertEqual(result['status'],label);self.assertNotIn('secret',repr(result))
            again=g.call(req,authorization=auth)
            self.assertEqual(again['status'],label);self.assertEqual(len(calls),1)

    def test_grant_cap_and_request_count_atomic_before_dispatch(self):
        req2=replace(self.req,prompt='another request');calls=[]
        auth=self.grant([self.req,req2],max_calls=1)
        g=self.gateway(lambda *a: calls.append(1) or raw())
        g.call(self.req,authorization=auth)
        result=g.call(req2,authorization=auth)
        self.assertIn('AUTHORIZED_BUDGET_EXHAUSTED',result['status']);self.assertEqual(len(calls),1)

    def test_insufficient_authorized_money_and_shared_global_money(self):
        calls=[];g=self.gateway(lambda *a:calls.append(1) or raw())
        result=g.call(self.req,authorization=replace(self.auth,cap_microdollars=1))
        self.assertIn('AUTHORIZED_BUDGET_EXHAUSTED',result['status']);self.assertEqual(calls,[])
        with patch.object(ds,'BUDGET_MICRODOLLARS',100001):
            result=self.gateway(cache='global-exhausted').call(self.req,authorization=replace(self.auth,cap_microdollars=100001))
        self.assertIn('EXHAUSTED',result['status']);self.assertEqual(calls,[])

    def test_concurrent_calls_different_cache_dirs_dispatch_once(self):
        from threading import Event
        entered,release=Event(),Event();calls=[]
        def sender(*a):calls.append(1);entered.set();release.wait(timeout=5);return raw()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.gateway(sender).call,self.req,authorization=self.auth)
            self.assertTrue(entered.wait(timeout=5))
            try:
                b=pool.submit(self.gateway(sender,cache='cache2').call,self.req,authorization=self.auth).result(timeout=5)
                self.assertIn(b['status'],('RESERVED_UNKNOWN','DEEPSEEK_CALL_ALREADY_CLAIMED_HOLD'))
            finally:release.set()
            self.assertEqual(a.result()['status'],'STRUCTURED_RESPONSE_REVIEW_ONLY')
        self.assertEqual(len(calls),1);self.assertEqual(len(self.ledger_rows()),2)

    def test_source_or_receipt_drift_and_price_day_block(self):
        g=self.gateway();g.call(self.req,authorization=self.auth)
        raw_path=next((self.root/'cache').glob('*/RAW_HTTP_RESPONSE.json'));raw_path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'RECEIPT_SHA_DRIFT'):g.call(self.req,authorization=self.auth)
        self.source.write_text('drift')
        with self.assertRaisesRegex(ValueError,'SOURCE_SHA_DRIFT'):g.call(self.req,authorization=self.auth)

    def test_reuse_grant_id_with_different_cap_cannot_spend(self):
        req2=replace(self.req,prompt='new payload');auth=self.grant([self.req,req2]);g=self.gateway()
        g.call(self.req,authorization=auth)
        result=g.call(req2,authorization=replace(auth,cap_microdollars=900000))
        self.assertIn('ID_REUSE_HOLD',result['status'])

    def test_authorization_binds_original_shared_ledger_not_a_fresh_budget(self):
        with self.assertRaisesRegex(ValueError,'LEDGER_IDENTITY'):
            self.gateway().call(self.req,authorization=replace(self.auth,ledger_path='/some/other/budget.sqlite3'))
        self.assertFalse((self.root/'ledger/deepseek_experiment_budget.sqlite3').exists())

    def test_budget_snapshot_readonly_no_credit_or_fee_mutation(self):
        self.gateway().call(self.req,authorization=self.auth)
        p=self.root/'ledger/deepseek_experiment_budget.sqlite3';before=sha(p.read_bytes())
        snap=ds.budget_snapshot(str(self.root/'ledger'))
        self.assertEqual(before,sha(p.read_bytes()))
        self.assertGreater(snap['remaining_microdollars'],0)

    def test_real_bridge_uses_original_keychain_and_fixed_origin_with_mock_http(self):
        # Initialize only a synthetic temporary shared ledger. No real credentials or network.
        ds.reserve(str(self.root/'ledger'),1,1)
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        calls=[]
        class Opener:
            def open(self,request,timeout):calls.append(request.full_url);return Response(raw())
        with (patch.dict(os.environ,ASTERLOOM_DEEPSEEK_BUDGET_DIR=str(self.root/'ledger')),
             patch.object(ds.subprocess,'run',return_value=SimpleNamespace(stdout='SYNTHETIC_TEST_SECRET')),
             patch.object(ds.urllib.request,'build_opener',return_value=Opener())):
            result=ModelGateway(self.root/'real-bridge').call(self.req,authorization=self.auth,allow_network=True)
        self.assertEqual(calls,[ds.URL]);self.assertEqual(result['status'],'STRUCTURED_RESPONSE_REVIEW_ONLY')
        self.assertNotIn('SYNTHETIC_TEST_SECRET',repr(result))

    def test_second_provider_live_is_explicitly_hold_not_mispriced(self):
        req=replace(self.req,provider='openai',model='fixture-openai')
        with self.assertRaisesRegex(ValueError,'PRICING_NOT_AUDITED'):
            ModelGateway(self.root/'no-network').call(req,authorization=self.grant([req]),allow_network=True)

    @unittest.skipUnless(find_spec('langextract') and find_spec('pydantic_ai'),'optional H0 dependencies')
    def test_full_author_source_to_gateway_to_langextract_to_review_candidate(self):
        from r031_evidence.llm_harness_gateway import langextract_gateway_factory
        from r031_evidence.llm_harness_relations import full_source_packet,run_discovery
        from r031_evidence.llm_harness_profiles import atp_content_profile
        from tests.test_r44_ai_science_review import TEXT,answer
        self.source.write_text('<article><body><p>'+TEXT+'</p></body></article>')
        entry=dict(paper='SYNTHETIC',split='PRIOR_EXPOSED_DEVELOPMENT_NOT_BLIND',
                   source=dict(path=str(self.source),sha256=sha(self.source.read_bytes())),license_url='CC BY')
        packet=full_source_packet(entry);profile=atp_content_profile();requests=[]
        factory=langextract_gateway_factory(self.gateway(),profile,self.source,'SYNTHETIC',request_collector=requests)
        run_discovery(packet,profile,factory,self.root/'dry-run')
        self.assertEqual(len(requests),2)
        self.assertFalse((self.root/'ledger/deepseek_experiment_budget.sqlite3').exists())
        rel=answer()['relations'][0]
        for ev in rel['evidence']:ev['chunk_id']=packet['chunks'][0]['chunk_id']
        frame=dict(extractions=[dict(experiment=TEXT,experiment_attributes=dict(relation_json=json.dumps(rel)))])
        calls=[]
        def sender(req,body):
            calls.append(req.request_sha256())
            self.assertIn('Berberine',body.decode());return raw(frame)
        gateway=self.gateway(sender)
        auth=self.grant(requests)
        factory=langextract_gateway_factory(gateway,profile,self.source,'SYNTHETIC',authorization=auth)
        result=run_discovery(packet,profile,factory,self.root/'pipeline')
        self.assertEqual(result['batch_status'],'COMPLETE')
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(result['candidates'][0]['state'],'SOURCE_CHECKED_REVIEW_B_CANDIDATE')
        self.assertEqual(len(result['candidates'][0]['seen_in']),2)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(list((self.root/'cache').glob('*/RAW_HTTP_RESPONSE.json'))),2)

    @unittest.skipUnless(find_spec('langextract') and find_spec('pydantic_ai'),'optional H0 dependencies')
    def test_pipeline_stops_after_unknown_effect_and_retains_partial_progress(self):
        from r031_evidence.llm_harness_relations import run_discovery
        from r031_evidence.llm_harness_profiles import atp_content_profile
        from langextract.core import base_model
        from tests.test_r44_ai_science_review import packet
        class Unknown(base_model.BaseLanguageModel):
            def infer(self,prompts,**kw):raise ds.DispatchHold('NETWORK_UNKNOWN_HOLD')
        result=run_discovery(packet(),atp_content_profile(),lambda *a:Unknown(),self.root/'unknown-pipeline')
        self.assertEqual(result['batch_status'],'CALL_HOLD_PARTIAL')
        self.assertEqual(result['completed_units'],1)
        self.assertEqual(result['not_attempted_units'],1)
