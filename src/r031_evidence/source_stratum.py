"""Verify donor strata against a separately reconstructed author pair.

Only ordered author strata from one source sentence are supported. This is a
source-consistency check, not independent scientific accuracy or a Gold label.
"""
from functools import lru_cache
from pathlib import Path
import re
from .llm_harness import sha
from .quantitative_evidence import analyze_paper

@lru_cache(maxsize=64)
def _pairs(path,paper,source_sha):
    raw=Path(path).read_bytes()
    if sha(raw)!=source_sha:raise ValueError('STRATUM_SOURCE_SHA_DRIFT')
    return analyze_paper(path,paper)['pairs']

def verify_source_stratum(packet, original, measurements):
    claim=(original.get('scope') or {}).get('author_stratum')
    if claim is None:return None
    if not isinstance(claim,str) or not claim.strip():return False
    arms={}
    for m in measurements:arms.setdefault(m['arm'],[]).append(m)
    if any(len(arms.get(a,[]))!=1 for a in ('TREATED','CONTROL')):return False
    t,c=arms['TREATED'][0],arms['CONTROL'][0]
    def clean(s):return re.sub(r'(?i)^the\s+','',(s or '').strip()).casefold()
    matches=[]
    for p in _pairs(str(Path(packet['source_path']).resolve()),packet['paper'],packet['source_sha256']):
        a,b=p['treated'],p['control']
        if (p.get('basis')=='SAME_SENTENCE_ORDERED_STRATA' and p.get('tier_hint')=='A_CANDIDATE'
            and p.get('stratum')==claim and a.get('stratum')==claim and b.get('stratum')==claim
            and a.get('chunk_id')==t.get('chunk_id') and b.get('chunk_id')==c.get('chunk_id')
            and clean(a.get('arm_label'))==clean(t.get('arm_label')) and clean(b.get('arm_label'))==clean(c.get('arm_label'))
            and a.get('value')==t['value'] and b.get('value')==c['value']
            and a.get('unit')==t.get('unit_literal') and b.get('unit')==c.get('unit_literal')):
            matches.append(p)
    return claim if len(matches)==1 else False
