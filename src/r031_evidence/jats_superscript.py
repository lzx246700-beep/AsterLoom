"""Recover unambiguous author-printed superscripts at an exact JATS locator."""
from pathlib import Path
import re,xml.etree.ElementTree as ET
from .llm_harness import sha

def _node(root,locator):
    parts=locator.strip('/').split('/')
    if not parts or parts[0].split('[')[0]!=root.tag.split('}')[-1]:return None
    node=root
    for part in parts[1:]:
        m=re.fullmatch(r'([\w.-]+)\[(\d+)\]',part)
        if not m:return None
        children=[x for x in node if x.tag.split('}')[-1]==m.group(1)]
        index=int(m.group(2))-1
        if index<0 or index>=len(children):return None
        node=children[index]
    return node

def _render(node):
    parts=[node.text or '']
    for child in node:
        tag=child.tag.split('}')[-1];value=''.join(child.itertext()).strip()
        parts.append(('^'+value) if tag=='sup' and re.fullmatch(r'[+\-−]?\d+',value) else _render(child))
        parts.append(child.tail or '')
    return ''.join(parts)

def restore_author_exponent(packet,relation,field):
    """Return a restored author literal only if its exact source has numeric <sup>."""
    literal=(relation.get('fields') or {}).get(field)
    if not isinstance(literal,str):return None
    match=(re.fullmatch(r'\s*10\s*([−-]\d+)\s*([a-zµμ]+)\s*',literal,re.I) if field=='dose'
           else re.fullmatch(r'\s*(?:in\s+)?10(\d+)\s+(sperm|cells)\s*',literal,re.I) if field=='normalization' else None)
    if not match:return None
    source=Path(packet['source_path']);raw=source.read_bytes()
    if sha(raw)!=packet['source_sha256']:raise ValueError('JATS_SUPERSCRIPT_SOURCE_DRIFT')
    root=ET.fromstring(raw);chunks={c['chunk_id']:c for c in packet['chunks']};found=[]
    for ev in relation.get('evidence',[]):
        if ev.get('field')!=field:continue
        ch=chunks.get(ev.get('chunk_id'))
        quote=ev.get('quote')
        if not ch or not isinstance(quote,str) or not quote or ch.get('author_sha256',packet['source_sha256'])!=packet['source_sha256'] or quote not in ch['text']:continue
        node=_node(root,ch['locator'])
        if node is None:continue
        rendered=re.sub(r'\s+',' ',_render(node))
        exp=match.group(1);unit=match.group(2)
        pattern=r'(?<!\d)10\s*\^\s*'+re.escape(exp)+r'\s*'+re.escape(unit)+r'\b'
        hits=list(re.finditer(pattern,rendered,re.I))
        if len(hits)==1:found.append(dict(field=field,original_literal=literal,restored_literal=('10^'+exp+' '+unit if field=='dose' else '10^'+exp+' '+unit),source_sha256=packet['source_sha256'],locator=ch['locator'],chunk_id=ch['chunk_id'],basis='AUTHOR_JATS_NUMERIC_SUPERSCRIPT',author_excerpt=hits[0].group()))
    unique={(x['locator'],x['restored_literal']):x for x in found}
    return next(iter(unique.values())) if len(unique)==1 else None
