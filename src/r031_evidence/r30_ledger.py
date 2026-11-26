"""Acquisition 2.0 durable paper work and atomic page checkpoints.

Operational tables extend the existing scientific SQLite; native science rows
and Profile contracts are untouched. Every hit (including duplicate occurrences)
has immutable raw provenance and an accountable paper work item before paging.
"""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import hashlib,json,re,sqlite3,time,uuid
from urllib.parse import unquote
TERMINAL=('PARSED','HOLD_ACCESS','HOLD_LICENSE','HOLD_FORMAT','HOLD_IDENTITY','TERMINAL_NO_SOURCE')
PENDING=('DISCOVERED','RESOLUTION_PENDING','LICENSE_CHECK','ACQUIRE_PENDING','VERIFIED','PARSE_PENDING','RETRY_AT')
STATES=TERMINAL+PENDING
DDL=(
'''CREATE TABLE IF NOT EXISTS r30_query(query_id TEXT PRIMARY KEY,provider TEXT NOT NULL,query_literal TEXT NOT NULL,cursor TEXT,page_number INTEGER NOT NULL DEFAULT 0,exhausted INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS r30_page(page_id TEXT PRIMARY KEY,query_id TEXT NOT NULL REFERENCES r30_query(query_id),request_cursor TEXT NOT NULL,next_cursor TEXT,raw_sha TEXT NOT NULL,raw_path TEXT NOT NULL,hit_count INTEGER NOT NULL,committed REAL NOT NULL,UNIQUE(query_id,request_cursor,raw_sha))''',
'''CREATE TABLE IF NOT EXISTS r30_paper(paper_id TEXT PRIMARY KEY,identity_json TEXT NOT NULL,created REAL NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS r30_alias(namespace TEXT NOT NULL,value TEXT NOT NULL,paper_id TEXT NOT NULL REFERENCES r30_paper(paper_id),PRIMARY KEY(namespace,value))''',
'''CREATE TABLE IF NOT EXISTS r30_hit(hit_id TEXT PRIMARY KEY,page_id TEXT NOT NULL REFERENCES r30_page(page_id),ordinal INTEGER NOT NULL,paper_id TEXT NOT NULL REFERENCES r30_paper(paper_id),metadata_json TEXT NOT NULL,metadata_sha TEXT NOT NULL,legacy_hit_id TEXT,UNIQUE(page_id,ordinal))''',
'''CREATE TABLE IF NOT EXISTS r30_work(paper_id TEXT PRIMARY KEY REFERENCES r30_paper(paper_id),state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,retry_at REAL,lease_token TEXT,lease_until REAL,owner TEXT,last_error TEXT,source_sha TEXT,source_path TEXT,source_url TEXT,science_paper_key TEXT,updated REAL NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS r30_event(event_id INTEGER PRIMARY KEY AUTOINCREMENT,paper_id TEXT NOT NULL REFERENCES r30_paper(paper_id),before_state TEXT,after_state TEXT NOT NULL,attempt INTEGER NOT NULL,lease_token TEXT,error TEXT,receipt_json TEXT NOT NULL,at REAL NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS r30_source_version(asset_id TEXT PRIMARY KEY,paper_id TEXT NOT NULL REFERENCES r30_paper(paper_id),url TEXT NOT NULL,source_version TEXT,license_literal TEXT,sha256 TEXT NOT NULL,path TEXT NOT NULL,mime TEXT,parent_asset_id TEXT,verified REAL NOT NULL)''',
'''CREATE INDEX IF NOT EXISTS r30_work_claim ON r30_work(state,retry_at,lease_until)''',
'''CREATE INDEX IF NOT EXISTS r30_hit_paper ON r30_hit(paper_id,page_id)''')

def digest(value):return hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()
def canonical_json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def db_path(home):
 root=Path(home).expanduser().resolve();return root/'science.sqlite3' if root.name=='state' else root/'state/science.sqlite3'
