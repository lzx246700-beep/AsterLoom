"""ATP-only percent-control graph estimates with explicit endpoint legend.

Other endpoint colors never supply ATP. Every category must align with one
ATP bar using the same rotated-label edge offset. Unknown image dose units
stay unbound even if Methods has a plausible dose. Estimates remain Review-B.
"""
import copy
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from .atp_plot_pixels import read_image,pixel
from .atp_content_plot import _box,_colored_bars,fit_linear_ticks
from .atp_content_grouped import _components,_runs
from .atp_content_relations import verify_relations

AXIS=re.compile(r'(?:In\s+)?%\s+of\s+control(?:\s+mean)?',re.I)
ORIGIN='GRAPH_MULTI_ENDPOINT_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE'
ATP_AXIS=re.compile(r'\bATP\s+(?:level|content)\s*/\s*cells?\b',re.I)
PERCENT_AXIS=re.compile(r'^\s*\(?\s*%\s*control\s*\)?\s*$',re.I)


def _alias(text):return text.casefold().translate(str.maketrans({'′':"'",'’':"'"}))


def direct_atp_panel_axes(tokens,width,height):
    """Recover direct-ATP panel axis scopes when OCR splits the unit label.

    Returns panel-scoped axis candidates only. It deliberately does not infer
    bar values or treatment assignments. A nearby MMP/Cells or other endpoint
    axis cannot be borrowed as ATP evidence.
    """
    boxes=[_box(t,width,height) for t in tokens];out=[];used_percent=set()
    for i,t in enumerate(tokens):
        if not ATP_AXIS.search(t.get('text','')):continue
        x,y,bw,bh=boxes[i]
        nearby=[]
        for j,z in enumerate(tokens):
            if j==i or not PERCENT_AXIS.fullmatch(z.get('text','')):continue
            xx,yy,ww,hh=boxes[j]
            overlap=max(0,min(y+bh,yy+hh)-max(y,yy))
            if abs(xx-x)<=max(18,.035*width) and overlap>=min(bh,hh)*.55:
                nearby.append((j,overlap))
        if len(nearby)!=1:continue
        unit_id=nearby[0][0];used_percent.add(unit_id)
        out.append({'endpoint':'OCR_ATP_AXIS_LABEL_UNVERIFIED','axis_token_id':i,
            'unit_token_id':unit_id,'axis_ocr_literal':t['text'],
            'unit_ocr_literal':tokens[unit_id]['text'],
            'panel_axis_bbox':[x/width,y/height,bw/width,bh/height],
            'scope':'SINGLE_OCR_AXIS_PAIR_ONLY',
            'endpoint_label_state':'OCR_ONLY_REQUIRES_AUTHOR_PANEL_RECONCILIATION',
            'numeric_values_inferred':False})
    # Non-target panels are retained as explicit exclusions for the audit.
    excluded=[]
    for i,t in enumerate(tokens):
        if re.search(r'\b(?:MMP|OCR|ECAR|ATPase|viability)\b',t.get('text',''),re.I):
            excluded.append({'axis_token_id':i,'axis_ocr_literal':t['text'],
                'reason':'NON_ATP_ENDPOINT_AXIS_NOT_ELIGIBLE_FOR_ATP_VALUE_BINDING'})
    return {'direct_atp_panels':out,'non_atp_axis_exclusions':excluded,
        'recognized_panel_count':len(out),'verified_atp_panel_count':0,
        'numeric_candidates':0,
        'status':'OCR_AXIS_LABELS_REQUIRE_AUTHOR_CAPTION_PANEL_RECONCILIATION',
        'coordinate_basis':'normalized bottom-left (Vision); image-space top row has larger y'}


