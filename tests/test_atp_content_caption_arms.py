"""Caption-declared multi-dose arms remain source-only until values bind."""
import unittest

from r031_evidence.atp_content_prose import expand_author_caption_dose_series


SHA='a'*64
QUOTE='Neurons were treated for 48 h with various concentrations (0.1 µM, 0.5 µM, 1 µM, 2 µM) of SPD.'


def parent(panel,cell):
    cite={'quote':QUOTE,'start':10,'end':10+len(QUOTE),'source_sha256':SHA,
          'source_anchor':'/article/fig[2]/caption[1]','offset_basis':'source_io.plain normalized Unicode characters'}
    return {'candidate_id':f'PARENT-{panel}','compound_name':'spermidine','compound_author_names':['spermidine','SPD'],
        'endpoint_kind':'ATP_CONTENT_CANDIDATE','numeric_origin':'NOT_EXTRACTED','figure_id':'F2','panel':panel,
        'dose_series_author_evidence':{'author_literal':QUOTE,'evidence':cite,'state':'MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED'},
        'panel_label_evidence':{'quote':f'relative ATP levels in ({panel.lower()}) {cell}','source_sha256':SHA},
        'fields':{'cell_model':{'value':cell},'total_compound_exposure_h':{'value':48.0},
            'dose_original':{'value':None},'dose_uM':{'value':None},'treated_arm':{'value':None},
            'comparator_arm':{'value':None},'normalization':{'value':None}},
        'treatment_stages':[{'stage':'AUTHOR_DRUG_EXPOSURE','duration_h':48.0,'evidence':[cite]}],
        'hold_reasons':['DOSE_BINDING_UNRESOLVED','MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED',
            'AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED','IMAGE_ARM_ASSIGNMENT_UNVERIFIED']}


class CaptionDoseArms(unittest.TestCase):
    def test_expands_doses_per_panel_without_assigning_values(self):
        candidates=[parent('A','young neurons'),parent('B','aged neurons')]
        caption={'source_anchor':'/article/fig[2]/caption[1]','text':QUOTE+
            ' Relative ATP levels in (a) young and (b) aged neurons. Values were normalized on the quantified CellTracker signal and are shown as the percentage of the control (untreated control condition).'}
        arms=expand_author_caption_dose_series(candidates,{'F2':caption},SHA)
        self.assertEqual(len(arms),8)
        expected={0.1,0.5,1.0,2.0}
        for panel,cell in [('A','young neurons'),('B','aged neurons')]:
            scoped=[a for a in arms if a['panel']==panel]
            self.assertEqual({a['fields']['dose_uM']['value'] for a in scoped},expected)
            self.assertEqual({a['fields']['cell_model']['value'] for a in scoped},{cell})
            for arm in scoped:
                self.assertIsNone(arm['treated_value']);self.assertIsNone(arm['control_value'])
                self.assertEqual(arm['numeric_origin'],'NOT_EXTRACTED')
                self.assertIn('AUTHOR_DOSE_ARM_CONTEXT_EXPANDED_RESULT_VALUE_UNASSIGNED',arm['hold_reasons'])
                self.assertEqual(arm['dose_series_author_evidence']['state'],'EXPLICIT_DOSE_ARM_CONTEXT_NO_RESULT_VALUE')
                self.assertEqual(arm['treatment_stages'][0]['dose_uM'],arm['fields']['dose_uM']['value'])
                self.assertEqual(arm['fields']['normalization']['value'],'CELLTRACKER_SIGNAL')
                self.assertEqual(arm['fields']['comparator_arm']['value'],'untreated control')
                self.assertIn('CellTracker signal',arm['fields']['normalization']['evidence'][0]['quote'])

    def test_wrong_source_or_ambiguous_identity_fails_closed(self):
        candidate=parent('A','young neurons')
        caption={'source_anchor':'/article/fig[2]/caption[1]','text':QUOTE}
        self.assertEqual(expand_author_caption_dose_series([candidate],{'F2':caption},'b'*64),[])
        candidate['compound_author_names']=['spermidine','SP']
        self.assertEqual(expand_author_caption_dose_series([candidate],{'F2':caption},SHA),[])


if __name__=='__main__':unittest.main()
