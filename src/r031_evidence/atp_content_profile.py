"""Source replay into the existing R23 Profile, separate from review approval.

Uses existing Evidence Core IDs and ProfilePlugin, never a new science store.
Positive R29 operator assertions are not copied: fields are reconstructed from
current source extraction; prior scientific quarantine flags are preserved.
"""
import copy
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from .atp_content_chemical import sha,load_references,bind_author_identity
from .atp_content_relations import verify_relations,_index,_body_paragraphs,_citation,_name_pattern,TIME,_hours
from .r23_profiles import SourceProjector,SQL,load,validate,_id


def literal_interval(candidate):
    f=candidate['fields']['total_compound_exposure_h']
    if f['value'] is None:return None
    literals=[]
    for e in f['evidence']:
        patterns=[rf'{_name_pattern(candidate["compound_author_names"])}\s+was\s+infused\s+for\s+(?P<t>{TIME})',
                  rf'\b(?:incubation|incubated|treatment|exposure)\s+for\s+(?P<t>{TIME})']
        for pattern in patterns:
            for m in re.finditer(pattern,e['quote'],re.I):
                if _hours(m['t'])==f['value']:literals.append({'literal':m['t'],'evidence':e})
    unique={x['literal']:x for x in literals}
    return next(iter(unique.values())) if len(unique)==1 else None


def hplc_pool_method(candidate,methods,author_sha):
    if candidate['fields']['assay_method']['value']!='HPLC':return None
    if not re.search(r'perfused livers',str(candidate['fields']['cell_model']['value']),re.I):return None
    found=[]
    for b in methods:
        for m in re.finditer(r'Hepatic adenine nucleotides[^.]*\bATP\b[^.]*quantified by HPLC[^.]*freeze[^.]*perfused livers[^.]*\.',b['text'],re.I):
            found.append(_citation(b,author_sha,m.group()))
    return found[0] if len(found)==1 else None


def calibrated_native_method(candidate,methods,author_sha):
    """Bounded astrocyte lysate protocol, with all proof in one Methods section.

    CellTiter-Glo by itself is never proof of ATP content. Direct unconverted
    ATP, a lysate/standard branch, linear calibration and protein normalization
    must all be explicit. Other cell models/protocol families remain unresolved.
    """
    if candidate.get('numeric_origin')!='AUTHOR_TABLE_REPORTED':return None
    point=candidate.get('native_points',{}).get('treated',{})
    if not re.fullmatch(r'(?:initial\s+|cellular\s+|intracellular\s+)?ATP(?:\s+content)?\s*(?:\(.*\))?',point.get('metric_author_literal',''),re.I):return None
    if not re.search(r'astrocytes',str(candidate['fields']['cell_model']['value']),re.I):return None
    sections={}
    for block in methods:sections.setdefault(block['path'].rsplit('/p[',1)[0],[]).append(block)
    proofs=[]
    for section,blocks in sections.items():
        direct=[];branches=[];calibration=[];normalization=[]
        for block in blocks:
            sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z])',block['text'])
            for sentence in sentences:
                if (re.search(r'ATP in the lysate was (?:determined|measured|quantified).{0,160}lucifer.{0,60}(?:assay|luminometric)',sentence,re.I)
                    and re.search(r'astrocytes',block['text'],re.I)
                    and not re.search(r'convert|viability|HepG2|RAW\s*264|hepatocytes',block['text'],re.I)):
                    direct.append(_citation(block,author_sha,sentence))
                if (re.search(r'neutrali[sz]ed lysate samples or ATP standards\s*\(ATP quantification\)',sentence,re.I)
                    and re.search(r'Cell\s*Titer\s*Glo',sentence,re.I)
                    and re.search(r'luciferase reaction',sentence,re.I)):
                    branches.append(_citation(block,author_sha,sentence))
                if re.search(r'ATP values for the samples.{0,100}linear calibration curve.{0,160}ATP standards',sentence,re.I):
                    calibration.append(_citation(block,author_sha,sentence))
                if re.search(r'Specific contents.{0,60}normaliz.{0,100}initial cellular protein content',sentence,re.I):
                    normalization.append(_citation(block,author_sha,sentence))
        if all(len(items)==1 for items in (direct,branches,calibration,normalization)):
            proofs.append({'schema':'asterloom.atp-content.calibrated-native-method/0.1',
                'endpoint_scope':'DIRECT_LYSATE_ATP_NOT_CONVERTED_NUCLEOTIDES_OR_VIABILITY',
                'methods_section':section,'direct_measurement_evidence':direct[0],
                'direct_lysate_standard_branch_evidence':branches[0],'linear_calibration_evidence':calibration[0],
                'normalization_evidence':normalization[0],'normalization_basis':'PROTEIN','scientific_approval':False})
    return proofs[0] if len(proofs)==1 else None