def connect(home,create_tables=True):
 db=db_path(home)
 if not db.is_file():raise ValueError('R30_EXISTING_EXPLICIT_SCIENCE_DATABASE_REQUIRED')
 c=sqlite3.connect(db,timeout=30,isolation_level=None);c.row_factory=sqlite3.Row
 c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=30000')
 if c.execute('PRAGMA user_version').fetchone()[0]!=832:
  c.close();raise ValueError('R30_ONLY_EXPLICIT_SCHEMA832_DEVELOPMENT_HOME')
 if create_tables:
  for sql in DDL:c.execute(sql)
  for table in ('r30_page','r30_hit','r30_event','r30_source_version'):
   for op in ('UPDATE','DELETE'):
    c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'R30_IMMUTABLE_RECEIPT');END")
 return c

def normalize_doi(value):
 s=unquote(str(value or '')).strip()
 s=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)','',s,flags=re.I).strip().lower()
 return s if re.fullmatch(r'10\.\d{4,9}/\S+',s) else None

def identifiers(hit):
 out=[];doi=normalize_doi(hit.get('doi'))
 if doi:out.append(('doi',doi))
 pm=str(hit.get('pmcid') or '').upper().strip('/')
 if pm.isdigit():pm='PMC'+pm
 if re.fullmatch(r'PMC[0-9]+',pm):out.append(('pmcid',pm))
 pmid=str(hit.get('pmid') or '').rstrip('/').split('/')[-1]
 if pmid.isdigit():out.append(('pmid',pmid))
 oa=str(hit.get('openalex_id') or '').rstrip('/').split('/')[-1].upper()
 if re.fullmatch(r'W[0-9]+',oa):out.append(('openalex',oa))
 return out

def query_key(provider,query):return 'r30:q:'+digest(provider+'\0'+query)[:32]
def progress(home,provider,query):
 with closing(connect(home)) as c:
  row=c.execute('SELECT * FROM r30_query WHERE query_id=?',(query_key(provider,query),)).fetchone()
  return dict(row) if row else dict(cursor='*',page_number=0,exhausted=0)

def _paper(c,hit,page_id,ordinal,now,alias_cache=None):
 tokens=identifiers(hit);found=({alias_cache[t] for t in tokens if t in alias_cache} if alias_cache is not None else {r[0] for ns,v in tokens for r in c.execute('SELECT paper_id FROM r30_alias WHERE namespace=? AND value=?',(ns,v))})
 conflict=len(found)>1
 if found:
  pid=sorted(found)[0]
  prior=c.execute("SELECT value FROM r30_alias WHERE paper_id=? AND namespace='doi'",(pid,)).fetchall()
  conflict=conflict or bool(prior and any(ns=='doi' and v not in {r[0] for r in prior} for ns,v in tokens))
 else:
  stable='|'.join(tokens[0]) if tokens else page_id+'|'+str(ordinal)
  pid='r30:paper:'+digest(stable)[:32]
  c.execute('INSERT OR IGNORE INTO r30_paper VALUES(?,?,?)',(pid,canonical_json(dict(tokens=tokens,title=hit.get('title'))),now))
 if not conflict:
  for ns,v in tokens:
   c.execute('INSERT OR IGNORE INTO r30_alias VALUES(?,?,?)',(ns,v,pid))
   if alias_cache is not None:alias_cache[(ns,v)]=pid
 state='HOLD_IDENTITY' if conflict else 'DISCOVERED'
 c.execute('INSERT OR IGNORE INTO r30_work(paper_id,state,last_error,updated) VALUES(?,?,?,?)',
  (pid,state,'CROSS_PROVIDER_IDENTIFIER_CONFLICT' if conflict else None,now))
 if conflict:
  c.execute("UPDATE r30_work SET state='HOLD_IDENTITY',last_error='CROSS_PROVIDER_IDENTIFIER_CONFLICT',updated=? WHERE paper_id=? AND lease_token IS NULL",(now,pid))
 return pid

