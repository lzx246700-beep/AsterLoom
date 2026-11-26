"""Field-grounded, cross-section candidates -> existing Chapter1 Factory.

Unknown/unsupported fields are NULL in normalized tables; the complete original
model proposal and every field decision remain in the SAME staging database.
No requirement that Methods, Results and figures share a paragraph.
"""
from __future__ import annotations
import json
from pathlib import Path
import re

from . import r44_arbiter, author_alias_resolution, chapter1_factory as factory
from .llm_harness import digest, sha

EXTRA_DDL='''
CREATE TABLE IF NOT EXISTS source_assets (
 asset_sha256 TEXT PRIMARY KEY, paper_id TEXT NOT NULL, path TEXT NOT NULL,
 source_url TEXT, license_literal TEXT, asset_kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS extraction_runs (
 run_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, model TEXT, provider TEXT,
 raw_response_sha256 TEXT, request_sha256 TEXT, run_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS harness_proposals (
 proposal_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id),
 contrast_id TEXT REFERENCES contrasts(contrast_id), run_id TEXT NOT NULL,
 original_json TEXT NOT NULL, original_sha256 TEXT NOT NULL,
 field_states_json TEXT NOT NULL, scope_json TEXT NOT NULL, hold_reasons_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS field_evidence (
 proposal_id TEXT NOT NULL REFERENCES harness_proposals(proposal_id), field TEXT NOT NULL,
 evidence_id TEXT NOT NULL, asset_sha256 TEXT NOT NULL, locator TEXT NOT NULL,
 quote TEXT NOT NULL, offsets_json TEXT NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(proposal_id,field,evidence_id));
CREATE VIEW IF NOT EXISTS harness_missing_fields AS
 SELECT proposal_id,paper_id,field_states_json,scope_json,hold_reasons_json FROM harness_proposals;
'''


def _figrefs(text):
    refs=set()
    for m in re.finditer(r'(?i)\bFig(?:ure)?\.?\s*(\d+)\s*([a-z](?![a-z]))?',text or ''):
        number,panel=m.groups();refs.add('fig'+number+(panel or '').lower())
        if panel:
            # Author shorthand "Fig. 6a, b" refers to BOTH panels. Reading
            # only6a spuriously rejected female observations anchored in6b.
            tail=(text or '')[m.end():]
            while (suffix:=re.match(r'(?i)^\s*(?:,|and|&)\s*([a-z])\b',tail)):
                refs.add('fig'+number+suffix.group(1).lower())
                tail=tail[suffix.end():]
    return refs


def _supported_in_cells(chunk, name, value):
    """Accept a literal that a flattened table holds only inside one cell.

    A JATS table is chunked as one text blob, so `0.001` (a row dose) and `30.1`
    (its first cell) are printed as `0.00130.1`.  The number is still the
    author's, and the cell boundary is retained on the chunk, so the literal is
    checked against each cell as well as against the whole blob.
    """
    for cell in (chunk.get('cells') or []):
        if _supported(name, value, cell):
            return True
    return False


def _supported(name,value,quote):
    if name in ('value','dose','exposure_time') and re.match(r'^[+-]?\d',value):
        literal=r44_arbiter.norm(value);source=r44_arbiter.norm(quote)
        end=r'(?![\d]|[.,]\d)' if literal[-1].isdigit() else r'(?!\w)'
        return re.search(r'(?<![\d.])(?<!\d,)'+re.escape(literal)+end,source) is not None
    return r44_arbiter.literal_supported(name,value,quote)


