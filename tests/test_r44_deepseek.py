import datetime
import os
import tempfile
import unittest
from unittest.mock import patch
from r031_evidence import r44_deepseek as ds, r44_models as models
REAL_DATE=datetime.date

class DeepSeekSafety(unittest.TestCase):
    def test_provider_is_opt_in(self):
        p=models.Provider('deepseek','deepseek-flash')
        with self.assertRaisesRegex(RuntimeError,'DEFAULT_OFF'):
            models.call(p,{})
    def test_unknown_price_day_fails_closed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ds.dt,'date') as date:
            date.today.return_value=REAL_DATE(2030,1,1)
            date.side_effect=REAL_DATE
            with self.assertRaisesRegex(RuntimeError,'REVALIDATION'):
                ds.reserve(d,100,10)
    def test_revalidated_2026_11_11_is_bounded(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ds.dt,'date') as date:
            date.today.return_value=REAL_DATE(2026,11,11)
            date.side_effect=REAL_DATE
            self.assertGreater(ds.reserve(d,100,10),0)
    def test_exhaustion_and_reservations(self):
        with tempfile.TemporaryDirectory() as d, patch.object(ds.dt,'date') as date:
            date.today.return_value=REAL_DATE(2026,11,10)
            date.side_effect=REAL_DATE
            with patch.object(ds,'BUDGET_MICRODOLLARS',110000):
                ds.reserve(d,10,10)
                with self.assertRaisesRegex(RuntimeError,'EXHAUSTED'):
                    ds.reserve(d,1000,100)
    def test_model_rejected_before_keychain_access(self):
        with patch.object(ds.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'PRICING_NOT_AUDITED'):
                ds.call('a','b','other')
            run.assert_not_called()
    def test_keychain_failure_retains_reservation_and_redacts(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'ASTERLOOM_DEEPSEEK_BUDGET_DIR':d}), patch.object(ds.dt,'date') as date:
            date.today.return_value=REAL_DATE(2026,11,10)
            date.side_effect=REAL_DATE
            with patch.object(ds.subprocess,'run',side_effect=Exception('secret must never appear')):
                with self.assertRaisesRegex(RuntimeError,'RESERVATION_RETAINED') as err:
                    ds.call('a','b','deepseek-flash',response_journal=os.path.join(d,'response.json'))
                self.assertNotIn('secret',str(err.exception))
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(os.path.join(d,'deepseek_experiment_budget.sqlite3'))) as db:
                self.assertEqual(db.execute('select count(*) from reservations').fetchone()[0],2)

class ReasonerProtocol(unittest.TestCase):
    @unittest.skip("depends on a test helper excluded because it hardcodes local paths")
    def test_invalid_model_output_is_retained_on_hold(self):
        from pathlib import Path
        from r031_evidence import r44_reasoner, r44_packets
        from tests.test_r44_ai_science_review import packet, answer
        import json
        with tempfile.TemporaryDirectory() as d:
            data=packet()
            data['packet_content_sha256']=r44_packets.sha(r44_packets.canonical(data).encode())
            p=Path(d)/'packet.json'; p.write_text(json.dumps(data))
            invalid=answer();invalid['relations'][0]['evidence'][0]['quote']='not in source'
            output={'answer':invalid,'provider':'deepseek','model':'deepseek-flash',
                    'usage':{'prompt_tokens':1},'budget_reserved_microdollars':123}
            with patch.object(r44_reasoner.r44_models,'call',return_value=output):
                result=r44_reasoner.reason(p,r44_packets.sha(p.read_bytes()))
            self.assertEqual(result['status'],'MODEL_OUTPUT_HOLD')
            self.assertEqual(result['validation_error'],'R44_HALLUCINATED_SOURCE_QUOTE_REJECTED')
            self.assertEqual(result['usage']['prompt_tokens'],1)
            self.assertEqual(result['model_output'],invalid)
            self.assertEqual(result['independent_gold'],0)

    @unittest.skip("depends on a test helper excluded because it hardcodes local paths")
    def test_reasoner_uses_source_validator_and_does_not_approve_gold(self):
        from pathlib import Path
        from r031_evidence import r44_reasoner, r44_packets
        from tests.test_r44_ai_science_review import packet, answer
        import json
        with tempfile.TemporaryDirectory() as d:
            data=packet()
            data['packet_content_sha256']=r44_packets.sha(r44_packets.canonical(data).encode())
            p=Path(d)/'packet.json'; p.write_text(json.dumps(data))
            output={'answer':answer(),'provider':'deepseek','model':'deepseek-flash',
                    'usage':{},'budget_reserved_microdollars':123}
            with patch.object(r44_reasoner.r44_models,'call',return_value=output):
                result=r44_reasoner.reason(p,r44_packets.sha(p.read_bytes()))
            self.assertEqual(result['independent_gold'],0)
            self.assertFalse(result['checked_candidates']['scientific_accuracy_verified'])
            self.assertEqual(result['checked_candidates']['relations'][0]['state'],'FIELD_SOURCE_VERIFIED_REVIEW_ONLY')

class MockProviderCall(unittest.TestCase):
    def test_response_uses_fixed_origin_and_sanitizes_usage(self):
        import io, json
        from types import SimpleNamespace
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        class Opener:
            def open(self, request, timeout):
                self.request=request
                return Response(json.dumps({'choices':[{'message':{'content':'{\"relations\":[]}'}}], 'usage':{'prompt_tokens':4,'completion_tokens':2,'other':'omit'}}).encode())
        opener=Opener()
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'ASTERLOOM_DEEPSEEK_BUDGET_DIR':d}), patch.object(ds.dt,'date') as date:
            date.today.return_value=REAL_DATE(2026,11,10)
            date.side_effect=REAL_DATE
            with patch.object(ds.subprocess,'run',return_value=SimpleNamespace(stdout='temporary-test-credential')), patch.object(ds.urllib.request,'build_opener',return_value=opener):
                result=ds.call('example','rules','deepseek-flash',response_journal=os.path.join(d,'response.json'))
            self.assertEqual(opener.request.full_url,ds.URL)
            self.assertEqual(result['usage'],{'prompt_tokens':4,'completion_tokens':2})
            self.assertNotIn('temporary-test-credential',repr(result))

if __name__=='__main__': unittest.main()
