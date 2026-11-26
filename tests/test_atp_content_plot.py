"""Known synthetic geometry only; not a human-labeled paper benchmark."""
import struct
import unittest
import zlib
from r031_evidence.atp_plot_pixels import read_png,pixel,dark_symbol
from r031_evidence.atp_content_plot import fit_linear_ticks,_match_controls,_colored_bars,_relative_bars
from r031_evidence.atp_content_plot import apply_plot_candidate


def image(w=50,h=50):return {'width':w,'height':h,'bpp':3,'rows':[bytearray([255]*(w*3)) for _ in range(h)]}
def dot(im,x,y,rgb):im['rows'][im['height']-1-y][x*3:x*3+3]=bytes(rgb)


class Pixels(unittest.TestCase):
    def test_png_decoder_preserves_pixels_and_rejects_modified_crc(self):
        def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
        raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\x00\x80\xff\xff\xff\xff'))+chunk(b'IEND',b'')
        im=read_png(raw);self.assertEqual(list(pixel(im,0,0)),[0,128,255])
        with self.assertRaisesRegex(ValueError,'CRC'):read_png(raw[:-1]+bytes([raw[-1]^1]))

    def test_antialiased_plus_minus_blank_and_x_are_distinct(self):
        im=image()
        for x in range(20,31):dot(im,x,25,(50,50,50))
        for y in range(20,31):dot(im,25,y,(200,200,200))
        self.assertEqual(dark_symbol(im,25,25)['symbol'],'PRESENT')
        im=image()
        for x in range(20,31):dot(im,x,25,(100,100,100))
        self.assertEqual(dark_symbol(im,25,25)['symbol'],'ABSENT')
        self.assertEqual(dark_symbol(image(),25,25)['symbol'],'UNKNOWN')
        im=image()
        for x in range(20,31):dot(im,x,x,(20,20,20));dot(im,x,50-x,(20,20,20))
        self.assertEqual(dark_symbol(im,25,25)['symbol'],'UNKNOWN')

    def test_nonrectangular_colored_legend_is_not_a_bar(self):
        im=image()
        for y in range(2,30):
            for x in range(10,20):dot(im,x,y,(0,220,100))
        for y in range(10,20):
            for x in range(30,40):dot(im,x,y,(220,0,100))
        bars=_colored_bars(im,[0,0,50,50]);self.assertEqual(len(bars),1)
        self.assertEqual(bars[0]['pixel_bbox'],[10,2,10,28])


class CalibrationAndPairing(unittest.TestCase):
    def test_linear_axis_value_and_bound(self):
        s=fit_linear_ticks([{'value':v,'y':y} for v,y in [(0,10),(1,110),(2,210)]])
        self.assertAlmostEqual(s['slope_value_per_pixel'],.01)
        self.assertAlmostEqual(s['zero_y_pixel'],10)

    def test_log_or_inconsistent_axis_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'NONLINEAR'):
            fit_linear_ticks([{'value':v,'y':y} for v,y in [(1,10),(10,110),(100,210)]])

    def test_drug_and_stimulus_mismatch_cannot_share_control(self):
        def bar(typ,pa='PRESENT',other='ABSENT'):
            return {'condition_states':{'LPS':{'state':'PRESENT'},'PA':{'state':pa},
                'TYP':{'state':typ},'AICAR':{'state':other}}}
        chart={'axis_text_ocr_literal':'ATP level (fold of LPS plus PA)',
               'bars':[bar('DOSE_REPORTED_IN_IMAGE'),bar('ABSENT',pa='ABSENT'),bar('ABSENT',other='PRESENT')]}
        matrix={'target_agent':'TYP','condition_rows':[{'agent':a} for a in ['LPS','PA','TYP','AICAR']]}
        self.assertEqual(_match_controls(chart,matrix),[])
        correct=bar('ABSENT');chart['bars'].append(correct)
        pair,=_match_controls(chart,matrix);self.assertIs(pair['control_bar'],correct)
        chart['bars'].append(bar('ABSENT'))
        self.assertEqual(_match_controls(chart,matrix),[])

    def test_image_dose_without_caption_corroboration_cannot_inherit_schedule(self):
        c={'candidate_id':'TEST','fields':{'dose_uM':{'value':100}}}
        data={'parent_candidate_id':'TEST','estimated_pairs':[{'dose':{'unit_ocr_literal':'uM','dose':{'value':40}}}]}
        self.assertIs(apply_plot_candidate(c,data,'not-needed-for-unmatched-dose'),c)

    def test_relative_axis_keeps_pale_and_gray_bars_unassigned(self):
        im=image(100,100)
        colors=[(247,202,194),(246,177,138),(254,224,190),(212,212,212)]
        tops=[60,66,72,77]
        for j,(color,top) in enumerate(zip(colors,tops)):
            for y in range(10,top):
                for x in range(10+j*20,20+j*20):dot(im,x,y,color)
        chart={'roi_pixel_bounds':[5,8,90,90],
               'scale':{'slope_value_per_pixel':2.0,'intercept':-20.0,'max_tick_residual_px':.2},
               'axis_text_ocr_literal':'Relative ATP'}
        found=_relative_bars(im,chart)
        self.assertEqual(len(found),4)
        self.assertEqual([x['arm_assignment'] for x in found],['HOLD_UNASSIGNED']*4)
        self.assertEqual([x['comparator_assignment'] for x in found],['HOLD_UNASSIGNED']*4)
        self.assertTrue(all(x['numeric_origin']=='GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE' for x in found))

    def test_relative_axis_rejects_no_filled_bars(self):
        chart={'roi_pixel_bounds':[5,8,45,45],
               'scale':{'slope_value_per_pixel':1.0,'intercept':-8.0,'max_tick_residual_px':0},
               'axis_text_ocr_literal':'Relative ATP'}
        with self.assertRaisesRegex(ValueError,'NO_UNAMBIGUOUS'):_relative_bars(image(),chart)