def verify_fields(packet: dict, relation: dict):
    chunks={c['chunk_id']:c for c in packet['chunks']}
    fields=relation.get('fields') or {};states={};known={};evidence=[]
    aliases=author_alias_resolution.definitions(packet)
    for name in r44_arbiter.FIELDS:
        value=fields.get(name);supported=[]
        if value is None or value=='' or value=='UNKNOWN':
            known[name]=None;states[name]='UNKNOWN';continue
        if not isinstance(value,str):
            known[name]=None;states[name]='INVALID_TYPE';continue
        if name in r44_arbiter.ENUMS and value not in r44_arbiter.ENUMS[name]:
            known[name]=None;states[name]='INVALID_ENUM';continue
        for ev in relation.get('evidence',[]):
            if not isinstance(ev,dict) or ev.get('field')!=name:continue
            c=chunks.get(ev.get('chunk_id'));q=ev.get('quote')
            if c is None or not isinstance(q,str) or not q or q not in c['text']:continue
            literal=(name not in r44_arbiter.DIRECT_LITERAL or _supported(name,value,q)
                     or _supported_in_cells(c,name,value))
            if not literal:
                literal=bool(author_alias_resolution.binding(name,value,q,aliases))
            if not literal:continue
            offsets=[m.start() for m in re.finditer(re.escape(q),c['text'])]
            item=dict(field=name,chunk_id=c['chunk_id'],asset_sha256=c.get('author_sha256',packet['source_sha256']),
                      locator=c['locator'],quote=q,offsets=offsets,state='SOURCE_QUOTE_CHECKED')
            supported.append(item);evidence.append(item)
        known[name]=value if supported else None
        states[name]='SOURCE_QUOTE_CHECKED' if supported else 'UNSUPPORTED'
    return known,states,evidence


