"""T1 v0.2 qualification over existing Factory candidates; no Gold mutation.

A/B/C labels are independent of legacy training approval. Every D row retains
source-grounded facts. No model/network calls or imputation in this module.
"""
import json,math,re,sqlite3
from collections import Counter,defaultdict
from dataclasses import dataclass
from pathlib import Path
from .llm_harness import digest
from .scientific_scope_store import normalize_candidate
from .atp_content_author_identity import _external_structure_match,_external_structure_records

SCHEMA='asterloom.auto-silver/t1-v0.2/1'
TIERS={'A':'SILVER_NUMERIC_PAIR','B':'SILVER_REPORTED_EFFECT','C':'SILVER_DIRECTION_WEAK','D':'SPARSE_UNLABELED'}
@dataclass(frozen=True)
class SilverPolicy:
    profile_id: str
    endpoint: str
    excluded_intervention_pattern: str = ''

def t1_policy():
    from .atp_context_profile import silver_policy
    return silver_policy()

X=('chemical_identity','tested_form','cell_model','dose','exposure_time','stimulation','normalization')

def identity(candidate,records=None):
    f=candidate['fields'];name=f.get('chemical_identity');form=f.get('tested_form')
    out=dict(state='UNRESOLVED',smiles=None,inchikey=None,scope='PARENT_NAME_CROSSWALK_ONLY_NOT_TESTED_SALT_OR_LOT',tested_form=form)
    if not name:return out
    # Do not map mixtures, delivery formulations, or qualified salts to a pure
    # tested substance. Parent lookup is distinct from tested-form resolution.
    if re.search(r'(?i)@|loaded|nanopart|extract|mixture|hydrochlor|salt|hydrate|liposom',form or name):return dict(out,state='FORM_SPECIFIC_MAPPING_REQUIRED')
    match=_external_structure_match(name,name,[],records=records)
    if match and match.get('canonical_structure_verified'):
        record=next(r for r in (records if records is not None else _external_structure_records()) if r['external_id']==match['external_id'])
        if not record.get('isomeric_smiles'):return out
        return dict(out,state='PINNED_REGISTRY_PARENT_MATCH',smiles=record['isomeric_smiles'],inchikey=match['inchikey'],
            registry=match,registry_record_sha256=digest(record),tested_form_resolved=False)
    if records is not None:return out
    from .ch1_parent_registry import resolve_parent
    res=resolve_parent(name,form)
    if res.get('state')=='PINNED_NATURAL_PARENT_MATCH' and res.get('smiles') and res.get('inchikey'):
        return dict(out,state='PINNED_REGISTRY_PARENT_MATCH',smiles=res['smiles'],inchikey=res['inchikey'],
            registry=res,registry_record_sha256=digest(res),tested_form_resolved=False)
    return out