def record_page(c,*,provider,query,cursor,next_cursor,raw_sha,raw_path,hits,legacy_ids=None,now=None,fault=None,identity_strategy='prefetch'):
 """Call inside the SAME transaction as legacy discovery rows; advance last."""
 now=time.time() if now is None else now;cursor=cursor or '*';qid=query_key(provider,query)
 path=Path(raw_path)
 if not path.is_file() or digest(path.read_bytes())!=raw_sha:raise ValueError('R30_PROVIDER_RAW_SHA_REQUIRED')
 page_id='r30:page:'+digest(qid+'\0'+cursor+'\0'+raw_sha)[:32]
 prior=c.execute('SELECT hit_count FROM r30_page WHERE page_id=?',(page_id,)).fetchone()
 if prior:
  if prior[0]!=len(hits):raise ValueError('R30_REPLAY_HIT_COUNT_DRIFT')
  return page_id
 c.execute('INSERT OR IGNORE INTO r30_query VALUES(?,?,?,?,0,0,?)',(qid,provider,query,'*',now))
 p=c.execute('SELECT cursor,exhausted FROM r30_query WHERE query_id=?',(qid,)).fetchone()
 if p[1] or p[0]!=cursor:raise ValueError('R30_STALE_OR_EXHAUSTED_PAGE_REFUSE_CURSOR_ADVANCE')
 c.execute('INSERT INTO r30_page VALUES(?,?,?,?,?,?,?,?)',(page_id,qid,cursor,next_cursor,raw_sha,str(path.resolve()),len(hits),now))
 if identity_strategy not in ('lookup','prefetch'):raise ValueError('R32_UNKNOWN_IDENTITY_STRATEGY')
 alias_cache=None
 if identity_strategy=='prefetch':
  if len(hits)>100:raise ValueError('R32_PREFETCH_PAGE_LIMIT_100')
  alias_cache={};by_namespace={}
  for hit in hits:
   for ns,value in identifiers(hit):by_namespace.setdefault(ns,set()).add(value)
  for ns,values in by_namespace.items():
   if values:
    rows=c.execute('SELECT namespace,value,paper_id FROM r30_alias WHERE namespace=? AND value IN ('+','.join('?' for _ in values)+')',(ns,*sorted(values)))
    alias_cache.update({(r[0],r[1]):r[2] for r in rows})
 for i,hit in enumerate(hits):
  pid=_paper(c,hit,page_id,i,now,alias_cache);raw=canonical_json(hit)
  c.execute('INSERT INTO r30_hit VALUES(?,?,?,?,?,?,?)',('r30:hit:'+digest(page_id+'|'+str(i))[:32],page_id,i,pid,raw,digest(raw),(legacy_ids or [None]*len(hits))[i]))
  if fault and i==fault.get('after_hit'):raise RuntimeError('R30_INJECTED_PAGE_ROLLBACK')
 exhausted=not hits or not next_cursor or next_cursor==cursor
 c.execute('UPDATE r30_query SET cursor=?,page_number=page_number+1,exhausted=?,updated=? WHERE query_id=?',
  (None if exhausted else next_cursor,int(exhausted),now,qid))
 return page_id

def ingest_page(home,**kwargs):
 with closing(connect(home)) as c:
  c.execute('BEGIN IMMEDIATE')
  try:r=record_page(c,**kwargs);c.commit();return r
  except BaseException:c.rollback();raise

def _event(c,pid,before,after,attempt,token,error=None,receipt=None,now=None):
 c.execute('INSERT INTO r30_event(paper_id,before_state,after_state,attempt,lease_token,error,receipt_json,at) VALUES(?,?,?,?,?,?,?,?)',
 (pid,before,after,attempt,token,error,canonical_json(receipt or {}),time.time() if now is None else now))

