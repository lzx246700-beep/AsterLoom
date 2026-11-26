"""Read-only categorical ATP arm candidates from explicit two-color legends.

This bounded layout requires one labelled ATP frame, distinct Control/target
swatches, adjacent two-series clusters, visible group underlines and labels,
and one category label per cluster. Missing labels are not propagated. No
calibration, ATP value, significance or treatment duration is inferred here.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from .atp_plot_pixels import read_image,pixel
from .atp_content_plot import _box,_colored_bars
from .atp_content_relations import verify_relations


def bind_pair_protocol(parent,pair):
    """Bind a visible named factor to the same ATP panel caption only.

    A stimulus-to-analysis interval is not drug exposure. Control/None do not
    prove absence of all other reagents. No mass-to-molar conversion.
    """
    from .atp_content_relations import NUMBER,UNIT,TIME,_hours
    source=parent['scope_evidence'];text=source['quote'];conditions=[];holds=[]
    for role,label in [('GROUP',pair['group_label']),('CATEGORY',pair['category_label'])]:
        if label.casefold() in ('control','none'):continue
        name=r'(?<![\w-])'+r'\s*'.join(re.escape(c) for c in re.sub(r'\s+','',label))+r'(?![\w-])'
        patterns=[rf'(?P<dose>{NUMBER}\s*{UNIT})\s+(?P<name>{name})',
                  rf'(?P<name>{name})\s*\(\s*(?P<dose>{NUMBER}\s*{UNIT})\s*\)']
        found=[]
        for pattern in patterns:
            for match in re.finditer(pattern,text,re.I):
                dose=re.fullmatch(rf'(?P<value>{NUMBER})\s*(?P<unit>{UNIT})',match['dose'])
                evidence={**source,'quote':match.group(),'start':source['start']+match.start(),'end':source['start']+match.end()}
                found.append({'author_name':match['name'],'dose_literal':match['dose'],
                    'dose_value':float(dose['value']),'dose_unit_literal':dose['unit'],'evidence':evidence})
        unique={(d['dose_value'],d['dose_unit_literal']):d for d in found}
        if len(unique)!=1:
            holds.append({'factor_role':role,'image_label':label,'reason':'NAMED_FACTOR_DOSE_MISSING_OR_CONFLICTING_IN_SAME_ATP_PANEL'});continue
        bound=next(iter(unique.values()));intervals=[]
        for match in re.finditer(rf'(?P<name>{name})\s+and\s+analy[sz]ed\s+after\s+(?P<time>{TIME})',text,re.I):
            evidence={**source,'quote':match.group(),'start':source['start']+match.start(),'end':source['start']+match.end()}
            intervals.append({'role':'NAMED_STIMULUS_TO_ANALYSIS_INTERVAL','duration_h':_hours(match['time']),
                'time_literal':match['time'],'evidence':evidence,'is_total_compound_exposure':False})
        times={i['duration_h']:i for i in intervals}
        if len(times)>1:holds.append({'factor_role':role,'image_label':label,'reason':'NAMED_FACTOR_ANALYSIS_INTERVAL_CONFLICT'})
        conditions.append({**bound,'factor_role':role,'image_label':label,'applies_to':'BOTH_VISIBLE_MATCHED_SERIES',
            'time_to_analysis':next(iter(times.values())) if len(times)==1 else None,
            'source_status':'AUTHOR_CAPTION_NAMED_FACTOR_CANDIDATE','independently_reviewed':False})
    return {'conditions':conditions,'holds':holds,'scope':'SAME_PARENT_ATP_PANEL_CAPTION_ONLY',
        'total_compound_exposure_h':None,'other_factors_absent':None,'independent_review_required':True}


def _runs(image,y,min_width):
    runs=[];start=None;w=image['width']
    for x in range(w+1):
        dark=x<w and max(pixel(image,x,y))<160
        if dark and start is None:start=x
        if not dark and start is not None:
            if x-start>=min_width:runs.append((start,x))
            start=None
    return runs


def _lines(image):
    # Consolidate thick, nearly identical horizontal runs; never join gaps.
    lines=[];w,h=image['width'],image['height']
    for y in range(h):
        for left,right in _runs(image,y,max(35,round(w*.045))):
            matched=[r for r in lines if abs(r['left']-left)<=1 and abs(r['right']-right)<=1 and y-r['ys'][-1]<=1]
            if len(matched)==1:matched[0]['ys'].append(y)
            else:lines.append({'left':left,'right':right,'ys':[y]})
    return [{**r,'y':statistics.median(r['ys'])} for r in lines]


def _frame(image,tokens,lines):
    w,h=image['width'],image['height']
    axes=[(i,t) for i,t in enumerate(tokens) if re.fullmatch(r'(?:Intracellular|Cellular|Total)\s+ATP',t['text'],re.I)]
    if len(axes)!=1:raise ValueError('ATP_GROUPED_UNIQUE_ATP_AXIS_REQUIRED')
    aid,axis=axes[0];x,y,bw,bh=_box(axis,w,h);candidates=[]
    for low in lines:
        if not x+bw<=low['left']<=x+bw+.12*w or not y-.15*h<=low['y']<y+bh/2:continue
        for high in lines:
            if abs(high['left']-low['left'])>2 or abs(high['right']-low['right'])>2:continue
            if high['y']-low['y']<max(40,h*.3) or high['y']>y+bh+.15*h:continue
            left=round((low['left']+high['left'])/2);right=round((low['right']+high['right'])/2)-1
            bottom=round(low['y']);top=round(high['y'])
            if top>=h or bottom<0 or right>=w:continue
            sides=[]
            for side in (left,right):
                sides.append(max(sum(max(pixel(image,a,b))<190 for b in range(bottom,top+1))/(top-bottom+1)
                                 for a in range(max(0,side-1),min(w,side+2))))
            if min(sides)<.8:continue
            item={'axis_token_id':aid,'axis_text_ocr_literal':axis['text'],'pixel_bounds':[left,bottom,right,top],
                  'side_dark_fractions':sides,'axis_unit':None}
            if not any(all(abs(a-b)<=2 for a,b in zip(item['pixel_bounds'],c['pixel_bounds'])) for c in candidates):candidates.append(item)
    if len(candidates)!=1:raise ValueError('ATP_GROUPED_UNIQUE_NEAREST_LABELLED_FRAME_REQUIRED')
    return candidates[0]


def _components(image,bounds):
    left,bottom,right,top=bounds;w=image['width'];mask=set()
    for y in range(bottom,top):
        for x in range(left,right):
            rgb=pixel(image,x,y)
            if max(rgb)-min(rgb)>=65 and max(rgb)>=110:mask.add(y*w+x)
    out=[]
    while mask:
        start=mask.pop();stack=[start];coords=[]
        while stack:
            i=stack.pop();yy,xx=divmod(i,w);coords.append((xx,yy))
            for nxt in (i-1,i+1,i-w,i+w):
                if nxt in mask:mask.remove(nxt);stack.append(nxt)
        xs=[p[0] for p in coords];ys=[p[1] for p in coords]
        x,y=min(xs),min(ys);bw,bh=max(xs)-x+1,max(ys)-y+1
        if bw>=4 and bh>=4 and len(coords)>=.7*bw*bh:
            rgb=[statistics.median(pixel(image,a,b)[c] for a,b in coords) for c in range(3)]
            out.append({'pixel_bbox':[x,y,bw,bh],'median_rgb':rgb})
    return out


def _legend(image,tokens,frame,aliases):
    w,h=image['width'],image['height'];left,bottom,right,top=frame['pixel_bounds']
    components=_components(image,[left,top+1,min(w,right+1),h]);names={n.casefold() for n in aliases};selected={}
    for i,t in enumerate(tokens):
        # Leading OCR bullet can represent the colored legend swatch itself.
        text=t['text'].lstrip('•●■□ ').casefold()
        role='TARGET' if text in names else 'CONTROL' if text=='control' else None
        if role is None:continue
        x,y,a,b=_box(t,w,h)
        if y<top or x>right:continue
        matches=[]
        for c in components:
            xx,yy,cw,ch=c['pixel_bbox']
            if cw>max(14,.03*w) or ch>max(15,.08*h):continue
            if x-.07*w<=xx+cw/2<=x+.012*w and abs(yy+ch/2-y-b/2)<=max(ch,b)*.6:matches.append(c)
        if len(matches)==1:
            if role in selected:raise ValueError('ATP_GROUPED_LEGEND_AMBIGUOUS')
            selected[role]={**matches[0],'token_id':i,'author_ocr_literal':t['text'],'series_label':t['text'].lstrip('•●■□ ')}
    if set(selected)!={'TARGET','CONTROL'}:raise ValueError('ATP_GROUPED_TWO_EXPLICIT_LEGEND_SERIES_REQUIRED')
    if math.dist(selected['TARGET']['median_rgb'],selected['CONTROL']['median_rgb'])<100:
        raise ValueError('ATP_GROUPED_LEGEND_COLORS_NOT_DISTINCT')
    return selected


def grouped_geometry(image,tokens,aliases):
    w,h=image['width'],image['height'];lines=_lines(image);frame=_frame(image,tokens,lines)
    legend=_legend(image,tokens,frame,aliases);left,bottom,right,top=frame['pixel_bounds']
    bars=_colored_bars(image,[left+2,bottom,right-1,top]);holds=[];pairs=[]
    if len(bars)<4 or len(bars)%2:raise ValueError('ATP_GROUPED_EVEN_TWO_SERIES_BAR_CLUSTERS_REQUIRED')
    for bar in bars:
        x,y,bw,bh=bar['pixel_bbox']
        samples=[pixel(image,a,b) for a in range(x+bw//3,x+2*bw//3+1) for b in range(y+bh//3,y+2*bh//3+1)]
        rgb=[statistics.median(v[c] for v in samples) for c in range(3)]
        distances=sorted((math.dist(rgb,l['median_rgb']),role) for role,l in legend.items())
        if distances[0][0]>80 or distances[1][0]-distances[0][0]<60:raise ValueError('ATP_GROUPED_BAR_COLOR_AMBIGUOUS')
        bar.update(series_role=distances[0][1],median_rgb=rgb,legend_color_distance=distances[0][0])
    clusters=[bars[i:i+2] for i in range(0,len(bars),2)]
    within=[b[1]['center_x']-b[0]['center_x'] for b in clusters]
    between=[b[0]['center_x']-a[1]['center_x'] for a,b in zip(clusters,clusters[1:])]
    if not between or max(within)>=min(between)*.8:raise ValueError('ATP_GROUPED_CLUSTER_GAPS_AMBIGUOUS')
    if any({b['series_role'] for b in cluster}!={'TARGET','CONTROL'} for cluster in clusters):
        raise ValueError('ATP_GROUPED_CLUSTER_SERIES_MISMATCH')
    centres=[sum(b['center_x'] for b in c)/2 for c in clusters]
    spacing=min(b-a for a,b in zip(centres,centres[1:]));underlines=[]
    for line in lines:
        if not left<=line['left']<line['right']<=right or line['y']>=bottom-.1*h:continue
        labels=[]
        for i,t in enumerate(tokens):
            x,y,a,b=_box(t,w,h)
            if re.search(r'[A-Za-z]',t['text']) and line['left']<=x+a/2<=line['right'] and 0<=y and y+b<=line['y']+2:
                labels.append({'token_id':i,'text':t['text'],'bbox':t['bbox']})
        if len(labels)==1:underlines.append({**line,'label':labels[0]})
    for cluster,cx in zip(clusters,centres):
        groups=[u for u in underlines if u['left']<=cluster[0]['center_x'] and cluster[1]['center_x']<=u['right']]
        if len(groups)!=1:
            holds.append({'cluster_center_x':cx,'reason':'GROUP_LABEL_OR_UNDERLINE_MISSING_OR_AMBIGUOUS'});continue
        group=groups[0];labels=[]
        for i,t in enumerate(tokens):
            x,y,a,b=_box(t,w,h)
            if re.search(r'[A-Za-z]',t['text']) and y>group['y']+.01*h and y+b<bottom+.02*h:
                if abs(x+a/2-cx)<spacing*.4:labels.append({'token_id':i,'text':t['text'],'bbox':t['bbox']})
        if len(labels)!=1:
            holds.append({'cluster_center_x':cx,'group_label':group['label']['text'],'reason':'CATEGORY_LABEL_MISSING_OR_AMBIGUOUS'});continue
        target=next(b for b in cluster if b['series_role']=='TARGET');control=next(b for b in cluster if b['series_role']=='CONTROL')
        pairs.append({'treated_series':legend['TARGET']['series_label'],'comparator_series':legend['CONTROL']['series_label'],
            'group_label':group['label']['text'],'category_label':labels[0]['text'],
            'group_evidence':group,'category_evidence':labels[0],'treated_bar':target,'control_bar':control,
            'treated_value':None,'control_value':None,'measurement_unit':None,
            'matched_basis':'SAME_VISIBLE_GROUP_AND_CATEGORY_DISTINCT_EXPLICIT_LEGEND_SERIES'})
    return {'frame':frame,'legend':legend,'bars':bars,'matched_pairs':pairs,'holds':holds,
            'image_dimensions':[w,h],'numeric_extraction_status':'NO_CALIBRATED_NUMERIC_VALUES'}


def extract_grouped(figure_packet,figure_sha,relations_packet,relations_sha,compound,parent_id,bind_protocol=True):
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if sha(figure_packet)!=figure_sha or sha(relations_packet)!=relations_sha:raise ValueError('ATP_GROUPED_INPUT_SHA_HOLD')
    visual=json.loads(Path(figure_packet).read_text());data=json.loads(Path(relations_packet).read_text())
    verify_relations(data,data['source_path'])
    if visual['source_sha256']!=data['source_sha256'] or sha(visual['image_path'])!=visual['image_sha256']:
        raise ValueError('ATP_GROUPED_AUTHOR_SOURCE_BINDING_HOLD')
    parents=[c for c in data['candidates'] if c['candidate_id']==parent_id and c['compound_name']==compound and c['figure_id']==visual['figure_id']]
    if len(parents)!=1:raise ValueError('ATP_GROUPED_UNIQUE_PARENT_REQUIRED')
    parent=parents[0];geometry=None;holds=[]
    if not any(re.fullmatch(r'(?:Intracellular|Cellular|Total)\s+ATP',t['text'],re.I) for t in visual['ocr']['tokens']):
        holds.append({'reason':'ATP_GROUPED_UNIQUE_ATP_AXIS_REQUIRED'})
    else:
        im=read_image(visual['image_path'])
        if (im['width'],im['height'])!=(visual['ocr']['width'],visual['ocr']['height']):raise ValueError('ATP_GROUPED_OCR_DIMENSIONS_HOLD')
        try:geometry=grouped_geometry(im,visual['ocr']['tokens'],parent['compound_author_names'])
        except ValueError as e:holds.append({'reason':str(e)})
    if geometry and bind_protocol:
        for pair in geometry['matched_pairs']:pair['protocol_context']=bind_pair_protocol(parent,pair)
    return {'schema':'asterloom.atp-content.grouped-arms/0.2' if bind_protocol else 'asterloom.atp-content.grouped-arms/0.1','source_sha256':data['source_sha256'],
        'image_sha256':visual['image_sha256'],'figure_id':visual['figure_id'],'compound':compound,'parent_candidate_id':parent_id,
        'figure_packet_path':str(Path(figure_packet).resolve()),'figure_packet_sha256':figure_sha,
        'relations_packet_path':str(Path(relations_packet).resolve()),'relations_packet_sha256':relations_sha,
        'geometry':geometry,'holds':holds,'independent_review_required':True,'training_approved':False,
        'status':'CATEGORICAL_ARM_PROPOSALS_ONLY_NO_ATP_VALUES'}


def read_grouped(path):
    data=json.loads(Path(path).read_text())
    if data.get('schema') not in ('asterloom.atp-content.grouped-arms/0.1','asterloom.atp-content.grouped-arms/0.2'):raise ValueError('ATP_GROUPED_PROTOCOL_HOLD')
    replay=extract_grouped(data['figure_packet_path'],data['figure_packet_sha256'],data['relations_packet_path'],
                           data['relations_packet_sha256'],data['compound'],data['parent_candidate_id'],
                           data['schema']=='asterloom.atp-content.grouped-arms/0.2')
    if replay!=data:raise ValueError('ATP_GROUPED_PIXEL_REPLAY_MISMATCH')
    return data


def apply_grouped(candidate,data,path):
    if candidate['candidate_id']!=data['parent_candidate_id'] or not data['geometry']:return []
    if candidate['treated_value'] is not None or candidate['control_value'] is not None:
        raise ValueError('ATP_GROUPED_CANNOT_BROADCAST_UNSCOPED_NUMERIC_VALUE')
    children=[];digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for pair in data['geometry']['matched_pairs']:
        child=copy.deepcopy(candidate);f=child['fields']
        key=candidate['candidate_id']+digest+json.dumps(pair,sort_keys=True)
        child['candidate_id']='ATP-ARM-'+hashlib.sha256(key.encode()).hexdigest()[:22]
        context=pair['group_label']+' | '+pair['category_label']
        image_ev={'packet_path':str(Path(path).resolve()),'packet_sha256':digest,'image_sha256':data['image_sha256'],
                  'pair':pair,'parent_candidate_id':candidate['candidate_id'],'unreviewed':True}
        for field,series in [('treated_arm',pair['treated_series']),('comparator_arm',pair['comparator_series'])]:
            f[field]={'value':series+' | '+context,'evidence':[],
                      'image_evidence':image_ev,'state':'VISIBLE_CATEGORICAL_SERIES_AND_GROUP_CANDIDATE'}
        child['categorical_arm_evidence']=image_ev
        interval=f['pretreatment_h']
        if not child['treatment_stages'] and interval['value'] is not None and interval['state']=='LOCAL_DRUG_INTERVAL_SEQUENCE_ROLE_UNRESOLVED':
            child['treatment_stages']=[{'role':'LOCAL_COMPOUND_INTERVAL_SEQUENCE_ROLE_UNRESOLVED',
                'duration_h':interval['value'],'evidence':interval['evidence'],
                'is_total_exposure':False,'is_pretreatment_proven':False}]
        protocol=pair.get('protocol_context')
        if protocol:
            child['matched_factor_protocol']=protocol
            for condition in protocol['conditions']:
                child['treatment_stages'].append({'role':'MATCHED_BOTH_ARMS_NAMED_FACTOR',
                    'agent':condition['author_name'],'dose_literal':condition['dose_literal'],
                    'dose_value':condition['dose_value'],'dose_unit_literal':condition['dose_unit_literal'],
                    'evidence':[condition['evidence']],'time_to_analysis':condition['time_to_analysis'],
                    'is_total_exposure':False,'applies_to':'BOTH_VISIBLE_MATCHED_SERIES'})
        child['hold_reasons']=sorted(set([h for h in child['hold_reasons'] if h!='MATCHED_COMPARATOR_UNRESOLVED']+
            ['CATEGORICAL_IMAGE_ARM_MATCH_REQUIRES_REVIEW','CATEGORY_LABELS_DO_NOT_ESTABLISH_ALL_PROTOCOL_CONDITIONS',
             'ATP_GRAPH_UNIT_AND_NUMERIC_VALUES_UNRESOLVED']))
        # Unchanged parent conditions remain provisional; global challenge/time
        # is never broadcast into None/control or a different group.
        children.append(child)
    return children
