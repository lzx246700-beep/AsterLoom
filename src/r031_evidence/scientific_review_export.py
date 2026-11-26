"""Export source-replayed LLM proposals to the existing isolated three tables."""
from __future__ import annotations
import csv, hashlib, json, re, sqlite3, tempfile
from decimal import Decimal, InvalidOperation
from contextlib import closing
from pathlib import Path
from .atp_content_export import source_name_identity
from .atp_content_relations import verify_relations
from . import r44_arbiter, r44_field_review, r44_packets, author_ordered_effects, atp_content_relations, atp_content_export, atp_content_author_identity, scientific_extraction_harness, scientific_quote_remap, scientific_json_repair, author_percent_control, author_alias_resolution
from .r44_arbiter import validate
from .r44_field_review import review_relations
from .author_ordered_effects import verify as verify_ordered_effect
from .r44_packets import verify_packet
from .scientific_extraction_harness import replay_packet


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def validation_fingerprint():
    modules=(r44_arbiter,r44_field_review,r44_packets,author_ordered_effects,atp_content_relations,atp_content_export,atp_content_author_identity,scientific_extraction_harness,scientific_quote_remap,scientific_json_repair,author_percent_control,author_alias_resolution)
    return {m.__name__:sha(m.__file__) for m in modules} | {'scientific_review_export':sha(__file__)}