def reconstruct(base,candidate,identity,method,assay_proof=None):
    r=copy.deepcopy(base);f=candidate['fields'];interval=literal_interval(candidate)
    r['chemical'].update(parent_id=None,parent_structure=None,tested_form_id=None,tested_form_literal=None,tested_form_source_locator=None)
    if identity:
        r['chemical'].update(parent_id=identity['parent_id'],parent_structure=identity['parent_structure'],
            tested_form_id=_id('tested_form',r['paper']['paper_key'],identity['tested_form_literal']),
            tested_form_literal=identity['tested_form_literal'],tested_form_source_locator=identity['author_evidence']['source_anchor'],
            author_pubchem_cid=identity['author_cid'],identity_scope=identity['scope'])
    r['biological'].update(model_literal=f['cell_model']['value'],model_source_locator=(f['cell_model']['evidence'][0]['source_anchor'] if f['cell_model']['evidence'] else None))
    r['conditions'].update(dose_literal=f['dose_original']['value'],
        dose_source_locator=(f['dose_original']['evidence'][0]['source_anchor'] if f['dose_original']['evidence'] else None),
        exposure_time_literal=interval['literal'] if interval else None,
        time_source_locator=interval['evidence']['source_anchor'] if interval else None)
    r['assay'].update(method_locator=method['source_anchor'] if method else None,platform=f['assay_method']['value'],
        normalization_basis=f['normalization']['value'],independent_biological_n=None,run_author_id=None,
        run_state='EXTRACTED_AUTHOR_CONTEXT_NOT_VERIFIED_INDIVIDUAL_BIOLOGICAL_RUN')
    r['arm'].update(control_linkage=None,control_verification_state='SOURCE_GROUP_COMPARATOR_NOT_INDIVIDUAL_RUN_VERIFIED')
    r['provenance']['atp_source_reconstruction']={'candidate_id':candidate['candidate_id'],'fields':f,
        'identity':identity,'method':method,'interval':interval,'positive_operator_annotations_used':False}
    if assay_proof:r['provenance']['atp_source_reconstruction']['calibrated_native_assay_proof']=assay_proof
    return r


