"""Synthetic percent-control ATP geometry, never scientific Gold."""
import unittest
import json
from unittest.mock import patch
import test_atp_content_export as fixtures
from test_atp_content_plot import image,dot
from r031_evidence.atp_content_percent_plot import percent_geometry,bind_percent_points,apply_percent,read_percent,ORIGIN,direct_atp_panel_axes
from r031_evidence.atp_content_export import export_candidates


def fixture(unit='uM',wrong_endpoint=False):
    im=image(640,480);tokens=[]
    def rect(x,y,w,h,color):
        for a in range(x,x+w):
            for b in range(y,y+h):dot(im,a,b,color)
    def token(text,x,y,w,h):tokens.append({'text':text,'bbox':[x/640,y/480,w/640,h/480]})
    rect(70,100,530,1,(0,0,0));rect(70,100,1,301,(0,0,0))
    token('In % of control mean',0,180,20,170)
    for v in [0,50,100,150,200]:token(str(v)+' -',35,100+v*1.5-6,30,12)
    rect(490,430,30,14,(0,0,255));token('Glucose' if wrong_endpoint else 'ATP',525,430,70,15)
    for x,value in [(100,100),(260,110),(420,120)]:
        rect(x,101,16,round(value*1.5)-1,(0,0,255));rect(x+16,101,16,269,(255,0,0))
    for text,right in [('Control',138),('10 uM quercetin',298),(f'25 {unit} quercetin',458)]:
        token(text,right-90,35,90,62)
    return im,tokens


