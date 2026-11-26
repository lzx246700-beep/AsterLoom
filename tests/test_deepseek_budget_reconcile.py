import datetime, hashlib, json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from r031_evidence import r44_deepseek as ds
from r031_evidence.deepseek_budget_reconcile import reconcile, validated_credits
REAL_DATE=datetime.date
class BudgetReceipt(unittest.TestCase):
 def test_reconciliation_and_tamper_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);budget=root/'budget';budget.mkdir()
   with patch.object(ds.dt,'date') as date:
    date.today.return_value=REAL_DATE(2026,11,10);date.side_effect=REAL_DATE
    amount=ds.reserve(str(budget),100,10)
    ledger=budget/'deepseek_experiment_budget.sqlite3'
    p=root/'B_model/PMC1234/RAW_HTTP_RESPONSE.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'model':'deepseek-flash','usage':{'prompt_tokens':25,'completion_tokens':7,'total_tokens':32}}))
    digest=hashlib.sha256(p.read_bytes()).hexdigest();ceiling=(3*25+12*7+4)//5
    plan={'schema':'asterloom.budget-reconciliation-plan/1','status':'DRY_RUN_NOT_APPLIED','ledger_path':str(ledger),'rows':[{'paper':'PMC1234','mode':'B','reservation_id':2,'reserved_microdollars':amount,'prompt_tokens':25,'completion_tokens':7,'credit_microdollars':amount-ceiling,'response_sha256':digest}]}
    pp=root/'plan.json';pp.write_text(json.dumps(plan));plan_sha=hashlib.sha256(pp.read_bytes()).hexdigest();r=reconcile(ledger,pp,plan_sha)
    self.assertEqual(r['total_credited_microdollars'],amount-ceiling)
    with self.assertRaises(sqlite3.IntegrityError):reconcile(ledger,pp,plan_sha)
    ds.reserve(str(budget),100,10)
    p.write_text('{}')
    with self.assertRaisesRegex(ValueError,'RECEIPT_SHA_MISMATCH'):ds.reserve(str(budget),100,10)
 def test_frozen_holdout_receipt_reconciles_by_hash(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);budget=root/'budget';budget.mkdir()
   with patch.object(ds.dt,'date') as date:
    date.today.return_value=REAL_DATE(2026,11,10);date.side_effect=REAL_DATE
    amount=ds.reserve(str(budget),100,10);ledger=budget/'deepseek_experiment_budget.sqlite3'
    p=root/'predictions/PMC1234/RAW_HTTP_RESPONSE.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'model':'deepseek-flash','usage':{'prompt_tokens':8,'completion_tokens':2,'total_tokens':10}}))
    ceiling=(3*8+12*2+4)//5
    plan={'schema':'asterloom.budget-reconciliation-plan/1','status':'DRY_RUN_NOT_APPLIED','ledger_path':str(ledger),'rows':[{'paper':'PMC1234','mode':'H','reservation_id':2,'reserved_microdollars':amount,'prompt_tokens':8,'completion_tokens':2,'credit_microdollars':amount-ceiling,'response_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}
    pp=root/'plan.json';pp.write_text(json.dumps(plan));result=reconcile(ledger,pp,hashlib.sha256(pp.read_bytes()).hexdigest())
    self.assertEqual(result['reconciled_reservations'],1)
