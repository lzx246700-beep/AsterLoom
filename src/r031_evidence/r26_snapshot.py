"""R26 frozen, reproducible source-only candidate data and leakage gate.

Exactly one shared Evidence Core. Four views projected from three versioned
Database Profiles; source SHA and original cell literal verified. Study-group
split is executable and stable, but unknown chemical parent/scaffold/material
batch NEVER gets a made-up group ID. Dataset is NOT model-trainable until
independent SCI Gold and all leakage keys are verified.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json
from .r23_profiles import NAMES,SourceProjector,load,validate,CONTRACT
from .r23_dataset import gap
SCHEMA="r031.r26.source-only-reproducible-snapshot/1"
def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build(home,out_directory):
 root=Path(home).expanduser().resolve()
 if root.name=="state":root=root.parent
 dest=Path(out_directory).expanduser().absolute()
 if dest.exists() and list(dest.iterdir()):
  raise ValueError("R26_OUTPUT_DESTINATION_MUST_BE_EMPTY_NOT_OVERWRITABLE")
 dest.mkdir(parents=True,exist_ok=True)
 info={"schema":SCHEMA,"contract_version":CONTRACT,
  "status":"R26_SOURCE_ONLY_NO_FIT_DATASET_SNAPSHOT_NOT_GOLD",
  "source_home":str(root),"source_db":str(root/"state/science.sqlite3"),
  "independent_SCI_annotations_verified":False,
  "read_only_original_science_db":True,
  "formal_training_export_authorized":False,
  "qualified_label_records":0,
  "model_training_allowed":False,
  "cross_profile_study_split_policy":"SAME_VERIFIED_STUDY_GROUP_MUST_HAVE_EXACTLY_ONE_DEVELOPMENT_PARTITION",
  "parent_scaffold_batch_leakage_policy":"UNKNOWN_PARENT_SCAFFOLD_OR_BATCH_BLOCKS_TRAINING_DO_NOT_IMPUTE",
  "no_unknown_negatives_fabricated":True,"Gold":0,
  "profiles":[]}
 all_studies=defaultdict(set)
 all_original_measured=set()
 all_export_row_keys=set()
 all_provenances=set()
 all_task_ready_study_groups=set()
 for name in NAMES:
  profile=load(name,root)
  projector=SourceProjector(root)
  try:
   records=list(projector.project(name))
  finally:projector.close()
  records.sort(key=lambda x:(x["paper"]["paper_key"],x["view"],
    x["observation"]["native_measurement_id"]))
  by_view=defaultdict(list)
  for x in records:
   assert x["profile"]==name and x["profile_version"]==profile.version
   validate(x,name)
   proof=x["provenance"]
   assert proof["byte_sha_verified"] and proof["cell_literal_verified"],x["ids"]["observation_id"]
   # This reproducible evidence snapshot cannot approve Gold. Do not assert
   # that scientific fields must stay NULL forever: legitimate new anchored
   # evidence is expected to fill them in later revisions.
   if x["task"]["approved_training_y"] is not None or x["task"]["qualification"]!="NO_FIT":
    raise ValueError("R26_UNAPPROVED_EXPORT_CONTAINS_TRAINING_LABEL")
   if (x["observation"]["normalized_numeric"] is not None and
       not x["observation"]["normalization_transform_version"]):
    raise ValueError("R26_NORMALIZED_VALUE_WITHOUT_TRANSFORM_VERSION")
   if x["task"]["decision"]=="TASK_READY_CANDIDATE":
    all_task_ready_study_groups.add(x["dataset"]["study_group"])
   proofkey=(x["paper"]["paper_key"],proof["author_sha256"],proof["original_locator"],
      x["observation"]["native_measurement_id"])
   all_provenances.add(proofkey)
   all_original_measured.add(x["observation"]["native_measurement_id"])
   record_key=(name,x["view"],x["observation"]["native_measurement_id"])
   if record_key in all_export_row_keys:raise ValueError("R26_PROFILE_DUPLICATE_NATIVE_OBSERVATION")
   all_export_row_keys.add(record_key)
   group=x["dataset"]["study_group"]
   partition=x["dataset"]["split"]
   if partition not in ("development_train","heldout_validation","heldout_test"):
    raise ValueError("R26_UNRECOGNIZED_STUDY_PARTITION")
   all_studies[group].add(partition)
   by_view[x["view"]].append(x)
  assert all(len(splits)==1 for splits in all_studies.values()),"R26_CROSS_PROFILE_STUDY_SPLIT_LEAKAGE"
  path=dest/(name+".NO_FIT_SOURCE_ONLY.jsonl")
  assert not path.exists()
  with path.open("w",encoding="utf-8") as fd:
   for row in records:
    fd.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
  byview=[]
  ref=gap(root,name)
  expected={v["view"]:v for v in ref["views"]}
  for v in profile.views:
   z=by_view[v["id"]]
   chk=expected[v["id"]]
   assert len(z)==chk["original_observations"]
   if chk["fully_structured_XY_independent_studies"] != len({
       x["dataset"]["study_group"] for x in z
       if x["task"]["decision"]=="TASK_READY_CANDIDATE"}):
    raise ValueError("R26_DYNAMIC_STUDY_QUALIFICATION_DRIFT")
   byview.append({"view":v["id"],"original_native_observations":len(z),
    "distinct_source_study_groups":len({x["dataset"]["study_group"] for x in z}),
    "distinct_source_literal_tested_forms":len({(
      x["paper"]["paper_key"],
      x["chemical"]["tested_form_literal"] or x["material"]["formulation_literal"])
     for x in z if x["chemical"]["tested_form_literal"] or x["material"]["formulation_literal"]}),
    "source_SHA_and_cell_verified":len(z),
    "task_ready_unapproved_candidates":chk["candidate_task_ready_unapproved"],
    "complete_qualified_XY_independent_studies":chk["fully_structured_XY_independent_studies"],
    "source_only_HOLD_n":sum(x["task"]["decision"]=="SOURCE_ONLY_HOLD" for x in z),
    "author_numeric_entries_not_training_Y":len(z),
    "top_required_missing_fields":sorted(
     [{"field":f,"missing_n":info["missing_n"],"reason_counts":info["reason_counts"]}
      for f,info in chk["field_gaps"].items() if info["required_type"]=="REQUIRED"],
       key=lambda x:-x["missing_n"])[:10]})
  info["profiles"].append({"id":name,"version":profile.version,
   "dataset":str(path),"dataset_sha256":_sha(path),
   "native_source_observations":len(records),
   "views":byview,
   "author_original_SHA_confirmed":len(records),
   "source_original_measured_not_qualified":len(records),
   "qualified_numeric_Y":0,
   "independent_SCI_approved":False})
 splits={g:next(iter(v)) for g,v in sorted(all_studies.items())}
 info["source_observations_unique_across_profile_views"]=len(all_original_measured)
 info["profile_view_observations_with_possible_reuse"]=len(all_export_row_keys)
 info["study_groups_unioned_across_profiles"]=len(splits)
 info["partition_map_study_only"]=splits
 info["split_partition_counts"]=dict(Counter(splits.values()))
 info["cross_profile_study_group_leakage_found"]=False
 info["parent_scaffold_grouping_verified"]=False
 info["cross_study_same_parent_possible_and_NOT_PROVEN_ABSENT"]=True
 info["material_batch_grouping_verified"]=False
 info["cross_study_same_material_batch_possible_and_NOT_PROVEN_ABSENT"]=True
 info["provenance_rows_unique"]=len(all_provenances)
 # Distinct verified study-group keys across all views, not a sum of rows.
 # These are merely complete-X/Y candidates, never approved training labels.
 info["fully_structured_XY_studies"]=len(all_task_ready_study_groups)
 info["complete_XY_candidates_unapproved"]=len(all_task_ready_study_groups)
 info["explicit_trainability_gate_status"]="NO_FIT_GOLD0_UNKNOWN_PARENT_SCAFFOLD_BATCH"
 f=dest/"R26_SOURCE_ONLY_DATASET_SNAPSHOT_MANIFEST.json"
 assert not f.exists()
 f.write_text(json.dumps(info,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 return {"status":info["status"],"manifest":str(f),
  "profile_view_records":len(all_export_row_keys),
  "unique_author_native_observations":len(all_original_measured),
  "study_group_partitions":len(splits),"views":{p["id"]:{
   v["view"]:v["original_native_observations"] for v in p["views"]}
   for p in info["profiles"]},"source_only":True,"Gold":0}
