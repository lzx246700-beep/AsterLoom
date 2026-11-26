from pathlib import Path
from contextlib import closing
import json,os,sys,tempfile,unittest
if os.environ.get('R31_TEST_INSTALLED')!='1':sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from r031_evidence.r31_observatory import frozen,rate,quantile,report
from r031_evidence import r30_ledger as L
from r031_evidence.r21_config import setup
class Coverage(unittest.TestCase):
 def test_empty_denominator_is_unknown_not_zero(self):
  self.assertIsNone(rate(0,0)['value']);self.assertIsNone(quantile([],.95));self.assertEqual(quantile([4,1,2,3],.95),4)
 def test_frozen_manifest_sha_and_duplicate_guard(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'m.json';p.write_text(json.dumps(dict(studies=[dict(paper_id='a')])));sha=L.digest(p.read_bytes());frozen(p,sha)
   p.write_text(p.read_text()+' ')
   with self.assertRaisesRegex(ValueError,'SHA_CHANGED'):frozen(p,sha)
   p.write_text(json.dumps(dict(studies=[dict(paper_id='a')]*2)))
   with self.assertRaisesRegex(ValueError,'DUPLICATE'):frozen(p)
 def test_backlog_is_counted_and_no_human_metrics_or_fake_XY(self):
  with tempfile.TemporaryDirectory() as t:
   h=Path(t);setup(h);raw=h/'raw.json';raw.write_text('{}');page=L.ingest_page(h,provider='TEST',query='q',cursor='*',next_cursor=None,raw_sha=L.digest(raw.read_bytes()),raw_path=raw,hits=[dict(doi='10.1234/abc')])
   with closing(L.connect(h)) as c:pid=c.execute('select paper_id from r30_paper').fetchone()[0]
   m=h/'frozen.json';m.write_text(json.dumps(dict(studies=[dict(paper_id=pid,stratum='ATP_CONTENT',query='q')],strata={'ATP_CONTENT':'q'},known_reference_catalog=[])))
   before=L.digest((h/'state/science.sqlite3').read_bytes());d=report(h,m,with_profiles=False);self.assertEqual(before,L.digest((h/'state/science.sqlite3').read_bytes()));self.assertEqual(d['denominator'],1);self.assertEqual(d['strata']['ATP_CONTENT']['states'],{'DISCOVERED':1});self.assertIsNone(d['human_F1']);self.assertIsNone(d['known_catalog_recall_at_retrieved_pages']['value']);self.assertEqual(d['Gold'],0);self.assertTrue(d['queue']['conservation_pass'])
if __name__=='__main__':unittest.main()
