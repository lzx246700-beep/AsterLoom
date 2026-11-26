"""Synthetic OCR geometry and three-table semantics; these are not human truth."""
import json
import math
import sqlite3
import unittest
from unittest.mock import patch
from r031_evidence.atp_content_printed import aligned_printed_values, apply_printed, read_printed
from r031_evidence.atp_content_export import export_candidates,sha
import test_atp_content_export as fixtures


class Printed(unittest.TestCase):
    def token(self,text,x,y=.9,w=.05,h=.02):return {'text':text,'bbox':[x,y,w,h]}

    def test_unique_aligned_number_only_not_dose_or_other_panel(self):
        t=[self.token('hesperetin',.02,w=.08),self.token('133.0',.26),
           self.token('160-',.47),self.token('117.2',.26,.7),self.token('10uM hesperetin',.4)]
        h,=aligned_printed_values(t,['hesperetin'])
        self.assertEqual(h['printed_value']['value'],133)
        self.assertFalse(aligned_printed_values(t+[self.token('140',.32)],['hesperetin']))

    def test_printed_percent_is_not_invented_raw_control_or_training_effect(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        parent=fx.data['candidates'][0]
        value={'parent_candidate_id':parent['candidate_id'],'printed_percentage':133,
               'numeric_origin':'AUTHOR_FIGURE_PRINTED_NUMERIC_OCR_CANDIDATE',
               'percentage_semantics':'MEAN_ATP_PERCENT_OF_AUTHOR_CONTROL','unit':'% of author primary-screen control'}
        data={'candidates':[value],'image_sha256':'synthetic','figure_id':parent['figure_id']}
        packet=fx.root/'printed.json';packet.write_text(json.dumps(data))
        transformed=apply_printed(parent,data,packet)
        self.assertIsNone(transformed['control_value'])
        with patch('r031_evidence.atp_content_printed.read_printed',return_value=data):
            export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'printed-export',printed_packet_paths=[packet])
        db=sqlite3.connect(fx.root/'printed-export/ATP_ENGINE_REVIEW_CANDIDATES.sqlite3')
        row=db.execute('select treated_value,control_value,author_reported_percent,descriptive_log2fc,training_effect_log2fc,train_eligible,independent_gold,training_approved from observations').fetchone()
        self.assertEqual(row[:3],(133,None,133));self.assertAlmostEqual(row[3],math.log2(1.33))
        self.assertEqual(row[4:],(None,0,0,0));db.close()

    def test_changed_printed_candidate_fails_replay(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        p=fx.root/'bad-printed.json'
        data={'schema':'asterloom.atp-content.printed-percent/0.1','figure_packet_path':'x','figure_packet_sha256':'x',
              'relations_packet_path':'y','relations_packet_sha256':'y','compound':'x','candidates':[{'printed_percentage':999}]}
        p.write_text(json.dumps(data));expected={**data,'candidates':[{'printed_percentage':133}]}
        with patch('r031_evidence.atp_content_printed.extract_printed',return_value=expected):
            with self.assertRaisesRegex(ValueError,'ATP_PRINTED_REPLAY_MISMATCH'):read_printed(p)
