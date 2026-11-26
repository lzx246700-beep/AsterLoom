"""Pinned offline source response replay; cache misses never access network."""
from pathlib import Path
import json
from .r30_ledger import digest
def reader(file,expected_sha):
 p=Path(file)
 if digest(p.read_bytes())!=expected_sha:raise ValueError('R39_OFFLINE_REPLAY_MANIFEST_SHA')
 data=json.loads(p.read_text())
 def read(url,limit=8000000):
  x=data.get('responses',{}).get(url)
  if not x:return dict(status=503,body=b'',sha=None,path=None,bytes=0,reason='OFFLINE_REPLAY_CACHE_MISS_NO_NETWORK',url=url)
  b=Path(x['path']).read_bytes() if x.get('path') else b''
  if b and digest(b)!=x['sha']:raise ValueError('R39_REPLAY_ORIGINAL_SHA_DRIFT')
  if len(b)>limit:raise ValueError('R39_REPLAY_BODY_SIZE_HOLD')
  return dict(x,url=url,body=b,bytes=len(b),reason='EXPLICIT_OFFLINE_REPLAY_NOT_LIVE')
 return read
