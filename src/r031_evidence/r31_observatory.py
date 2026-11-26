"""Read-only coverage with frozen denominators. No invented human metrics."""
from pathlib import Path
from collections import Counter
from contextlib import closing
import json,sqlite3,math
from . import r30_ledger as L

def frozen(path,expected_sha=None):
 p=Path(path);raw=p.read_bytes()
 if expected_sha and L.digest(raw)!=expected_sha:raise ValueError('R31_FROZEN_MANIFEST_SHA_CHANGED')
 d=json.loads(raw);rows=d['studies'];ids=[x['paper_id'] for x in rows]
 if not rows or len(ids)!=len(set(ids)):raise ValueError('R31_REFERENCE_DENOMINATOR_EMPTY_OR_DUPLICATE')
 return d,L.digest(raw)

def quantile(values,p):
 if not values:return None
 a=sorted(values);return a[max(0,math.ceil(p*len(a))-1)]

def rate(n,d):return dict(numerator=n,denominator=d,value=n/d if d else None)

def report(home,manifest,expected_sha=None,with_profiles=True):
 m,sha=frozen(manifest,expected_sha);groups={};rows=[]
 with closing(L.connect(home,create_tables=False)) as c:
  has_http=c.execute("select 1 from sqlite_master where name='r30_http_receipt'").fetchone()
  for s in m['studies']:
   w=c.execute('select * from r30_work where paper_id=?',(s['paper_id'],)).fetchone()
   if not w:raise ValueError('R31_FROZEN_PAPER_NOT_ACCOUNTED')
   r=dict(s,acquisition=dict(w));key=w['science_paper_key'];parsed=w['state']=='PARSED'
   measures=c.execute('select count(*) from r17_measurement where paper_key=?',(key,)).fetchone()[0] if key else 0
   supplements=c.execute('select count(*) from r17_si_attachment a join r16_parsed_asset p on a.parent_parsed_asset_id=p.parsed_asset_id where p.paper_key=? and a.child_parsed_asset_id is not null',(key,)).fetchone()[0] if key else 0
   declared=c.execute('select sum(supplement_count) from r16_parsed_asset where paper_key=? and source_format in (\'.xml\',\'.nxml\')',(key,)).fetchone()[0] if key else 0
   r.update(parsed=parsed,original_measurements=measures,declared_supplements=declared or 0,parsed_SI_assets=supplements);rows.append(r)
  for stratum in dict.fromkeys(s['stratum'] for s in m['studies']):
   selected=[x for x in rows if x['stratum']==stratum];n=len(selected);nparsed=sum(x['parsed'] for x in selected)
   downloaded=sum(bool(c.execute("select 1 from r30_event where paper_id=? and after_state='LICENSE_CHECK'",(x['paper_id'],)).fetchone()) for x in selected)
   groups[stratum]=dict(metadata_primary_research_candidates=n,native_source_retrieved=rate(downloaded,n),lawfully_verified_parsed=rate(nparsed,n),parsing_after_permission=rate(nparsed,nparsed+sum(x['acquisition']['state']=='HOLD_FORMAT' and bool(x['acquisition']['source_sha']) for x in selected)),SI_papers_with_parsed_asset=rate(sum(x['parsed_SI_assets']>0 for x in selected),sum(x['declared_supplements']>0 for x in selected)),any_supported_numeric_yield=rate(sum(x['original_measurements']>0 for x in selected),n),states=dict(Counter(x['acquisition']['state'] for x in selected)),HOLD_reasons=dict(Counter(x['acquisition']['last_error'] for x in selected if x['acquisition']['last_error'])))
  known={L.normalize_doi(x.get('doi')) for x in m.get('known_reference_catalog',[]) if L.normalize_doi(x.get('doi'))};retrieved=set()
  for r in c.execute('select metadata_json from r30_hit h join r30_page p using(page_id) join r30_query q using(query_id) where q.query_literal in ('+','.join('?' for _ in dict.fromkeys(x['query'] for x in m['studies']))+')',tuple(dict.fromkeys(x['query'] for x in m['studies']))):
   doi=L.normalize_doi(json.loads(r[0]).get('doi'))
   if doi:retrieved.add(doi)
  reference_recall=rate(len(known&retrieved),len(known))
  http=[dict(r) for r in c.execute('select status,bytes,elapsed,taxonomy from r30_http_receipt')] if has_http else []
  gold=c.execute('select count(*) from qualified_training_labels').fetchone()[0]
 profiles={}
 if with_profiles:
  from .r23_dataset import gap
  from .r23_profiles import NAMES
  papers=[x['acquisition']['science_paper_key'] for x in rows if x['parsed']]
  for name in NAMES:profiles[name]=gap(home,name,papers=papers) if papers else {'views':[]}
 return dict(schema='asterloom.coverage-observatory/1',frozen_manifest_sha256=sha,denominator=len(rows),strata=groups,studies=rows,known_catalog_recall_at_retrieved_pages=reference_recall,reference_scope=m.get('reference_definition','Known database studies'),reference_limits='Catalog convenience denominator, query/date mismatch possible; not population recall. Metadata research-article is not independent scientific adjudication.',SI_scope='Counts cover all declared supplements; bounded SI attempts are separate. Zero acquired is not proof none are available.',dedup_human_precision=None,human_recall=None,human_F1=None,profiles=profiles,queue=L.audit(home,initialize=False),http_cumulative=dict(requests=len(http),bytes=sum(r['bytes'] for r in http),p50_seconds=quantile([r['elapsed'] for r in http],.5),p95_seconds=quantile([r['elapsed'] for r in http],.95),statuses=dict(Counter(r['status'] for r in http))),Gold=gold,independent_scientific_reviewer_count=0)
