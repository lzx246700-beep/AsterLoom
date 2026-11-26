"""R45 direct-Chat-MCP import safety regression (offline, no real model calls)."""
from pathlib import Path
from unittest.mock import patch
import hashlib,json,sqlite3,tempfile,unittest

from r031_evidence import r45_chat_bridge as B
from r031_evidence.r44_packets import sha,canonical,FORMAT
from r031_evidence.r44_arbiter import FIELDS

TEXT=("In L6 myoblasts, Berberine (50 μM) decreased intracellular ATP levels by "
      "approximately 57% compared with the control following 48 h of exposure.")
def packet():
 x={"schema":FORMAT,"paper":"PMC_TEST_R45","paper_title":"Synthetic source",
    "source_sha256":"f"*64,"selection_scope":"DEVELOPMENT_ONLY_NOT_HIDDEN",
    "source_license_literal":"cc by","context_truncated":False,
    "chunks":[{"chunk_id":"r44:test","locator":"/article/body[1]/sec[1]/p[1]",
      "kind":"PARAGRAPH","section":"Results","text":TEXT,
      "author_sha256":"f"*64}]}
 x["packet_content_sha256"]=sha(canonical(x).encode())
 return x
def answer():
 fields={k:None for k in FIELDS}
 fields.update(chemical_identity="Berberine",tested_form="Berberine",
   cell_model="L6 myoblasts",treatment="Berberine",comparator="the control",
   endpoint="ATP_CONTENT",direction="DECREASE",value="57%",unit="%",
   dose="50 μM",exposure_time="48 h")
 q={"chemical_identity":"Berberine (50 μM)","tested_form":"Berberine (50 μM)",
     "cell_model":"In L6 myoblasts","treatment":"Berberine (50 μM)",
     "comparator":"compared with the control","endpoint":"intracellular ATP levels",
     "direction":"decreased intracellular ATP levels",
     "value":"57% compared with the control","unit":"57%",
     "dose":"Berberine (50 μM)","exposure_time":"following 48 h of exposure"}
 return {"paper":"PMC_TEST_R45","relations":[{
   "fields":fields,
   "evidence":[{"field":k,"chunk_id":"r44:test","quote":v} for k,v in q.items()],
   "uncertainties":["No author-based normalisation or assay specification known"]}],
   "reason_if_none":None}

