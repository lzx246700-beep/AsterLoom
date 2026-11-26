"""Independent A/B blank tasks from immutable R33 sources, no fabricated humans."""
from pathlib import Path
import csv,json,shlex
from .r33_evaluation import load_packet,FIELDS,ERRORS,write_new
from .r30_ledger import digest

def prepare(packet_file,packet_sha,output,core_home):
 packet=load_packet(packet_file,packet_sha);out=Path(output)
 if out.exists():raise ValueError('R37_NEW_BLIND_ASSIGNMENT_DIRECTORY_REQUIRED')
 for sha,a in packet['source_assets'].items():
  if digest(Path(a['path']).read_bytes())!=sha:raise ValueError('R37_FROZEN_ORIGINAL_SHA_DRIFT')
 out.mkdir(parents=True);assignments=[]
 for slot in ('A','B'):
  folder=out/('reviewer_'+slot);folder.mkdir();tasks=[dict(case_id=c['case_id'],paper_key=c['paper_key'],source_sha256=c['source_sha256'],source_locator=c['source_locator'],fields_to_annotate=list(FIELDS)) for c in packet['cases']]
  data=dict(schema='asterloom.independent-blind-assignment/1',reviewer_slot=slot,reviewer_identity=None,signature=None,canonical_packet_sha256=packet_sha,reference_status='HUMAN_REVIEW_PENDING',source_assets=packet['source_assets'],cases=tasks,Gold=0)
  write_new(folder/'blind_assignment.json',data)
  with (folder/'BLANK_ANNOTATIONS.csv').open('x',newline='') as f:
   cols=['reviewer_slot','reviewer_identity','case_id','paper_key','field','decision','author_value','source_sha256','source_locator','error_code','reason','signature','date'];writer=csv.DictWriter(f,fieldnames=cols);writer.writeheader()
   for c in tasks:
    for field in FIELDS:writer.writerow(dict(reviewer_slot=slot,case_id=c['case_id'],paper_key=c['paper_key'],field=field,decision='UNREVIEWED',source_sha256=c['source_sha256'],source_locator=c['source_locator']))
  command='"${R031_BIN:-r031}" --home '+shlex.quote(str(Path(core_home).resolve()))+' review serve --packet '+shlex.quote(str(Path(packet_file).resolve()))+' --expected-sha '+shlex.quote(packet_sha)+' --store '+shlex.quote(str(folder/'real_human_events'))+' --minutes 15\n'
  (folder/'OPEN_BLIND_REVIEW.command').write_text('#!/bin/sh\nset -eu\n'+command);(folder/'OPEN_BLIND_REVIEW.command').chmod(0o755)
  (folder/'README.md').write_text('真实审阅者 '+slot+' 的独立盲标任务。先填写本人身份；此角色名不是科学签名。请勿读取另一审阅者输出或系统候选。CSV作者值留空不是0。原件按SHA核对，保留待核验、作者未报告和错误的区别。\n\n本机可通过OPEN_BLIND_REVIEW.command打开15分钟盲标页。R031_BIN指向实际安装的r031。不要使用--test-mode或--show-candidates进行正式盲标。UI保存仅形成评测意见，需负责人裁决冲突及明确发布reference；不批准Gold。软件不能认证专业身份或防止在软件外查看候选。\n')
  assignments.append(dict(slot=slot,papers=packet['paper_count'],cases=len(tasks),field_judgements=len(tasks)*len(FIELDS),assignment_sha256=digest((folder/'blind_assignment.json').read_bytes()),CSV_sha256=digest((folder/'BLANK_ANNOTATIONS.csv').read_bytes()),human_annotations=0))
 result=dict(status='HUMAN_REVIEW_PENDING',canonical_packet_sha256=packet_sha,assignments=assignments,error_taxonomy=ERRORS,real_reviewers_assigned=0,human_answers=0,scientific_precision=None,scientific_recall=None,scientific_F1=None,effort_estimate='Planning assumption only: 71 cases x6-12min +24 papers x10-20min context = about11-22h per reviewer, not measured',third_adjudicator_required=True,model_judge_is_not_truth=True,Gold=0);write_new(out/'HUMAN_REFERENCE_STATUS.json',result);return result
