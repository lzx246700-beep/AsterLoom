"""Verify two author-ordered dose/effect pairs from a single source paragraph.

This is a literal ordering check, not an independent scientific interpretation.
"""
from __future__ import annotations
import re
from decimal import Decimal,InvalidOperation
from .r44_arbiter import literal_supported

DOSES=re.compile(r'(?<![\d.])(?P<a>\d+(?:\.\d+)?)\s+(?:and|,)\s+(?P<b>\d+(?:\.\d+)?)\s*(?P<unit>[μµu]M|mM)\b',re.I)
EFFECTS=re.compile(r'\b(?P<direction>decrease|increase)\s+(?:of\s+)?(?P<a>\d+(?:\.\d+)?)\s*%\s+(?:and|,)\s+(?P<b>\d+(?:\.\d+)?)\s*%\s*,?\s*respectively\b',re.I)
DURATION=re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(h|hours?)\s*$',re.I)
SINGLE_DOSE=re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([μµu]M|mM)\s*$',re.I)
CONTROL=re.compile(r'\bcompared\s+(?:to|with)\s+(?:the\s+)?control\b',re.I)

def _unit(s):return s.casefold().replace('µ','u').replace('μ','u')

def verify(packet, relation):
 if not isinstance(relation,dict) or not isinstance(relation.get('fields'),dict) or not isinstance(relation.get('evidence'),list):return None
 f=relation['fields'];es=relation['evidence'];required=('chemical_identity','cell_model','treatment','comparator','endpoint','direction','value','unit','dose','exposure_time')
 if any(not f.get(k) for k in required) or f.get('endpoint')!='ATP_CONTENT' or f.get('unit')!='%':return None
 # A paragraph may mention several chemicals. Do not transfer the dose of
 # one intervention to a separately quoted identity or combined formulation.
 if not all(isinstance(f[k],str) for k in ('chemical_identity','treatment')):return None
 if f['chemical_identity'].strip().casefold()!=f['treatment'].strip().casefold():return None
 if not re.fullmatch(r'\d+(?:\.\d+)?',str(f['value'])):return None
 dose=SINGLE_DOSE.fullmatch(str(f['dose']));time=DURATION.fullmatch(str(f['exposure_time']))
 if not dose or not time or str(f['comparator']).casefold()!='control':return None
 chunks={x['chunk_id']:x for x in packet['chunks']}; groups={k:[e for e in es if isinstance(e,dict) and e.get('field')==k] for k in required}
 common=None
 for k in required:
  valid={e.get('chunk_id') for e in groups[k] if e.get('chunk_id') in chunks and isinstance(e.get('quote'),str) and e['quote'] in chunks[e['chunk_id']]['text']}
  common=valid if common is None else common&valid
 if not common:return None
 found=[]
 for cid in common:
  chunk=chunks[cid];text=chunk['text']
  for de in groups['dose']:
   if de.get('chunk_id')!=cid or not isinstance(de.get('quote'),str) or de['quote'] not in text:continue
   ds=DOSES.search(de['quote'])
   if not ds or not re.search(r'(?<!\w)'+re.escape(str(f['treatment']))+r'(?!\w)',de['quote'],re.I):continue
   if _unit(ds.group('unit'))!=_unit(dose.group(2)):continue
   for ve in groups['value']:
    if ve.get('chunk_id')!=cid or not isinstance(ve.get('quote'),str) or ve['quote'] not in text:continue
    vs=EFFECTS.search(ve['quote'])
    if not vs or vs.group('direction').upper()!=f['direction']:continue
    try:
     a,b=Decimal(ds.group('a')),Decimal(ds.group('b'));v1,v2=Decimal(vs.group('a')),Decimal(vs.group('b'))
     target_dose,target_value=Decimal(dose.group(1)),Decimal(str(f['value']))
    except InvalidOperation:continue
    if a==b or v1==v2 or (target_dose,target_value) not in ((a,v1),(b,v2)):continue
    dose_pos=text.find(de['quote']);value_pos=text.find(ve['quote']);prior=text[max(0,dose_pos-300):dose_pos];tail=text[value_pos+len(ve['quote']):value_pos+len(ve['quote'])+80]
    if dose_pos<0 or value_pos<=dose_pos or not CONTROL.search(prior):continue
    # Link cell and comparator in one nearby sentence, not merely anywhere
    # in the paragraph; otherwise a later or earlier experiment can leak in.
    sentences=re.split(r'(?<=[.!?])\s+',prior)
    nearby=sentences[-2:]
    scoped=any(CONTROL.search(sentence) and any(
      e.get('chunk_id')==cid and isinstance(e.get('quote'),str) and
      e['quote'] in sentence and literal_supported('cell_model',f['cell_model'],e['quote'])
      for e in groups['cell_model']) and any(
      e.get('chunk_id')==cid and isinstance(e.get('quote'),str) and
      e['quote'] in sentence and CONTROL.search(e['quote'])
      for e in groups['comparator']) for sentence in nearby)
    if not scoped:continue
    # Stop at the next contrasting phase, so a later 48 h statement cannot
    # satisfy a 24 h result. Check the field against its own quoted text too.
    tail=re.split(r',\s*(?:while|whereas|but)\b|;',tail,maxsplit=1,flags=re.I)[0]
    if not any(e.get('chunk_id')==cid and literal_supported('exposure_time',f['exposure_time'],e.get('quote','')) for e in groups['exposure_time']):continue
    if not any(e.get('chunk_id')==cid and literal_supported('cell_model',f['cell_model'],e.get('quote','')) for e in groups['cell_model']):continue
    if not any(e.get('chunk_id')==cid and literal_supported('chemical_identity',f['chemical_identity'],e.get('quote','')) for e in groups['chemical_identity']):continue
    time_pattern=r'\b(?:after|at)\s*'+re.escape(time.group(1))+r'\s*(?:h|hours?)\b'
    if not re.search(time_pattern,tail,re.I):continue
    if not any(e.get('chunk_id')==cid and e.get('quote','') in text and CONTROL.search(e['quote']) for e in groups['comparator']):continue
    found.append({'state':'AUTHOR_ORDERED_PAIR_SOURCE_CHECKED_REVIEW_ONLY','dose_original':f['dose'],
      'author_reported_percent':float(target_value),'direction':f['direction'],'exposure_time':f['exposure_time'],
      'chunk_id':cid,'locator':chunk['locator'],'source_sha256':packet['source_sha256'],
      'dose_quote':de['quote'],'effect_quote':ve['quote'],'scope_not_independently_verified':True})
 return found[0] if len(found)==1 else None
