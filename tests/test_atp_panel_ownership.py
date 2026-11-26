"""Synthetic panel-placement counterexamples; no human benchmark labels."""
import unittest
from r031_evidence.atp_content_relations import _panels,extract_relations,verify_relations
from r031_evidence.atp_content_common import explicit_protein_normalization
from r031_evidence.atp_content_cross_context import linked_prose
import test_atp_content_relations as fixtures


class PanelOwnership(unittest.TestCase):
    def test_postfix_atp_label_belongs_to_preceding_description(self):
        cap={'text':'Drug experiment. TEM observed mitochondria (A) Scale bar = 1 μm ATP content (B) and mitochondrial complex IV (C) activities were measured.'}
        got=_panels(cap)
        atp=[(p,s) for p,s,m in got if 'ATP content' in s]
        self.assertEqual(atp,[('B','Scale bar = 1 μm ATP content')])

    def test_grouped_postfix_labels_and_nonsequential_descriptions_keep_author_owner(self):
        cap={'text':'CCK8 detects viability (A,B), microscopy (C). ATP content (E), membrane potential (D), ROS (F).'}
        atp=[(p,s) for p,s,m in _panels(cap) if 'ATP content' in s]
        self.assertEqual(atp[0][0],'E')
        self.assertNotIn('membrane potential',atp[0][1])

    def test_prefix_captions_keep_next_clause_scope(self):
        cap={'text':'Effects. (A) Intracellular ATP content. (B) ROS after drug (100 μM) for 24 h.'}
        parts=_panels(cap);self.assertEqual(parts[0][0],'A');self.assertNotIn('100 μM',parts[0][1])

    def test_mixed_conflicting_prefix_and_postfix_ownership_is_unresolved(self):
        got=_panels({'text':'(A) ATP content (B) was measured. (C) ROS staining.'})
        self.assertEqual(got[0][2]['panel_scope_state'],'PANEL_LABEL_PLACEMENT_AMBIGUOUS')

    def test_legacy_continuation_caption_preserves_shared_protocol_without_new_panel_borrowing(self):
        fx=fixtures.Relations();fx.setUp();self.addCleanup(fx.doCleanups)
        data=fx.run_source();c,=data['candidates'];self.assertEqual(c['panel'],'B')
        self.assertEqual(c['fields']['dose_uM']['value'],100)
        self.assertEqual(c['fields']['pretreatment_h']['value'],3)
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])

    def test_relative_clause_atp_normalization_is_not_parallel_protein_measurement(self):
        text='The standard curve was used to compute the ATP content in the samples, which was then normalized to the protein concentration.'
        self.assertEqual(explicit_protein_normalization(text),text)
        for negative in ['ATP and glucose were measured. Protein was determined separately.',
            'ATP was measured. ROS, which was then normalized to the protein concentration.',
            'The glucose content in the samples, which was then normalized to the protein concentration.',
            'ATP and ROS content in samples, which was then normalized to protein concentration.']:
            self.assertIsNone(explicit_protein_normalization(negative))

    def test_background_drug_does_not_own_later_explicit_other_cotreatment(self):
        text='To investigate the role of choline in rescue by baicalin, we co-treated choline with Cd and measured ATP content (Figure 8D).'
        blocks=[{'text':text,'role':'RESULTS'}]
        self.assertFalse(linked_prose(blocks,'Figure 8','Fig8','D',['baicalin'],strict_roles=True))
        self.assertTrue(linked_prose(blocks,'Figure 8','Fig8','D',['baicalin'],strict_roles=False))
        blocks[0]['text']=text.replace('we co-treated choline','we co-treated baicalin')
        self.assertTrue(linked_prose(blocks,'Figure 8','Fig8','D',['baicalin'],strict_roles=True))

    def test_repeated_atp_caption_text_uses_own_panel_span_and_label(self):
        fx=fixtures.Relations();fx.setUp();self.addCleanup(fx.doCleanups)
        text=fixtures.BODY
        start=text.index('<caption>');end=text.index('</caption>',start)+len('</caption>')
        text=text[:start]+'<caption><p>TYP experiment. Intracellular ATP content (A) Intracellular ATP content (B)</p></caption>'+text[end:]
        data=fx.run_source(text);by_panel={c['panel']:c for c in data['candidates']}
        self.assertEqual(set(by_panel),{'A','B'})
        a,b=by_panel['A'],by_panel['B'];self.assertGreater(b['scope_evidence']['start'],a['scope_evidence']['start'])
        self.assertEqual(b['panel_label_evidence']['quote'],'(B)')
        self.assertGreater(b['panel_label_evidence']['start'],b['scope_evidence']['end'])
        self.assertTrue(verify_relations(data,fx.p)['source_replay_pass'])


if __name__=='__main__':unittest.main()