def verify_context(packet, relation, fields, evidence):
    """Check explicit cross-section links; do not invent a comparator or a link."""
    chunks={c['chunk_id']:c for c in packet['chunks']};holds=[];links=[]
    scope=relation.get('scope') or {}
    refs=set()
    for ref in scope.get('author_experiment_refs',[]):refs|=_figrefs(ref)
    if refs and not refs.issubset(set().union(*(_figrefs(c['text']) for c in packet['chunks']))):
        holds.append('EXPERIMENT_REF_NOT_IN_AUTHOR_SOURCE')
    for link in scope.get('context_links',[]):
        if not isinstance(link,dict):holds.append('CONTEXT_LINK_SHAPE');continue
        a=chunks.get(link.get('from_chunk_id'));b=chunks.get(link.get('to_chunk_id'))
        anchor=chunks.get(link.get('evidence_chunk_id'));quote=link.get('quote')
        if not a or not b or not anchor or not isinstance(quote,str) or not quote or quote not in anchor['text']:
            holds.append('CONTEXT_LINK_SOURCE_UNSUPPORTED');continue
        ar=_figrefs(a['text'])|_figrefs(a.get('author_context',{}).get('container',{}).get('label') or '')
        br=_figrefs(b['text'])|_figrefs(b.get('author_context',{}).get('container',{}).get('label') or '')
        shared=any(x==y or x==re.sub(r'[a-z]$','',y) or y==re.sub(r'[a-z]$','',x) for x in ar for y in br)
        generic=bool(re.search(r'(?i)\ball (?:cells|experiments|groups|samples)|ATP (?:levels|content).*?(?:measur|determin)|(?:measur|determin).*?ATP',quote))
        if fields.get('assay_method') and _supported('assay_method',fields['assay_method'],quote) and re.search(r'(?i)measur|quantif|determin|assay',quote):generic=True
        links.append(dict(**link,state='AUTHOR_REF_LINKED' if shared else 'SHARED_METHOD_REVIEW' if generic else 'MODEL_LINK_REVIEW'))
    pair=relation.get('comparison') or {}
    pair_state='UNKNOWN'
    # Only an explicit AUTHOR comparison quote, not a model matched=true flag.
    if fields.get('treatment') and fields.get('comparator'):
        quote=pair.get('quote');c=chunks.get(pair.get('chunk_id'))
        if (c and isinstance(quote,str) and quote in c['text']
            and r44_arbiter.literal_supported('treatment',fields['treatment'],quote)
            and r44_arbiter.literal_supported('comparator',fields['comparator'],quote)
            and re.search(r'(?i)compar|relative|versus|\bvs\b|control',quote)):
            pair_state='AUTHOR_COMPARISON_QUOTE_SUPPORTED'
        else:
            # Legacy responses may cite a complete original comparison in their
            # own field evidence. This is a source check, not a guessed join.
            for ev in evidence:
                q=ev['quote']
                if (r44_arbiter.literal_supported('treatment',fields['treatment'],q)
                    and r44_arbiter.literal_supported('comparator',fields['comparator'],q)
                    and re.search(r'(?i)compar|relative|versus|\bvs\b|control',q)):
                    pair_state='AUTHOR_COMPARISON_QUOTE_SUPPORTED';break
    # Author comparison may name only the control while the intervention is
    # bound elsewhere in the SAME source chunk or by explicit experiment link.
    # This is a reviewable context join, not a same-quote requirement.
    if pair_state=='UNKNOWN' and fields.get('treatment') and fields.get('comparator'):
        q=pair.get('quote');pc=chunks.get(pair.get('chunk_id'))
        if (pc and isinstance(q,str) and q and q in pc['text']
            and r44_arbiter.literal_supported('comparator',fields['comparator'],q)
            and re.search(r'(?i)compar|relative|versus|\bvs\b|control|untreated',q)):
            treatment_ids={ev['chunk_id'] for ev in evidence if ev['field']=='treatment'}
            linked=pc['chunk_id'] in treatment_ids or any(
                {l['from_chunk_id'],l['to_chunk_id']}=={pc['chunk_id'],tid}
                and l['state'] in ('AUTHOR_REF_LINKED','SHARED_METHOD_REVIEW')
                for l in links for tid in treatment_ids)
            if linked:pair_state='AUTHOR_COMPARISON_CONTEXT_LINKED_REVIEW'
    if pair_state=='UNKNOWN' and fields.get('treatment') and fields.get('comparator'):
        tids={ev['chunk_id'] for ev in evidence if ev['field']=='treatment'}
        for ev in evidence:
            if ev['field']!='comparator' or not re.search(r'(?i)compar|relative|versus|control|untreated',ev['quote']):continue
            cid=ev['chunk_id']
            if cid in tids or any({l['from_chunk_id'],l['to_chunk_id']}=={cid,t} and l['state']=='AUTHOR_REF_LINKED' for l in links for t in tids):
                pair_state='AUTHOR_COMPARISON_CONTEXT_LINKED_REVIEW';break
    # A single explicit, incompatible figure in different role quotes is a
    # hard conflict. Broad paragraphs with many figures are NOT globally vetoed.
    refs_by_field={}
    for e in evidence:refs_by_field.setdefault(e['field'],set()).update(_figrefs(e['quote']))
    def compatible(a,b):
        return a==b or (a==re.sub(r'[a-z]$','',b)) or (b==re.sub(r'[a-z]$','',a))
    hard_conflict=False
    target=refs
    for field,source_refs in refs_by_field.items():
        if field in ('value','cell_model','dose','exposure_time','treatment','comparator') and len(target)==1 and len(source_refs)==1:
            if not any(compatible(a,b) for a in target for b in source_refs):
                holds.append('EXPERIMENT_REF_CONFLICT_'+field.upper());hard_conflict=True
    a=refs_by_field.get('value',set());b=refs_by_field.get('comparator',set())
    if len(a)==len(b)==1 and not any(compatible(x,y) for x in a for y in b):
        holds.append('COMPARATOR_EXPERIMENT_REF_CONFLICT');hard_conflict=True
    if hard_conflict:pair_state='CONFLICT_REVIEW_REQUIRED'
    if pair_state=='UNKNOWN':holds.append('MATCHED_COMPARATOR_REVIEW_REQUIRED')
    critical={e['chunk_id'] for e in evidence if e['field'] in ('cell_model','dose','exposure_time','treatment','comparator','value')}
    if len(critical)>1 and not links:holds.append('CROSS_SECTION_LINK_NOT_EXPLICIT')
    # Count author *quantities*, not digit runs: 10-3 M is one dose, while a
    # printed series or range is still several and still needs a split.
    from .quantity_literals import count_quantities
    for field in ('dose','exposure_time'):
        if fields.get(field) and count_quantities(fields[field])>1:
            holds.append('MULTIPLE_'+field.upper()+'_REQUIRE_SPLIT')
    # Check age context contradictions, but do not veto unrelated figures in
    # an otherwise correctly cited paragraph.
    cell=fields.get('cell_model') or ''
    for e in evidence:
        if e['field'] not in ('value','dose','exposure_time'):continue
        q=e['quote']
        if (re.search(r'(?i)\byoung\b',cell) and re.search(r'(?i)\baged\b',q) and not re.search(r'(?i)\byoung\b',q)) or (
            re.search(r'(?i)\baged\b',cell) and re.search(r'(?i)\byoung\b',q) and not re.search(r'(?i)\baged\b',q)):
            holds.append('CELL_AGE_CONTEXT_CONFLICT')
    return dict(pairing_state=pair_state,context_links=links,author_experiment_refs=sorted(refs),
                cross_section=len(critical)>1,hold_reasons=sorted(set(holds)),scientific_accuracy_verified=False)