def audit_native_profiles(reconciliation_path,reconciliation_sha,reference_path=None,reference_sha=None,calibrated_assays=True):
    from .atp_content_reconcile import read_reconciliation
    recon=read_reconciliation(reconciliation_path,reconciliation_sha)
    refs=load_references(reference_path,reference_sha) if reference_path else {}
    db=Path(recon['science_db']);before=sha(db)
    if before!=recon['science_sha256']:raise ValueError('ATP_PROFILE_SCIENCE_DB_SHA_HOLD')
    projector=SourceProjector(db.parent)
    actual_db=Path(projector.c.execute('PRAGMA database_list').fetchone()[2]).resolve()
    if actual_db!=db.resolve():
        projector.close();raise ValueError('ATP_PROFILE_READER_DATABASE_BINDING_MISMATCH')
    plugin=load('small_molecule_atp_content_v1');view=plugin.views[0]
    result=[];not_applicable=[];existing_ready=set();seen_packets=set()
    try:
        for pp in recon['source_packets']:
            if pp['sha256'] in seen_packets:continue
            seen_packets.add(pp['sha256'])
            data=json.loads(Path(pp['path']).read_text());verify_relations(data,data['source_path'])
            paper=data['paper']['pmcid'];rows={r['measurement_id']:r for r in projector.c.execute(SQL,(paper,paper))}
            previous={r['observation']['native_measurement_id']:r for r in projector.project(plugin,[paper])}
            methods=[b for b in _body_paragraphs(_index(ET.fromstring(Path(data['source_path']).read_bytes()))) if b['role']=='METHODS']
            for c in data['candidates']:
                if c['numeric_origin']!='AUTHOR_TABLE_REPORTED':
                    not_applicable.append({'candidate_id':c['candidate_id'],'paper':paper,'reason':'RELATIVE_ESTIMATED_OR_NONNUMERIC_NOT_AUTHOR_ABSOLUTE_PROFILE_INPUT'});continue
                pt=c['native_points']['treated'];links=recon['point_to_measurements'].get(pt['point_id'],[])
                if len(links)!=1 or links[0]['measurement_id'] not in rows:
                    result.append({'candidate_id':c['candidate_id'],'paper':paper,'decision':'SOURCE_ONLY_HOLD','record':None,'reason':'EXACT_EXISTING_NATIVE_OBSERVATION_ID_NOT_BOUND'});continue
                link=links[0];mid=link['measurement_id'];base=projector.base(rows[mid])
                if base['observation']['endpoint']!='ATP_CONTENT':
                    result.append({'candidate_id':c['candidate_id'],'paper':paper,'decision':'SOURCE_ONLY_HOLD','record':None,
                        'reason':'EXISTING_CORE_ENDPOINT_DIFFERS_FROM_ABSOLUTE_ATP_CONTENT'});continue
                if base['observation']['author_numeric']!=pt['numeric_value'] or base['provenance']['author_sha256']!=data['source_sha256']:
                    raise ValueError('ATP_PROFILE_NATIVE_OBSERVATION_VALUE_DRIFT')
                identity=bind_author_identity(c['compound_name'],methods,data['source_sha256'],refs)
                proof=calibrated_native_method(c,methods,data['source_sha256']) if calibrated_assays else None
                method=hplc_pool_method(c,methods,data['source_sha256']) or (proof['direct_measurement_evidence'] if proof else None)
                record=reconstruct(base,c,identity,method,proof)
                # Preserve previous quarantine; never inherit positive human/
                # operator labels as new author facts or scientific approval.
                prior=previous.get(mid)
                if prior and prior['task']['decision']=='TASK_READY_CANDIDATE':existing_ready.add(mid)
                record['provenance']['r29_scientific_holds']=(prior or {}).get('provenance',{}).get('r29_scientific_holds',[])
                controls=recon['point_to_measurements'].get(c['native_points']['control']['point_id'],[])
                if len(controls)==1:record['arm']['control_linkage']=controls[0]['measurement_id']
                record.update(profile=plugin.id,profile_version=plugin.version,view=view['id'])
                decision,missing=plugin.qualify(view,record);record.update(task=decision,missing_fields=missing);validate(record,plugin)
                result.append({'candidate_id':c['candidate_id'],'paper':paper,'measurement_id':mid,'record':record,
                    'decision':decision['decision'],'previous_core_decision':prior['task']['decision'] if prior else None,
                    'field_requiredness_is_not_independent_review':True})
    finally:projector.close()
    if sha(db)!=before:raise ValueError('ATP_PROFILE_READONLY_DATABASE_CHANGED')
    ready=[r for r in result if r['decision']=='TASK_READY_CANDIDATE']
    return {'schema':'asterloom.atp-content.profile-requiredness/0.2' if calibrated_assays else 'asterloom.atp-content.profile-requiredness/0.1','reconciliation_path':str(Path(reconciliation_path).resolve()),
        'reconciliation_sha256':reconciliation_sha,'reference_manifest_path':str(Path(reference_path).resolve()) if reference_path else None,
        'reference_manifest_sha256':reference_sha,'science_db':str(db),'science_sha256':before,
        'profile':plugin.manifest,'source_packets':recon['source_packets'],'records':result,'not_applicable':not_applicable,
        'profile_required_complete_unapproved_rows':len(ready),'profile_required_complete_unapproved_studies':len({r['record']['dataset']['study_group'] for r in ready}),
        'existing_core_ready_native_rows_mapped':len(existing_ready),'independently_reviewed_complete_xy':0,
        'Gold':0,'training_approved':0,'status':'EXISTING_PROFILE_REQUIREDNESS_NOT_SCIENTIFIC_APPROVAL'}


def read_profile_audit(path,expected_sha):
    if sha(path)!=expected_sha:raise ValueError('ATP_PROFILE_AUDIT_SHA_HOLD')
    data=json.loads(Path(path).read_text())
    if data.get('schema') not in ('asterloom.atp-content.profile-requiredness/0.1','asterloom.atp-content.profile-requiredness/0.2'):
        raise ValueError('ATP_PROFILE_AUDIT_PROTOCOL_HOLD')
    replay=audit_native_profiles(data['reconciliation_path'],data['reconciliation_sha256'],data['reference_manifest_path'],data['reference_manifest_sha256'],
        data['schema']=='asterloom.atp-content.profile-requiredness/0.2')
    if replay!=data:raise ValueError('ATP_PROFILE_REQUIREDNESS_REPLAY_MISMATCH')
    return data
