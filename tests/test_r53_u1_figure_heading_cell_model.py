"""R53 U1: one cell model in a shared AUTHOR figure heading, no cross-figure join."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest
from r031_evidence.atp_content_relations import extract_relations,verify_relations

HEAD=("MGF relieves CCl4-induced inflammatory response and OS in HepG2 cells. "
      "(A) IL-6 levels in different groups. (I) ATP content in different groups.")
MULTI=("Apigenin-induced ATP changes in HGEpiC and OECM-1 cells. "
       "(A) Confluence was measured. (F) Quantification of total ATP levels "
       "in HGEpiC vs. OECM-1 cells after 24 and 48 h of exposure to apigenin.")


def paper(title=HEAD, additional_figure='', compound='Mangiferin', abbrev='MGF', ref_panel='I'):
    return f"""<article><front><article-meta><article-id pub-id-type="pmcid">PMC_R53_SINGLE_CELL</article-id>
      <title-group><article-title>Figure heading test</article-title></title-group>
      </article-meta></front><body><sec><title>Results</title>
      <p>{compound} ({abbrev}) was tested by ATP assay. {abbrev} exposure affected ATP content (Figure 6{ref_panel}).</p>
      {additional_figure}
      <fig id="F6"><caption><p>{title}</p></caption></fig>
      </sec></body></article>"""


class HeadingCellModel(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.p=Path(d.name)/'author.xml'

    def extract(self,txt,compounds):
        self.p.write_text(txt,encoding='utf-8')
        sha=hashlib.sha256(self.p.read_bytes()).hexdigest()
        o=extract_relations(self.p,sha,compounds)
        self.assertTrue(verify_relations(o,self.p)['source_replay_pass'])
        return o

    def test_shared_single_cell_figure_mgf_atp_panel(self):
        out=self.extract(paper(),['mangiferin'])
        c,=out['candidates']
        self.assertEqual(c['panel'],'I')
        self.assertEqual(c['fields']['cell_model']['value'],'HepG2')
        self.assertEqual(c['fields']['cell_model']['state'],'SINGLE_AUTHOR_MODEL_IN_SAME_FIGURE_HEADING')
        self.assertIsNone(c['fields']['dose_uM']['value'])
        self.assertIsNone(c['fields']['comparator_arm']['value'])
        self.assertFalse(c['training_approved'])

    def test_two_models_in_author_heading_does_not_choose_first(self):
        t=HEAD.replace('HepG2 cells','HepG2 and H9c2 cells')
        out=self.extract(paper(t),['mangiferin'])
        c,=out['candidates']
        self.assertIsNone(c['fields']['cell_model']['value'])

    def test_cell_model_of_neighbor_panel_with_no_shared_heading_not_borrowed(self):
        t='MGF modulates metabolism. (A) IL-6 in HepG2 cells. (I) ATP content in different groups.'
        out=self.extract(paper(t),['mangiferin'])
        c,=out['candidates']
        self.assertIsNone(c['fields']['cell_model']['value'])

    def test_cell_model_of_other_figure_not_borrowed(self):
        fig='<fig id="F5"><caption><p>HepG2 cells displayed swelling. (A) Mitochondria.</p></caption></fig>'
        t='MGF modulates metabolism. (I) ATP content in different groups.'
        out=self.extract(paper(t,fig),['mangiferin'])
        c,=out['candidates']
        self.assertIsNone(c['fields']['cell_model']['value'])

    def test_apigenin_two_cell_models_time_dose_assay_not_single_arm(self):
        txt=paper(MULTI,compound='Apigenin',abbrev='APG',ref_panel='F')
        out=self.extract(txt,['apigenin'])
        c,=out['candidates']
        self.assertIsNone(c['fields']['cell_model']['value'])
        self.assertIsNone(c['fields']['total_compound_exposure_h']['value'])
        self.assertFalse(c['training_approved'])


if __name__=='__main__':unittest.main()
