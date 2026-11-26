"""Versioned derived candidate export using the existing three-table schema.

The pinned R51 database is a read-only identity/schema reference. No changes to
that authority, no background job, and no train_a view or approval promotion.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sqlite3
import tempfile
from pathlib import Path

from .atp_content_relations import verify_relations


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_name_identity(candidate,author_sha):
    """A source-scoped unresolved name, never a canonical chemical parent."""
    name=candidate['compound_name'];key=hashlib.sha256((author_sha+'\0'+name).encode()).hexdigest()
    return dict(molecule_id='SOURCE-NAME-'+key[:24],preferred_name=name+' [unresolved:'+key[:12]+']',
        chemical_identity_alias=name,natural_origin_scope='NOT_VERIFIED_FROM_AUTHOR_SOURCE',
        pubchem_cid=None,isomeric_smiles=None,inchikey=None,molecular_formula=None,pubchem_url=None,
        structure_status='AUTHOR_NAME_ONLY_NO_VERIFIED_STRUCTURE',structure_rdkit_verified=0,
        chemical_form_caveat='Source-scoped name only; no canonical identity, tested form, purity, natural provenance or structure approval.',
        technical_source_sha256=author_sha)


def author_relative_series_candidates(parent, series):
    """Expand only fully source-bound ordered relative effects to Review-B rows.

    Raw group means remain null. Ambiguous order, dose, duration, panel/model,
    or comparator stays in the source packet and is not standardized here.
    """
    if not (
        series.get('condition_mapping_state')=='AUTHOR_RESPECTIVELY_SEQUENCE_MATCHED_TO_SAME_FIGURE_CAPTION'
        and series.get('dose_state')=='AUTHOR_SINGLE_EXPLICIT_COMPOUND_DOSE_IN_RESULTS_PARAGRAPH'
        and series.get('exposure_time_state')=='AUTHOR_FIGURE_CAPTION_DISTINCT_TREATMENT_PHASE'
        and series.get('arm_assignment_state')=='AUTHOR_COMPOUND_SPECIFIC_FIGURE_PANEL_EFFECT'
        and series.get('comparator_assignment_state')=='AUTHOR_CORRESPONDING_NO_DRUG_CONTROL'
    ):
        return []
    levels=series.get('ordered_precondition_levels') or []
    effects=series.get('ordered_effects_percent') or []
    if len(levels)<2 or len(levels)!=len(effects):
        return []
    dose=series.get('compound_dose') or {}
    caption=series.get('condition_mapping_evidence') or {}
    dose_ev=dose.get('evidence') or {}
    # Keep the full author result clause alongside the panel-local fragment.
    # A panel-local fragment alone omits the trailing ``respectively`` that
    # maps the ordered effects to the caption's ordered preconditions.
    effect_ev=series.get('evidence') or series.get('panel_effect_evidence') or {}
    figure_ev=series.get('figure_reference_evidence') or {}
    cell_ev=series.get('cell_model_evidence') or {}
    comparator_ev=series.get('comparator_evidence') or {}
    treatment_ev=(series.get('treatment_duration_evidence') or {})
    if not all((dose_ev.get('source_sha256'),caption.get('source_sha256'),
                effect_ev.get('source_sha256'),figure_ev.get('source_sha256'),
                cell_ev.get('source_sha256'),comparator_ev.get('source_sha256'),
                treatment_ev.get('source_sha256'))):
        return []
    if len({dose_ev['source_sha256'],caption['source_sha256'],effect_ev['source_sha256'],
            figure_ev['source_sha256'],cell_ev['source_sha256'],comparator_ev['source_sha256'],
            treatment_ev['source_sha256']})!=1:
        return []
    fields_parent=parent.get('fields') or {}
    compound_field=fields_parent.get('compound') or {}
    assay_field=fields_parent.get('assay_method') or {}
    common_holds=[
        'INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED',
        'AUTHOR_RELATIVE_EFFECT_NOT_RAW_GROUP_MEANS',
        'ATP_NORMALIZATION_NOT_REPORTED',
    ]
    rows=[]
    for index,(level,effect) in enumerate(zip(levels,effects),1):
        level_literal=f"{level:g} {series['precondition_unit']} {series['precondition_substrate']}"
        context=f"Preincubated at {level_literal} for {series['precondition_h']:g} h; treatment medium {series['treatment_medium_context']['value']} for {series['compound_treatment_h']:g} h."
        treatment=f"{parent['compound_name']} {dose['literal']} after {level_literal} preincubation"
        comparator=f"Corresponding same-cell, same-precondition control without compound"
        key=f"{series['series_id']}|{index}|{level}|{effect}"
        candidate_id='ATP-REL-'+hashlib.sha256(key.encode()).hexdigest()[:22]
        fields={
            'compound':compound_field,
            'cell_model':{'value':series['cell_model'],'state':series.get('cell_model_binding_state'),'evidence':[cell_ev]},
            'dose_original':{'value':dose['literal'].replace(dose.get('agent',''),'').strip(),'state':series['dose_state'],'evidence':[dose_ev]},
            'dose_uM':{'value':float(dose['value']),'state':series['dose_state'],'evidence':[dose_ev]},
            'total_compound_exposure_h':{'value':float(series['compound_treatment_h']),'state':series['exposure_time_state'],'evidence':[treatment_ev]},
            'treated_arm':{'value':treatment,'state':series['arm_assignment_state'],'evidence':[effect_ev,dose_ev,caption]},
            'comparator_arm':{'value':comparator,'state':series['comparator_assignment_state'],'evidence':[comparator_ev,caption]},
            'assay_method':assay_field,
            'normalization':{'value':None,'state':'AUTHOR_NORMALIZATION_NOT_REPORTED','evidence':[]},
            'effect_direction':{'value':'DECREASE','state':'AUTHOR_RESULTS_EXPLICIT_DIRECTION','evidence':[effect_ev]},
        }
        rows.append({
            'candidate_id':candidate_id,'compound_name':parent['compound_name'],
            'compound_author_names':parent.get('compound_author_names',[]),
            'figure_id':series.get('figure_id'),'figure_label':series.get('figure_label'),
            'panel':series.get('panel'),'table_id':None,'fields':fields,
            'endpoint_kind':'ATP_CONTENT_AUTHOR_REPORTED_RELATIVE_CHANGE_CANDIDATE',
            'measurement_unit':'percent decrease versus corresponding same-condition no-drug control (author reported)',
            'numeric_origin':'AUTHOR_PROSE_REPORTED_PERCENT_CHANGE',
            'author_reported_percent':float(effect),
            'percentage_semantics':'AUTHOR_REPORTED_PERCENT_DECREASE_VS_MATCHED_CONTROL',
            'author_effect_literal':effect_ev.get('quote'),
            'treated_value':None,'control_value':None,'descriptive_log2fc':None,
            'effect_direction':'DECREASE','reported_significance':None,
            'relative_prose_evidence':{
                'series_id':series['series_id'],'condition_index':index,
                'condition_value':level,'condition_unit':series['precondition_unit'],
                'condition_name':series['precondition_substrate'],
                'condition_mapping_state':series['condition_mapping_state'],
                'condition_mapping_evidence':caption,'effect_value':float(effect),
                'effect_evidence':effect_ev,
                'panel_local_effect_evidence':series.get('panel_effect_evidence') or {},
                'panel_reference_evidence':figure_ev,
                'cell_model_evidence':cell_ev,'dose_evidence':dose_ev,
                'treatment_duration_evidence':treatment_ev,
                'comparator_evidence':comparator_ev,
                'raw_group_means_invented':False,
            },
            'treatment_stages':[
                {'role':'AUTHOR_PRECONDITION','label':level_literal,
                 'duration_h':float(series['precondition_h']),'evidence':[caption]},
                {'role':'AUTHOR_COMPOUND_TREATMENT','label':treatment,
                 'duration_h':float(series['compound_treatment_h']),
                 'medium':series['treatment_medium_context']['value'],'evidence':[caption,dose_ev]},
            ],
            'hold_reasons':common_holds,'independent_gold':False,'training_approved':False,
            'native_points':{},'chemical_identity_resolution':parent.get('chemical_identity_resolution'),
        })
    return rows


def export_candidates(packet_paths, registry_db, registry_sha, out, figure_packet_paths=(),
                      parent_registry=None,parent_registry_sha=None,reconciliation=None,reconciliation_sha=None,
                      plot_packet_paths=(),printed_packet_paths=(),grouped_packet_paths=(),percent_plot_packet_paths=(),profile_audit=None,profile_audit_sha=None,
                      include_identity_holds=False):
    out = Path(out).resolve(); registry_db = Path(registry_db).resolve()
    if not registry_sha or sha(registry_db) != registry_sha:
        raise ValueError('ATP_REGISTRY_SHA256_MISMATCH')
    if out.exists():
        raise ValueError('ATP_EXPORT_NEW_VERSION_DIRECTORY_REQUIRED')
    parents={};measurements={}
    if parent_registry or parent_registry_sha:
        if not parent_registry or not parent_registry_sha:raise ValueError('ATP_PARENT_REGISTRY_PATH_AND_SHA_REQUIRED')
        from .atp_content_identities import load_parent_reference
        parents=load_parent_reference(parent_registry,parent_registry_sha)
    if reconciliation or reconciliation_sha:
        if not reconciliation or not reconciliation_sha:raise ValueError('ATP_RECONCILIATION_PATH_AND_SHA_REQUIRED')
        from .atp_content_reconcile import read_reconciliation
        measurements=read_reconciliation(reconciliation,reconciliation_sha)['point_to_measurements']
    packets = []
    for path in packet_paths:
        data = json.loads(Path(path).read_text())
        replay = verify_relations(data, data['source_path'])
        packets.append((Path(path), data, replay))
    supplementary_assets = {}
    for _, data, _ in packets:
        for item in data.get('supplementary_documents', []):
            asset = {
                'source_path': item['source_path'],
                'source_sha256': item['source_sha256'],
                'parent_jats_sha256': item.get('acquisition_context', {}).get('parent_source_sha256',
                                                                             data['source_sha256']),
                'acquisition_context': item.get('acquisition_context', {}),
                'direct_atp_method_block_count': len(item.get('direct_atp_method_blocks', [])),
            }
            old = supplementary_assets.get(item['source_sha256'])
            if old is not None and old != asset:
                raise ValueError('ATP_SUPPLEMENT_ASSET_SHA_COLLISION_HOLD')
            supplementary_assets[item['source_sha256']] = asset
    profile_data=None;profile_entries={}
    if bool(profile_audit)!=bool(profile_audit_sha):raise ValueError('ATP_PROFILE_AUDIT_PATH_AND_SHA_REQUIRED')
    if profile_audit:
        from .atp_content_profile import read_profile_audit
        profile_data=read_profile_audit(profile_audit,profile_audit_sha)
        audited={p['sha256'] for p in profile_data['source_packets']}
        if not {sha(p) for p,d,r in packets}<=audited:raise ValueError('ATP_PROFILE_AUDIT_INPUT_PACKET_SET_MISMATCH')
        profile_entries={e['candidate_id']:e for e in profile_data['records']}
    figure_packets = []
    for path in figure_packet_paths:
        d = json.loads(Path(path).read_text())
        if d.get('schema') != 'asterloom.atp-content.figure-text/0.1' or d.get('training_approved') is not False:
            raise ValueError('ATP_UNAPPROVED_FIGURE_PACKET_REQUIRED')
        if sha(d['image_path']) != d['image_sha256'] or sha(d['source_path']) != d['source_sha256']:
            raise ValueError('ATP_FIGURE_PACKET_SOURCE_SHA256_MISMATCH')
        figure_packets.append((Path(path), d))
    plot_packets=[]
    if plot_packet_paths:
        from .atp_content_plot import read_plot_packet,apply_plot_candidate
        for path in plot_packet_paths:plot_packets.append((Path(path),read_plot_packet(path)))
    printed_packets=[]
    if printed_packet_paths:
        from .atp_content_printed import read_printed,apply_printed
        for path in printed_packet_paths:printed_packets.append((Path(path),read_printed(path)))
    grouped_packets=[]
    if grouped_packet_paths:
        from .atp_content_grouped import read_grouped,apply_grouped
        for path in grouped_packet_paths:grouped_packets.append((Path(path),read_grouped(path)))
    percent_packets=[]
    if percent_plot_packet_paths:
        from .atp_content_percent_plot import read_percent,apply_percent
        for path in percent_plot_packet_paths:percent_packets.append((Path(path),read_percent(path)))
    reg = sqlite3.connect(registry_db.as_uri()+'?mode=ro', uri=True)
    reg.row_factory = sqlite3.Row
    identities = {}; records = []; skipped = []; seen = set(); paperrows = {}
    for packet_path, data, replay in packets:
        paperid = data['paper']['pmcid'] or data['paper']['doi']
        if not paperid:
            raise ValueError('ATP_PAPER_IDENTITY_REQUIRED')
        paperrows[paperid] = dict(paper_id=paperid, doi=data['paper']['doi'],
            title=data['paper']['title'],
            paper_url=('https://pmc.ncbi.nlm.nih.gov/articles/'+paperid+'/' if data['paper']['pmcid']
                       else 'https://doi.org/'+data['paper']['doi']),
            source_kind=('AUTHOR_PMC_HTML_ENGINE_FIELD_CANDIDATE' if data.get('source_format')=='PMC_HTML'
                         else 'AUTHOR_JATS_ENGINE_FIELD_CANDIDATE'),
            original_jats_sha256=(data['source_sha256'] if data.get('source_format') in (None,'JATS_XML') else None),
            source_verified=1,
            source_acquisition_note=('Local author PMC HTML replay; HTML source SHA is retained per observation; license and independent science approval not granted'
                if data.get('source_format')=='PMC_HTML' else 'Local author JATS replay; license and independent science approval not granted'))
        expanded=[]
        series_by_parent={}
        for series in data.get('author_reported_effect_series',[]):
            series_by_parent.setdefault(series.get('parent_candidate_id'),[]).append(series)
        for c in data['candidates']:
            expanded.append(c)  # unresolved original includes unparsed branches
            for series in series_by_parent.get(c['candidate_id'],[]):
                expanded.extend(author_relative_series_candidates(c,series))
            arms=[(p,d) for p,d in grouped_packets if d['parent_candidate_id']==c['candidate_id']]
            if len(arms)>1:raise ValueError('ATP_MULTIPLE_GROUPED_PACKETS_SAME_CONTEXT_HOLD')
            if arms:
                if any(d['parent_candidate_id']==c['candidate_id'] and d['estimated_pairs'] for p,d in plot_packets) or any(
                    any(v['parent_candidate_id']==c['candidate_id'] for v in d['candidates']) for p,d in printed_packets+percent_packets):
                    raise ValueError('ATP_GROUPED_CANNOT_BROADCAST_UNSCOPED_NUMERIC_VALUE')
                expanded.extend(apply_grouped(c,arms[0][1],arms[0][0]))
        for c in expanded:
            percent_matches=[(p,d) for p,d in percent_packets if any(v['parent_candidate_id']==c['candidate_id'] for v in d['candidates'])]
            if len(percent_matches)>1:raise ValueError('ATP_MULTIPLE_PERCENT_VALUES_SAME_CONTEXT_HOLD')
            printed_matches=[(p,d) for p,d in printed_packets if any(v['parent_candidate_id']==c['candidate_id'] for v in d['candidates'])]
            if len(printed_matches)>1:raise ValueError('ATP_MULTIPLE_PRINTED_VALUES_SAME_CONTEXT_HOLD')
            matching=[(p,d) for p,d in plot_packets if d['parent_candidate_id']==c['candidate_id']]
            if len(matching)>1:raise ValueError('ATP_MULTIPLE_GRAPH_ESTIMATES_SAME_CONTEXT_HOLD')
            if printed_matches and any(d['estimated_pairs'] for p,d in matching):
                raise ValueError('ATP_PRINTED_AND_GRAPH_VALUES_SAME_CONTEXT_HOLD')
            if percent_matches and (printed_matches or any(d['estimated_pairs'] for p,d in matching)):
                raise ValueError('ATP_MULTIPLE_NUMERIC_PATHS_SAME_CONTEXT_HOLD')
            if matching:c=apply_plot_candidate(c,matching[0][1],matching[0][0])
            if printed_matches:c=apply_printed(c,printed_matches[0][1],printed_matches[0][0])
            if percent_matches:c=apply_percent(c,percent_matches[0][1],percent_matches[0][0])
            rows = reg.execute('SELECT * FROM molecules WHERE lower(preferred_name)=?',
                               (c['compound_name'].lower(),)).fetchall()
            author_identity=c.get('chemical_identity_resolution') or {}
            identity_specificity_hold=False
            registry_candidate=dict(rows[0]) if len(rows)==1 else None
            if (author_identity.get('state')=='AUTHOR_EXPLICIT_BILINGUAL_NAME_CROSSWALK_PENDING'
                    and registry_candidate):
                caveat=registry_candidate.get('chemical_form_caveat') or ''
                stereo_tokens=re.findall(r'(?i)\b(?:trans|cis|E|Z)\b',caveat)
                stereospecific=bool(stereo_tokens or re.search(r'(?i)stereo(?:chemistry)?',caveat))
                author_name=author_identity.get('author_name','')
                reagent_context=[]
                for evidence in author_identity.get('reagent_evidence',[]):
                    for fragment in re.split(r'[;；]',evidence.get('quote','')):
                        if author_name and author_name in fragment:
                            reagent_context.append(fragment)
                author_text=' '.join([author_name,
                    *[e.get('quote','') for e in author_identity.get('definition_evidence',[])],
                    *reagent_context])
                author_specified=bool(stereo_tokens and any(
                    re.search(rf'(?i)(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])',author_text)
                    for token in stereo_tokens))
                identity_specificity_hold=stereospecific and not author_specified
            if identity_specificity_hold:
                proposed={
                    'state':'REGISTRY_CROSSWALK_HELD_STEREOSPECIFIC_FORM_NOT_DECLARED_BY_AUTHOR',
                    'author_name':author_identity.get('author_name'),
                    'registry_candidate_name':registry_candidate['preferred_name'],
                    'registry_candidate_molecule_id':registry_candidate['molecule_id'],
                    'registry_candidate_pubchem_cid':registry_candidate.get('pubchem_cid'),
                    'registry_candidate_inchikey':registry_candidate.get('inchikey'),
                    'registry_candidate_form_caveat':registry_candidate.get('chemical_form_caveat'),
                    'registry_database_sha256':registry_sha,
                    'author_source_sha256':data['source_sha256'],
                    'author_definition_evidence':author_identity.get('definition_evidence',[]),
                    'author_reagent_evidence':author_identity.get('reagent_evidence',[]),
                    'canonical_structure_verified':False,
                    'tested_chemical_form_verified':False,
                    'independent_identity_reviewed':False,
                }
                molecule=source_name_identity(c,data['source_sha256'])
                c={**c,'chemical_identity_resolution':{**author_identity,
                    'state':proposed['state'],'canonical_structure_verified':False,
                    'registry_crosswalk_candidate':proposed},
                    'hold_reasons':sorted(set(c.get('hold_reasons',[])+[
                        'CHEMICAL_PARENT_IDENTITY_UNRESOLVED',
                        'REGISTRY_STEREOSPECIFICITY_NOT_AUTHOR_DECLARED',
                        'TESTED_CHEMICAL_FORM_UNVERIFIED']))}
            elif len(rows)==1:molecule=dict(rows[0])
            elif len(rows)==0 and c['compound_name'].lower() in parents:
                molecule=dict(parents[c['compound_name'].lower()])
                known=reg.execute('SELECT * FROM molecules WHERE inchikey=?',(molecule['inchikey'],)).fetchall()
                if len(known)==1:molecule=dict(known[0])
            elif len(rows)==0 and include_identity_holds:
                molecule=source_name_identity(c,data['source_sha256'])
                c={**c,'chemical_identity_resolution':{'state':'AUTHOR_NAME_ONLY_HOLD','source_scoped_molecule_id':molecule['molecule_id'],
                    'author_name':c['compound_name'],'author_sha256':data['source_sha256'],'canonical_structure_verified':False},
                    'hold_reasons':sorted(set(c['hold_reasons']+['CHEMICAL_PARENT_IDENTITY_UNRESOLVED','TESTED_CHEMICAL_FORM_UNVERIFIED']))}
            else:
                skipped.append({'candidate_id':c['candidate_id'],'paper':paperid,'compound':c['compound_name'],
                    'table_id':c.get('table_id'),'figure_id':c.get('figure_id'),
                    'reason':'REGISTRY_IDENTITY_UNRESOLVED'})
                continue
            author_identity=c.get('chemical_identity_resolution') or {}
            if author_identity.get('state')=='AUTHOR_EXPLICIT_BILINGUAL_NAME_CROSSWALK_PENDING':
                # Resolve only through one exact preferred-name registry row
                # with a technically verified structure. This is a parent
                # identity crosswalk, not validation of the purchased lot,
                # stereospecific form, natural provenance, or experiment.
                if not (molecule.get('structure_rdkit_verified') and molecule.get('inchikey')
                        and molecule.get('pubchem_cid')):
                    skipped.append({'candidate_id':c['candidate_id'],'paper':paperid,'compound':c['compound_name'],
                        'table_id':c.get('table_id'),'figure_id':c.get('figure_id'),
                        'reason':'AUTHOR_NAME_MATCH_BUT_REGISTRY_STRUCTURE_NOT_VERIFIED'})
                    continue
                crosswalk={
                    'state':'AUTHOR_EXPLICIT_BILINGUAL_NAME_TO_REGISTERED_PARENT_REVIEW_REQUIRED',
                    'author_name':author_identity['author_name'],
                    'registry_preferred_name':molecule['preferred_name'],
                    'registry_molecule_id':molecule['molecule_id'],
                    'registry_database_sha256':registry_sha,
                    'registry_pubchem_cid':molecule['pubchem_cid'],
                    'registry_inchikey':molecule['inchikey'],
                    'registry_structure_rdkit_verified':True,
                    'author_source_sha256':data['source_sha256'],
                    'author_definition_evidence':author_identity.get('definition_evidence',[]),
                    'author_reagent_evidence':author_identity.get('reagent_evidence',[]),
                    'tested_chemical_form_verified':False,
                    'independent_identity_reviewed':False,
                    'training_approved':False,
                }
                fields=dict(c['fields'])
                fields['compound']={**fields['compound'],
                    'state':'AUTHOR_DECLARED_BILINGUAL_NAME_REGISTERED_PARENT_REVIEW_REQUIRED'}
                c={**c,'fields':fields,'chemical_identity_resolution':{**author_identity,
                    'state':crosswalk['state'],'canonical_structure_verified':True,
                    'identity_approved_for_training':False,'registry_crosswalk':crosswalk},
                    'hold_reasons':sorted(set([h for h in c.get('hold_reasons',[])
                        if h!='CHEMICAL_PARENT_IDENTITY_UNRESOLVED']+
                        ['AUTHOR_NAME_CROSSWALK_REQUIRES_INDEPENDENT_REVIEW',
                         'TESTED_CHEMICAL_FORM_UNVERIFIED',
                         'SUPPLIER_LOT_PURITY_AND_PHYSICAL_SPECIATION_NOT_VERIFIED']))}
            identities[molecule['molecule_id']] = molecule
            f = c['fields']; val = lambda k: f[k]['value']
            pe=profile_entries.get(c['candidate_id'])
            if pe and c['numeric_origin']!='AUTHOR_TABLE_REPORTED':raise ValueError('ATP_ABSOLUTE_PROFILE_INPUT_ORIGIN_MISMATCH')
            identity=((pe or {}).get('record') or {}).get('provenance',{}).get('atp_source_reconstruction',{}).get('identity')
            if identity and (int(molecule.get('pubchem_cid') or 0)!=identity['author_cid'] or molecule.get('inchikey')!=identity['official_properties']['InChIKey']):
                raise ValueError('ATP_PROFILE_CANONICAL_PARENT_REFERENCE_CONFLICT')
            if identity:
                c={**c,'hold_reasons':sorted(set([h for h in c['hold_reasons'] if h!='TESTED_CHEMICAL_FORM_UNVERIFIED']+
                    ['AUTHOR_DECLARED_FORM_PARENT_MATCH_REQUIRES_INDEPENDENT_REVIEW','SUPPLIER_LOT_PURITY_AND_PHYSICAL_SPECIATION_NOT_VERIFIED']))}
            profile_link=({'audit_path':str(Path(profile_audit).resolve()),'audit_sha256':profile_audit_sha,
                'profile':profile_data['profile']['id'],'view':profile_data['profile']['views'][0]['id'],
                'decision':pe['decision'],'measurement_id':pe.get('measurement_id'),
                'study_group':(pe.get('record') or {}).get('dataset',{}).get('study_group',paperid),
                'missing_fields':pe['record']['missing_fields'] if pe.get('record') else {'native_join':pe['reason']},
                'independently_reviewed':False,'training_approved':False} if pe else None)
            stages = c['treatment_stages']
            context = json.dumps(stages, ensure_ascii=False, sort_keys=True)
            key = [paperid, molecule['molecule_id'], c['figure_id'], c['panel'],c.get('table_id'),
                   val('cell_model'), val('dose_uM'),val('dose_original'),context,val('comparator_arm'),
                   [p['point_id'] for p in c.get('native_points',{}).values()],
                   # Different author value semantics for the same arm are
                   # distinct review evidence. In particular, a by-times
                   # prose hold must not be deduped against its parent arm.
                   c.get('numeric_origin'), c.get('percentage_semantics'),
                   c.get('fold_change_semantics'), c.get('by_times_semantics'),
                   c.get('author_reported_percent'), c.get('author_reported_percent_of_control'),
                   c.get('author_reported_fold_change'), c.get('author_reported_by_times_literal'),
                   (c.get('by_times_evidence') or {}).get('value_evidence',{}).get('start')]
            unique = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]
            if unique in seen:
                continue
            seen.add(unique)
            citations = [e for x in f.values() for e in x['evidence']]
            quotes = list(dict.fromkeys(e['quote'] for e in citations))
            old_ids={role:measurements.get(p['point_id'],[]) for role,p in c.get('native_points',{}).items()}
            ratio=(math.log2(c['treated_value']/c['control_value']) if c['numeric_origin'] in
                   ('AUTHOR_TABLE_REPORTED','GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE','GRAPH_MULTI_ENDPOINT_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE')
                   and c['treated_value'] is not None and c['control_value'] is not None
                   and c['treated_value']>0 and c['control_value']>0 else None)
            if c.get('percentage_semantics')=='MEAN_ATP_PERCENT_OF_AUTHOR_CONTROL' and c.get('author_reported_percent',0)>0:
                ratio=math.log2(c['author_reported_percent']/100)
            if c.get('percentage_semantics')=='SIGNED_PERCENT_CHANGE_VS_MATCHED_UNTREATED_VARIANT':
                factor=1+c['author_reported_percent']/100
                ratio=math.log2(factor) if factor>0 else None
            if c.get('percentage_semantics')=='AUTHOR_REPORTED_PERCENT_OF_MATCHED_CONTROL' and c.get('author_reported_percent_of_control',0)>0:
                ratio=math.log2(c['author_reported_percent_of_control']/100)
            if c.get('fold_change_semantics')=='AUTHOR_REPORTED_RATIO_TO_UNTREATED_SAME_CELL_TIMEPOINT':
                ratio=math.log2(c['author_reported_fold_change']) if c.get('author_reported_fold_change',0)>0 else None
            # A pretreatment interval is not the total compound exposure.
            # Keep exposure_h NULL until continuous presence/washout is known.
            visual = [{'packet_path':str(p.resolve()),'packet_sha256':sha(p),
                       'image_sha256':d['image_sha256'], 'image_path':d['image_path'],
                       'state':'OCR_FOR_REVIEW_NOT_NUMERIC_OR_ARM_TRUTH'} for p,d in figure_packets
                      if d['source_sha256']==data['source_sha256'] and d['figure_id']==c['figure_id']]
            supplementary_assay = (c.get('supplementary_field_proposals') or {}).get('assay_method') or {}
            assay_method_value = val('assay_method') or supplementary_assay.get('value')
            record = dict(observation_id='R52-'+unique, molecule_id=molecule['molecule_id'],
                paper_id=paperid, source_observation_id=c['candidate_id'], source_version=data['schema'],
                cell_model=val('cell_model'), biological_context=context,
                dose_original=val('dose_original'), dose_uM=val('dose_uM'),
                exposure_original=context if stages else None, exposure_h=val('total_compound_exposure_h'),
                treated_arm=val('treated_arm'), comparator_arm=val('comparator_arm'),
                assay_method=assay_method_value, endpoint_kind=c['endpoint_kind'],
                normalization=val('normalization'), measurement_unit=c['measurement_unit'],
                effect_direction=val('effect_direction') or 'UNKNOWN',
                author_effect_literal='\n'.join(e['quote'] for e in f['effect_direction']['evidence']) or None,
                author_reported_percent=(c.get('author_reported_percent') if c.get('author_reported_percent') is not None
                    else c.get('author_reported_percent_of_control')),
                treated_value=c['treated_value'], control_value=c['control_value'],
                descriptive_log2fc=ratio, training_effect_log2fc=None,
                reported_significance=c.get('reported_significance'),
                source_locator=json.dumps({'figure_id':c['figure_id'], 'panel':c['panel'],
                    'panel_label_evidence':c.get('panel_label_evidence'),
                    'field_packet_sha256':sha(packet_path), 'candidate_id':c['candidate_id'],
                    'author_method_arm_context':c.get('author_method_arm_context'),
                    'field_evidence':{k:{'value':v.get('value'),'state':v.get('state'),
                        'evidence':v.get('evidence',[])} for k,v in f.items()},
                    'figure_text_evidence':visual,'table_id':c.get('table_id'),
                    'numeric_origin':c['numeric_origin'],'native_measurement_reconciliation':old_ids,
                    'method_panel_scope_binding':c.get('method_panel_scope_binding'),
                    'panel_intervention_context':c.get('panel_intervention_context'),
                    'calibrated_atp_content_proof':c.get('calibrated_atp_content_proof'),
                    'native_measurement_summary':c.get('native_measurement_summary'),
                    'native_measurement_points':{
                        role:{'point_id':point.get('point_id'),
                              'source_sha256':point.get('source_sha256'),
                              'source_literal':point.get('source_literal'),
                              'numeric_value':point.get('numeric_value'),
                              'uncertainty':point.get('uncertainty'),
                              'uncertainty_kind':point.get('uncertainty_kind'),
                              'sample_size_n':point.get('sample_size_n'),
                              'unit_author_literal':point.get('unit_author_literal'),
                              'author_group_literal':point.get('author_group_literal'),
                              'value_evidence':point.get('value_evidence'),
                              'group_evidence':point.get('group_evidence'),
                              'metric_evidence':point.get('metric_evidence')}
                        for role,point in c.get('native_points',{}).items()},
                    'numeric_pair_evidence':c.get('numeric_pair_evidence',[]),
                    'reported_significance':c.get('reported_significance'),
                    'reported_significance_evidence':c.get('reported_significance_evidence'),
                    'graph_estimate_evidence':c.get('graph_estimate_evidence'),
                    'printed_value_evidence':c.get('printed_value_evidence'),'percentage_semantics':c.get('percentage_semantics'),
                    'categorical_arm_evidence':c.get('categorical_arm_evidence'),
                    'matched_factor_protocol':c.get('matched_factor_protocol'),
                    'chemical_identity_resolution':c.get('chemical_identity_resolution'),
                    'percent_plot_evidence':c.get('percent_plot_evidence'),
                    'relative_prose_evidence':c.get('relative_prose_evidence'),
                    'author_reported_fold_change':c.get('author_reported_fold_change'),
                    'fold_change_semantics':c.get('fold_change_semantics'),
                    'fold_change_evidence':c.get('fold_change_evidence'),
                    'author_reported_percent_of_control':c.get('author_reported_percent_of_control'),
                    'percent_of_control_evidence':c.get('percent_of_control_evidence'),
                    'author_reported_relative_value':({
                        'value':c.get('author_reported_percent_of_control',c.get('author_reported_percent')),
                        'uncertainty':c.get('author_reported_sd_percent'),
                        'uncertainty_kind':c.get('relative_uncertainty_kind'),
                        'unit':c.get('measurement_unit'),
                        'semantics':c.get('percentage_semantics'),
                        'scale_control_arm':c.get('scale_control_arm'),
                        'statistical_comparator_arm':c.get('statistical_comparator_arm'),
                        'literal':c.get('relative_value_literal'),
                        'value_evidence':c.get('relative_value_evidence'),
                        'comparison_evidence':c.get('comparison_evidence'),
                        'sample_size_n':c.get('reported_sample_size'),
                        'figure_caption_evidence':c.get('figure_caption_evidence'),
                        'raw_means_invented':False,
                    } if c.get('relative_value_evidence') else None),
                    'author_reported_by_times_literal':c.get('author_reported_by_times_literal'),
                    'by_times_semantics':c.get('by_times_semantics'),
                    'by_times_evidence':c.get('by_times_evidence'),
                    'caption_dose_arm_evidence':c.get('caption_dose_arm_evidence'),
                    'method_protocol_evidence':c.get('method_protocol_evidence'),
                    'method_protocol_exclusion':c.get('method_protocol_exclusion'),
                    'supplementary_documents':[{
                        'source_sha256':x['source_sha256'],
                        'parent_jats_sha256':x.get('acquisition_context',{}).get('parent_source_sha256',
                                                                                 data['source_sha256']),
                        'acquisition_context':x.get('acquisition_context',{}),
                        'direct_atp_method_block_count':len(x.get('direct_atp_method_blocks',[])),
                    } for x in data.get('supplementary_documents',[])],
                    'supplementary_method_context':c.get('supplementary_method_context',[]),
                    'supplementary_method_review':c.get('supplementary_method_review'),
                    'supplementary_field_proposals':c.get('supplementary_field_proposals',{}),
                    'cell_model_variant_candidates':c.get('cell_model_variant_candidates',[]),
                    'profile_requiredness':profile_link,
                    'author_source_conflicts':c.get('source_conflicts',[])}, sort_keys=True),
                author_primary_quote='\n'.join(quotes), author_original_sha256=data['source_sha256'],
                legacy_tier='ENGINE_SOURCE_BOUND_CANDIDATE', review_tier='Review-B', train_subtier=None,
                train_eligible=0, review_state='ENGINE_CANDIDATE_INDEPENDENT_REVIEW_REQUIRED',
                hold_reasons_json=json.dumps(c['hold_reasons'], ensure_ascii=False),
                independent_gold=0, training_approved=0,
                legacy_risk='Registry parent does not establish tested chemical form. Native means/ratios do not establish significance. Graph estimates and OCR printed values are distinct unreviewed candidates; source replay is not science approval.')
            records.append(record)
    # No new or alternative schema: execute exactly the existing three tables.
    schemas = [reg.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?', (t,)).fetchone()[0]
               for t in ('molecules', 'papers', 'observations')]
    reg.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    # A failed export never leaves a partially completed version directory.
    with tempfile.TemporaryDirectory(prefix='.atp-export-', dir=out.parent) as scratch:
        tmp = Path(scratch); db = tmp/'ATP_ENGINE_REVIEW_CANDIDATES.sqlite3'
        conn = sqlite3.connect(db); conn.execute('PRAGMA foreign_keys=ON')
        for schema in schemas:
            conn.execute(schema)
        def insert(table, row):
            names = list(row)
            conn.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',
                         [row[k] for k in names])
        for row in identities.values(): insert('molecules', row)
        used = {r['paper_id'] for r in records}
        for pid in sorted(used): insert('papers', paperrows[pid])
        for row in records: insert('observations', row)
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('ATP_EXPORT_INTEGRITY_FAILED')
        for table in ('molecules', 'papers', 'observations'):
            cur = conn.execute('SELECT * FROM '+table+' ORDER BY 1')
            columns = [c[0] for c in cur.description]; rows = cur.fetchall()
            with (tmp/(table+'.csv')).open('w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f); w.writerow(columns); w.writerows(rows)
            (tmp/(table+'.jsonl')).write_text(''.join(json.dumps(dict(zip(columns,row)),ensure_ascii=False)+'\n' for row in rows))
        conn.close()
        manifest = {'schema':'asterloom.atp-engine.derived-three-table-export/1',
            'status':'VERSIONED_DERIVED_REVIEW_CANDIDATES_NOT_NEW_AUTHORITY',
            'registry_db':str(registry_db), 'registry_sha256':registry_sha,
            'packets':[{'path':str(p.resolve()),'sha256':sha(p),'source_replay':v} for p,d,v in packets],
            'figure_packets':[{'path':str(p.resolve()), 'sha256':sha(p)} for p,d in figure_packets],
            'plot_packets':[{'path':str(p.resolve()),'sha256':sha(p)} for p,d in plot_packets],
            'printed_packets':[{'path':str(p.resolve()),'sha256':sha(p)} for p,d in printed_packets],
            'grouped_packets':[{'path':str(p.resolve()),'sha256':sha(p)} for p,d in grouped_packets],
            'percent_plot_packets':[{'path':str(p.resolve()),'sha256':sha(p)} for p,d in percent_packets],
            'supplementary_assets':[supplementary_assets[k] for k in sorted(supplementary_assets)],
            'profile_requiredness_audit':{'path':str(Path(profile_audit).resolve()),'sha256':profile_audit_sha} if profile_audit else None,
            'parent_registry_manifest':{'path':str(Path(parent_registry).resolve()),'sha256':parent_registry_sha} if parent_registry else None,
            'reconciliation':{'path':str(Path(reconciliation).resolve()),'sha256':reconciliation_sha} if reconciliation else None,
            'counts':{'molecules':len(identities), 'papers':len(used), 'observations':len(records),
                      'Train-A':0, 'Review-B':len(records), 'Gold':0, 'training_approved':0,
                      'source_name_only_molecules':sum(m['structure_status']=='AUTHOR_NAME_ONLY_NO_VERIFIED_STRUCTURE' for m in identities.values()),
                      'identity_hold_candidates_preserved':sum(json.loads(r['source_locator']).get('chemical_identity_resolution') is not None for r in records),
                      'named_method_protocol_bound_candidates':sum(json.loads(r['source_locator']).get('method_protocol_evidence') is not None for r in records),
                      'supplementary_assay_method_candidates':sum(bool(json.loads(r['source_locator']).get('supplementary_field_proposals',{}).get('assay_method')) for r in records),
                      'ambiguous_author_fold_statement_candidates':sum(bool((json.loads(r['source_locator']).get('method_protocol_evidence') or {}).get('reported_effect_token')) for r in records),
                      'multi_intervention_context_candidates':sum(r['endpoint_kind']=='ATP_CONTENT_MULTI_INTERVENTION_CONTEXT_CANDIDATE' for r in records),
                      'author_numeric_pairs':sum(json.loads(r['source_locator'])['numeric_origin']=='AUTHOR_TABLE_REPORTED' for r in records),
                      'graph_estimated_pairs':sum(json.loads(r['source_locator'])['numeric_origin'] in ('GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE','GRAPH_MULTI_ENDPOINT_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE') for r in records),
                      'multi_endpoint_atp_graph_estimated_pairs':sum(json.loads(r['source_locator'])['numeric_origin']=='GRAPH_MULTI_ENDPOINT_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE' for r in records),
                      'author_printed_figure_value_candidates':sum(json.loads(r['source_locator'])['numeric_origin']=='AUTHOR_FIGURE_PRINTED_NUMERIC_OCR_CANDIDATE' for r in records),
                      'author_prose_relative_effect_candidates':sum(json.loads(r['source_locator'])['numeric_origin']=='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE' for r in records),
                      'author_prose_fold_effect_candidates':sum(json.loads(r['source_locator']).get('numeric_origin')=='AUTHOR_PROSE_REPORTED_FOLD_CHANGE' for r in records),
                      'author_prose_percent_of_control_candidates':sum(json.loads(r['source_locator']).get('numeric_origin')=='AUTHOR_PROSE_PERCENT_OF_CONTROL' for r in records),
                      'author_by_times_semantics_hold_candidates':sum(json.loads(r['source_locator']).get('numeric_origin')=='AUTHOR_PROSE_BY_TIMES_SEMANTICS_HOLD' for r in records),
                      'author_caption_dose_arm_context_candidates':sum(json.loads(r['source_locator']).get('caption_dose_arm_evidence') is not None for r in records),
                      'author_method_arm_schedule_candidates':sum(
                          len(arm.get('schedule_context_candidates',[]))
                          for r in records
                          for arm in (json.loads(r['source_locator']).get('author_method_arm_context') or {}).get('arm_sentence_contexts',[])),
                      'author_method_arm_schedule_holds':sum(
                          schedule.get('state')=='MULTIPLE_ARM_SENTENCE_CONDITIONS_NOT_SPLIT' or
                          bool(schedule.get('phase_hold_reason'))
                          for r in records
                          for arm in (json.loads(r['source_locator']).get('author_method_arm_context') or {}).get('arm_sentence_contexts',[])
                          for schedule in arm.get('schedule_context_candidates',[])),
                      'categorical_arm_candidates_without_numeric_values':sum(bool(json.loads(r['source_locator']).get('categorical_arm_evidence')) for r in records),
                      'categorical_rows_with_source_bound_named_factors':sum(bool((json.loads(r['source_locator']).get('matched_factor_protocol') or {}).get('conditions')) for r in records),
                      'categorical_source_bound_named_factor_bindings':sum(len((json.loads(r['source_locator']).get('matched_factor_protocol') or {}).get('conditions',[])) for r in records),
                      'native_numeric_pairs_with_bound_core_conditions':sum(
                          json.loads(r['source_locator'])['numeric_origin']=='AUTHOR_TABLE_REPORTED' and
                          r['treated_value'] is not None and r['control_value'] is not None and
                          all(r[k] is not None for k in ('cell_model','dose_uM','exposure_h','assay_method','normalization','measurement_unit'))
                          for r in records),
                      'profile_required_complete_unapproved_rows':sum(bool((json.loads(r['source_locator']).get('profile_requiredness') or {}).get('decision')=='TASK_READY_CANDIDATE') for r in records) if profile_data else None,
                      'profile_required_complete_unapproved_studies':len({json.loads(r['source_locator'])['profile_requiredness']['study_group'] for r in records if (json.loads(r['source_locator']).get('profile_requiredness') or {}).get('decision')=='TASK_READY_CANDIDATE'}) if profile_data else None,
                      'profile_native_rows_evaluated':sum(json.loads(r['source_locator']).get('profile_requiredness') is not None for r in records) if profile_data else None,
                      'independently_reviewed_complete_xy':sum(r['independent_gold']==1 and (json.loads(r['source_locator']).get('profile_requiredness') or {}).get('decision')=='TASK_READY_CANDIDATE' for r in records),
                      'complete_xy':sum(r['independent_gold']==1 and (json.loads(r['source_locator']).get('profile_requiredness') or {}).get('decision')=='TASK_READY_CANDIDATE' for r in records)},
            'count_semantics':{'complete_xy':'LEGACY_ALIAS_OF_INDEPENDENTLY_REVIEWED_COMPLETE_XY',
                'profile_required_complete_unapproved_rows':'CURRENT_SOURCE_RECONSTRUCTION_PASSES_EXISTING_PROFILE_REQUIRED_FIELDS_NO_FIT_NOT_REVIEWED',
                'absolute_profile_scope':'AUTHOR_NATIVE_ABSOLUTE_ONLY_RELATIVE_GRAPH_PROSE_NOT_APPLICABLE'},
            'skipped_candidates':skipped, 'files':{p.name:sha(p) for p in tmp.iterdir()},
            'license_notice':'Author source rights remain separate; no source originals in this export; no training permission granted.'}
        (tmp/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        if sha(registry_db) != registry_sha:
            raise ValueError('ATP_REGISTRY_CHANGED_DURING_EXPORT')
        Path(scratch).rename(out)
    return manifest
