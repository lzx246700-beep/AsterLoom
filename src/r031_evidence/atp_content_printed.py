"""OCR candidates for author-printed mean ATP percentages, not bar estimates.

The figure caption must explicitly define mean cellular ATP as percent of
control in the primary screen. A standalone compound name must align with one
printed number; dose labels, other panel numbers and multiple matches HOLD.
"""
import copy
import hashlib
import json
import re
from pathlib import Path
from .atp_content_relations import verify_relations


def aligned_printed_values(tokens,names):
    hits=[]
    for i,t in enumerate(tokens):
        if t['text'].casefold() not in {n.casefold() for n in names}:continue
        x,y,w,h=t['bbox'];numbers=[]
        for j,v in enumerate(tokens):
            a,b,c,d=v['bbox']
            if re.fullmatch(r'\d+(?:\.\d+)?',v['text']) and a>x+w and a<x+w+.35 and abs(b+d/2-y-h/2)<max(h,d)*.6:
                numbers.append({'value':float(v['text']),'token_id':j,'author_ocr_literal':v['text'],'bbox':v['bbox']})
        if len(numbers)==1:hits.append({'compound_label_token_id':i,'compound_ocr_literal':t['text'],
                                      'compound_label_bbox':t['bbox'],'printed_value':numbers[0]})
    return hits


def extract_printed(figure_packet,figure_sha,relations_packet,relations_sha,compound):
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if sha(figure_packet)!=figure_sha or sha(relations_packet)!=relations_sha:raise ValueError('ATP_PRINTED_INPUT_SHA256_HOLD')
    visual=json.loads(Path(figure_packet).read_text());data=json.loads(Path(relations_packet).read_text())
    verify_relations(data,data['source_path'])
    if visual['source_sha256']!=data['source_sha256'] or sha(visual['image_path'])!=visual['image_sha256']:
        raise ValueError('ATP_PRINTED_AUTHOR_IMAGE_BINDING_HOLD')
    candidates=[]
    for c in data['candidates']:
        if c['compound_name']!=compound or c['figure_id']!=visual['figure_id']:continue
        cap=c['scope_evidence']['quote']
        if not c.get('primary_screen_protocol_candidate') or not re.search(r'mean cellular ATP in\s*%\s*of control',cap,re.I):continue
        hits=aligned_printed_values(visual['ocr']['tokens'],c['compound_author_names'])
        if len(hits)!=1:continue
        candidates.append({'parent_candidate_id':c['candidate_id'],'compound':compound,'panel':c['panel'],
            'printed_percentage':hits[0]['printed_value']['value'],'percentage_semantics':'MEAN_ATP_PERCENT_OF_AUTHOR_CONTROL',
            'unit':'% of author primary-screen control','ocr_evidence':hits[0],
            'numeric_origin':'AUTHOR_FIGURE_PRINTED_NUMERIC_OCR_CANDIDATE',
            'raw_control_value':None,'training_approved':False})
    return {'schema':'asterloom.atp-content.printed-percent/0.1','source_sha256':data['source_sha256'],
        'image_sha256':visual['image_sha256'],'figure_id':visual['figure_id'],'compound':compound,
        'figure_packet_path':str(Path(figure_packet).resolve()),'figure_packet_sha256':figure_sha,
        'relations_packet_path':str(Path(relations_packet).resolve()),'relations_packet_sha256':relations_sha,
        'candidates':candidates,'status':'AUTHOR_PRINTED_PERCENT_OCR_CANDIDATES_REQUIRE_REVIEW',
        'training_approved':False,'human_gold':0}


def read_printed(path):
    data=json.loads(Path(path).read_text())
    if data.get('schema')!='asterloom.atp-content.printed-percent/0.1':raise ValueError('ATP_PRINTED_PROTOCOL_HOLD')
    replay=extract_printed(data['figure_packet_path'],data['figure_packet_sha256'],
        data['relations_packet_path'],data['relations_packet_sha256'],data['compound'])
    if replay!=data:raise ValueError('ATP_PRINTED_REPLAY_MISMATCH')
    return data


def apply_printed(candidate,data,path):
    matches=[c for c in data['candidates'] if c['parent_candidate_id']==candidate['candidate_id']]
    if not matches:return candidate
    if len(matches)!=1:raise ValueError('ATP_PRINTED_AMBIGUOUS_VALUE_HOLD')
    value=matches[0];out=copy.deepcopy(candidate);digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    out.update(candidate_id='ATP-PRINT-'+hashlib.sha256((candidate['candidate_id']+digest).encode()).hexdigest()[:22],
        numeric_origin=value['numeric_origin'],endpoint_kind='ATP_CONTENT_AUTHOR_PRINTED_PERCENT_CANDIDATE',
        treated_value=value['printed_percentage'],control_value=None,measurement_unit=value['unit'],
        author_reported_percent=value['printed_percentage'],percentage_semantics=value['percentage_semantics'],
        printed_value_evidence={'packet_path':str(Path(path).resolve()),'packet_sha256':digest,
            'image_sha256':data['image_sha256'],'figure_id':data['figure_id'],'candidate':value})
    out['hold_reasons']=sorted(set([h for h in out['hold_reasons'] if h!='AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED']+
        ['AUTHOR_PRINTED_OCR_REQUIRES_VERIFICATION','CONTROL_IS_PERCENT_REFERENCE_NOT_RAW_MEASUREMENT']))
    return out