def _native_match(relation, candidates, source_sha, aliases=None):
    f=relation['fields'];ev=relation['evidence']
    if any(x in relation.get('risk_flags',[]) for x in ('SYNTAX_REPAIR_REVIEW_REQUIRED','COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED','CELL_AGE_CONTEXT_CONFLICT','AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE','AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED')):return None
    if f['endpoint'] != 'ATP_CONTENT' or not all(f.get(k) for k in ('chemical_identity','cell_model','dose','exposure_time','treatment','comparator','unit','value')):
        return None
    value_match=re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*(?:[±]\s*(\d+(?:\.\d+)?))?\s*',f['value'])
    if not value_match:return None
    try: wanted=Decimal(value_match.group(1));wanted_uncertainty=Decimal(value_match.group(2)) if value_match.group(2) else None
    except InvalidOperation:return None
    tm=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hours?)\s*',f['exposure_time'],re.I)
    if not tm:return None
    byfield={k:[e for e in ev if e['field']==k] for k in ('value','comparator','unit','dose')}
    common=set(e['chunk_id'] for e in byfield['value'])
    for k in ('comparator','unit','dose'):common &= set(e['chunk_id'] for e in byfield[k])
    if not common:return None
    matches=[]
    for c in candidates:
        pts=c.get('native_points') or {};a=pts.get('treated') or {};b=pts.get('control') or {}; fields=c.get('fields') or {}
        if c.get('numeric_origin')!='AUTHOR_TABLE_REPORTED' or not a or not b:continue
        if not all((a.get('source_sha256')==source_sha,b.get('source_sha256')==source_sha,a.get('table_id')==b.get('table_id'),a.get('cell_column')==b.get('cell_column'),a.get('unit_author_literal')==b.get('unit_author_literal')==f['unit'])):continue
        anchors=[e.get('source_anchor','') for e in a.get('value_evidence',[])]
        table_anchors={v.split('/table[',1)[0] for v in anchors if '/table[' in v}
        valid_chunks={e['chunk_id'] for e in byfield['value'] if e['chunk_id'] in common and e.get('locator') in table_anchors}
        if not valid_chunks:continue
        # The author may label a single arm as either the compound name or
        # its exact table group label, e.g. 'Compound (5 μM)'. Only accept the
        # latter if chemical and dose match the same replayed native group.
        treatment=str(f['treatment']).strip(); chemical=f['chemical_identity'].strip(); dose_literal=f['dose'].strip()
        group=str(a.get('author_group_literal') or '').strip()
        plain=treatment.casefold()==chemical.casefold()
        labelled=(treatment.casefold()==group.casefold() and bool(re.fullmatch(
            re.escape(chemical)+r'\s*\(\s*'+re.escape(dose_literal)+r'\s*\)',group,re.I)))
        # The model may name the author-defined full name while the table uses
        # the author's abbreviation (or the reverse). Accept only an explicit,
        # unambiguous author declaration from this same packet.
        same_entity=bool(aliases and treatment.casefold()!=chemical.casefold() and
            author_alias_resolution.equivalent(treatment,chemical,aliases))
        group_agent=re.fullmatch(r'(.*?)\s*\(\s*(.*?)\s*\)',group)
        alias_labelled=bool(aliases and group_agent and
            group_agent.group(2).strip().casefold()==dose_literal.casefold() and
            group_agent.group(1).strip().casefold()!=chemical.casefold() and
            author_alias_resolution.equivalent(group_agent.group(1),chemical,aliases))
        if (not (str(c.get('compound_name','')).casefold()==chemical.casefold() or
                 author_alias_resolution.equivalent(c.get('compound_name',''),chemical,aliases)) or
            not (plain or labelled or same_entity or alias_labelled) or
            str((fields.get('dose_original') or {}).get('value','')).casefold()!=f['dose'].casefold() or
            str((fields.get('comparator_arm') or {}).get('value','')).casefold()!=f['comparator'].casefold() or
            f['cell_model'].casefold() not in str((fields.get('cell_model') or {}).get('value','')).casefold()):continue
        try:
            if Decimal(str(a['numeric_value']))!=wanted or Decimal(str((fields.get('total_compound_exposure_h') or {})['value']))!=Decimal(tm.group(1)):continue
            if wanted_uncertainty is not None and Decimal(str(a.get('uncertainty'))) != wanted_uncertainty:continue
        except (InvalidOperation,KeyError,TypeError):continue
        if not any(e['chunk_id'] in valid_chunks and f['value'] in e['source_literal'] and f['value'] in a.get('source_literal','') for e in byfield['value']):continue
        comparator_literal=any(b.get('source_literal','') in e['source_literal'] for e in byfield['comparator'])
        # An explicit author comparison marker in the same table can identify
        # the control group; the actual control value still comes from the
        # independently replayed author-native control cell, never the model.
        comparator_marker=any(
            e['chunk_id'] in valid_chunks and re.search(r'\b(?:compared\s+(?:with|to)|versus|vs\.?)\s+control\b',e['source_literal'],re.I)
            for e in byfield['comparator']) and str(b.get('author_group_literal','')).strip().casefold()==f['comparator'].strip().casefold()=='control'
        table_compare=any(re.search(r'\b(?:compared\s+(?:with|to)|versus|vs\.?)\s+control\b',e.get('quote',''),re.I) for e in a.get('table_evidence',[]))
        control_group_quoted=any(e['chunk_id'] in valid_chunks and e['source_literal'].strip().casefold()=='control' for e in byfield['comparator']) and str(b.get('author_group_literal','')).strip().casefold()==f['comparator'].strip().casefold()=='control'
        if not (comparator_literal or comparator_marker or (table_compare and control_group_quoted)):continue
        if not a.get('author_group_literal') or f['dose'].casefold() not in a['author_group_literal'].casefold():continue
        matches.append(c)
    return matches[0] if len(matches)==1 else None