class R45ChatMCP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.base=Path(self.tmp.name)
  self.packet=self.base/"author_packet.json"
  self.packet.write_text(json.dumps(packet()))
  self.packet_sha=sha(self.packet.read_bytes())
  self.chat=self.base/"chat_review.json"
  self.chat.write_text(json.dumps(answer()))
  self.chat_sha=sha(self.chat.read_bytes())
  self.manifest=self.base/"frozen_dev_manifest.jsonl"
 def run_import(self,**kwargs):
  with patch.object(B,"verify_training_packet",return_value={"paper":"PMC_TEST_R45"}):
   return B.import_chat(self.packet,self.packet_sha,self.chat,self.chat_sha,
      self.base/"safe_output",manifest_file=self.manifest,
      manifest_sha=B.OFFICIAL_R43_DEV_MANIFEST_SHA,**kwargs)

 def test_exact_source_verified_import_non_gold(self):
  res=self.run_import()
  self.assertEqual(res["relations"],1)
  self.assertGreater(res["author_field_quotes_verified"],5)
  self.assertEqual(res["Gold"],0)
  receipt=json.loads((self.base/"safe_output/RECEIPT.json").read_text())
  self.assertEqual(receipt["declared_author"],
       "CHAT_MODEL_OUTPUT_FILE_ORIGIN_NOT_CRYPTOGRAPHICALLY_ATTESTED")
  self.assertEqual(receipt["paid_API_calls"],0)
  self.assertIsNone(receipt["scientific_precision"])
  with sqlite3.connect(self.base/"safe_output/CHAT_REVIEW_SIDECAR.sqlite3") as c:
   self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok")
   with self.assertRaises(sqlite3.IntegrityError):
    c.execute("UPDATE chat_review_import SET gold=1")
   with self.assertRaises(sqlite3.IntegrityError):
    c.execute("UPDATE chat_review_relation SET training_approved=1")
 def test_cannot_reuse_output_directory(self):
  self.run_import()
  with self.assertRaisesRegex(RuntimeError,"OUTPUT_DIRECTORY_EXISTS"):
   self.run_import()
 def test_cannot_hallucinate_original_quote(self):
  changed=answer()
  changed["relations"][0]["evidence"][0]["quote"]="The authors claimed ATP went up 900%"
  self.chat.write_text(json.dumps(changed));self.chat_sha=sha(self.chat.read_bytes())
  with self.assertRaisesRegex(ValueError,"HALLUCINATED"):
   self.run_import()
  self.assertFalse((self.base/"safe_output").exists())
 def test_unsupported_direct_chat_origin_not_trusted_for_gold(self):
  a=answer();a["Gold"]=1
  self.chat.write_text(json.dumps(a));self.chat_sha=sha(self.chat.read_bytes())
  with self.assertRaisesRegex(ValueError,"SCHEMA"):
   self.run_import()
 def test_review_sha_pinned_required(self):
  self.chat_sha="0"*64
  with self.assertRaisesRegex(ValueError,"MUST_BE_SHA_PINNED"):
   self.run_import()
 def test_r43_cannot_be_unpinned(self):
  with self.assertRaisesRegex(ValueError,"BOTH_REQUIRED"):
   self.run_import(r43_db=self.base/"nonexistent.db")
 def test_candidate_id_requires_actual_r43_db(self):
  with self.assertRaisesRegex(ValueError,"CANDIDATE_IDS"):
   self.run_import(candidate_ids=("r43:forged",))
 def test_prepared_chat_prompt_does_not_execute_model(self):
  with patch.object(B,"verify_training_packet",return_value={"paper":"PMC_TEST_R45"}):
   rec=B.prepare_chat(self.packet,self.packet_sha,self.base/"prepare",
    manifest_file=self.manifest,manifest_sha=B.OFFICIAL_R43_DEV_MANIFEST_SHA)
  self.assertEqual(rec["model_calls"],0)
  self.assertEqual(rec["Gold"],0)
  self.assertIn("UNREVIEWED_TEMPLATE", (self.base/"prepare/UNREVIEWED_CHAT_ANSWER_TEMPLATE.json").read_text())
  self.assertNotIn("r43:candidate:", (self.base/"prepare/CHAT_SOURCE_ONLY_PROMPT.md").read_text())
 def test_official_manifest_only(self):
  self.manifest.write_text("{}\n")
  with self.assertRaisesRegex(ValueError,"ONLY_OFFICIAL"):
   B.check_manifest(self.manifest,"0"*64)
 def test_hidden_or_unlisted_packet_blocked(self):
  with patch.object(B,"check_manifest",return_value=[]):
   with self.assertRaisesRegex(ValueError,"ONLY_FROZEN_R43_TRAIN"):
    B.verify_training_packet(packet(),self.manifest,B.OFFICIAL_R43_DEV_MANIFEST_SHA)
 def test_negative_review_needs_reason(self):
  a={"paper":"PMC_TEST_R45","relations":[],"reason_if_none":None}
  self.chat.write_text(json.dumps(a));self.chat_sha=sha(self.chat.read_bytes())
  with self.assertRaisesRegex(ValueError,"REQUIRES_REASON"):
   self.run_import()
 def test_negative_review_reason_not_true_negative(self):
  self.chat.write_text(json.dumps({
    "paper":"PMC_TEST_R45","relations":[],
    "reason_if_none":"No reliable author pair; absence does not prove negative"}))
  self.chat_sha=sha(self.chat.read_bytes())
  res=self.run_import()
  self.assertEqual(res["relations"],0)
  self.assertEqual(res["Gold"],0)
 def test_source_packet_hash_failure(self):
  with self.assertRaisesRegex(ValueError,"PACKET_SHA"):
   with patch.object(B,"verify_training_packet",return_value={"paper":"PMC_TEST_R45"}):
    B.import_chat(self.packet,"0"*64,self.chat,self.chat_sha,self.base/"out",
      manifest_file=self.manifest,manifest_sha=B.OFFICIAL_R43_DEV_MANIFEST_SHA)

if __name__=="__main__": unittest.main()
