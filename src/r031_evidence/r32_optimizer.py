"""Bounded offline strategy evaluator on an externally SHA-pinned corpus.
No generated code, models, network, production configuration or Gold updates.
"""
from pathlib import Path
from contextlib import closing
import json,statistics,tempfile,time
from . import r30_ledger as L
from .r30_providers import get
from .r21_config import setup

def benchmark(manifest,expected_sha,rounds=3):
 path=Path(manifest);raw=path.read_bytes()
 if not expected_sha or L.digest(raw)!=expected_sha:raise ValueError('R32_FIXED_BENCHMARK_SHA_REQUIRED_OR_CHANGED')
 if not 1<=rounds<=3:raise ValueError('R32_BENCHMARK_ROUNDS_1_TO_3')
 m=json.loads(raw);pages=m['pages']
 if not 1<=len(pages)<=50:raise ValueError('R32_BENCHMARK_MAX50_PAGES')
 inputs=[];total_bytes=0
 for p in pages:
  f=Path(p['raw_path']);total_bytes+=f.stat().st_size
  if total_bytes>30000000 or f.stat().st_size>4000000:raise ValueError('R32_BENCHMARK_RAW_BYTE_BUDGET')
  data=f.read_bytes()
  if L.digest(data)!=p['raw_sha']:raise ValueError('R32_BENCHMARK_PROVIDER_RAW_SHA_DRIFT')
  hits,_=get('EUROPE_PMC').parse(data)
  if len(hits)>100:raise ValueError('R32_BENCHMARK_MAX100_HITS_PAGE')
  inputs.append((p,hits))
 runs=[]
 for round in range(rounds):
  for strategy in ['lookup','prefetch'] if round%2==0 else ['prefetch','lookup']:
   with tempfile.TemporaryDirectory(prefix='asterloom_offline_strategy_') as h:
    setup(h);stats=dict(alias_selects=0,total_sql=0);start=time.perf_counter()
    def trace(sql):
     stats['total_sql']+=1
     if sql.lstrip().upper().startswith('SELECT') and 'FROM r30_alias' in sql:stats['alias_selects']+=1
    with closing(L.connect(h)) as c:
     c.set_trace_callback(trace)
     for p,hits in inputs:
      c.execute('BEGIN IMMEDIATE')
      try:
       L.record_page(c,provider=p['provider'],query=p['query'],cursor=p['request_cursor'],next_cursor=p['next_cursor'],raw_sha=p['raw_sha'],raw_path=p['raw_path'],hits=hits,identity_strategy=strategy);c.commit()
      except BaseException:c.rollback();raise
     elapsed=time.perf_counter()-start;c.set_trace_callback(None)
     payload={name:[tuple(r) for r in c.execute(sql)] for name,sql in dict(papers='select paper_id,identity_json from r30_paper order by paper_id',aliases='select * from r30_alias order by namespace,value',hits='select hit_id,paper_id,metadata_sha from r30_hit order by hit_id',work='select paper_id,state,last_error from r30_work order by paper_id').items()}
    a=L.audit(h)
    if not a['conservation_pass'] or a['dedup_discovered']!=m['real_dedup_papers'] or a['metadata_hit_occurrences']!=m['real_occurrences']:raise ValueError('R32_BENCHMARK_DENOMINATOR_OR_CONSERVATION_MISMATCH')
    runs.append(dict(round=round,strategy=strategy,wall_seconds=elapsed,canonical_fingerprint=L.digest(L.canonical_json(payload)),papers=a['dedup_discovered'],occurrences=a['metadata_hit_occurrences'],**stats))
 baseline=[x for x in runs if x['strategy']=='lookup'];candidate=[x for x in runs if x['strategy']=='prefetch'];b=statistics.median(x['wall_seconds'] for x in baseline);c=statistics.median(x['wall_seconds'] for x in candidate);reduction=1-candidate[0]['alias_selects']/baseline[0]['alias_selects'] if baseline[0]['alias_selects'] else 0;equivalent=len({x['canonical_fingerprint'] for x in runs})==1
 return dict(schema='asterloom.offline-strategy-benchmark/1',input_manifest_sha256=expected_sha,identity_equivalent=equivalent,runs=runs,baseline_median_seconds=b,candidate_median_seconds=c,alias_query_reduction=reduction,decision='CANDIDATE_MEETS_FIXED_ENGINEERING_THRESHOLDS_FOR_REVIEW' if equivalent and reduction>=.8 and c<=b*1.1 else 'KEEP_BASELINE',automatically_changes_production=False,network_requests=0,Gold=0)
