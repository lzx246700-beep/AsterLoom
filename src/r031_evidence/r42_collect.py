"""Resumable, local-first high-volume discovery and official OA JATS acquisition.

Run with a dedicated R41 Home. Metadata licensing is only a prefilter; raw XML
must independently confirm identity, article type and license before publication.
"""
import argparse
import concurrent.futures
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

from .r30_http import retry_delay
from .r30_ledger import normalize_doi

EPMC = 'https://www.ebi.ac.uk/europepmc/webservices/rest/'
EUTIL = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
ALEX = 'https://api.openalex.org/works'
PMC = 'https://pmc-oa-opendata.s3.amazonaws.com/'
PHENO = '(TITLE_ABS:"ATP content" OR TITLE_ABS:"intracellular ATP levels")'
END = ' AND OPEN_ACCESS:Y AND IN_PMC:Y AND NOT PUB_TYPE:"Review"'
SCHEMA = """
CREATE TABLE IF NOT EXISTS r42_paper(paper TEXT PRIMARY KEY, pmcid TEXT, pmid TEXT, doi TEXT, title TEXT NOT NULL,
 license TEXT, status TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0, lease TEXT, lease_until REAL, path TEXT, sha TEXT, reason TEXT, via TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS r42_by_pmcid ON r42_paper(pmcid) WHERE pmcid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS r42_by_pmid ON r42_paper(pmid) WHERE pmid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS r42_by_doi ON r42_paper(doi) WHERE doi IS NOT NULL;
CREATE TABLE IF NOT EXISTS r42_query(id TEXT PRIMARY KEY,lane TEXT,provider TEXT,query TEXT,cursor TEXT,
 priority INTEGER,page INTEGER DEFAULT 0,state TEXT DEFAULT 'PENDING',retry_at REAL DEFAULT 0,attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS r42_identity_hold(query_id TEXT,ids TEXT,matched_keys TEXT,PRIMARY KEY(query_id,ids));
CREATE TABLE IF NOT EXISTS r42_query_lease(id TEXT PRIMARY KEY,token TEXT,until REAL);
CREATE TABLE IF NOT EXISTS r42_support_page(url TEXT,sha TEXT,path TEXT,PRIMARY KEY(url,sha));
CREATE TABLE IF NOT EXISTS r42_hit(query_id TEXT,paper TEXT,PRIMARY KEY(query_id,paper));
CREATE TABLE IF NOT EXISTS r42_page(query_id TEXT,cursor TEXT,at REAL,count INTEGER,new_papers INTEGER,
 url TEXT,sha TEXT,path TEXT,PRIMARY KEY(query_id,cursor));
CREATE TABLE IF NOT EXISTS r42_http(id TEXT PRIMARY KEY,at REAL,url TEXT,status INTEGER,bytes INTEGER,elapsed REAL,error TEXT);
CREATE TABLE IF NOT EXISTS r42_pacing(host TEXT PRIMARY KEY,next_at REAL DEFAULT 0,blocked REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS r42_batch(batch TEXT PRIMARY KEY,kind TEXT,started REAL,finished REAL,receipt TEXT);
CREATE TABLE IF NOT EXISTS r42_fetch_attempt(token text primary key,batch text not null,paper text not null,status text not null,started real,finished real,via text);
CREATE VIEW IF NOT EXISTS r42_batch_member AS SELECT batch,paper,status,via FROM r42_fetch_attempt;
CREATE TRIGGER IF NOT EXISTS r42_finish_attempt AFTER UPDATE OF status ON r42_paper
 WHEN old.lease IS NOT NULL AND new.lease IS NULL BEGIN
 UPDATE r42_fetch_attempt SET status=new.status,finished=unixepoch('now'),via=new.via WHERE token=old.lease; END;
"""
# r42_available is created by ensure_available_view(). The paper primary key is
# not always the PMCID column, so the view must project r42_paper.paper.
AVAILABLE_VIEW = """
DROP VIEW IF EXISTS r42_available;
CREATE VIEW r42_available AS
 SELECT paper,doi,path,sha FROM r42_paper
 WHERE status='PARSED' AND path IS NOT NULL AND sha IS NOT NULL;
"""
def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def connect(home):
    c = sqlite3.connect(home/'state/science.sqlite3',timeout=90)
    c.row_factory = sqlite3.Row
    c.execute('pragma busy_timeout=90000')
    return c