def claim(home,limit=1,owner='interactive',lease_seconds=600,now=None,paper_ids=None):
 if not 1<=limit<=100 or not 1<=lease_seconds<=3600:raise ValueError('R30_BOUNDED_CLAIM_REQUIRED')
 now=time.time() if now is None else now;result=[]
 with closing(connect(home)) as c:
  c.execute('BEGIN IMMEDIATE')
  try:
   expired=c.execute('SELECT * FROM r30_work WHERE lease_token IS NOT NULL AND lease_until<=?',(now,)).fetchall()
   for row in expired:
    target='HOLD_ACCESS' if row['attempts']>=row['max_attempts'] else 'RETRY_AT'
    c.execute('UPDATE r30_work SET state=?,lease_token=NULL,lease_until=NULL,owner=NULL,retry_at=?,last_error=?,updated=? WHERE paper_id=?',
      (target,now,'EXPIRED_LEASE_PROCESS_INTERRUPTED',now,row['paper_id']))
    _event(c,row['paper_id'],row['state'],target,row['attempts'],row['lease_token'],'EXPIRED_LEASE_PROCESS_INTERRUPTED',now=now)
   args=[now];filter_sql=''
   if paper_ids is not None:
    if not paper_ids:c.commit();return []
    if len(paper_ids)>1000:raise ValueError('R30_CLAIM_SELECTION_BUDGET')
    filter_sql=' AND paper_id IN ('+','.join('?' for _ in paper_ids)+')';args+=list(paper_ids)
   args.append(limit)
   rows=c.execute("SELECT * FROM r30_work WHERE lease_token IS NULL AND attempts<max_attempts AND state IN ('DISCOVERED','RETRY_AT','VERIFIED','PARSE_PENDING') AND (retry_at IS NULL OR retry_at<=?)"+filter_sql+' ORDER BY updated,paper_id LIMIT ?',args).fetchall()
   for row in rows:
    token=uuid.uuid4().hex;stage='PARSE_PENDING' if row['source_sha'] else 'RESOLUTION_PENDING'
    c.execute('UPDATE r30_work SET state=?,attempts=attempts+1,lease_token=?,lease_until=?,owner=?,retry_at=NULL,updated=? WHERE paper_id=?',
       (stage,token,now+lease_seconds,owner,now,row['paper_id']))
    _event(c,row['paper_id'],row['state'],stage,row['attempts']+1,token,now=now)
    out=dict(row);out.update(state=stage,attempts=row['attempts']+1,lease_token=token);result.append(out)
   c.commit()
  except BaseException:c.rollback();raise
 return result

def transition(home,paper_id,token,state,*,error=None,retry_at=None,receipt=None,source=None,now=None):
 if state not in STATES:raise ValueError('R30_INVALID_STATE')
 now=time.time() if now is None else now
 with closing(connect(home)) as c:
  c.execute('BEGIN IMMEDIATE')
  try:
   row=c.execute('SELECT * FROM r30_work WHERE paper_id=?',(paper_id,)).fetchone()
   if not row or row['lease_token']!=token or row['lease_until'] is None or row['lease_until']<=now:raise ValueError('R30_STALE_OR_EXPIRED_LEASE_TOKEN')
   # A local token-bucket denial made no network attempt. Preserve the
   # network retry budget while keeping the claim/defer audit trail.
   if state=='RETRY_AT' and error in ('LOCAL_HOST_THROTTLE','LOCAL_OPERATION_BUDGET_HOLD','LOCAL_OPERATION_STOP_HOLD'):
    c.execute('UPDATE r30_work SET attempts=MAX(0,attempts-1) WHERE paper_id=?',(paper_id,))
    row=dict(row);row['attempts']=max(0,row['attempts']-1)
   if state=='RETRY_AT' and row['attempts']>=row['max_attempts']:state='HOLD_ACCESS';error='RETRY_BUDGET_EXHAUSTED:'+str(error)
   values=dict(source or {})
   if state=='PARSED':
    sha=values.get('sha256') or row['source_sha'];key=values.get('science_paper_key') or row['science_paper_key']
    assets=c.execute('SELECT original_path,ir_path,ir_sha FROM r16_parsed_asset WHERE paper_key=? AND original_sha=?',(key,sha)).fetchall() if sha and key else []
    if not any(Path(a[0]).is_file() and digest(Path(a[0]).read_bytes())==sha and Path(a[1]).is_file() and digest(Path(a[1]).read_bytes())==a[2] for a in assets):
     raise ValueError('R30_PARSED_REQUIRES_VERIFIED_ORIGINAL_AND_WORKER_IR')
   done=state in TERMINAL or state=='RETRY_AT'
   c.execute('UPDATE r30_work SET state=?,last_error=?,retry_at=?,lease_token=?,lease_until=?,owner=?,source_sha=COALESCE(?,source_sha),source_path=COALESCE(?,source_path),source_url=COALESCE(?,source_url),science_paper_key=COALESCE(?,science_paper_key),updated=? WHERE paper_id=?',
    (state,error,retry_at,None if done else token,None if done else row['lease_until'],None if done else row['owner'],values.get('sha256'),values.get('path'),values.get('url'),values.get('science_paper_key'),now,paper_id))
   _event(c,paper_id,row['state'],state,row['attempts'],token,error,receipt,now)
   c.commit()
  except BaseException:c.rollback();raise
 return state

