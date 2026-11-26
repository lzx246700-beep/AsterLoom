import os
os.environ['PYDANTIC_AI_NO_BANNER'] = '1'
import unittest
from importlib.util import find_spec

from r031_evidence.llm_harness_oss import pydantic_ai_replay, langextract_grounding, field_retrieval


@unittest.skipUnless(find_spec('pydantic_ai') and find_spec('langextract'), 'optional H0 OSS experiment dependencies')
class OptionalFrameworks(unittest.TestCase):
    def packet(self):
        return dict(source_sha256='a'*64, chunks=[
            dict(chunk_id='c1', locator='/article/p[1]', text='The ATP content increased in treated cells.'),
            dict(chunk_id='c2', locator='/article/p[2]', text='The control vehicle was given for 24 hours.')])

    def test_pydantic_ai_actual_function_model_rejects_extra_and_bad_type(self):
        for bad in (dict(paper=123, relations=[], reason_if_none='insufficient'),
                    dict(paper='fixture', relations=[], reason_if_none='insufficient', training_approved=True)):
            result = pydantic_ai_replay(bad)
            self.assertEqual(result['status'], 'TYPED_REPLAY_HOLD')
            self.assertEqual(result['local_calls'], 1)
            self.assertEqual(result['new_api_calls'], 0)

    def test_two_pass_langextract_aligns_true_quote_and_rejects_wrong_chunk(self):
        answer = dict(relations=[dict(evidence=[
            dict(chunk_id='c1', field='endpoint', quote='ATP content increased'),
            dict(chunk_id='c1', field='comparator', quote='control vehicle')])])
        result = langextract_grounding(self.packet(), answer)
        self.assertEqual(result['local_calls'], 2)
        self.assertEqual(result['projected_unique_quotes'], 2)
        self.assertEqual(result['returned_spans'], 2)
        self.assertEqual(result['exact_in_original_chunk'], 1)
        self.assertIsNone(result['recall'])

    def test_per_field_plan_retains_source_identity_and_does_not_mutate_packet(self):
        packet = self.packet()
        plan = field_retrieval(packet, dict(comparator='control vehicle', endpoint='ATP content'), top_k=1)
        self.assertEqual(plan['fields']['comparator']['chunks'], ['c2'])
        self.assertEqual(plan['fields']['endpoint']['chunks'], ['c1'])
        self.assertEqual(len(packet['chunks']), 2)
        self.assertEqual(plan['source_sha256'], 'a'*64)

    def test_component_call_budgets(self):
        with self.assertRaisesRegex(ValueError, 'PASS_BUDGET'):
            langextract_grounding(self.packet(), dict(relations=[]), passes=100)
        with self.assertRaisesRegex(ValueError, 'TOP_K'):
            field_retrieval(self.packet(), {}, top_k=0)
