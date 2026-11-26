"""Add bounded author-defined intervention context to a frozen development packet.

This only selects source text. It does not establish identity, conditions or arms.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re
from .atp_content_author_identity import resolve_author_entity, name_pattern
from .r44_packets import sha, canonical, tree_chunks, verify_packet
from .scientific_extraction_harness import replay_packet

RULE='author-context-additions/1'

def enrich(packet_path, expected_packet_sha, source_path, requested_name, *, max_extra=6, max_chars=30000):
    if not isinstance(requested_name,str) or not 2<=len(requested_name.strip())<=120 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\- '(),.]+",requested_name.strip()):
        raise ValueError('SCIENTIFIC_CONTEXT_NAME_INVALID')
    if not 1<=max_extra<=8 or not 17000<=max_chars<=30000:
        raise ValueError('SCIENTIFIC_CONTEXT_BUDGET_INVALID')
    packet=verify_packet(Path(packet_path),expected_packet_sha)
    if packet.get('selection_scope')!='DEVELOPMENT_ONLY_NOT_HIDDEN':
        raise ValueError('SCIENTIFIC_CONTEXT_DEVELOPMENT_ONLY')
    source=Path(source_path);replay_packet(packet,source)
    chunks=tree_chunks(source.read_bytes());blocks=[{'path':x['locator'],'text':x['text']} for x in chunks]
    identity=resolve_author_entity(requested_name.strip(),blocks,packet['source_sha256'])
    names=identity['retrieval_names'];declarations=identity['declarations']
    # A short alias is usable for retrieval only after an explicit author
    # declaration and conflict check; it never establishes a free chemical.
    patterns=re.compile(name_pattern(names),re.I)
    declaration_locs={d['evidence']['source_anchor'] for d in declarations}
    existing={c['locator'] for c in packet['chunks']};candidates=[]
    for row in chunks:
        if row['locator'] in existing or len(row['text'])>2200:continue
        if not patterns.search(row['text']):continue
        loc=row['locator'];section=row['section'].casefold();score=0
        if loc in declaration_locs:score+=100
        if re.search(r'cell\s+(?:group|treat)|grouping\s+and\s+treatment',section):score+=80
        elif re.search(r'(?:treatment|preparation|reagent|materials|methods)',section):score+=20
        if re.search(r'\b(?:cells?|cellular)\b',row['text'],re.I):score+=8
        if re.search(r'\b(?:control|comparator|groups?)\b',row['text'],re.I):score+=6
        if not score:continue
        candidates.append((-score,loc,row))
    candidates.sort(key=lambda x:(x[0],x[1]))
    out=deepcopy(packet);total=sum(len(c['text']) for c in out['chunks']);added=[];omitted=0
    if total>max_chars:raise ValueError('SCIENTIFIC_CONTEXT_ORIGINAL_OVER_BUDGET')
    for _,_,row in candidates:
        if len(added)>=max_extra or total+len(row['text'])>max_chars:
            omitted+=1;continue
        record=dict(row);record['chunk_id']='r44:'+sha((packet['paper']+'|'+packet['source_sha256']+'|'+row['locator']+'|'+row['text']).encode())[:20];record['author_sha256']=packet['source_sha256']
        out['chunks'].append(record);added.append(row['locator']);total+=len(row['text'])
    out['chunks'].sort(key=lambda x:x['locator']);out['included_chunks']=len(out['chunks'])
    out['context_truncated']=bool(packet['context_truncated'] or omitted)
    out['packet_rule']=packet['packet_rule']+'+'+RULE
    out['context_augmentation']={'rule':RULE,'original_packet_sha256':expected_packet_sha,'requested_name':requested_name.strip(),
        'author_declarations':declarations,'ambiguous_abbreviations':identity['ambiguous_abbreviations'],
        'added_locators':added,'eligible_omitted':omitted,'not_experiment_binding':True,'independent_gold':0,'training_approved':0}
    out.pop('packet_content_sha256',None);out['packet_content_sha256']=sha(canonical(out).encode())
    replay_packet(out,source)
    return out