def verify_measurements(packet,relation,fields):
    chunks={c['chunk_id']:c for c in packet['chunks']};checked=[];holds=[]
    for m in relation.get('measurements',[]):
        if not isinstance(m,dict):holds.append('MEASUREMENT_SCHEMA_HOLD');continue
        arm=m.get('arm');label=fields.get('treatment' if arm=='TREATED' else 'comparator')
        def supported(source,literal,field):
            if not isinstance(source,dict) or not isinstance(literal,str) or not literal:return None
            c=chunks.get(source.get('chunk_id'));q=source.get('quote')
            if not c or not isinstance(q,str) or not q or q not in c['text']:return None
            if not (_supported(field,literal,q) or _supported_in_cells(c,field,literal)):return None
            return dict(chunk_id=c['chunk_id'],quote=q,locator=c['locator'],asset_sha256=c.get('author_sha256',packet['source_sha256']))
        value_source=dict(chunk_id=m.get('chunk_id'),quote=m.get('quote'))
        ve=supported(value_source,m.get('value_literal'),'value')
        ue=supported(m.get('unit_source') or value_source,m.get('unit_literal'),'unit')
        ae=supported(m.get('arm_source') or value_source,m.get('arm_label'),'treatment')
        valid=(arm in ('TREATED','CONTROL') and label is not None and isinstance(m.get('arm_label'),str)
               and label.casefold().strip()==m['arm_label'].casefold().strip() and ve and ue and ae)
        if not valid:holds.append('MEASUREMENT_SOURCE_OR_ARM_HOLD');continue
        vc=chunks[ve['chunk_id']];context=' '.join(vc.get('author_context',{}).get('section_path',[]))
        if re.search(r'(?i)methods|experimental|materials',context) and re.search(r'(?i)standard curve|ATP standards?',vc['text']):
            holds.append('CALIBRATOR_NOT_SAMPLE_MEASUREMENT');continue
        # Do not let independently valid literals authorize a cross-experiment join.
        evidence_ids={x['chunk_id'] for x in (ve,ue,ae)}
        refs=[]
        for ev in (ve,ue,ae):
            cx=chunks[ev['chunk_id']];rf=_figrefs(ev['quote']) or _figrefs(cx.get('author_context',{}).get('container',{}).get('label') or '')
            if not rf and len(_figrefs(cx['text']))==1:rf=_figrefs(cx['text'])
            refs.append(rf)
        def compatible(a,b):return a==b or a==re.sub(r'[a-z]$','',b) or b==re.sub(r'[a-z]$','',a)
        if any(len(a)==len(b)==1 and not any(compatible(x,y) for x in a for y in b) for a in refs for b in refs):
            holds.append('MEASUREMENT_EXPERIMENT_REF_CONFLICT');continue
        if len({x['asset_sha256'] for x in (ve,ue,ae)})>1:
            links=(relation.get('scope') or {}).get('context_links',[])
            graph={}
            for l in links:
                a,b=l.get('from_chunk_id'),l.get('to_chunk_id')
                if a==b or a not in chunks or b not in chunks:continue
                if not supported(dict(chunk_id=l.get('evidence_chunk_id'),quote=l.get('quote')),l.get('quote'),'interpretation'):continue
                graph.setdefault(a,set()).add(b);graph.setdefault(b,set()).add(a)
            connected=set();pending=[ve['chunk_id']]
            while pending:
                node=pending.pop()
                if node not in connected:connected.add(node);pending.extend(graph.get(node,set())-connected)
            if not evidence_ids<=connected:
                holds.append('MEASUREMENT_CROSS_ASSET_LINK_REQUIRED');continue
        from .quantity_literals import scalar
        value=scalar(m.get('value_literal'))
        uncertainty=None
        if value is None and isinstance(m.get('value_literal'),str):
            # Preserve the author's mean and uncertainty. Do not assume SD
            # versus SEM, strip a dose, or invent a unit conversion.
            literal=m['value_literal'].strip().replace('−','-')
            parsed=re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)(?:\s*(.*?))?',literal)
            if parsed and (not parsed.group(3) or parsed.group(3).strip()==m.get('unit_literal','').strip()):
                value=scalar(parsed.group(1));uncertainty=scalar(parsed.group(2))
        if value is None:holds.append('MEASUREMENT_NOT_EXACT_SCALAR');continue
        checked.append(dict(**m,value=value,uncertainty=uncertainty,uncertainty_kind='AUTHOR_UNSPECIFIED' if uncertainty is not None else None,asset_sha256=ve['asset_sha256'],locator=ve['locator'],component_evidence=dict(value=ve,unit=ue,arm=ae)))
    return checked,holds


