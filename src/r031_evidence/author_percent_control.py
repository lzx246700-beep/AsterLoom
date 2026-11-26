"""Review-only validation of explicitly author-ordered percent-of-control values.

This deliberately supports only one narrow author grammar. It never reconstructs
raw means, determines chemical structure, or grants scientific approval.
"""
from __future__ import annotations
from decimal import Decimal,InvalidOperation
import re

PAIR=re.compile(r'\b(?P<a>[A-Za-z][A-Za-z0-9-]{1,11})\s+and\s+(?P<b>[A-Za-z][A-Za-z0-9-]{1,11})\s+treatment\b[^.]{0,100}?\bATP\s+(?:levels?|content)\s+in\s+(?P<cell>[A-Za-z0-9-]+)\s+cells?\s+to\s*\(\s*(?P<x>\d+(?:\.\d+)?)\s+and\s+(?P<y>\d+(?:\.\d+)?)\s*\)\s*%\s+of\s+(?:the\s+)?control\s+values?\s*,\s*respectively\b',re.I)
FIG=re.compile(r'\bFigure\s+(?P<number>\d+)(?P<panel>[A-Za-z]?)\b',re.I)

def verify(packet,relation):
    f=relation.get('fields') or {};ev=relation.get('evidence') or []
    required=('chemical_identity','cell_model','treatment','comparator','endpoint','dose','exposure_time','value','unit')
    if any(not f.get(k) for k in required) or f['endpoint']!='ATP_CONTENT' or f['unit'].casefold()!='% of control':return None
    if str(f['comparator']).casefold().strip()!='control':return None
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,11}',str(f['treatment'])):return None
    dose=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([μµu]M|mM)\s*',str(f['dose']),re.I)
    duration=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hours?)\s*',str(f['exposure_time']),re.I)
    if not dose or not duration:return None
    try: wanted=Decimal(str(f['value']))
    except InvalidOperation:return None
    chunks={c['chunk_id']:c for c in packet['chunks']};by={k:[e for e in ev if e.get('field')==k and e.get('chunk_id') in chunks and isinstance(e.get('source_literal'),str) and e['source_literal'] in chunks[e['chunk_id']]['text']] for k in required}
    if any(not by[k] for k in required):return None
    alias=str(f['treatment']);name=str(f['chemical_identity']);identity=re.compile(r'(?<![\w-])'+re.escape(name)+r'\s*\(\s*'+re.escape(alias)+r'\s*\)',re.I)
    if not any(identity.search(e['source_literal']) for e in by['chemical_identity']):return None
    dose_pattern=re.compile(r'(?<![\w-])'+re.escape(alias)+r'\s*\(\s*'+re.escape(str(f['dose']).strip())+r'\s*\)',re.I)
    if not any(dose_pattern.search(e['source_literal']) and re.search(r'\bcells?\s+were\s+treated\s+with\b',chunks[e['chunk_id']]['text'],re.I) for e in by['dose']):return None
    hits=[]
    for e in by['value']:
        chunk=chunks[e['chunk_id']];text=chunk['text']
        for match in PAIR.finditer(text):
            if e['source_literal'] not in match.group():continue
            if str(f['cell_model']).casefold().strip()!=match['cell'].casefold():continue
            if alias.casefold() not in (match['a'].casefold(),match['b'].casefold()):continue
            if match['a'].casefold()==match['b'].casefold():continue
            target=match['x'] if alias.casefold()==match['a'].casefold() else match['y']
            if Decimal(target)!=wanted:continue
            if not any(e2['chunk_id']==e['chunk_id'] and e2['source_literal'] in match.group() for e2 in by['unit']):continue
            if not any(e2['chunk_id']==e['chunk_id'] and e2['source_literal'] in match.group() for e2 in by['comparator']):continue
            if not any(e2['chunk_id']==e['chunk_id'] and e2['source_literal'] in match.group() for e2 in by['endpoint']):continue
            figure_before=list(FIG.finditer(text[:match.start()]))
            if not figure_before:continue
            figure=figure_before[-1]
            matching_time=[]
            for te in by['exposure_time']:
                t=chunks[te['chunk_id']];timed=re.search(r'\bafter\s+'+re.escape(duration.group(1))+r'\s*(?:h|hours?)\s+of\s+treatment\b',te['source_literal'],re.I)
                refs=list(FIG.finditer(t['text']))
                same_figure=any(q['number']==figure['number'] and (not q['panel'] or not figure['panel'] or q['panel'].casefold()==figure['panel'].casefold()) for q in refs)
                if timed and t['section']==chunk['section'] and same_figure and re.search(r'\bATP\b',t['text'],re.I):matching_time.append(te)
            if len(matching_time)!=1:continue
            hits.append({'state':'AUTHOR_ORDERED_PERCENT_OF_CONTROL_SOURCE_CHECKED_REVIEW_ONLY','numeric_type':'PERCENT_OF_CONTROL',
              'author_reported_percent':float(wanted),'author_sentence':match.group(),'chunk_id':e['chunk_id'],'locator':chunk['locator'],
              'source_sha256':packet['source_sha256'],'figure_number':figure['number'],'figure_panel':figure['panel'] or None,
              'not_percent_decrease':True,'not_raw_group_means':True,'scientific_binding_verified':False})
    return hits[0] if len(hits)==1 else None
