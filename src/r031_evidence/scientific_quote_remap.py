"""Exact author quote relocation, with full-document uniqueness and audit trail.

Relocation corrects a source pointer only; it never validates scientific scope.
"""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re
from .r44_packets import tree_chunks
from .r44_field_review import review_relations
from .scientific_extraction_harness import replay_packet


def review_with_remap(packet,answer,source_path,*,drop_unsupported_fields=False):
    replay_packet(packet,Path(source_path))
    if not isinstance(answer,dict) or not isinstance(answer.get('relations'),list):
        raise ValueError('QUOTE_REMAP_INVALID_ANSWER')
    result=deepcopy(answer);all_chunks=tree_chunks(Path(source_path).read_bytes())
    indexed={x['chunk_id']:x for x in packet['chunks']};by_locator={x['locator']:x for x in packet['chunks']};changes={}
    for i,rel in enumerate(result['relations']):
        if not isinstance(rel,dict) or not isinstance(rel.get('evidence'),list):continue
        for j,e in enumerate(rel['evidence']):
            if not isinstance(e,dict) or set(e)!={'field','chunk_id','quote'}:continue
            quote=e['quote'];old=e['chunk_id'];old_chunk=indexed.get(old)
            if not isinstance(quote,str) or len(quote)<30 or (old_chunk and quote in old_chunk['text']):continue
            # Unique in the entire parsed author source, not just the selected
            # packet. Repeated text, overlapping table/caption paths or a source
            # outside the packet must remain unresolved.
            hits=[(x['locator'],len(re.findall(r'(?='+re.escape(quote)+r')',x['text']))) for x in all_chunks if quote in x['text']]
            if len(hits)!=1 or hits[0][1]!=1 or hits[0][0] not in by_locator:continue
            target=by_locator[hits[0][0]];e['chunk_id']=target['chunk_id']
            changes.setdefault(i,[]).append({'field':e['field'],'evidence_index':j,'original_chunk_id':old,
              'resolved_chunk_id':target['chunk_id'],'resolved_locator':target['locator'],'source_sha256':packet['source_sha256'],
              'quote':quote,'method':'EXACT_QUOTE_UNIQUE_IN_AUTHOR_DOCUMENT','scientific_binding_verified':False})
    reviewed=review_relations(packet,result,drop_unsupported_fields=drop_unsupported_fields)
    for row in reviewed['relations']:
        c=changes.get(row['review_index'],[])
        if c:
            row['citation_repairs']=c
            row['risk_flags']=sorted(set(row['risk_flags']+['AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED']))
    reviewed['citation_repairs_by_index']={str(i):c for i,c in changes.items()}
    return reviewed