def metadata(home,paper_id):
 with closing(connect(home)) as c:
  rows=c.execute('SELECT metadata_json FROM r30_hit WHERE paper_id=? ORDER BY hit_id',(paper_id,)).fetchall()
  result=[json.loads(r[0]) for r in rows]
  if c.execute("SELECT 1 FROM sqlite_master WHERE name='r30_legacy_hit'").fetchone():
   result += [json.loads(r[0]) for r in c.execute('SELECT metadata_json FROM r30_legacy_hit WHERE paper_id=?',(paper_id,))]
  return result

def add_source(home,paper_id,url,sha256,path,mime,license_literal,version='publisher-current',parent=None):
 if digest(Path(path).read_bytes())!=sha256:raise ValueError('R30_ASSET_BYTE_SHA_MISMATCH')
 ident='r30:asset:'+digest(paper_id+'\0'+url+'\0'+version+'\0'+sha256)[:32]
 with closing(connect(home)) as c:
  c.execute('INSERT OR IGNORE INTO r30_source_version VALUES(?,?,?,?,?,?,?,?,?,?)',
   (ident,paper_id,url,version,license_literal,sha256,str(Path(path).resolve()),mime,parent,time.time()))
 return ident

def audit(home,initialize=True):
 with closing(connect(home,create_tables=initialize)) as c:
  counts={r[0]:r[1] for r in c.execute('SELECT state,count(*) FROM r30_work GROUP BY state')}
  papers=c.execute('SELECT count(*) FROM r30_paper').fetchone()[0];work=c.execute('SELECT count(*) FROM r30_work').fetchone()[0]
  missing=c.execute('SELECT count(*) FROM r30_paper p LEFT JOIN r30_work w USING(paper_id) WHERE w.paper_id IS NULL').fetchone()[0]
  page_mismatch=c.execute('SELECT count(*) FROM r30_page p WHERE p.hit_count!=(SELECT count(*) FROM r30_hit h WHERE h.page_id=p.page_id)').fetchone()[0]
  terminal=sum(counts.get(s,0) for s in TERMINAL);pending=sum(counts.get(s,0) for s in PENDING)
  unknown=sum(v for k,v in counts.items() if k not in STATES)
  has_legacy=c.execute("SELECT 1 FROM sqlite_master WHERE name='r30_legacy_hit'").fetchone()
  legacy=c.execute('SELECT count(*) FROM r30_legacy_hit').fetchone()[0] if has_legacy else 0
  unmapped=c.execute('''SELECT count(*) FROM r15_paper_hit h WHERE NOT EXISTS(SELECT 1 FROM r30_hit n WHERE n.legacy_hit_id=h.hit_id)'''+(' AND NOT EXISTS(SELECT 1 FROM r30_legacy_hit n WHERE n.hit_id=h.hit_id)' if has_legacy else '')).fetchone()[0]
  return dict(schema='asterloom.acquisition-conservation/2',metadata_hit_occurrences=c.execute('SELECT count(*) FROM r30_hit').fetchone()[0],legacy_hit_occurrences=legacy,legacy_unmapped_hits=unmapped,dedup_discovered=papers,terminal=terminal,pending_or_inflight=pending,states=counts,work_items=work,missing_work_items=missing,page_cardinality_errors=page_mismatch,unknown_states=unknown,conservation_pass=papers==terminal+pending==work and missing==page_mismatch==unknown==unmapped==0,leased=c.execute('SELECT count(*) FROM r30_work WHERE lease_token IS NOT NULL').fetchone()[0],source_versions=c.execute('SELECT count(*) FROM r30_source_version').fetchone()[0],pages=[dict(r) for r in c.execute('SELECT provider,query_literal,cursor,page_number,exhausted FROM r30_query ORDER BY query_id')],Gold=0)

