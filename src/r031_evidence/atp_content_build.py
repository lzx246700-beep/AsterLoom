"""Bounded offline ATP build using existing extractors and three-table export.

This is a task-specific command, not an agent harness or another database.
Persistent receipts make interrupted extraction resumable without overwrites.
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from . import __version__
from .atp_content_relations import extract_relations
from .atp_content_export import export_candidates,sha

SI_PERMISSION_STATES = {
    'AUTHOR_NATIVE_CC_BY_VERIFIED',
    'PARENT_CC_BY_AND_MEDIA_MD5_VERIFIED',
    'AUTHOR_DECLARED_CC_BY_4_0_VERIFIED',
}


def _supplement_specs(case):
    """Validate exact JATS-declared, permission-verified DOCX supplement inputs."""
    rows = case.get('supplementary_sources', [])
    if not isinstance(rows, list) or len(rows) > 5:
        raise ValueError('ATP_BUILD_SUPPLEMENT_SCOPE_OR_COUNT_HOLD')
    if not rows:
        return []
    parent_sha = case['source_sha256']
    parent_path = Path(case['source_path'])
    if parent_path.suffix.lower() not in ('.xml', '.nxml'):
        raise ValueError('ATP_BUILD_SUPPLEMENT_PARENT_MUST_BE_JATS')
    try:
        root = ET.fromstring(parent_path.read_bytes())
    except ET.ParseError as exc:
        raise ValueError('ATP_BUILD_SUPPLEMENT_PARENT_JATS_PARSE_HOLD') from exc
    license_text = ' '.join(' '.join(node.itertext()) for node in root.iter()
                            if node.tag.rsplit('}', 1)[-1] == 'license')
    if not re.search(r'CC BY\s*4\.0|Creative Commons Attribution\s*4\.0|'
                     r'creativecommons\.org/licenses/by/4\.0', license_text, re.I):
        raise ValueError('ATP_BUILD_SUPPLEMENT_ARTICLE_LICENSE_HOLD')
    from .si_adapter import _source_external_href
    output = []
    seen = set()
    required = {'source_path', 'source_sha256', 'parent_source_sha256',
                'declared_filename', 'native_supplement_id', 'native_supplement_href',
                'permission_status', 'official_source_url', 'http_status'}
    for row in rows:
        if not isinstance(row, dict) or required - row.keys():
            raise ValueError('ATP_BUILD_SUPPLEMENT_PROVENANCE_MANIFEST_REQUIRED')
        path = Path(row['source_path']).resolve()
        if path.suffix.lower() != '.docx' or path.name != row['declared_filename']:
            raise ValueError('ATP_BUILD_SUPPLEMENT_DECLARED_FILENAME_OR_FORMAT_HOLD')
        if row['source_sha256'] in seen:
            raise ValueError('ATP_BUILD_DUPLICATE_SUPPLEMENT_SOURCE_HOLD')
        seen.add(row['source_sha256'])
        if row['parent_source_sha256'] != parent_sha:
            raise ValueError('ATP_BUILD_SUPPLEMENT_PARENT_SHA_MISMATCH')
        if sha(path) != row['source_sha256']:
            raise ValueError('ATP_BUILD_SUPPLEMENT_SOURCE_SHA256_MISMATCH')
        if row['permission_status'] not in SI_PERMISSION_STATES:
            raise ValueError('ATP_BUILD_SUPPLEMENT_PERMISSION_NOT_VERIFIED')
        url = urlparse(str(row['official_source_url']))
        if url.scheme != 'https' or not url.hostname or row['http_status'] != 200:
            raise ValueError('ATP_BUILD_SUPPLEMENT_OFFICIAL_FETCH_RECEIPT_HOLD')
        matches = _source_external_href(parent_path, row['declared_filename'])
        expected_link = (row['native_supplement_id'], row['native_supplement_href'])
        if len(matches) != 1 or matches[0] != expected_link:
            raise ValueError('ATP_BUILD_SUPPLEMENT_NOT_UNIQUELY_DECLARED_IN_PARENT_JATS')
        output.append({
            'path': str(path), 'sha256': row['source_sha256'],
            'parent_source_sha256': row['parent_source_sha256'],
            'declared_filename': row['declared_filename'],
            'native_supplement_id': row['native_supplement_id'],
            'native_supplement_href': row['native_supplement_href'],
            'permission_status': row['permission_status'],
            'official_source_url': row['official_source_url'],
            'http_status': row['http_status'],
        })
    return output


def build(manifest_path,manifest_sha,registry_db,registry_sha,out,figure_packets=(),
          parent_registry=None,parent_registry_sha=None,science_db=None,science_sha=None,resume=False,
          chemical_reference=None,chemical_reference_sha=None,include_identity_holds=False):
    manifest_path=Path(manifest_path).resolve();out=Path(out).resolve()
    if sha(manifest_path)!=manifest_sha:raise ValueError('ATP_BUILD_MANIFEST_SHA256_MISMATCH')
    manifest=json.loads(manifest_path.read_text());cases=manifest['cases']
    if not 1<=len(cases)<=20 or len({c['paper'] for c in cases})!=len(cases):
        raise ValueError('ATP_BUILD_1_TO_20_UNIQUE_SOURCE_CASES_REQUIRED')
    for c in cases:
        if sha(c['source_path'])!=c['source_sha256']:raise ValueError('ATP_BUILD_AUTHOR_SOURCE_SHA256_MISMATCH')
        if not c['compounds'] or len(c['compounds'])>30:raise ValueError('ATP_BUILD_TARGET_COMPOUND_SCOPE_REQUIRED')
        _supplement_specs(c)
        for f in c.get('figures',[]):
            if sha(f['image_path'])!=f['image_sha256'] or sha(f['acquisition_receipt'])!=f['receipt_sha256']:
                raise ValueError('ATP_BUILD_ORIGINAL_FIGURE_SHA256_MISMATCH')
            receipt=json.loads(Path(f['acquisition_receipt']).read_text())
            if receipt.get('official_metadata_path') and sha(receipt['official_metadata_path'])!=receipt.get('official_metadata_sha256'):
                raise ValueError('ATP_BUILD_OFFICIAL_METADATA_SHA256_MISMATCH')
    if bool(science_db)!=bool(science_sha):raise ValueError('ATP_BUILD_SCIENCE_DB_AND_SHA_REQUIRED')
    if sha(registry_db)!=registry_sha:raise ValueError('ATP_BUILD_REGISTRY_SHA256_MISMATCH')
    if science_db and sha(science_db)!=science_sha:raise ValueError('ATP_BUILD_SCIENCE_DB_SHA256_MISMATCH')
    if bool(chemical_reference)!=bool(chemical_reference_sha):raise ValueError('ATP_BUILD_CHEMICAL_REFERENCE_PATH_AND_SHA_REQUIRED')
    if chemical_reference:
        from .atp_content_chemical import load_references
        load_references(chemical_reference,chemical_reference_sha)
    if parent_registry or parent_registry_sha:
        from .atp_content_identities import load_parent_reference
        load_parent_reference(parent_registry,parent_registry_sha)
    for p in figure_packets:
        v=json.loads(Path(p).read_text())
        if sha(v['image_path'])!=v['image_sha256'] or sha(v['source_path'])!=v['source_sha256']:
            raise ValueError('ATP_BUILD_FIGURE_PACKET_SOURCE_DRIFT')
    inputs={'manifest':str(manifest_path),'manifest_sha256':manifest_sha,
        'registry_db':str(Path(registry_db).resolve()),'registry_sha256':registry_sha,
        'figure_packets':[{'path':str(Path(p).resolve()),'sha256':sha(p)} for p in figure_packets],
        'parent_registry':str(Path(parent_registry).resolve()) if parent_registry else None,
        'parent_registry_sha256':parent_registry_sha,'science_db':str(Path(science_db).resolve()) if science_db else None,
        'science_sha256':science_sha,'core_version':__version__,
        'chemical_reference':str(Path(chemical_reference).resolve()) if chemical_reference else None,
        'chemical_reference_sha256':chemical_reference_sha,'include_identity_holds':include_identity_holds}
    state_path=out/'BUILD_STATE.json'
    if out.exists():
        if not resume or not state_path.exists():raise ValueError('ATP_BUILD_NEW_DIRECTORY_OR_EXPLICIT_RESUME_REQUIRED')
        state=json.loads(state_path.read_text())
        if state['inputs']!=inputs:raise ValueError('ATP_BUILD_RESUME_INPUT_OR_VERSION_DRIFT')
        for rel,h in state['files'].items():
            if not (out/rel).is_file() or sha(out/rel)!=h:raise ValueError('ATP_BUILD_RESUME_ARTIFACT_SHA256_MISMATCH')
    else:
        if resume:raise ValueError('ATP_BUILD_NOTHING_TO_RESUME')
        out.mkdir(parents=True)
        state={'schema':'asterloom.atp-content.build-state/1','inputs':inputs,'status':'RUNNING',
               'files':{},'events':[],'network_requests':0,'model_api_calls':0,'Gold':0}
    def save():
        p=out/'BUILD_STATE.tmp';p.write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+'\n');os.replace(p,state_path)
    def artifact(relative,fn):
        p=out/relative
        if relative in state['files']:return json.loads(p.read_text())
        if p.exists():raise ValueError('ATP_BUILD_UNRECEIPTED_EXISTING_ARTIFACT_HOLD')
        start=time.monotonic();data=fn();p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('x',encoding='utf-8') as f:f.write(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        state['files'][relative]=sha(p);state['events'].append({'step':relative,'elapsed_s':time.monotonic()-start,'state':'DONE'});save()
        return data
    if resume and state['status']!='COMPLETE':
        state['events'].append({'step':'RESUME','previous_status':state['status'],'previous_error':state.get('last_error')})
    state['status']='RUNNING';state.pop('last_error',None);save()
    try:
        paths=[];packets=[]
        for c in cases:
            name='relations/'+c['paper']+'.json'
            supplements=_supplement_specs(c)
            d=artifact(name,lambda c=c,supplements=supplements:extract_relations(
                c['source_path'],c['source_sha256'],c['compounds'],
                supplementary_sources=supplements or None))
            if d['paper']['pmcid']!=c['paper']:raise ValueError('ATP_BUILD_AUTHOR_PAPER_IDENTITY_MISMATCH')
            paths.append(out/name);packets.append(d)
        visuals=list(figure_packets)
        for case in cases:
            for i,f in enumerate(case.get('figures',[])):
                from .atp_content_figure import extract_figure
                name=f'figures/{case["paper"]}-{i}.json'
                artifact(name,lambda case=case,f=f:extract_figure(case['source_path'],case['source_sha256'],f['figure_id'],
                    f['image_path'],f['image_sha256'],f['acquisition_receipt']))
                visuals.append(out/name)
        plots=[];printed=[];grouped=[];percent=[]
        for i,visual_path in enumerate(visuals):
            visual=json.loads(Path(visual_path).read_text())
            for packet_path,d in zip(paths,packets):
                if d['source_sha256']!=visual['source_sha256']:continue
                from .atp_content_percent_plot import extract_percent
                name=f'percent/figure-{i}.json'
                value=artifact(name,lambda:extract_percent(visual_path,sha(visual_path),packet_path,sha(packet_path)))
                if value['candidates']:percent.append(out/name)
                targets=sorted((c['compound_name'],c['candidate_id']) for c in d['candidates'] if c['figure_id']==visual['figure_id'])
                for j,target in enumerate(sorted({t[0] for t in targets})):
                    from .atp_content_printed import extract_printed
                    name=f'printed/figure-{i}-compound-{j}.json'
                    value=artifact(name,lambda:extract_printed(visual_path,sha(visual_path),packet_path,sha(packet_path),target))
                    if value['candidates']:printed.append(out/name)
                for j,(target,parent_id) in enumerate(targets):
                    from .atp_content_grouped import extract_grouped
                    name=f'grouped/figure-{i}-target-{j}.json'
                    result=artifact(name,lambda:extract_grouped(visual_path,sha(visual_path),packet_path,sha(packet_path),target,parent_id))
                    if result['geometry'] and result['geometry']['matched_pairs']:grouped.append(out/name)
                    from .atp_content_plot import digitize
                    name=f'plots/figure-{i}-target-{j}.json'
                    result=artifact(name,lambda:digitize(visual_path,sha(visual_path),packet_path,sha(packet_path),target,parent_id))
                    plots.append(out/name)
        recon=None
        if science_db:
            from .atp_content_reconcile import reconcile
            artifact('NATIVE_RECONCILIATION.json',lambda:reconcile(science_db,science_sha,paths));recon=out/'NATIVE_RECONCILIATION.json'
        profile=None
        if recon:
            from .atp_content_profile import audit_native_profiles
            artifact('PROFILE_REQUIREDNESS.json',lambda:audit_native_profiles(recon,sha(recon),chemical_reference,chemical_reference_sha))
            profile=out/'PROFILE_REQUIREDNESS.json'
        export=out/'three_table_candidates_v001'
        if 'three_table_candidates_v001/MANIFEST.json' not in state['files']:
            if export.exists():raise ValueError('ATP_BUILD_UNRECEIPTED_EXISTING_EXPORT_HOLD')
            start=time.monotonic()
            result=export_candidates(paths,registry_db,registry_sha,export,visuals,
                parent_registry,parent_registry_sha,recon,sha(recon) if recon else None,plots,printed,grouped,percent,profile,sha(profile) if profile else None,include_identity_holds)
            for p in export.iterdir():
                if p.is_file():state['files'][str(p.relative_to(out))]=sha(p)
            state['events'].append({'step':'CANONICAL_EXPORT','elapsed_s':time.monotonic()-start,'state':'DONE'});save()
        result=json.loads((export/'MANIFEST.json').read_text())
        summary=artifact('BUILD_RECEIPT.json',lambda:{'schema':'asterloom.atp-content.build-receipt/1',
            'core_version':__version__,
            'source_cases':len(cases),'source_packet_count':len(paths),'plot_packet_count':len(plots),'printed_packet_count':len(printed),'grouped_packet_count':len(grouped),'percent_plot_packet_count':len(percent),
            'native_numeric_cells':sum(len(d['native_numeric_points']) for d in packets),
            'counts':result['counts'],'skipped':result['skipped_candidates'],'export_manifest_sha256':sha(export/'MANIFEST.json'),
            'network_requests':0,'model_api_calls':0,'human_gold':0,'training_approved':0,
            'status':'BOUNDED_AUTOMATIC_ATP_BUILD_COMPLETE_SCIENTIFIC_REVIEW_REQUIRED'})
        state['status']='COMPLETE';save();return summary
    except Exception as e:
        state['status']='HOLD';state['last_error']=type(e).__name__+':'+str(e);save();raise