def classify(c,packet,policy=None):
    policy=policy or t1_policy()
    f=c['fields'];q=c['scope'].get('quality_audit',{});reasons=[]
    if policy.profile_id=='ATP_CONTENT':
        from .atp_context_profile import source_intervention_scope,intervention_role_reasons
        reasons+=source_intervention_scope(c,packet)
        reasons+=intervention_role_reasons(f,packet)
    fresh=normalize_candidate(packet,c['original'])
    if fresh['fields']!=f:raise ValueError('SILVER_FIELD_VERIFIER_DRIFT')
    from .source_stratum import verify_source_stratum
    stratum=verify_source_stratum(packet,c['original'],fresh.get('measurements',[]))
    if stratum is False:reasons.append('AUTHOR_STRATUM_PAIR_UNSUPPORTED')
    if not q or not c['run'].get('raw_response_sha256'):reasons.append('MISSING_STRUCTURED_SCIENCE_REVIEW')
    if f['endpoint']!=policy.endpoint:reasons.append('OUTSIDE_DIRECT_ATP_CONTENT')
    if not f.get('treatment') or not f.get('comparator'):reasons.append('MISSING_TESTED_ARM_OR_COMPARATOR')
    if not f.get('assay_method'):reasons.append('ATP_ASSAY_NOT_SOURCE_BOUND')
    if policy.profile_id=='ATP_CONTENT':
        # Unresolved biological/material interventions remain facts, not a
        # substitute for the T1 small-molecule intervention.
        name=' '.join(f.get(k) or '' for k in ('chemical_identity','tested_form','treatment'))
        treatment=(f.get('treatment') or '').strip()
        form_context=' '.join(f.get(k) or '' for k in ('chemical_identity','tested_form','treatment'))
        if re.search(r'(?i)\b(?:mixture|combination|co-formulation|coformulation|cocktail)\b',form_context) or re.search(r'(?i)\b\d+\s*[a-z]+\s*:\s*\d+\s*[a-z]+\b',form_context):
            reasons.append('MIXTURE_NOT_SINGLE_SMALL_MOLECULE')
        if re.search(r'(?i)\b(?:liposomes?|nanoparticles?|micelles?|hydrogels?)\b', ' '.join((f.get('treatment') or '',f.get('tested_form') or ''))):
            reasons.append('AUTHOR_IDENTIFIED_DELIVERY_SYSTEM_NOT_FREE_MOLECULE')
        for label in (f.get('treatment'),):
            if label and not re.search(r'(?i)\bfree\b',f.get('tested_form') or '') and any(re.search(r'(?i)\b(?:mixed\s+micell\w*|nanoparticles?|liposomes?|hydrogels?)\b[^.]{0,200}'+re.escape(label)+r'(?![\w-])',ch['text']) for ch in packet['chunks']):
                reasons.append('AUTHOR_IDENTIFIED_DELIVERY_SYSTEM_NOT_FREE_MOLECULE');break
        if re.fullmatch(r'(?i)(?:excessive iron|iron overload|iron loading|iron accumulation|iron exposure|ferric overload)(?:\s+(?:group|condition|treatment))?',treatment):reasons.append('IRON_STRESS_NOT_SMALL_MOLECULE_INTERVENTION')
        for ch in packet['chunks']:
            text=ch['text']
            for match in re.finditer(r'(?i)\b(?:hydrogel|nanocomposite|scaffold)\s*\(\s*([A-Za-z][\w-]{1,15})\s*\)',text):
                if any((f.get(k) or '').casefold()==match.group(1).casefold() for k in ('treatment','tested_form','chemical_identity')):reasons.append('COMPOSITE_MATERIAL_NOT_SMALL_MOLECULE_INTERVENTION')
        method=f.get('assay_method') or ''
        method_ids={e['chunk_id'] for e in fresh['evidence'] if e['field']=='assay_method'}
        method_context=' '.join(ch['text'] for ch in packet['chunks'] if ch['chunk_id'] in method_ids)
        lysate_source=bool(re.search(r'(?is)lys(?:ate|is|ed|ing|e)\w*.{0,220}centrifug\w*.{0,120}supernatant',method_context))
        if re.search(r'(?i)\b(?:cell culture|culture medium|culture media|extracellular)\s+supernatant\b',method):reasons.append('EXTRACELLULAR_ATP_NOT_INTRACELLULAR_CONTENT')
        elif re.search(r'(?i)\bcell supernatant\b',method) and not re.search(r'(?i)lys(?:ate|is|ed)|homogeni[sz]',method) and not lysate_source:reasons.append('ATP_SAMPLE_COMPARTMENT_UNRESOLVED')
    if f.get('direction') in ('INCREASE','DECREASE'):
        evidence=' '.join(e['quote'] for e in fresh['evidence'] if e['field']=='direction')
        direct=(r'(?i)\b(?:increas\w*|rais\w*|elevat\w*|enhanc\w*|restor\w*|revers\w*|recover\w*|ameliorat\w*|higher|upregulat\w*)\b' if f['direction']=='INCREASE' else r'(?i)\b(?:decreas\w*|reduc\w*|lower\w*|declin\w*|diminish\w*|deplet\w*|suppress\w*|inhibit\w*|downregulat\w*)\b')
        if not re.search(direct,evidence):reasons.append('DIRECTION_NOT_SUPPORTED_BY_AUTHOR_QUOTE')
        elif re.search(r"(?i)\b(?:no|not(?!\s+only)|without|failed to|did not|does not)\s+(?:\w+\s+){0,3}(?:increas\w*|decreas\w*|reduc\w*|elevat\w*|rais\w*|lower\w*|inhibit\w*|suppress\w*)\b",evidence):reasons.append('NEGATED_DIRECTION_NOT_A_SIGNED_EFFECT')
        direction_chunks={e['chunk_id'] for e in fresh['evidence'] if e['field']=='direction'}
        context_chunks={e['chunk_id'] for e in fresh['evidence'] if e['field'] in ('treatment','comparator')}
        linked=bool(direction_chunks & context_chunks) or any(
            l.get('state')=='AUTHOR_REF_LINKED' and ((l['from_chunk_id'] in direction_chunks and l['to_chunk_id'] in context_chunks) or (l['to_chunk_id'] in direction_chunks and l['from_chunk_id'] in context_chunks))
            for l in fresh['scope']['context_links'])
        if not linked:reasons.append('DIRECTION_CONTEXT_NOT_LINKED_TO_ARMS')
    if re.search(r'(?i)\bor\b',f.get('comparator') or ''):reasons.append('MULTIPLE_COMPARATOR_CONDITIONS_UNRESOLVED')
    if q.get('comparison_role') not in ('MATCHED_CONTROL','RESCUE_COMPARATOR'):reasons.append('NOT_MATCHED_EXPERIMENT_CONTROL')
    if fresh['scope']['pairing_state'] not in ('AUTHOR_COMPARISON_QUOTE_SUPPORTED','AUTHOR_COMPARISON_CONTEXT_LINKED_REVIEW'):reasons.append('COMPARISON_SCOPE_NOT_SUPPORTED')
    for h in fresh['holds']:
        if any(x in h for x in ('CONFLICT','CONTEXT_LINK_SOURCE_UNSUPPORTED','EXPERIMENT_REF_NOT_IN_AUTHOR_SOURCE','MULTIPLE_DOSE','MEASUREMENT_')):reasons.append(h)
    for i in q.get('issues',[]):
        if i.get('category')=='AUTHOR_CONFLICT' and i.get('adjudication_state') not in ('SUPERSEDED_FIELD_JUDGEMENT','UNVERIFIED_MODEL_CITATION'):reasons.append('AUTHOR_SOURCE_CONFLICT')
    if re.search(r'(?i)\b(?:and|or)\b|[,;]',f.get('cell_model') or ''):reasons.append('MULTIPLE_MODELS_UNRESOLVED')
    if policy.excluded_intervention_pattern and re.search(policy.excluded_intervention_pattern,f.get('treatment') or ''):reasons.append('GENETIC_INTERVENTION_NOT_SMALL_MOLECULE_TASK')
    kind=q.get('effect_kind','UNKNOWN');literal=f.get('value');direction=f.get('direction')
    if kind in ('PERCENT_CHANGE','PERCENT_OF_CONTROL') and literal:
        quoted=' '.join(e['quote'] for e in fresh['evidence'] if e['field'] in ('direction','value','treatment','comparator'))
        number=re.search(r'\d+(?:\.\d+)?',str(literal))
        if number:
            v=re.escape(number.group())
            to_level=bool(re.search(r'(?i)\b(?:reduc\w*|decreas\w*|declin\w*|fell|dropped|increas\w*|rose)(?:\s+\w+){0,5}\s+to\s+'+v+r'\s*%',quoted))
            by_amount=bool(re.search(r'(?i)\b(?:reduc\w*|decreas\w*|declin\w*|fell|dropped|increas\w*|rose)(?:\s+\w+){0,5}\s+by\s+'+v+r'\s*%',quoted))
            if (kind=='PERCENT_CHANGE' and to_level) or (kind=='PERCENT_OF_CONTROL' and by_amount):reasons.append('PERCENT_SEMANTICS_CONFLICT')
    if kind.startswith('SHARED') or kind in ('REPORTED_BOUND','ABSOLUTE_SINGLE_ARM'):reasons.append('NO_EXACT_MATCHED_RELATIVE_Y')
    # Independent fields can be missing. Conflicts and unsupported Y cannot.
    y_type='UNKNOWN';value=None;tier='D'
    ms=fresh.get('measurements',[]);by_arm={a:[m for m in ms if m['arm']==a] for a in ('TREATED','CONTROL')}
    if not reasons and all(len(by_arm[a])==1 for a in by_arm):
        t,u=by_arm['TREATED'][0],by_arm['CONTROL'][0]
        if t['unit_literal']==u['unit_literal'] and t['value']>0 and u['value']>0 and f.get('normalization'):
            tier,y_type,value='A','LOG2FC',math.log2(t['value']/u['value'])
    if tier=='D' and not reasons and literal is not None and fresh['field_states']['value']=='SOURCE_QUOTE_CHECKED':
        if kind in ('PERCENT_CHANGE','PERCENT_OF_CONTROL'):
            m=re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*%?\s*',literal)
            if m:
                value=float(m[1]);y_type=kind
                if kind=='PERCENT_CHANGE':
                    if direction not in ('INCREASE','DECREASE'):value=None
                    elif (value<0 and direction=='INCREASE') or (literal.lstrip().startswith('+') and direction=='DECREASE'):value=None
                    else:value=abs(value)*(1 if direction=='INCREASE' else -1)
                if value is not None:tier='B'
        elif kind in ('AUTHOR_FOLD_PHRASE','FOLD_CHANGE'):
            m=re.fullmatch(r'(?i)\s*(two|\d+(?:\.\d+)?)[- ]fold(?: increase)?\s*',literal)
            if m and direction=='INCREASE':tier,y_type,value='B','FOLD_CHANGE',2.0 if m[1].lower()=='two' else float(m[1])
        if tier=='D':reasons.append('REPORTED_VALUE_SEMANTICS_UNRESOLVED')
    if tier=='D' and not reasons and direction in ('INCREASE','DECREASE') and fresh['field_states']['direction']=='SOURCE_QUOTE_CHECKED':tier,y_type='C','DIRECTION'
    if tier=='D':
        value=None;y_type='UNKNOWN'
        if not reasons:reasons.append('NO_SOURCE_BOUND_NUMERIC_OR_DIRECTION_Y')
    structure=identity(c)
    key=dict(paper=c['paper'],refs=sorted(fresh['scope']['author_experiment_refs']),
        **{k:f.get(k) for k in ('chemical_identity','tested_form','treatment','comparator','cell_model','dose','exposure_time','stimulation','endpoint','normalization')})
    if stratum:key['author_stratum']=stratum
    return dict(proposal_id=c['proposal_id'],contrast_id='r53:harness:'+c['proposal_id'],paper_id=c['paper'],experiment_key=digest(key),tier=tier,auto_silver=TIERS[tier],
        y_type=y_type,y_literal=literal,effect_value=value,direction=direction if tier in ('A','B','C') else None,
        missing_x_mask={**{k:f.get(k) is None for k in X},'smiles':structure['smiles'] is None,'inchikey':structure['inchikey'] is None},risk_flags=sorted(set(c['holds']+(['FOLD_LITERAL_CONVENTION_RETAINED'] if kind=='AUTHOR_FOLD_PHRASE' else []))),
        downgrade_reasons=sorted(set(reasons)),source_sha=packet['source_sha256'],model_run_sha=c['run']['raw_response_sha256'],
        reviewer_verdict=q.get('verdict'),schema_version=SCHEMA,structure=structure,duplicate_of=None)


