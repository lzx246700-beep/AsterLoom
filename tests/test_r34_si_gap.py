from pathlib import Path
import io,json,os,sys,tempfile,unittest,zipfile
ROOT=Path(__file__).resolve().parents[1]
if os.environ.get('R33_TEST_INSTALLED')!='1':sys.path.insert(0,str(ROOT/'src'))
from r031_evidence.r34_si_acquisition import media_link,zip_inventory
from r031_evidence.r34_gap_diagnostics import attribute
class SI(unittest.TestCase):
 def z(self,name='data.csv',body=b'a,b\n1,2'):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr(name,body)
  return b.getvalue()
 def test_official_media_identity_version_and_MD5_binding(self):
  good='s3://pmc-oa-opendata/PMC12345.1/data.xlsx?md5='+'a'*32
  self.assertIsNotNone(media_link(good,'PMC12345',1,'data.xlsx'))
  for u in [good.replace('PMC12345.1','PMC54321.1'),good.replace('.1/','.2/'),good.replace('pmc-oa-opendata','evil-bucket'),good.split('?')[0]]:self.assertIsNone(media_link(u,'PMC12345',1,'data.xlsx'))
 def test_zip_source_only_inventory_not_executed(self):
  r=zip_inventory(self.z());self.assertTrue(r[0]['source_file_supported']);self.assertEqual(r[0]['state'],'SOURCE_ONLY_NO_EXECUTION');self.assertIsNotNone(r[0]['sha256'])
 def test_zip_code_not_supported_and_traversal_rejected(self):
  self.assertFalse(zip_inventory(self.z('run.py',b'print(1)'))[0]['source_file_supported'])
  with self.assertRaisesRegex(ValueError,'UNSAFE'):zip_inventory(self.z('../escape.csv'))
 def test_zip_duplicate_member_rejected(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('x.csv','a');z.writestr('x.csv','b')
  with self.assertRaisesRegex(ValueError,'DUPLICATE'):zip_inventory(b.getvalue())
 def test_zip_expansion_limit(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   for i in range(81):z.writestr(str(i)+'.txt','a')
  with self.assertRaisesRegex(ValueError,'BUDGET'):zip_inventory(b.getvalue())
 def state(self,**kwargs):
  args=dict(metadata_found=True,lawful_fulltext=True,parsed=True,si_declared=False,si_acquired=0,target_numeric=0,complete_XY=0);args.update(kwargs);return attribute(**args)
 def test_stage_gaps_have_distinct_remediation(self):
  expected=[self.state(metadata_found=False),self.state(lawful_fulltext=False),self.state(parsed=False),self.state(si_declared=True),self.state(),self.state(target_numeric=2),self.state(target_numeric=2,complete_XY=1)];self.assertEqual(len(set(expected)),7);self.assertIn('REVIEW_HOLD',expected[-1])
 def test_SI_retrieval_does_not_imply_target_or_XY(self):
  self.assertEqual(self.state(si_declared=True,si_acquired=1),'PARSED_TARGET_NUMERIC_NOT_EXTRACTED')
if __name__=='__main__':unittest.main()
