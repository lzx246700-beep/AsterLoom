"""Engineering specimens only, never deposited in a scientific Home."""
import csv
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from r031_evidence import r41_model_data as store
from r031_evidence.r41_model_export import assess, evaluate, freeze, partition


class ModelData(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.c = sqlite3.connect(":memory:")
        self.addCleanup(self.c.close)
        self.c.executescript(store.SCHEMA)
        self.c.execute("pragma foreign_keys=on")

    def file(self, name, value):
        p = self.path/name
        p.write_text(json.dumps(value))
        return p, store.digest(p.read_bytes())

    def specimen(self, chem="A", batch="1"):
        key = chem*14 + "-" + chem*10 + "-X"
        t = dict(cell="RAW264.7", stimulus="LPS", lps_ng_ml=100, hours=24, pretreat_hours=1,
                 additional_treatments=[], vehicle="DMSO0.1", medium="DMEM", batch=chem+batch, protocol="SOP1", role="TREATED",
                 well="A1",control_well="B1",compound_uM=10,inchi_key=key,atp_nmol=2,viable_cells=100,
                 raw_luminescence=20,count_method="INDEPENDENT_VIABLE_CELL_COUNT")
        control = dict(t, role="MATCHED_VEHICLE", well="B1",compound_uM=0,atp_nmol=3,raw_luminescence=30)
        control.pop("inchi_key")
        return dict(origin="EXPERIMENT",study_id="study"+chem,comparison_id=batch,treated=t,control=control,
                    scaffold="scaffold"+chem, family="family"+chem,structure_smiles="CC" if chem=="A" else "CCC",
                    rights_basis="test",extractor="operator",coa_id="coa",qc_locator="/qc",batch_source=chem+batch,
                    calibration_id="calibration",assay_method="luciferase",orthogonal_viability_pass=True,
                    spike_in_pass=True,endpoint="INTRACELLULAR_ATP_CONTENT",inchi_key=key)

    def add(self, r):
        name=r["study_id"]+r["comparison_id"]+str(r["treated"]["atp_nmol"])
        p,h=self.file(name+".json",dict(comparisons=[r]))
        manifest,sha=self.file(name+"-index.json",dict(schema="asterloom.paired-assay/1", comparisons=[
            dict(asset=dict(file=p.name,sha256=h),origin=r["origin"],index=0)]))
        with self.c:
            store.ingest(self.c,"comparisons",manifest,sha)

    def decisions(self, **changes):
        rows=[dict(dict(id=r[0],science="APPROVE",gold="APPROVE",training="APPROVE",
                        reviewers=["A","B"],owner="owner"),**changes)
              for r in self.c.execute("select id from r41_observation")]
        return self.file("decisions.json",dict(schema="asterloom.owner-decisions/1",task=store.TASK,decisions=rows))

    def test_real_gate_requires_separate_decisions_and_replicates(self):
        self.add(self.specimen())
        self.assertEqual(assess(self.c)[0]["gold_including_model_ready"],0)
        self.assertEqual(assess(self.c,*self.decisions(reviewers=["A","a "]))[0]["gold_including_model_ready"],0)
        self.assertEqual(assess(self.c,*self.decisions(reviewers=["A","operator"]))[0]["gold_including_model_ready"],0)
        self.assertEqual(assess(self.c,*self.decisions())[0]["eligible"],0)
        self.add(self.specimen(batch="2"))
        self.assertEqual(assess(self.c,*self.decisions(training="HOLD"))[0]["eligible"],0)
        audit,samples,stages=assess(self.c,*self.decisions())
        self.assertEqual(audit["eligible"],2)
        self.assertEqual(freeze(self.path/"held",audit,samples,stages)["exported"],0)

    def test_roundtrip_signed_off_fixture(self):
        for chem in ("A","B"):
            for batch in ("1","2"):
                self.add(self.specimen(chem,batch))
        audit,samples,stages=assess(self.c,*self.decisions())
        self.assertEqual(audit["known_group_components"],2)
        out=self.path/"freeze"
        self.assertEqual(freeze(out,audit,samples,stages)["exported"],4)
        with (out/"model_ready.csv").open() as f:
            rows=list(csv.DictReader(f))
        self.assertEqual({r["split"] for r in rows},{"train","test"})
        for r in rows:
            self.assertAlmostEqual(float(r["Y"]),-.01)
            self.assertFalse(any(json.loads(r["missing_mask"]).values()))
        with self.assertRaises(ValueError):
            freeze(out,audit,samples,stages)

    def test_contradictory_revision_and_bad_target_hold(self):
        r=self.specimen()
        self.add(r)
        r["treated"]["atp_nmol"]=5
        self.add(r)
        self.assertEqual(assess(self.c,*self.decisions())[0]["gap_counts"]["conflicting_comparison_versions"],2)
        for k,v in (("cell","PEM"),("hours",12),("count_method","PROTEIN"),("atp_nmol",float("nan")),("compound_uM",True)):
            broken=deepcopy(r)
            broken["treated"][k]=v
            self.assertTrue(evaluate(broken))

    def test_approval_cannot_survive_raw_or_record_tamper(self):
        self.add(self.specimen())
        approval = self.decisions()
        with self.assertRaisesRegex(ValueError, "observation identity drift"):
            with self.c:
                self.c.execute("update r41_observation set payload=json_set(payload,'$.treated.atp_nmol',99)")
                assess(self.c,*approval)
        with self.assertRaisesRegex(ValueError, "asset identity drift"):
            with self.c:
                self.c.execute("update r41_source set raw=?", (b"{}",))
                assess(self.c,*approval)

    def test_transitive_components(self):
        samples=[dict(id=str(i),groups=dict(scaffold=a,family=b)) for i,(a,b) in enumerate(
            [("s1","f1"),("s1","f2"),("s2","f2"),("s3","f3")])]
        self.assertEqual(partition(samples),2)
        self.assertEqual(len({s["split"] for s in samples[:3]}),1)
        self.assertNotEqual(samples[0]["split"],samples[3]["split"])

    def test_missing_and_feedback_never_y(self):
        p,h=self.file("feedback",dict(weak_increase=29,weak_decrease=3,Gold=32))
        with self.c:
            store.ingest(self.c,"feedback",p,h)
        a=assess(self.c)[0]
        self.assertEqual(a["eligible"],0)
        self.assertIn("AUTHOR_NS_WORDING",a["review_queue_by_gap"])
        with self.assertRaises(ValueError):
            assess(self.c,p,"incorrect")

    def test_cycle_recovers_past_bad_first_batch(self):
        (self.path/"state").mkdir()
        sqlite3.connect(self.path/"state/science.sqlite3").close()
        fp,h=self.file("ok",{})
        p,sha=self.file("list",dict(schema="asterloom.model-batches/1",batches=[
            dict(kind="feedback",path="absent",sha="0"*64),dict(kind="feedback",path=fp.name,sha=h)]))
        args=SimpleNamespace(model_action="cycle",input=p,sha=sha,limit=1)
        self.assertEqual(store.run(self.path,args)["receipts"][0]["status"],"HOLD")
        self.assertEqual(store.run(self.path,args)["receipts"][0]["status"],"COMMITTED")
        self.assertEqual(store.run(self.path,args)["receipts"],[])

    def test_independent_science_review_of_wording_without_training(self):
        raw = b"<article/>"
        h = store.digest(raw)
        with self.c:
            self.c.execute("insert into r41_batch values(?,?,?,?)",(h,"jats","fixture","{}"))
            store.source(self.c,h,"paper","doi","fixture",self.path,raw)
            store.record(self.c,h,"LITERATURE","ARM_WORDING",h,"/article",
                         dict(compound="fixture",author_wording="AUTHOR_UNCHANGED_WORDING",target_effect=None))
        audit,_,_ = assess(self.c,*self.decisions())
        self.assertEqual(audit["stage_counts_exclusive"]["SCIENCE_REVIEWED"],1)
        self.assertEqual(audit["gold_including_model_ready"],0)
        self.assertEqual(audit["eligible"],0)

    def test_numeric_pair_scope(self):
        q="intracellular levels of ATP were depleted by oligomycin (30 μM) and nigericin (≥10 μM) by 24% (p<0.01) and 28% (p<0.001), respectively"
        self.assertEqual(store.DEPLETED_PAIR.search(q)["d2"],"≥10 μM")
        self.assertIsNone(store.DEPLETED_PAIR.search(q.replace(", respectively","")))