def qualify(candidates,packets,policy=None):
    rows=[classify(c,packets[c['paper']],policy) for c in candidates];groups=defaultdict(list)
    for r in rows:groups[r['experiment_key']].append(r)
    for group in groups.values():
        labels={(r['y_type'],r['effect_value'],r['direction']) for r in group if r['tier']!='D'}
        # Same type but inconsistent effect/direction cannot be resolved by order.
        conflict=len({d for _,_,d in labels if d in ('INCREASE','DECREASE')})>1 or any(len({(v,d) for t,v,d in labels if t==typ})>1 for typ,_,_ in labels)
        if conflict:
            for r in group:r.update(tier='D',auto_silver=TIERS['D'],y_type='UNKNOWN',effect_value=None,direction=None,downgrade_reasons=sorted(set(r['downgrade_reasons']+['DUPLICATE_EXPERIMENT_LABEL_CONFLICT'])))
        if len(group)>1 and any(r['missing_x_mask'][k] for r in group for k in ('cell_model','dose')):
            for r in group:r['risk_flags']=sorted(set(r['risk_flags']+['POTENTIAL_DUPLICATE_CONTEXT_INCOMPLETE']))
            continue
        ordered=sorted(group,key=lambda r:(r['tier'],r['proposal_id']))
        for r in ordered[1:]:r['duplicate_of']=ordered[0]['proposal_id']
    return rows