def export(packet_path,packet_sha,source_path,model_output_path,model_output_sha,
           registry_path,registry_sha,out,*,allow_isolated_relations=False, rule_packet_path=None, rule_packet_sha=None, drop_unsupported_fields=False, remap_unique_quotes=False):
    packet_path, source_path, model_output_path, registry_path, out = map(lambda x:Path(x).resolve(),
        (packet_path,source_path,model_output_path,registry_path,out))
    if out.exists() or out.is_relative_to(source_path.parent) or out.is_relative_to(packet_path.parent) or out.is_relative_to(registry_path.parent):raise ValueError('SCIENTIFIC_EXPORT_NEW_DIRECTORY_REQUIRED')
    if any(x in str(out) for x in ('phase1b_r42/production_home','phase1b_r43/full_replay_home','state/current')):
        raise ValueError('SCIENTIFIC_EXPORT_PROTECTED_PATH')
    if sha(registry_path)!=registry_sha or sha(model_output_path)!=model_output_sha:
        raise ValueError('SCIENTIFIC_EXPORT_INPUT_SHA_MISMATCH')
    packet=verify_packet(packet_path,packet_sha);replay=replay_packet(packet,source_path)
    aliases=author_alias_resolution.definitions(packet)
    if bool(rule_packet_path)!=bool(rule_packet_sha):raise ValueError('SCIENTIFIC_RULE_PACKET_AND_SHA_REQUIRED')
    native=[];rule_replay=None
    if rule_packet_path:
        if sha(rule_packet_path)!=rule_packet_sha:raise ValueError('SCIENTIFIC_RULE_PACKET_SHA_MISMATCH')
        rules=json.loads(Path(rule_packet_path).read_text())
        if rules.get('source_sha256')!=packet['source_sha256']:raise ValueError('SCIENTIFIC_RULE_SOURCE_MISMATCH')
        rule_replay=verify_relations(rules,source_path);native=rules.get('candidates',[])

    model=json.loads(model_output_path.read_text())
    journal=model_output_path.parent
    request=json.loads((journal/'REQUEST.json').read_text())
    result=json.loads((journal/'RESULT.json').read_text())
    raw=json.loads((journal/'RAW_HTTP_RESPONSE.json').read_text())
    if (request.get('packet_sha256')!=packet_sha or
        request.get('source_replay',{}).get('source_sha256')!=packet['source_sha256'] or
        result.get('model_output_sha256')!=model_output_sha or
        result.get('status') not in (('SOURCE_QUOTE_CHECKED_REVIEW_ONLY','VALIDATION_HOLD') if allow_isolated_relations else ('SOURCE_QUOTE_CHECKED_REVIEW_ONLY',)) or
        raw.get('model')!=model.get('model')):
        raise ValueError('SCIENTIFIC_EXPORT_MODEL_JOURNAL_MISMATCH')
    syntax_repair=scientific_json_repair.verify_journal_answer(journal,raw,model)
    if syntax_repair and not allow_isolated_relations:raise ValueError('SCIENTIFIC_SYNTAX_REPAIR_REQUIRES_ISOLATION')
    if (drop_unsupported_fields or remap_unique_quotes) and not allow_isolated_relations:raise ValueError('SCIENTIFIC_SANITIZE_REQUIRES_ISOLATION')
    if allow_isolated_relations:
        checked=(scientific_quote_remap.review_with_remap(packet,model['answer'],source_path,drop_unsupported_fields=drop_unsupported_fields) if remap_unique_quotes else review_relations(packet,model['answer'],drop_unsupported_fields=drop_unsupported_fields))
        source_count=checked['source_relation_count']; rejected=checked['rejected']
    else:
        checked=validate(packet,model['answer']);source_count=len(checked['relations']);rejected=[]
    if packet['paper'] != model['answer']['paper'] or not re.fullmatch(r'PMC[0-9]+',packet['paper']):
        raise ValueError('SCIENTIFIC_EXPORT_PAPER_ID_INVALID')
    with closing(sqlite3.connect(registry_path.as_uri()+'?mode=ro',uri=True)) as reg:
        schemas=[reg.execute('select sql from sqlite_master where type="table" and name=?',(t,)).fetchone()[0]
                 for t in ('molecules','papers','observations')]
    molecules={};records=[];skips=[]
    for x in checked['relations']:
        f=x['fields'];name=f['chemical_identity'];index=x['review_index']
        if not name:
            skips.append({'index':index,'reason':'CHEMICAL_IDENTITY_MISSING'});continue
        ident=source_name_identity({'compound_name':name},packet['source_sha256']);molecules[ident['molecule_id']]=ident
        ev=x['evidence']; flags=sorted(set(x['risk_flags']+['MODEL_PROPOSAL_REQUIRES_INDEPENDENT_REVIEW','EXPERIMENT_SCOPE_NOT_INDEPENDENTLY_VERIFIED']+(['SYNTAX_REPAIR_REVIEW_REQUIRED'] if syntax_repair else [])))
        endpoint=f['endpoint'] or 'UNKNOWN'
        if endpoint=='ATP_CONTENT' and 'ATP_PRODUCTION_TERMINOLOGY_REQUIRES_METHOD_REVIEW' in flags:
            endpoint='ATP_PRODUCTION_REPORTED'
        endpoint=endpoint+'_MODEL_CANDIDATE'
        # Numeric candidate is allowed only for a single literal percentage
        # quoted in the same author source block as its named comparator.
        percent=None
        value=f['value']; value_ev=[e for e in ev if e['field']=='value']; cmp_ev=[e for e in ev if e['field']=='comparator']
        if (isinstance(value,str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value) and f['unit']=='%'
            and all(f.get(k) for k in ('chemical_identity','cell_model','dose','exposure_time','treatment','comparator'))
            and 'MULTI_ARM_EFFECT_MUST_BE_SPLIT' not in flags and 'CELL_AGE_CONTEXT_CONFLICT' not in flags and 'AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE' not in flags and 'AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED' not in flags and 'COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED' not in flags and 'SYNTAX_REPAIR_REVIEW_REQUIRED' not in flags):
            common_ids=set(e['chunk_id'] for e in value_ev)&set(e['chunk_id'] for e in cmp_ev)
            if any(e['chunk_id'] in common_ids and re.search(r'(?<![\d.])'+re.escape(value)+r'\s*%',e['source_literal']) for e in value_ev):
                percent=float(value)
        ordered=verify_ordered_effect(packet,model['answer']['relations'][index]) if not any(x in flags for x in ('SYNTAX_REPAIR_REVIEW_REQUIRED','COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED','CELL_AGE_CONTEXT_CONFLICT','AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE','AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED')) else None
        if ordered:
            percent=ordered['author_reported_percent']
            flags=sorted(set(flags+['AUTHOR_ORDERED_PAIR_REVIEW_REQUIRED']))
        percent_control=None
        if not any(z in flags for z in ('CELL_AGE_CONTEXT_CONFLICT','COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED','AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED')):
            percent_control=author_percent_control.verify(packet,x)
        if percent_control:
            percent=percent_control['author_reported_percent']
            flags=sorted(set(flags+['PERCENT_OF_CONTROL_NOT_PERCENT_DECREASE','AUTHOR_ORDERED_PERCENT_CONTROL_REVIEW_REQUIRED']))
        matched=_native_match(x,native,packet['source_sha256'],aliases) if native else None
        if matched:
            flags=sorted(set(flags+['LLM_RULE_AUTHOR_TABLE_CONCORDANT_STILL_REVIEW_ONLY']))
            if any(e.get('alias_definition') for e in ev):
                flags=sorted(set(flags+['AUTHOR_ALIAS_BOUND_NATIVE_PAIR_REVIEW_REQUIRED']))
            table_evidence=(matched.get('native_points') or {}).get('treated',{}).get('table_evidence',[])
            if any(re.search(r'\bATP\s+production\b',e.get('quote',''),re.I) for e in table_evidence):
                flags=sorted(set(flags+['ATP_PRODUCTION_TERMINOLOGY_REQUIRES_METHOD_REVIEW']))
        direction=f['direction'] if f['direction'] in ('INCREASE','DECREASE') else 'UNKNOWN'
        key=hashlib.sha256((model_output_sha+'|'+str(index)).encode()).hexdigest()
        locator={'schema':'asterloom.model-source-provenance/1','source_replay':replay,
                 'packet_sha256':packet_sha,'model_output_sha256':model_output_sha,
                 'model':model.get('model'),'evidence':ev,'model_fields':f,'sanitized_fields':x.get('sanitized_fields',{}),'risk_flags':flags,
                 'source_literal_checked_not_scientific_approval':True,'syntax_repair':syntax_repair,'citation_repairs':x.get('citation_repairs',[]),
                 'author_ordered_effect':ordered,'author_percent_of_control':percent_control,
                 'matched_rule_candidate_id':matched['candidate_id'] if matched else None,
                 'native_points':matched.get('native_points') if matched else None,
                 'rule_packet_sha256':rule_packet_sha if matched else None,
                 'numeric_semantics':('PERCENT_OF_CONTROL_NOT_RAW_MEANS' if percent_control else 'AUTHOR_LITERAL_PERCENT_CANDIDATE_NOT_RAW_GROUP_MEANS') if percent is not None else None}
        record={'observation_id':'LLM-'+key[:24],'source_observation_id':'LLM-'+key[:24],
            'molecule_id':ident['molecule_id'],'paper_id':packet['paper'],'source_version':'asterloom.scientific-extraction/0.1',
            'cell_model':f['cell_model'],'dose_original':ordered['dose_original'] if ordered else (None if 'MULTIPLE_DOSES_REQUIRE_ARM_MAPPING' in flags else f['dose']),
            'exposure_original':None if any(x in flags for x in ('EXPOSURE_IS_PRETREATMENT_ONLY_REVIEW_PHASE','MULTIPLE_TIMES_REQUIRE_PHASE_MAPPING')) else f['exposure_time'],
            'treated_arm':f['treatment'],'comparator_arm':f['comparator'],
            'assay_method':None if any(x in flags for x in ('ASSAY_REAGENT_CITATION_NOT_EXPERIMENT_METHOD','ASSAY_METHOD_NONSPECIFIC')) else f['assay_method'],
            'endpoint_kind':endpoint,'normalization':f['normalization'],'measurement_unit':f['unit'],
            'effect_direction':direction,'author_effect_literal':value,'author_reported_percent':percent,
            'treated_value':matched['treated_value'] if matched else None,
            'control_value':matched['control_value'] if matched else None,'training_effect_log2fc':None,
            'source_locator':json.dumps(locator,ensure_ascii=False,sort_keys=True),
            'author_primary_quote':'\n'.join(e['source_literal'] for e in ev),
            'author_original_sha256':packet['source_sha256'],'legacy_tier':'LLM_SOURCE_QUOTE_CHECKED',
            'review_tier':'Review-B','train_eligible':0,'review_state':'LLM_PROPOSAL_SCIENTIFIC_REVIEW_HOLD',
            'hold_reasons_json':json.dumps(flags,ensure_ascii=False),'independent_gold':0,'training_approved':0,
            'legacy_risk':'Source quote validation does not prove matching, endpoint, identity, or scientific accuracy.'}
        records.append(record)
    paper={'paper_id':packet['paper'],'title':packet['paper_title'],
       'paper_url':'https://pmc.ncbi.nlm.nih.gov/articles/'+packet['paper']+'/',
       'source_kind':'AUTHOR_JATS_LLM_REVIEW_PROPOSAL','original_jats_sha256':packet['source_sha256'],
       'source_verified':1,'source_acquisition_note':'SHA and source chunks replayed; no independent science approval'}
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.harness-export-',dir=out.parent) as scratch:
        tmp=Path(scratch);db=tmp/'ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
        with closing(sqlite3.connect(db)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for schema in schemas:c.execute(schema)
            def insert(table,row):
                cols=list(row);c.execute('INSERT INTO '+table+'('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[row[k] for k in cols])
            for row in molecules.values():insert('molecules',row)
            if records:insert('papers',paper)
            for row in records:insert('observations',row)
            c.commit()
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SCIENTIFIC_EXPORT_INTEGRITY_FAILURE')
            for t in ('molecules','papers','observations'):
                cursor=c.execute('SELECT * FROM '+t+' ORDER BY 1');cols=[x[0] for x in cursor.description];rows=cursor.fetchall()
                with (tmp/(t+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                    w=csv.writer(f);w.writerow(cols);w.writerows(rows)
                (tmp/(t+'.jsonl')).write_text(''.join(json.dumps(dict(zip(cols,row)),ensure_ascii=False)+'\n' for row in rows))
        if sha(registry_path)!=registry_sha:raise ValueError('SCIENTIFIC_EXPORT_REGISTRY_CHANGED')
        manifest={'schema':'asterloom.scientific-review-export/0.1','status':'REVIEW_B_ONLY',
           'source_replay':replay,'packet_sha256':packet_sha,'model_output_sha256':model_output_sha,
           'registry_sha256':registry_sha,'validator_source_sha256':sha(r44_arbiter.__file__),'exporter_source_sha256':sha(__file__),'validation_fingerprint':validation_fingerprint(),
           'rule_packet_sha256':rule_packet_sha,'rule_replay':rule_replay,
           'native_numeric_pairs':sum(r['treated_value'] is not None and r['control_value'] is not None for r in records),
           'raw_response_sha256':sha(journal/'RAW_HTTP_RESPONSE.json'),
           'request_sha256':sha(journal/'REQUEST.json'),'result_sha256':sha(journal/'RESULT.json'),
           'source_level_candidates':source_count,'relation_validation_rejections':rejected,
           'syntax_repair':syntax_repair,'isolated_relation_validation':allow_isolated_relations,'drop_unsupported_fields':drop_unsupported_fields,'remap_unique_quotes':remap_unique_quotes,
           'exported_observations':len(records),'numeric_percent_candidates':sum(r['author_reported_percent'] is not None for r in records),
           'source_supported_numeric_relation_candidates':sum(r['author_reported_percent'] is not None or (r['treated_value'] is not None and r['control_value'] is not None) for r in records),
           'source_numeric_literals_only':sum(bool(re.search(r'\d',str((json.loads(r['source_locator']).get('model_fields') or {}).get('value') or ''))) for r in records),
           'core_xy_fields_complete_unreviewed':sum(
               all(r.get(k) not in (None,'','UNKNOWN') for k in ('cell_model','dose_original','exposure_original','treated_arm','comparator_arm','assay_method','measurement_unit'))
               and (r['author_reported_percent'] is not None or (r['treated_value'] is not None and r['control_value'] is not None))
               and not any(flag in json.loads(r['hold_reasons_json']) for flag in ('AUTHOR_ORDERED_PERCENT_CONTROL_REVIEW_REQUIRED','SYNTAX_REPAIR_REVIEW_REQUIRED','COMPARATOR_DIRECTION_CONFLICT_REVIEW_REQUIRED','AUTOMATED_SOURCE_POINTER_REMAP_REVIEW_REQUIRED','AMBIGUOUS_TREATMENT_QUOTE_OCCURRENCE','CELL_AGE_CONTEXT_CONFLICT','MISSING_REQUIRED_TESTED_FORM','ATP_PRODUCTION_TERMINOLOGY_REQUIRES_METHOD_REVIEW','TREATMENT_COMPARATOR_NOT_SAME_SOURCE_BLOCK','ASSAY_REAGENT_CITATION_NOT_EXPERIMENT_METHOD','ASSAY_METHOD_NONSPECIFIC','CELLTITER_GLO_VIABILITY_SIGNAL_ENDPOINT_REVIEW','EXPOSURE_IS_PRETREATMENT_ONLY_REVIEW_PHASE','MULTIPLE_DOSES_REQUIRE_ARM_MAPPING','MULTIPLE_TIMES_REQUIRE_PHASE_MAPPING'))
               for r in records),
           'core_xy_semantics':'Mechanical field presence plus explicit blocking flags; not a scientific accuracy or Gold claim.',
           'independent_gold':0,'training_approved':0,'skipped':skips,'files':{p.name:sha(p) for p in tmp.iterdir()}}
        (tmp/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        Path(scratch).rename(out)
    return manifest
