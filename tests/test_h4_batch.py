import datetime,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from r031_evidence import r44_deepseek as ds

def test_audited_weekend_reservation_bounds_real_cost(tmp_path):
 class Day(datetime.date):
  @classmethod
  def today(cls):return cls(2026,11,14)
 with patch.object(ds,'dt',SimpleNamespace(date=Day)):
  ds.reserve(str(tmp_path),100000,65536)
 with sqlite3.connect(tmp_path/'deepseek_experiment_budget.sqlite3') as db:
  reserved=db.execute('select microdollars from reservations order by id desc limit 1').fetchone()[0]
 assert reserved==((100000+4096)*3+65536*12+19)//20
 assert reserved>=100000*.15+65536*.6

def test_acquisition_deterministic_resume_without_network(tmp_path):
 import sys;sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
 from ast_scope_store_acquire import acquire
 from r031_evidence.llm_harness import sha
 src=tmp_path/'author.xml';src.write_text('preserved original author fixture')
 receipt=tmp_path/'receipt.json';receipt.write_text('{}')
 entry=dict(paper='PMC99999999',source=dict(path=str(src),sha256=sha(src.read_bytes())),acquisition_receipt=str(receipt),acquisition_receipt_sha256=sha(receipt.read_bytes()))
 out=tmp_path/'out';folder=out/'PMC99999999';folder.mkdir(parents=True);(folder/'ENTRY.json').write_text(json.dumps(entry))
 search=tmp_path/'search.json';search.write_text(json.dumps(dict(hitCount=1,resultList=dict(result=[dict(pmcid=entry['paper'],title='Unselected author paper',isOpenAccess='Y',license='cc by')]))))
 excluded=tmp_path/'exclude.json';excluded.write_text(json.dumps(dict(holdout_ids=[],known_project_pmcids=[])))
 with patch('ast_scope_store_acquire.read_official',side_effect=AssertionError('NETWORK_USED')):
  acquire(search,excluded,out,limit=1,order='source_sha');acquire(search,excluded,out,limit=1,order='source_sha')
 assert len(json.load(open(out/'MANIFEST.json'))['rows'])==1

def test_peptide_scope_is_source_identity_not_paper_keyword():
 from r031_evidence.atp_context_profile import source_intervention_scope
 p=dict(chunks=[dict(text='Synthetic peptides—DrugP (ABCDE) and DrugQ (EDCBA)—were synthesized. EGCG was used in another assay.')])
 assert source_intervention_scope(dict(fields=dict(chemical_identity='DrugP')),p)
 assert not source_intervention_scope(dict(fields=dict(chemical_identity='EGCG')),p)