def normalize_candidate(packet, raw_relation, *, proposal_id=None, run=None):
    original=raw_relation
    if not isinstance(raw_relation,dict) or not isinstance(raw_relation.get('fields'),dict):
        return dict(proposal_id=proposal_id or digest(original),paper=packet['paper'],original=original,
                    relation=None,fields={k:None for k in r44_arbiter.FIELDS},field_states={k:'UNKNOWN' for k in r44_arbiter.FIELDS},
                    evidence=[],scope=dict(pairing_state='UNKNOWN',hold_reasons=['SCHEMA_HOLD']),holds=['SCHEMA_HOLD'],run=run or {})
    known,states,evidence=verify_fields(packet,raw_relation)
    scope=verify_context(packet,raw_relation,known,evidence)
    measurements,mholds=verify_measurements(packet,raw_relation,known)
    holds=list(scope['hold_reasons'])+mholds
    holds += ['FIELD_'+k.upper()+'_'+v for k,v in states.items() if v in ('UNSUPPORTED','INVALID_TYPE','INVALID_ENUM')]
    holds += ['UNKNOWN_'+k.upper() for k in ('chemical_identity','tested_form','cell_model','dose','exposure_time','assay_method','normalization') if known[k] is None]
    if known['endpoint']!='ATP_CONTENT':holds.append('ENDPOINT_NOT_VERIFIED_DIRECT_ATP_CONTENT')
    if known['assay_method'] and re.search(r'(?i)cell\s*titer\s*[- ]?glo',known['assay_method']):holds.append('VIABILITY_PROXY_ENDPOINT_REVIEW')
    return dict(proposal_id=proposal_id or digest(dict(paper=packet['paper'],source=packet['source_sha256'],relation=original)),
                paper=packet['paper'],original=original,relation=raw_relation,fields=known,field_states=states,
                evidence=evidence,measurements=measurements,scope=scope,holds=sorted(set(holds)),run=run or {})


def _quantity(literal,unit):
    if literal is None:return None
    m=re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*'+unit+r'\s*',literal,re.I)
    return float(m[1]) if m else None