def percent_geometry(image,tokens):
    w,h=image['width'],image['height'];axes=[(i,t) for i,t in enumerate(tokens) if AXIS.fullmatch(t['text'])]
    if len(axes)!=1:raise ValueError('ATP_PERCENT_UNIQUE_CONTROL_PERCENT_AXIS_REQUIRED')
    aid,axis=axes[0];x,y,a,b=_box(axis,w,h);ticks=[]
    for i,t in enumerate(tokens):
        xx,yy,ww,hh=_box(t,w,h);m=re.fullmatch(r'(\d+(?:\.\d+)?)\s*[-–]?',t['text'])
        if m and x+a<=xx<=x+a+.12*w and y-.23*h<=yy+hh/2<=y+b+.15*h:
            ticks.append({'token_id':i,'value':float(m[1]),'y':yy+hh/2,'text':t['text'],'bbox':t['bbox']})
    scale=fit_linear_ticks(ticks);zero=scale['zero_y_pixel']
    if not 0<=zero<h:raise ValueError('ATP_PERCENT_VISIBLE_ZERO_REQUIRED')
    baseline=[]
    for yy in range(max(0,round(zero)-4),min(h,round(zero)+5)):
        for left,right in _runs(image,yy,round(w*.4)):
            if x+a<left<max(_box(tokens[t['token_id']],w,h)[0]+_box(tokens[t['token_id']],w,h)[2] for t in ticks)+15:
                baseline.append((right-left,-abs(yy-zero),yy,left,right))
    if not baseline:raise ValueError('ATP_PERCENT_ZERO_LINE_NOT_VERIFIED')
    _,_,bottom,left,right=max(baseline);top=min(h-1,round(max(t['y'] for t in ticks))+10)
    vertical=[]
    for xx in range(max(0,left-2),min(right,left+max(20,round(w*.04)))):
        vertical.append((sum(max(pixel(image,xx,yy))<200 for yy in range(bottom,top))/(top-bottom),xx))
    fraction,axis_x=max(vertical)
    if fraction<.8:raise ValueError('ATP_PERCENT_VERTICAL_AXIS_NOT_VERIFIED')
    components=_components(image,[0,0,w,h]);legend=[]
    for i,t in enumerate(tokens):
        norm=t['text'].translate(str.maketrans({'А':'A','Α':'A'}))
        if norm!='ATP':continue
        xx,yy,ww,hh=_box(t,w,h);matches=[]
        for c in components:
            cx,cy,cw,ch=c['pixel_bbox']
            if cw>max(15,.1*w) or ch>max(15,.08*h):continue
            if xx-.12*w<=cx+cw/2<xx and abs(cy+ch/2-yy-hh/2)<max(ch,hh)*.6:matches.append(c)
        if len(matches)==1:legend.append({**matches[0],'token_id':i,'ocr_literal':t['text'],'confusable':norm!=t['text']})
    if len(legend)!=1:raise ValueError('ATP_PERCENT_UNIQUE_ATP_LEGEND_REQUIRED')
    legend=legend[0];bars=[]
    for bar in _colored_bars(image,[axis_x+2,bottom,right-1,top],legend['median_rgb']):
        xx,yy,bw,bh=bar['pixel_bbox'];samples=[pixel(image,xx+bw//2,yy+k) for k in range(max(1,bh//3),max(2,2*bh//3))]
        rgb=[statistics.median(v[c] for v in samples) for c in range(3)]
        if math.dist(rgb,legend['median_rgb'])<=80:bars.append({**bar,'median_rgb':rgb})
    if len(bars)<3:raise ValueError('ATP_PERCENT_AT_LEAST_THREE_ATP_BARS_REQUIRED')
    if any(bar['top_y_pixel']>=top for bar in bars):raise ValueError('ATP_PERCENT_BAR_TRUNCATED_BY_CALIBRATION_REGION')
    labels=[]
    for i,t in enumerate(tokens):
        xx,yy,ww,hh=_box(t,w,h)
        if yy+hh>bottom+.025*h or xx+ww<axis_x:continue
        if t['text'].casefold()=='control' or re.fullmatch(r'\d+(?:\.\d+)?\s+[A-Za-zµμ]+\s+.+',t['text']):
            labels.append({'token_id':i,'text':t['text'],'bbox':t['bbox'],'right_edge':xx+ww})
    labels.sort(key=lambda t:t['right_edge'])
    if len(labels)!=len(bars):raise ValueError('ATP_PERCENT_LABEL_BAR_COUNT_MISMATCH')
    offsets=[label['right_edge']-bar['center_x'] for label,bar in zip(labels,bars)]
    offset=statistics.median(offsets);residual=max(abs(v-offset) for v in offsets)
    spacing=min(b['center_x']-a['center_x'] for a,b in zip(bars,bars[1:]))
    if residual>min(6,spacing*.15):raise ValueError('ATP_PERCENT_ROTATED_LABEL_ALIGNMENT_AMBIGUOUS')
    points=[];error=(2+scale['max_tick_residual_px'])*scale['slope_value_per_pixel']
    for bar,label in zip(bars,labels):
        value=bar['top_y_pixel']*scale['slope_value_per_pixel']+scale['intercept']
        points.append({'bar':bar,'category_label':label,'estimated_value':value,'pixel_calibration_halfwidth':error,
            'unit_ocr_literal':axis['text'],'numeric_origin':ORIGIN,'uncertainty_kind':'PIXEL_CALIBRATION_NOT_BIOLOGICAL_SD_OR_SEM'})
    controls=[p for p in points if p['category_label']['text'].casefold()=='control']
    if len(controls)!=1 or controls[0]['estimated_value']-error<=0:raise ValueError('ATP_PERCENT_UNIQUE_POSITIVE_CONTROL_REQUIRED')
    return {'axis_token_id':aid,'axis_ocr_literal':axis['text'],'scale':scale,'zero_line_pixel':bottom,
        'vertical_axis_pixel':axis_x,'vertical_axis_dark_fraction':fraction,'atp_legend':legend,
        'endpoint_ocr_confusable_requires_review':legend['confusable'],'image_dimensions':[w,h],
        'label_bar_edge_offset':offset,'max_label_alignment_residual_px':residual,'points':points,
        'control_point':controls[0],'control_value_forced_to_100':False}


def bind_percent_points(geometry,parents):
    bound=[];holds=[]
    for point in geometry['points']:
        text=point['category_label']['text']
        if text.casefold()=='control':continue
        m=re.fullmatch(r'(\d+(?:\.\d+)?)\s+([A-Za-zµμ]+)\s+(.+)',text)
        if not m:holds.append({'point':point,'reason':'IMAGE_DRUG_DOSE_LABEL_UNRESOLVED'});continue
        factor={'nM':.001,'µM':1,'μM':1,'uM':1,'mM':1000}.get(m[2])
        if factor is None:
            holds.append({'point':point,'reason':'IMAGE_DOSE_UNIT_UNSUPPORTED_OR_UNRESOLVED','ocr_unit':m[2]});continue
        matches=[c for c in parents if _alias(m[3]) in {_alias(n) for n in c['compound_author_names']} and
                 c['fields']['dose_uM']['value'] is not None and c['fields']['dose_uM']['value']==float(m[1])*factor]
        if len(matches)!=1:
            holds.append({'point':point,'reason':'IMAGE_DOSE_AND_AUTHOR_METHODS_UNIQUE_CONTEXT_NOT_BOUND'});continue
        tv=point['estimated_value'];cv=geometry['control_point']['estimated_value']
        te=point['pixel_calibration_halfwidth'];ce=geometry['control_point']['pixel_calibration_halfwidth']
        bound.append({'parent_candidate_id':matches[0]['candidate_id'],'compound':matches[0]['compound_name'],
            'treated_point':point,'control_point':geometry['control_point'],'image_dose_literal':m.group(),
            'estimated_log2fc':math.log2(tv/cv) if min(tv,cv)>0 else None,
            'log2fc_pixel_bounds':[math.log2((tv-te)/(cv+ce)),math.log2((tv+te)/(cv-ce))] if min(tv-te,cv-ce)>0 else None,
            'dose_uM':float(m[1])*factor,'matched_basis':'EXPLICIT_ATP_COLOR_AND_SINGLE_GRAPH_CONTROL_WITH_NAMED_DOSE_METHODS_CORROBORATION'})
    return bound,holds


def extract_percent(figure_packet,figure_sha,relations_packet,relations_sha,protocol_version='0.2'):
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if sha(figure_packet)!=figure_sha or sha(relations_packet)!=relations_sha:raise ValueError('ATP_PERCENT_INPUT_SHA_HOLD')
    visual=json.loads(Path(figure_packet).read_text());data=json.loads(Path(relations_packet).read_text())
    meta=visual.get('official_metadata_evidence')
    if meta and sha(meta['path'])!=meta['sha256']:raise ValueError('ATP_PERCENT_OFFICIAL_METADATA_SHA_HOLD')
    if visual.get('acquisition_receipt_path') and sha(visual['acquisition_receipt_path'])!=visual['acquisition_receipt_sha256']:
        raise ValueError('ATP_PERCENT_ACQUISITION_RECEIPT_SHA_HOLD')
    verify_relations(data,data['source_path'])
    if visual['source_sha256']!=data['source_sha256'] or sha(visual['image_path'])!=visual['image_sha256']:
        raise ValueError('ATP_PERCENT_AUTHOR_SOURCE_BINDING_HOLD')
    parents=[c for c in data['candidates'] if c['figure_id']==visual['figure_id']]
    if protocol_version not in ('0.1','0.2'):raise ValueError('ATP_PERCENT_PROTOCOL_VERSION_UNSUPPORTED')
    geometry=None;candidates=[];holds=[];panel_scope=None
    if not any(AXIS.fullmatch(t['text']) for t in visual['ocr']['tokens']):
        if protocol_version=='0.2':
            panel_scope=direct_atp_panel_axes(visual['ocr']['tokens'],visual['ocr']['width'],visual['ocr']['height'])
            if panel_scope['direct_atp_panels']:
                holds.append({'reason':panel_scope['status']})
            else:holds.append({'reason':'UNSUPPORTED_PERCENT_CONTROL_AXIS'})
        else:holds.append({'reason':'UNSUPPORTED_PERCENT_CONTROL_AXIS'})
    else:
        im=read_image(visual['image_path'])
        if (im['width'],im['height'])!=(visual['ocr']['width'],visual['ocr']['height']):raise ValueError('ATP_PERCENT_DIMENSIONS_HOLD')
        try:
            geometry=percent_geometry(im,visual['ocr']['tokens']);candidates,holds=bind_percent_points(geometry,parents)
        except ValueError as e:holds.append({'reason':str(e)})
    result={'schema':f'asterloom.atp-content.percent-plot/{protocol_version}','source_sha256':data['source_sha256'],
        'image_sha256':visual['image_sha256'],'figure_id':visual['figure_id'],'figure_packet_path':str(Path(figure_packet).resolve()),
        'figure_packet_sha256':figure_sha,'relations_packet_path':str(Path(relations_packet).resolve()),'relations_packet_sha256':relations_sha,
        'geometry':geometry,'candidates':candidates,'holds':holds,'human_gold':0,'training_approved':False,
        'status':'MULTI_ENDPOINT_ATP_ONLY_GRAPH_ESTIMATES_REQUIRE_REVIEW'}
    if protocol_version=='0.2':result['panel_scope']=panel_scope
    return result


def read_percent(path):
    data=json.loads(Path(path).read_text())
    version=(data.get('schema','').rsplit('/',1)[-1])
    if version not in ('0.1','0.2'):raise ValueError('ATP_PERCENT_PROTOCOL_HOLD')
    replay=extract_percent(data['figure_packet_path'],data['figure_packet_sha256'],data['relations_packet_path'],data['relations_packet_sha256'],version)
    if data!=replay:raise ValueError('ATP_PERCENT_PIXEL_REPLAY_MISMATCH')
    return data


def apply_percent(candidate,data,path):
    matches=[c for c in data['candidates'] if c['parent_candidate_id']==candidate['candidate_id']]
    if len(matches)!=1:raise ValueError('ATP_PERCENT_UNIQUE_CONTEXT_VALUE_REQUIRED')
    pair=matches[0];out=copy.deepcopy(candidate);digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    out.update(candidate_id='ATP-PCT-'+hashlib.sha256((candidate['candidate_id']+digest).encode()).hexdigest()[:22],
        endpoint_kind='ATP_CONTENT_GRAPH_ESTIMATE_CANDIDATE',numeric_origin=ORIGIN,
        treated_value=pair['treated_point']['estimated_value'],control_value=pair['control_point']['estimated_value'],
        measurement_unit=data['geometry']['axis_ocr_literal'],
        percent_plot_evidence={'packet_path':str(Path(path).resolve()),'packet_sha256':digest,'image_sha256':data['image_sha256'],
            'pair':pair,'parent_candidate_id':candidate['candidate_id'],'axis_scale':data['geometry']['scale'],
            'endpoint_legend':data['geometry']['atp_legend'],'author_reported_numeric_value':False})
    for field,value in [('treated_arm',pair['image_dose_literal']+' | ATP'),('comparator_arm','Control | ATP')]:
        out['fields'][field]={'value':value,'evidence':[],'image_evidence':out['percent_plot_evidence'],
                              'state':'AUTHOR_METHODS_AND_ENDPOINT_COLOR_GRAPH_CONTROL_CANDIDATE'}
    out['hold_reasons']=sorted(set([h for h in out['hold_reasons'] if h not in ('AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED','MATCHED_COMPARATOR_UNRESOLVED','IMAGE_ARM_ASSIGNMENT_UNVERIFIED')]+
        ['GRAPH_ESTIMATE_NOT_AUTHOR_REPORTED_NUMERIC','GRAPH_PIXEL_METHOD_NEEDS_INDEPENDENT_SCIENCE_REVIEW',
         'GRAPH_CONTROL_LABEL_DOES_NOT_ESTABLISH_VEHICLE_OR_RAW_ABSOLUTE_ATP']))
    if data['geometry']['endpoint_ocr_confusable_requires_review']:out['hold_reasons'].append('ATP_LEGEND_OCR_CONFUSABLE_REQUIRES_REVIEW')
    return out