def store(db_path,rows):
    """Add qualification to the SAME Factory; preserve source tables and Gold."""
    with sqlite3.connect(db_path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('''CREATE TABLE IF NOT EXISTS auto_silver (
          proposal_id TEXT PRIMARY KEY REFERENCES harness_proposals(proposal_id),
          contrast_id TEXT NOT NULL REFERENCES contrasts(contrast_id), paper_id TEXT NOT NULL,
          experiment_key TEXT NOT NULL,tier TEXT NOT NULL CHECK(tier IN ('A','B','C','D')),
          auto_silver TEXT NOT NULL,y_type TEXT NOT NULL,y_literal TEXT,effect_value REAL,direction TEXT,
          missing_x_mask TEXT NOT NULL,risk_flags TEXT NOT NULL,downgrade_reasons TEXT NOT NULL,
          source_sha TEXT NOT NULL,model_run_sha TEXT NOT NULL,reviewer_verdict TEXT,schema_version TEXT NOT NULL,
          structure_json TEXT NOT NULL,duplicate_of TEXT)''')
        keys=list(rows[0]) if rows else []
        for r in rows:
            prior=db.execute('SELECT tier,duplicate_of,risk_flags FROM auto_silver WHERE proposal_id=?',(r['proposal_id'],)).fetchone()
            if prior and prior[0]=='D' and r['tier']=='D' and prior[1] and 'AUTHOR_PAIR_SUPERSEDED' in json.loads(prior[2] or '[]'):
                if db.execute('SELECT 1 FROM auto_silver WHERE proposal_id=?',(prior[1],)).fetchone():
                    r['duplicate_of']=prior[1]
                    r['risk_flags']=sorted(set(r['risk_flags']+['AUTHOR_PAIR_SUPERSEDED']))
            db.execute('INSERT OR REPLACE INTO auto_silver VALUES ('+','.join('?' for _ in range(19))+')',tuple(json.dumps(r[k],ensure_ascii=False,sort_keys=True) if isinstance(r[k],(list,dict)) else r[k] for k in keys))
            s=r['structure']
            if s['smiles'] and s['inchikey']:
                db.execute('UPDATE tested_entities SET smiles=?,inchikey=?,structure_status=? WHERE molecule_id=(SELECT molecule_id FROM contrasts WHERE contrast_id=?)',
                    (s['smiles'],s['inchikey'],s['state']+':PARENT_ONLY',r['contrast_id']))
        # Qualification columns first; the original Factory record stays joined.
        for view,where in [('silver_numeric',"s.tier IN ('A','B')"),('silver_direction',"s.tier='C'"),('sparse_unlabeled',"s.tier='D'"),('sparse_facts','1'),('silver_structure_numeric',"s.tier IN ('A','B') AND m.inchikey IS NOT NULL")]:
            db.execute('DROP VIEW IF EXISTS '+view)
            db.execute(f'''CREATE VIEW {view} AS SELECT s.*,m.smiles,m.inchikey,e.cell_model,e.biological_context,e.assay_method,e.normalization,e.endpoint_kind,c.author_direction AS reported_direction,c.dose_uM,e.exposure_h,c.molecule_id,
                a.author_label AS treated_arm,b.author_label AS comparator_arm,e.source_locator
                FROM auto_silver s JOIN contrasts c USING(contrast_id) JOIN experiments e USING(experiment_id)
                JOIN tested_entities m USING(molecule_id) JOIN arms a ON a.arm_id=c.treated_arm_id JOIN arms b ON b.arm_id=c.control_arm_id
                WHERE s.duplicate_of IS NULL AND {where}''')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SILVER_FOREIGN_KEY_FAILURE')
    return dict(tiers={t:sum(r['tier']==t and not r['duplicate_of'] for r in rows) for t in TIERS},duplicates=sum(r['duplicate_of'] is not None for r in rows),
        numeric_y=dict(Counter(r['y_type'] for r in rows if r['tier'] in ('A','B') and not r['duplicate_of'])),
        direction_y=dict(Counter(r['direction'] for r in rows if r['tier']=='C' and not r['duplicate_of'])),
        structure_mapped=sum(r['structure']['smiles'] is not None for r in rows),structure_numeric=sum(r['structure']['smiles'] is not None and r['tier'] in ('A','B') and not r['duplicate_of'] for r in rows))