def ensure_available_view(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='r42_paper'").fetchone():
        raise ValueError("existing R42 paper ledger required")
    c.executescript(AVAILABLE_VIEW)

def init(home):
    if not (home/'state/science.sqlite3').is_file():
        raise ValueError("existing Evidence Core required")
    with closing(connect(home)) as c:
        c.executescript(SCHEMA)
        ensure_available_view(c)

def add_query(c, lane, provider, q, priority):
    key=digest((provider+'\n'+lane+'\n'+q).encode())
    cursor='0' if provider=='PUBMED' else '*'
    c.execute('insert or ignore into r42_query(id,lane,provider,query,cursor,priority) values(?,?,?,?,?,?)',
              (key,lane,provider,q,cursor,priority))

def seed_queries(home, labels):
    data=[json.loads(line) for line in labels.read_bytes().splitlines()]
    names=[r['name'] for r in sorted(data,key=lambda r:(r.get('weak_label_1_increase_0_decrease') is None,r['name']))]
    with closing(connect(home)) as c,c:
        add_query(c,'phenotype','EPMC',PHENO+END,1)
        add_query(c,'molecule','EPMC','(resveratrol OR melatonin OR apigenin OR shikonin) AND ("ATP content" OR "intracellular ATP levels")'+END,0)
        for i in range(0,len(names),6):
            group=names[i:i+6]
            # Exact names in title/abstract, endpoint text anywhere in the original.
            terms=' OR '.join('TITLE_ABS:"'+n.replace('"',' ')+'"' for n in group)
            add_query(c,'molecule','EPMC','('+terms+') AND ("ATP content" OR "ATP levels" OR "ATP concentration" OR "oxygen consumption rate")'+END,2+i//6)
        add_query(c,'phenotype','EPMC','(macrophage OR monocyte) AND ("intracellular ATP" OR "ATP assay" OR "ATP content")'+END,4)
        add_query(c,'phenotype','PUBMED','("intracellular ATP"[Title/Abstract] OR "ATP content"[Title/Abstract]) AND free full text[sb] NOT review[pt]',5)
        add_query(c,'phenotype','OPENALEX','intracellular ATP content treatment cells',6)

def seed_existing(home):
    """Bring known study IDs and verified unambiguous synonyms to discovery."""
    with closing(connect(home)) as c,c:
        if not c.execute("select 1 from sqlite_master where name='r42_seed_scope'").fetchone():return
        keys=c.execute("""select doi,max(priority) as priority from (
            select e.doi,3+coalesce(s.weak is not null,0)+(lower(e.endpoint) like '%atp%') as priority
            from r42_prior_evidence e join r42_seed_scope s using(molecule)
            union all select doi,1 from r42_seed_prior_signal) where doi is not null and doi!='' group by doi order by priority desc,doi""").fetchall()
        ids=[r[0] for r in keys if re.fullmatch(r'10[.]\d{4,9}/[^"\s]{1,170}',r[0])]
        for i in range(0,len(ids),25):
            q=' OR '.join('DOI:"'+d+'"' for d in ids[i:i+25])
            add_query(c,'molecule','EPMC','('+q+') AND OPEN_ACCESS:Y AND IN_PMC:Y',-2+i//25)
        seen=set()
        alias_rows=c.execute("""select n.name from r42_names n join r42_molecule m using(molecule)
            join r42_seed_scope s using(molecule) where lower(n.name)!=lower(m.preferred_name)
            order by s.weak is null,m.molecule,n.name""")
        names=[r[0] for r in alias_rows if 4<=len(r[0])<=60 and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9 (),'-]+",r[0])]
        for i in range(0,len(names),6):
            q=' OR '.join('TITLE_ABS:"'+n+'"' for n in names[i:i+6])
            add_query(c,'molecule','EPMC','('+q+') AND ("ATP content" OR "ATP levels")'+END,14+i//6)

def pace(home,host):
    interval={'eutils.ncbi.nlm.nih.gov':0.55,'api.openalex.org':0.6,
              'www.ebi.ac.uk':0.4,'pmc-oa-opendata.s3.amazonaws.com':0.25}[host]
    while True:
        with closing(connect(home)) as c,c:
            c.execute('begin immediate')
            c.execute('insert or ignore into r42_pacing(host) values(?)',(host,))
            next_at,blocked=c.execute('select next_at,blocked from r42_pacing where host=?',(host,)).fetchone()
            now=time.time()
            if blocked>now+2:
                raise RuntimeError('provider backoff until '+str(blocked))
            delay=max(0,next_at-now,blocked-now)
            if not delay:
                c.execute('update r42_pacing set next_at=? where host=?',(now+interval,host))
                return
        time.sleep(min(2,delay))

def request(home,url,limit=10000000):
    host=urllib.parse.urlsplit(url).hostname
    pace(home,host)
    started=time.time()
    with tempfile.TemporaryDirectory() as td:
        head=Path(td)/'headers'
        body=Path(td)/'body'
        # curl does not implicitly import macOS system proxy settings.
        proxy=urllib.request.getproxies().get('https')
        cmd=['curl','-sS','--proto','=https','--connect-timeout','7','--max-time','25','--max-filesize',str(limit),
             '--user-agent','AsterLoom/0.36 scientific literature mining','--dump-header',str(head),'-o',str(body),'-w','%{http_code}']
        if proxy: cmd+=['--proxy',proxy]
        res=subprocess.run(cmd+[url],capture_output=True,text=True)
        code=int(res.stdout or 0) if not res.returncode else 0
        raw=body.read_bytes() if body.exists() and res.returncode==0 else b''
        elapsed=time.time()-started
        err=res.stderr[:300]
        with closing(connect(home)) as c,c:
            c.execute('insert into r42_http values(?,?,?,?,?,?,?)',(uuid.uuid4().hex,started,url,code,len(raw),elapsed,err))
            if code in (429,503):
                m=re.search(r'(?im)^retry-after:\s*([^\r\n]+)',head.read_text(errors='replace'))
                until=time.time()+retry_delay(m[1] if m else '10')
                c.execute('update r42_pacing set blocked=max(blocked,?) where host=?',(until,host))
        if code!=200:raise RuntimeError(f'HTTP {code} {err}')
        return raw

def save(home,sub,raw,suffix):
    sha=digest(raw)
    folder=home/'sources/r42'/sub
    folder.mkdir(exist_ok=True,parents=True)
    dest=folder/(sha+suffix)
    if not dest.exists():
        temp=dest.with_name('.'+uuid.uuid4().hex+'.part')
        temp.write_bytes(raw)
        os.replace(temp,dest)
    return sha,str(dest)

def upsert(c,query,hit):
    pmcid,pmid,doi=hit['pmcid'],hit['pmid'],normalize_doi(hit['doi'])
    if not any((pmcid,pmid,doi)):return 0
    matched=c.execute('select * from r42_paper where pmcid=? or pmid=? or doi=?',(pmcid,pmid,doi)).fetchall()
    if len(matched)>1 or any(any(a and b and a!=b for a,b in zip((pmcid,pmid,doi),(r['pmcid'],r['pmid'],r['doi']))) for r in matched):
        c.execute("insert or ignore into r42_identity_hold values(?,?,?)",
                  (query['id'],json.dumps([pmcid,pmid,doi]),json.dumps([r['paper'] for r in matched])))
        return 0
    key=matched[0]['paper'] if matched else (pmcid or ('PMID:'+pmid if pmid else 'DOI:'+doi))
    state='QUEUED' if pmcid and hit['license'] in ('cc by','cc0') else 'LICENSE_HOLD' if pmcid and hit['license'] else 'METADATA_ONLY'
    if not matched:
        c.execute('insert into r42_paper(paper,pmcid,pmid,doi,title,license,status,score) values(?,?,?,?,?,?,?,?)',
                  (key,pmcid,pmid,doi,hit['title'],hit['license'],state,hit['score']))
    else:
        c.execute('update r42_paper set pmcid=coalesce(pmcid,?),pmid=coalesce(pmid,?),doi=coalesce(doi,?),'
                  "title=case when title='' then ? else title end,license=coalesce(?,license),score=max(score,?),"
                  "status=case when status in ('METADATA_ONLY','LICENSE_HOLD') and ?='QUEUED' then ? else status end where paper=?",
                  (pmcid,pmid,doi,hit['title'],hit['license'],hit['score'],state,state,key))
    c.execute('insert or ignore into r42_hit values(?,?)',(query['id'],key))
    return int(not matched)

def page(home,q):
    provider=q['provider']
    cur=q['cursor']
    query=q['query']
    if provider=='EPMC':
        params=dict(query=query,format='json',pageSize=100,resultType='core',cursorMark=cur)
        url=EPMC+'search?'+urllib.parse.urlencode(params)
    elif provider=='PUBMED':
        url=EUTIL+'esearch.fcgi?'+urllib.parse.urlencode(dict(db='pubmed',term=query,retstart=cur,retmax=100,retmode='json',tool='asterloom'))
    else:
        url=ALEX+'?'+urllib.parse.urlencode(dict(search=query,per_page=100,cursor=cur,select='id,ids,doi,display_name,open_access',filter='type:article'))
    f=home/'sources/r42/metadata'/q['lane']
    if provider=='EPMC' and f.with_suffix('.url').is_file() and f.with_suffix('.url').read_text()==url and f.with_suffix('.seed.json').is_file():
        raw=f.with_suffix('.seed.json').read_bytes()
    else:
        raw=request(home,url)
    obj=json.loads(raw)
    items=[]
    if provider=='EPMC':
        nxt=obj.get('nextCursorMark')
        for r in obj['resultList']['result']:
            abstract=(r.get('abstractText') or '').lower()
            title=r.get('title') or ''
            score=5*bool(re.search(r'ATP (?:content|level|concentration)',abstract,re.I))+3*(q['lane']=='molecule')+2*bool(re.search('treat|compound|supplement|dose|administ',abstract))
            if q['lane']=='molecule' and any(n.lower() in title.lower() for n in re.findall(r'TITLE_ABS:"([^"]+)"',query)):score+=8
            if 'DOI:"' in query:score+=24
            items.append(dict(pmcid=r.get('pmcid'),pmid=r.get('pmid'),doi=r.get('doi'),title=title,license=(r.get('license') or '').lower() or None,score=score))
    elif provider=='PUBMED':
        res=obj['esearchresult']
        if res.get('errorlist'):raise ValueError('PubMed rejected query terms: '+str(res['errorlist']))
        if res['idlist']:
            summary_url=EUTIL+'esummary.fcgi?'+urllib.parse.urlencode(dict(db='pubmed',id=','.join(res['idlist']),retmode='json',tool='asterloom'))
            summary_raw=request(home,summary_url)
            summary=json.loads(summary_raw)['result']
            summary_sha,summary_path=save(home,'metadata',summary_raw,'.json')
            with closing(connect(home)) as c,c:
                c.execute('insert or ignore into r42_support_page values(?,?,?)',(summary_url,summary_sha,summary_path))
            for i in res['idlist']:
                details=summary[i]
                ids={a['idtype']:a['value'] for a in details.get('articleids',[])}
                items.append(dict(pmcid=ids.get('pmc'),pmid=i,doi=ids.get('doi'),title=details['title'],license=None,score=0))
        nxt=str(int(cur)+len(items)) if int(cur)+len(items)<int(res['count']) else None
    else:
        nxt=obj['meta'].get('next_cursor')
        for r in obj['results']:
            ids=r['ids'];pmid=(ids.get('pmid') or '').split('/')[-1] or None
            pmc=(ids.get('pmcid') or '').rstrip('/').split('/')[-1] or None
            items.append(dict(pmcid=pmc,pmid=pmid,doi=r.get('doi'),title=r.get('display_name') or '',license=None,score=0))
    sha,path=save(home,'metadata',raw,'.json')
    with closing(connect(home)) as c,c:
        c.execute('begin immediate')
        if c.execute('select 1 from r42_page where query_id=? and cursor=?',(q['id'],cur)).fetchone():return
        new=sum(upsert(c,q,i) for i in items)
        c.execute('insert into r42_page values(?,?,?,?,?,?,?,?)',(q['id'],cur,time.time(),len(items),new,url,sha,path))
        c.execute("update r42_query set cursor=?,page=page+1,state=?,retry_at=0,attempts=0 where id=?",
                  (nxt,'PENDING' if items and nxt and nxt!=cur else 'DONE',q['id']))
        print('PAGE',provider,q['lane'],len(items),new,flush=True)

def discover(home,limit):
    for _ in range(limit):
        with closing(connect(home)) as c,c:
            c.execute("begin immediate")
            now=time.time()
            q=c.execute("select q.* from r42_query q left join r42_query_lease l on l.id=q.id where state='PENDING' and retry_at<=? and coalesce(l.until,0)<=? order by priority+page*8,q.id limit 1",(now,now)).fetchone()
            token=uuid.uuid4().hex
            if q:c.execute("insert or replace into r42_query_lease values(?,?,?)",(q['id'],token,now+120))
        if not q:break
        try: page(home,q)
        except (OSError,RuntimeError,ValueError,KeyError) as exc:
            print('PAGE_HOLD',q['provider'],str(exc),flush=True)
            with closing(connect(home)) as c,c:
                state='ACCESS_HOLD' if any(t in str(exc) for t in ('HTTP 401','HTTP 403','PubMed rejected query')) else 'PENDING'
                c.execute('update r42_query set state=?,attempts=attempts+1,retry_at=? where id=?',(state,time.time()+retry_delay(None,attempt=q['attempts']+2),q['id']))
        finally:
            with closing(connect(home)) as c,c:
                c.execute('delete from r42_query_lease where id=? and token=?',(q['id'],token))

class SourceHold(ValueError):
    def __init__(self,state,message):
        self.state=state
        super().__init__(message)

def verify_xml(raw,pmcid,doi):
    try: root=ET.fromstring(raw)
    except ET.ParseError as exc:raise SourceHold('PARSE_HOLD',str(exc)) from exc
    if root.tag!='article' or root.find('./body') is None:
        raise SourceHold('PARSE_HOLD','Missing native JATS article body')
    if root.get('article-type') in ('review-article','systematic-review','retraction','correction','editorial','expression-of-concern'):
        raise SourceHold('TYPE_HOLD',root.get('article-type'))
    meta=root.find('./front/article-meta')
    if meta is None:raise SourceHold('PARSE_HOLD','Missing front matter')
    ids={i.get('pub-id-type'):i.text for i in meta.findall('article-id')}
    native=(ids.get('pmcid') or ids.get('pmc') or '').removeprefix('PMC')
    if not native or 'PMC'+native!=pmcid or (doi and normalize_doi(ids.get('doi'))!=doi):
        raise SourceHold('IDENTITY_HOLD','Native PMCID or DOI mismatch')
    licenses=' '.join(ET.tostring(n,encoding='unicode') for n in meta.findall('./permissions/license'))
    if re.search(r'creativecommons\.org/licenses/by-(?:nc|sa|nd)',licenses,re.I):
        raise SourceHold('LICENSE_HOLD','Restricted native license')
    rule=re.search(r'creativecommons\.org/(?:licenses/by/|publicdomain/zero/)\d[.\d]*/',licenses,re.I)
    if not rule:raise SourceHold('LICENSE_HOLD','No explicit native CC BY / CC0 URI')
    return normalize_doi(ids.get('doi'))

def get_source(home,row):
    pmcid,doi=row['pmcid'],row['doi']
    if not re.fullmatch(r'PMC[0-9]+',pmcid):raise SourceHold('IDENTITY_HOLD','Invalid PMCID')
    with closing(connect(home)) as c:
        cached=c.execute("select original_path,original_sha from r16_parsed_asset where paper_key=? and source_format in ('.xml','.nxml') union select path,sha from r41_source where paper=?",(pmcid,pmcid)).fetchall()
    for path,sha in cached:
        if Path(path).is_file():
            raw=Path(path).read_bytes()
            if digest(raw)!=sha:raise SourceHold('IDENTITY_HOLD','Cached source hash drift')
            return str(Path(path).resolve()),sha,verify_xml(raw,pmcid,doi),'LOCAL_VERIFIED'
    meta_url=PMC+'metadata/'+pmcid+'.1.json'
    try:
        meta_raw=request(home,meta_url,1000000)
    except RuntimeError as exc:
        # Only a missing version permits fallback. Actual backpressure is respected.
        if 'HTTP 404' not in str(exc) and 'HTTP 403' not in str(exc): raise
        url=EPMC+pmcid+'/fullTextXML'
        raw=request(home,url)
        via='EUROPE_PMC'
    else:
        version=json.loads(meta_raw)
        if version.get('pmcid')!=pmcid or version.get('version')!=1 or (doi and normalize_doi(version.get('doi'))!=doi):
            raise SourceHold('IDENTITY_HOLD','PMC metadata identity mismatch')
        if version.get('is_retracted') or not version.get('is_pmc_openaccess') or version.get('license_code') not in ('CC BY','CC0'):
            raise SourceHold('LICENSE_HOLD','PMC version restricted or retracted')
        link=urllib.parse.urlsplit(version.get('xml_url',''))
        md5=urllib.parse.parse_qs(link.query).get('md5',[''])[0]
        if (link.scheme,link.netloc,link.path)!=('s3','pmc-oa-opendata',f'/{pmcid}.1/{pmcid}.1.xml') or not re.fullmatch('[a-fA-F0-9]{32}',md5):
            raise SourceHold('IDENTITY_HOLD','Mismatch in official versioned XML pointer')
        save(home,'pmc_version',meta_raw,'.json')
        # Name index allows the SI worker to reuse official media declarations.
        index=home/'sources/r42/pmc_version'/(pmcid+'.1.json')
        if not index.exists():
            temp=index.with_suffix('.'+uuid.uuid4().hex+'.part')
            temp.write_bytes(meta_raw);os.replace(temp,index)
        raw=request(home,PMC+link.path.lstrip('/'))
        if hashlib.md5(raw).hexdigest()!=md5.lower():raise SourceHold('IDENTITY_HOLD','PMC version MD5 drift')
        via='PMC_S3'
    author_doi=verify_xml(raw,pmcid,doi)
    sha,path=save(home,'xml',raw,'.xml')
    return path,sha,author_doi,via

def take(home,batch):
    with closing(connect(home)) as c,c:
        c.execute('begin immediate')
        now=time.time()
        row=c.execute("""select * from r42_paper where (status in ('QUEUED','RETRY') and retry_at<=?)
          or (status='FETCHING' and lease_until<=?) order by score desc,attempts,paper limit 1""",(now,now)).fetchone()
        if not row:return None
        token=uuid.uuid4().hex
        if row['lease']:
            c.execute("update r42_fetch_attempt set status='LEASE_EXPIRED',finished=? where token=? and status='FETCHING'",(now,row['lease']))
        c.execute("insert into r42_fetch_attempt values(?,?,?,'FETCHING',?,null,null)",(token,batch,row['paper'],now))
        c.execute("update r42_paper set status='FETCHING',lease=?,lease_until=?,attempts=attempts+1 where paper=?",(token,now+240,row['paper']))
        return row,token

def fetch_one(home,batch):
    item=take(home,batch)
    if not item:return 'NO_WORK'
    row,token=item
    reason=path=sha=doi=via=None; retry=0
    try:
        path,sha,doi,via=get_source(home,row);state='PARSED'
    except SourceHold as exc:
        state,reason=exc.state,str(exc)
    except (OSError,RuntimeError,ValueError,KeyError) as exc:
        reason=str(exc)[:400]
        state='RETRY' if row['attempts']<3 else 'TECHNICAL_HOLD'
        retry=time.time()+retry_delay(None,attempt=row['attempts']+3)
    with closing(connect(home)) as c,c:
        try:
            c.execute('update r42_paper set status=?,reason=?,path=?,sha=?,doi=coalesce(doi,?),via=?,retry_at=?,lease=null,lease_until=null where paper=? and lease=?',
                      (state,reason,path,sha,doi,via,retry,row['paper'],token))
        except sqlite3.IntegrityError:
            state='IDENTITY_HOLD'
            c.execute("update r42_paper set status=?,reason='Native DOI maps to another admitted record',lease=null,lease_until=null where paper=? and lease=?",(state,row['paper'],token))
    return state

def collect(home,limit=50,workers=4):
    if not 1<=limit<=100 or not 1<=workers<=6:raise ValueError('1..100 papers, 1..6 workers per bounded batch')
    free=shutil.disk_usage(home).free
    if free<1500*1024**2:return dict(state='DISK_HOLD',free=free)
    with closing(connect(home)) as c:
        http=c.execute('select status,elapsed from r42_http order by at desc limit 20').fetchall()
    if http and (sum(r[0] in (0,429,503) for r in http)>len(http)*.15 or sum(r[1] for r in http)/len(http)>5):workers=min(workers,2)
    if free<3*1024**3:workers=1
    batch=uuid.uuid4().hex;started=time.time()
    with closing(connect(home)) as c,c:c.execute('insert into r42_batch values(?,?,?,?,?)',(batch,'FULLTEXT',started,None,None))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        states=list(pool.map(lambda _:fetch_one(home,batch),range(limit)))
    result=dict(batch=batch,limit=limit,workers=workers,seconds=time.time()-started,states={s:states.count(s) for s in sorted(set(states))})
    with closing(connect(home)) as c,c:c.execute('update r42_batch set finished=?,receipt=? where batch=?',(time.time(),json.dumps(result),batch))
    return result

def report(home):
    with closing(connect(home)) as c:
        return dict(metadata=c.execute('select count(*) from r42_paper').fetchone()[0],statuses=dict(c.execute('select status,count(*) from r42_paper group by status')),
                    via=dict(c.execute('select via,count(*) from r42_paper where status="PARSED" group by via')),
                    providers=dict(c.execute('select q.provider,sum(count) from r42_page p join r42_query q on q.id=p.query_id group by q.provider')),
                    lanes=dict(c.execute('select q.lane,count(distinct h.paper) from r42_query q join r42_hit h on h.query_id=q.id group by q.lane')))
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,required=True)
    s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('seed');a.add_argument('labels',type=Path)
    a=s.add_parser('discover');a.add_argument('--pages',type=int,default=10)
    a=s.add_parser('collect');a.add_argument('--papers',type=int,default=50);a.add_argument('--workers',type=int,default=4)
    s.add_parser('status')
    a=p.parse_args();home=a.home.resolve()
    init(home)
    if a.action=='seed':
        seed_queries(home,a.labels)
        seed_existing(home)
    if a.action=='discover':discover(home,a.pages)
    if a.action=='collect':print(json.dumps(collect(home,a.papers,a.workers)))
    print(json.dumps(report(home)))
if __name__=='__main__':main()