class PercentPlot(unittest.TestCase):
    def test_split_axis_labels_keep_direct_atp_panels_separate_from_mmp(self):
        def token(text,x,y,w,h):return {'text':text,'bbox':[x/790,y/728,w/790,h/728]}
        tokens=[token('ATP Level/ Cells',38,486,16,94),token('(% Control)',52,498,16,70),
                token('ATP Level/ Cells',484,486,18,94),token('(% Control)',498,498,16,70),
                token('MMP/ Cells',38,110,16,66),token('(% Control)',52,110,16,70),
                token('MMP/ Cells',484,110,18,66),token('(% Control)',498,110,16,70)]
        result=direct_atp_panel_axes(tokens,790,728)
        self.assertEqual(result['recognized_panel_count'],2)
        self.assertEqual([p['axis_token_id'] for p in result['direct_atp_panels']],[0,2])
        self.assertEqual(result['verified_atp_panel_count'],0)
        self.assertEqual(result['status'],'OCR_AXIS_LABELS_REQUIRE_AUTHOR_CAPTION_PANEL_RECONCILIATION')
        self.assertEqual(result['coordinate_basis'],'normalized bottom-left (Vision); image-space top row has larger y')
        self.assertTrue(all(p['endpoint_label_state']=='OCR_ONLY_REQUIRES_AUTHOR_PANEL_RECONCILIATION' for p in result['direct_atp_panels']))
        self.assertEqual(len(result['non_atp_axis_exclusions']),2)
        self.assertEqual(result['numeric_candidates'],0)

    def test_blue_atp_not_red_glucose_and_measured_control_not_forced_100(self):
        im,t=fixture();r=percent_geometry(im,t)
        self.assertEqual(len(r['points']),3)
        self.assertAlmostEqual(r['points'][0]['estimated_value'],100,delta=1)
        self.assertAlmostEqual(r['points'][1]['estimated_value'],110,delta=1)
        self.assertAlmostEqual(r['points'][2]['estimated_value'],120,delta=1)
        self.assertFalse(any(abs(p['estimated_value']-180)<2 for p in r['points']))
        self.assertFalse(r['control_value_forced_to_100'])

    def test_missing_label_and_wrong_endpoint_cannot_borrow(self):
        im,t=fixture();t.pop()
        with self.assertRaisesRegex(ValueError,'LABEL_BAR_COUNT'):percent_geometry(im,t)
        im,t=fixture(wrong_endpoint=True)
        with self.assertRaisesRegex(ValueError,'ATP_LEGEND'):percent_geometry(im,t)

    def test_unknown_dose_unit_keeps_numeric_point_unbound(self):
        im,t=fixture(unit='MM');g=percent_geometry(im,t)
        parents=[{'candidate_id':'p10','compound_name':'quercetin','compound_author_names':['quercetin'],
                  'fields':{'dose_uM':{'value':10}}},
                 {'candidate_id':'p25','compound_name':'quercetin','compound_author_names':['quercetin'],
                  'fields':{'dose_uM':{'value':25}}}]
        bound,holds=bind_percent_points(g,parents)
        self.assertEqual([p['parent_candidate_id'] for p in bound],['p10'])
        self.assertTrue(any(h['reason']=='IMAGE_DOSE_UNIT_UNSUPPORTED_OR_UNRESOLVED' for h in holds))

    def test_other_drug_same_numeric_dose_does_not_match(self):
        im,t=fixture();g=percent_geometry(im,t)
        parent={'candidate_id':'p','compound_name':'phloretin','compound_author_names':['phloretin','PHL'],'fields':{'dose_uM':{'value':10}}}
        bound,holds=bind_percent_points(g,[parent]);self.assertFalse(bound)

    def test_cyrillic_endpoint_confusable_is_explicit_review_hold(self):
        im,t=fixture();next(t for t in t if t['text']=='ATP')['text']='АTP'
        g=percent_geometry(im,t);self.assertTrue(g['endpoint_ocr_confusable_requires_review'])

    def test_bar_above_calibrated_region_is_not_silently_truncated(self):
        im,t=fixture()
        for x in range(100,116):
            for y in range(101,476):dot(im,x,y,(0,0,255))
        with self.assertRaisesRegex(ValueError,'TRUNCATED'):percent_geometry(im,t)

    def test_percent_export_retains_estimate_origin_and_training_null(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups);im,t=fixture();g=percent_geometry(im,t)
        c=fx.data['candidates'][0]
        value={'parent_candidate_id':c['candidate_id'],'treated_point':g['points'][1],'control_point':g['control_point'],'image_dose_literal':'10 uM synthetic'}
        data={'candidates':[value],'geometry':g,'image_sha256':'synthetic'};p=fx.root/'percent.json';p.write_text(json.dumps(data))
        with patch('r031_evidence.atp_content_percent_plot.read_percent',return_value=data):
            result=export_candidates([fx.packet],fx.registry,fx.digest,fx.root/'percent-export',percent_plot_packet_paths=[p])
        row=json.loads((fx.root/'percent-export/observations.jsonl').read_text())
        self.assertEqual(result['counts']['multi_endpoint_atp_graph_estimated_pairs'],1)
        self.assertEqual(json.loads(row['source_locator'])['numeric_origin'],ORIGIN)
        self.assertAlmostEqual(row['treated_value'],110,delta=1)
        self.assertIsNone(row['training_effect_log2fc']);self.assertEqual(row['train_eligible'],0)
        self.assertIn('GRAPH_ESTIMATE_NOT_AUTHOR_REPORTED_NUMERIC',row['hold_reasons_json'])

    def test_changed_percent_value_is_rejected_by_pixel_replay(self):
        fx=fixtures.Export();fx.setUp();self.addCleanup(fx.doCleanups)
        data={'schema':'asterloom.atp-content.percent-plot/0.1','figure_packet_path':'f','figure_packet_sha256':'f',
              'relations_packet_path':'r','relations_packet_sha256':'r','candidates':[{'estimated_value':999}]}
        p=fx.root/'forged-percent.json';p.write_text(json.dumps(data));replay={**data,'candidates':[{'estimated_value':110}]}
        with patch('r031_evidence.atp_content_percent_plot.extract_percent',return_value=replay):
            with self.assertRaisesRegex(ValueError,'PIXEL_REPLAY_MISMATCH'):read_percent(p)
