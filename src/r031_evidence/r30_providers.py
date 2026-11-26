"""Small official provider adapters: search / resolve hints / fetch contract."""
from __future__ import annotations
import json,urllib.parse
from dataclasses import dataclass
from .provider_discovery import _normalize
from .r30_ledger import normalize_doi
from .r30_http import request
@dataclass(frozen=True)
class Adapter:
 name:str
 def search_url(self,query,cursor='*',limit=100):
  if not 1<=limit<=100:raise ValueError('R30_PROVIDER_PAGE_LIMIT_100')
  if self.name=='EUROPE_PMC':return 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urllib.parse.urlencode(dict(query=query,format='json',resultType='core',pageSize=limit,cursorMark=cursor or '*'))
  if self.name=='OPENALEX':return 'https://api.openalex.org/works?'+urllib.parse.urlencode(dict(search=query,per_page=limit,cursor=cursor or '*'))
  if self.name=='CROSSREF':
   doi=normalize_doi(query)
   if not doi:raise ValueError('R30_CROSSREF_REQUIRES_DOI')
   return 'https://api.crossref.org/works/'+urllib.parse.quote(doi,safe='/')
  raise ValueError('R30_UNSUPPORTED_PROVIDER')
 def parse(self,raw):
  p=json.loads(raw)
  items=p.get('resultList',{}).get('result',[]) if self.name=='EUROPE_PMC' else p.get('results',[]) if self.name=='OPENALEX' else [p.get('message',{})]
  hits=[]
  for item in items:
   hit=_normalize(item,self.name);hit['provider_record']=item;hit['provider']=self.name
   if self.name=='EUROPE_PMC':
    hit['pmid']=item.get('id') if item.get('source')=='MED' else None
   hit['doi']=normalize_doi(hit.get('doi'));hits.append(hit)
  cursor=p.get('nextCursorMark') if self.name=='EUROPE_PMC' else (p.get('meta') or {}).get('next_cursor') if self.name=='OPENALEX' else None
  return hits,cursor
 def search(self,home,query,cursor='*',limit=100,reader=None):
  url=self.search_url(query,cursor,limit);r=(reader(url) if reader else request(home,url))
  hits,nxt=self.parse(r['body']) if r['status']==200 and r['body'] else ([],None)
  return dict(hits=hits,next_cursor=nxt,raw_SHA=r.get('sha'),response=r,url=url)
 def resolve(self,identifier):
  if self.name=='CROSSREF':return self.search_url(identifier)
  if self.name=='OPENALEX':return 'https://api.openalex.org/works/https://doi.org/'+urllib.parse.quote(normalize_doi(identifier) or '',safe='/')
  return 'https://www.ebi.ac.uk/europepmc/webservices/rest/'+identifier+'/fullTextXML'
 def fetch(self,home,url,**kwargs):return request(home,url,**kwargs)

def get(name):return Adapter(name)

def source_candidates(hits):
 result=[]
 for hit in hits:
  if hit.get('pmcid'):
   result.append(dict(url='https://www.ebi.ac.uk/europepmc/webservices/rest/'+hit['pmcid']+'/fullTextXML',format='JATS',license_state='VERIFY_NATIVE_JATS',pmcid=hit['pmcid'],doi=hit.get('doi'),version='PMC-current',provider='EUROPE_PMC'))
   result.append(dict(url='https://pmc-oa-opendata.s3.amazonaws.com/'+hit['pmcid']+'.1/'+hit['pmcid']+'.1.xml',format='JATS',license_state='VERIFY_NATIVE_JATS',pmcid=hit['pmcid'],doi=hit.get('doi'),version='PMC-version-1',provider='PMC_OFFICIAL_S3'))
  doi=normalize_doi(hit.get('doi'))
  # A deterministic publisher-native endpoint, followed by native DOI/license
  # verification; no OA boolean or metadata license alone grants permission.
  if doi and doi.startswith('10.1371/journal.'):
   stem=doi.split('journal.',1)[1].split('.',1)[0]
   journal={'pone':'plosone','pbio':'plosbiology','pcbi':'ploscompbiol','pgen':'plosgenetics','pmed':'plosmedicine','ppat':'plospathogens'}.get(stem)
   if journal:result.append(dict(url='https://journals.plos.org/'+journal+'/article/file?'+urllib.parse.urlencode(dict(id=doi,type='manuscript')),format='JATS',license_state='VERIFY_NATIVE_JATS',pmcid=None,doi=doi,version='publisher-version-of-record',provider='PUBLISHER_PLOS'))
  raw=hit.get('provider_record') or {}
  locations=(raw.get('locations') or [])+([raw['best_oa_location']] if raw.get('best_oa_location') else [])
  for loc in locations:
   url=loc.get('pdf_url') or loc.get('landing_page_url')
   if url:result.append(dict(url=url,format='PDF' if loc.get('pdf_url') else 'HTML',license_state='UNVERIFIED_REPOSITORY_LICENSE',license=loc.get('license'),doi=doi,pmcid=None,version=loc.get('version'),provider='OPENALEX_LOCATION'))
 return list({(x['url'],x.get('version')):x for x in reversed(result)}.values())