def canonical_quantity(literal,kind):
    # Scalar explicit units only; never infer a missing unit or split a range.
    units=({'nm':.001,'µm':1,'μm':1,'um':1,'mm':1000,'m':1000000} if kind=='concentration'
           else {'h':1,'hr':1,'hrs':1,'hour':1,'hours':1,'min':1/60,'minutes':1/60,'minute':1/60,'d':24,'day':24,'days':24})
    if not isinstance(literal,str):return None
    if kind=='time':literal=re.sub(r'(?i)^\s*for\s+|\bconsecutive\s+', '',literal)
    # Only unambiguous exponent notation: a flattened "10−3" could also be
    # a range until the original JATS superscript is independently verified.
    scientific=re.fullmatch(r'\s*(?:(10)\s*(?:\^|\*\*)\s*([+\-−]?\d+)|(\d+(?:\.\d+)?)[eE]([+\-−]?\d+))\s*([a-zµμ]+)\s*',literal,re.I)
    if scientific:
        base,exponent,coefficient,e_exp,unit=scientific.groups()
        if unit.lower() not in units:return None
        try:
            value=(float(base)**int(exponent.replace('−','-')) if base else float(coefficient)*10.0**int(e_exp.replace('−','-')))*units[unit.lower()]
        except (ValueError,OverflowError):return None
        import math
        return value if math.isfinite(value) else None
    m=re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*([a-zµμ]+)\s*',literal,re.I)
    return float(m[1])*units[m[2].lower()] if m and m[2].lower() in units else None