def reconcile_legacy(home,raw_roots=(),limit=2000):
 """Make pre-R30 hits explicitly accountable. Never invent missing raw bytes.
 Existing parsed IR is carried as PARSED only after its original file SHA
 verifies; unresolved metadata with missing original response becomes HOLD.
 """
 if not 1<=limit<=10000:raise ValueError('R30_LEGACY_RECONCILE_LIMIT')
 raw_index={}
 for root in raw_roots:
  for f in Path(root).glob('*.json'):
   if f.is_file() and f.stat().st_size<=8000000:raw_index[digest(f.read_bytes())]=str(f.resolve())
 with closing(connect(home)) as c:
  c.execute('''CREATE TABLE IF NOT EXISTS r30_legacy_hit(hit_id TEXT PRIMARY KEY,paper_id TEXT NOT NULL REFERENCES r30_paper(paper_id),raw_sha TEXT,raw_path TEXT,provenance_state TEXT NOT NULL,metadata_json TEXT NOT NULL)''')
  rows=c.execute('''SELECT h.*,b.provider,b.raw_sha FROM r15_paper_hit h JOIN r15_discovery_batch b USING(batch_id)
    WHERE NOT EXISTS(SELECT 1 FROM r30_hit n WHERE n.legacy_hit_id=h.hit_id)
    AND NOT EXISTS(SELECT 1 FROM r30_legacy_hit n WHERE n.hit_id=h.hit_id) ORDER BY h.hit_id LIMIT ?''',(limit,)).fetchall()
  now=time.time();added=0;holds=0;parsed=0
  c.execute('BEGIN IMMEDIATE')
  try:
   for r in rows:
    hit={k:r[k] for k in ('pmcid','doi','pmid','openalex_id','title','year')};hit['provider']=r['provider']
    path=raw_index.get(r['raw_sha'])
    pid=_paper(c,hit,'legacy:'+r['batch_id'],0,now)
    c.execute('INSERT INTO r30_legacy_hit VALUES(?,?,?,?,?,?)',(r['hit_id'],pid,r['raw_sha'],path,'ORIGINAL_PROVIDER_RAW_VERIFIED' if path else 'LEGACY_SQLITE_METADATA_RAW_UNAVAILABLE',canonical_json(hit)))
    current=c.execute('SELECT state FROM r30_work WHERE paper_id=?',(pid,)).fetchone()[0]
    if current=='DISCOVERED':
     assets=c.execute("SELECT original_path,original_sha FROM r16_parsed_asset WHERE paper_key=? AND source_format IN ('.xml','.nxml')",(hit.get('pmcid'),)).fetchall()
     verified=any(Path(a[0]).is_file() and digest(Path(a[0]).read_bytes())==a[1] for a in assets)
     if verified:target='PARSED';parsed+=1
     elif not path:target='HOLD_ACCESS';holds+=1
     else:target='DISCOVERED'
     c.execute('UPDATE r30_work SET state=?,last_error=?,updated=? WHERE paper_id=?',(target,'INHERITED_PARSED_SOURCE_SHA_VERIFIED' if verified else 'LEGACY_PROVIDER_RAW_MISSING' if not path else None,now,pid))
     _event(c,pid,current,target,0,None,receipt={'legacy_hit_id':r['hit_id'],'raw_path':path},now=now)
    added+=1
   c.commit()
  except BaseException:c.rollback();raise
 return dict(legacy_occurrences_accounted=added,inherited_parsed=parsed,missing_raw_holds=holds,not_a_new_source_or_scientific_result=True)