def ingest_candidates(packets:dict, candidates:list[dict], target:Path):
    """Normalize into existing Factory tables; retain every proposal and field."""
    from .scientific_extraction_harness import replay_packet
    for p in packets.values():
        source=Path(p['source_path'])
        if Path(target).resolve()==source.resolve():raise ValueError('FACTORY_SOURCE_TARGET_COLLISION')
        replay_packet(p,source)
    papers=[];molecules=[];observations=[]
    for p in packets.values():
        papers.append(dict(paper_id=p['paper'],doi=p.get('doi'),title=p.get('paper_title'),source_kind='AUTHOR_JATS',
                           original_jats_sha256=p['source_sha256'],source_acquisition_note=p.get('coverage')))
    for c in candidates:
        if c['relation'] is None:continue
        p=packets[c['paper']];f=c['fields'];pid=c['proposal_id'];mid='harness-entity:'+digest(dict(paper=c['paper'],name=f['chemical_identity'],form=f['tested_form']))[:24]
        molecules.append(dict(molecule_id=mid,preferred_name=f['chemical_identity'] or 'UNKNOWN',
                              structure_status='UNRESOLVED_AUTHOR_NAME',chemical_form_caveat=f['tested_form'] or 'UNKNOWN'))
        percent=_quantity(f['value'],r'%')
        if percent is None and f.get('unit') and '%' in f['unit']:
            from .quantity_literals import scalar
            percent=scalar(f.get('value'))
        dose=canonical_quantity(f['dose'],'concentration');hours=canonical_quantity(f['exposure_time'],'time')
        values={};units=set()
        for arm in ('TREATED','CONTROL'):
            ms=[m for m in c.get('measurements',[]) if m['arm']==arm]
            if len(ms)==1:values[arm]=ms[0]['value'];units.add(ms[0]['unit_literal'])
        if len(units)>1:values={}
        loc=json.dumps(dict(field_evidence=c['evidence'],scope=c['scope'],measurements=c.get('measurements',[]),source_sha256=p['source_sha256']),ensure_ascii=False,sort_keys=True)
        measurement_meta={}
        for arm,key in [('TREATED','treated_measurement'),('CONTROL','control_measurement')]:
            ms=[m for m in c.get('measurements',[]) if m['arm']==arm]
            if len(ms)==1:measurement_meta[key]=dict(source_locator=json.dumps(ms[0],ensure_ascii=False,sort_keys=True),author_sha256=ms[0]['asset_sha256'])
        observations.append(dict(**measurement_meta,observation_id='harness:'+pid,paper_id=c['paper'],molecule_id=mid,
                cell_model=f['cell_model'],biological_context=f['stimulation'],assay_method=f['assay_method'],
                endpoint_kind=f['endpoint'],normalization=f['normalization'],exposure_h=hours,source_locator=loc,
                author_original_sha256=p['source_sha256'],treated_arm=f['treatment'],comparator_arm=f['comparator'],
                dose_uM=dose,author_reported_percent=percent,author_effect_literal=f['value'],effect_direction=f['direction'],
                review_state='LLM_FIELD_GROUNDED_CANDIDATE_REVIEW',hold_reasons_json=json.dumps(c['holds']),
                treated_value=values.get('TREATED'),control_value=values.get('CONTROL'),measurement_unit=next(iter(units)) if len(units)==1 else f['unit']))
    def provenance(db):
        for p in packets.values():
            for a in p.get('source_assets',[]):
                db.execute('INSERT OR IGNORE INTO source_assets VALUES (?,?,?,?,?,?)',(a['sha256'],p['paper'],a['path'],a.get('url'),a.get('license'),'JATS_OR_SI'))
        for c in candidates:
            run=c['run'];rid=run.get('run_id') or 'offline:'+digest(dict(paper=c['paper'],run=run))
            db.execute('INSERT OR IGNORE INTO extraction_runs VALUES (?,?,?,?,?,?,?)',(rid,c['paper'],run.get('model'),run.get('provider'),run.get('raw_response_sha256'),run.get('request_sha256'),json.dumps(run,ensure_ascii=False,sort_keys=True)))
            row=(c['proposal_id'],c['paper'],'r53:harness:'+c['proposal_id'] if c['relation'] is not None else None,rid,
                 json.dumps(c['original'],ensure_ascii=False,sort_keys=True),digest(c['original']),json.dumps(c['field_states']),json.dumps(c['scope']),json.dumps(c['holds']))
            old=db.execute('SELECT original_sha256 FROM harness_proposals WHERE proposal_id=?',(c['proposal_id'],)).fetchone()
            if old and old[0]!=digest(c['original']):raise ValueError('HARNESS_PROPOSAL_ID_CONFLICT')
            db.execute('INSERT OR IGNORE INTO harness_proposals VALUES (?,?,?,?,?,?,?,?,?)',row)
            for e in c['evidence']:
                db.execute('INSERT OR IGNORE INTO field_evidence VALUES (?,?,?,?,?,?,?,?)',
                  (c['proposal_id'],e['field'],digest(e),e['asset_sha256'],e['locator'],e['quote'],json.dumps(e['offsets']),e['state']))
            # Quantitative component evidence uses the existing provenance table.
            chunks={x['chunk_id']:x for x in packets[c['paper']]['chunks']}
            for m in c.get('measurements',[]):
                for part,e in m.get('component_evidence',{}).items():
                    name='measurement:'+m['arm']+':'+part
                    offsets=[x.start() for x in re.finditer(re.escape(e['quote']),chunks[e['chunk_id']]['text'])]
                    db.execute('INSERT OR IGNORE INTO field_evidence VALUES (?,?,?,?,?,?,?,?)',
                        (c['proposal_id'],name,digest(dict(measurement=m,part=part)),e['asset_sha256'],e['locator'],e['quote'],json.dumps(offsets),'SOURCE_QUOTE_CHECKED'))
    receipt=factory.ingest_records(papers,molecules,observations,Path(target),digest(dict(packets={k:p['source_sha256'] for k,p in packets.items()},proposals=[c['proposal_id'] for c in candidates])),extra_ddl=EXTRA_DDL,after_ingest=provenance)
    receipt.update(proposals=len(candidates),normalized_records=len(observations),malformed_retained=len(candidates)-len(observations))
    return receipt
