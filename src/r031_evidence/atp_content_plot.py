"""Bounded linear colored-bar digitization and visible-condition matching.

No hardcoded paper IDs, pixel positions, doses, times, outcomes or manually
filled comparison labels. OCR, pixels and original JATS are retained as
separate evidence. Every result is an image estimate and remains Review-B.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path

from .atp_plot_pixels import read_png,read_image,pixel,dark_symbol
from .atp_content_relations import verify_relations


def _box(t,w,h):
    x,y,a,b=t['bbox'];return [x*w,y*h,a*w,b*h]


def fit_linear_ticks(ticks):
    if len(ticks)<3 or len({t['value'] for t in ticks})<3:
        raise ValueError('ATP_GRAPH_THREE_DISTINCT_TICKS_REQUIRED')
    n=len(ticks);mx=sum(t['y'] for t in ticks)/n;my=sum(t['value'] for t in ticks)/n
    denom=sum((t['y']-mx)**2 for t in ticks)
    if denom<=0:raise ValueError('ATP_GRAPH_DEGENERATE_AXIS')
    slope=sum((t['y']-mx)*(t['value']-my) for t in ticks)/denom;intercept=my-slope*mx
    if slope<=0:raise ValueError('ATP_GRAPH_NON_MONOTONIC_OR_LOG_SCALE_HOLD')
    residual=max(abs((t['value']-intercept)/slope-t['y']) for t in ticks)
    if residual>2:raise ValueError('ATP_GRAPH_NONLINEAR_OR_OCR_AXIS_HOLD')
    return {'slope_value_per_pixel':slope,'intercept':intercept,'max_tick_residual_px':residual,
            'zero_y_pixel':-intercept/slope,'calibration_tokens':ticks}


def _colored_bars(image,roi,target_color=None):
    left,bottom,right,top=roi;mask=set()
    for y in range(bottom,top):
        for x in range(left,right):
            rgb=pixel(image,x,y)
            if max(rgb)-min(rgb)>=65 and max(rgb)>=110 and (target_color is None or math.dist(rgb,target_color)<=80):
                mask.add(y*image['width']+x)
    bars=[];w=image['width']
    while mask:
        start=mask.pop();stack=[start];xs=[];ys=[]
        while stack:
            i=stack.pop();y,x=divmod(i,w);xs.append(x);ys.append(y)
            for k in (i-1,i+1,i-w,i+w):
                if k in mask:mask.remove(k);stack.append(k)
        xmin,xmax,ymin,ymax=min(xs),max(xs),min(ys),max(ys)
        bw,bh=xmax-xmin+1,ymax-ymin+1
        # Only filled columns seated on the common baseline are supported.
        if bw>=6 and bh>=8 and len(xs)>=bw*bh*.7 and abs(ymin-bottom)<=8:
            bars.append({'pixel_bbox':[xmin,ymin,bw,bh],'center_x':(xmin+xmax)/2,
                         'top_y_pixel':ymax+1,'color_pixel_count':len(xs)})
    return sorted(bars,key=lambda b:b['center_x'])


def _axis_region(image,tokens,axis_id,relative=False):
    w,h=image['width'],image['height'];axis=tokens[axis_id];x,y,bw,bh=_box(axis,w,h)
    ticks=[]
    for i,t in enumerate(tokens):
        a,b,c,d=_box(t,w,h)
        if re.fullmatch(r'\d+(?:\.\d+)?',t['text']) and x+bw-.005*w<=a<=x+bw+.06*w and y-.035*h<=b+d/2<=y+bh+.035*h:
            ticks.append({'token_id':i,'text':t['text'],'value':float(t['text']),'y':b+d/2,'bbox':t['bbox']})
    scale=fit_linear_ticks(ticks);zero=scale['zero_y_pixel']
    if zero<0 or zero>=h:raise ValueError('ATP_GRAPH_BASELINE_OUTSIDE_IMAGE')
    left=math.ceil(max(_box(tokens[t['token_id']],w,h)[0]+_box(tokens[t['token_id']],w,h)[2] for t in ticks)+2)
    # Adjacent vertical plot labels at the same height bound this subplot.
    neighbours=[]
    for i,t in enumerate(tokens):
        a,b,c,d=_box(t,w,h)
        overlap=max(0,min(y+bh,b+d)-max(y,b))
        if i!=axis_id and d>3*c and a>x+bw and overlap>min(bh,d)*.4:neighbours.append(a)
    right=int(min(neighbours)-.01*w) if neighbours else min(w-1,left+int(.33*w))
    if not left+30<right:raise ValueError('ATP_GRAPH_SUBPLOT_BOUNDS_HOLD')
    # Verify an actual zero-axis line near the extrapolated zero; never assume
    # every chart starts at 0 or use a truncated axis as a zero denominator.
    lines=[]
    for baseline in range(max(0,round(zero)-4),min(h,round(zero)+5)):
        fraction=sum(max(pixel(image,a,baseline))<230 for a in range(left,right))/(right-left)
        lines.append((fraction,baseline))
    zero_fraction,baseline=max(lines)
    if zero_fraction<.55:raise ValueError('ATP_GRAPH_ZERO_BASELINE_NOT_VERIFIED')
    maxvalue=max(t['value'] for t in ticks);top=min(h-1,round((maxvalue-scale['intercept'])/scale['slope_value_per_pixel'])+10)
    vertical=[]
    for axis_x in range(left,min(right,left+round(.035*w))):
        fraction_vertical=sum(max(pixel(image,axis_x,b))<230 for b in range(baseline,top))/(top-baseline)
        vertical.append((fraction_vertical,axis_x))
    vertical_fraction,axis_x=max(vertical)
    if vertical_fraction<.6:raise ValueError('ATP_GRAPH_VERTICAL_AXIS_NOT_VERIFIED')
    if relative:
        # A neighboring subplot may have no OCR-readable vertical title. Bound
        # this plot by its own visible horizontal baseline instead of allowing
        # the ROI to spill into the next chart.
        dark=[]
        for x in range(axis_x,min(w-1,right)):
            fraction=sum(max(pixel(image,x,yy))<190 for yy in range(max(0,baseline-1),min(h,baseline+2)))/3
            dark.append((x,fraction>=.67))
        started=False;last_dark=None;gap=0;end=None
        for x,is_dark in dark:
            if not started:
                if is_dark:started=True;last_dark=x
                continue
            if is_dark:last_dark=x;gap=0
            else:
                gap+=1
                if gap>3:end=last_dark+1;break
        if end is None:end=(last_dark+1) if last_dark is not None else 0
        if end-left<40:raise ValueError('ATP_GRAPH_BASELINE_SUBPLOT_BOUNDARY_HOLD')
        right=end
    left=axis_x+2
    # Bars start a few pixels above the axis line, inside the colored outline.
    roi=[left,max(0,baseline),right,top]
    bars=_colored_bars(image,roi)
    if not relative and len(bars)<3:raise ValueError('ATP_GRAPH_INSUFFICIENT_FILLED_BARS')
    return {'axis_token_id':axis_id,'axis_text_ocr_literal':axis['text'],'roi_pixel_bounds':roi,
            'scale':scale,'zero_axis_dark_fraction':zero_fraction,'vertical_axis_dark_fraction':vertical_fraction,
            'baseline_pixel':baseline,'bars':bars}


def _relative_bars(image,chart):
    """Return calibrated, deliberately unassigned bar estimates for a relative axis."""
    left,baseline,right,top=chart['roi_pixel_bounds'];w=image['width']
    eligible=[]
    for x in range(left+1,right-1):
        count=0
        for y in range(baseline+2,top):
            rgb=pixel(image,x,y)
            # Includes pale colored and neutral gray fills; excludes white
            # background and dark outlines, axes, text and error-bar strokes.
            if min(rgb)>100 and sum(255-v for v in rgb)>=25:count+=1
        if count>=24:eligible.append((x,count))
    groups=[]
    for item in eligible:
        if not groups or item[0]>groups[-1][-1][0]+1:groups.append([item])
        else:groups[-1].append(item)
    scale=chart['scale'];out=[]
    for group in groups:
        x0,x1=group[0][0],group[-1][0]
        if not 6<=x1-x0+1<=60:continue
        interior=range(x0+max(1,(x1-x0+1)//8),x1+1-max(1,(x1-x0+1)//8))
        rows=[]
        for y in range(baseline+2,top):
            filled=0
            for x in interior:
                rgb=pixel(image,x,y)
                if min(rgb)>100 and sum(255-v for v in rgb)>=25:filled+=1
            if filled/max(1,len(interior))>=.55:rows.append(y)
        # The bar must be continuously seated on the verified zero baseline.
        contiguous=[]
        for y in rows:
            if not contiguous or y<=contiguous[-1]+2:contiguous.append(y)
            elif contiguous:break
        if len(contiguous)<12 or contiguous[0]>baseline+4:continue
        top_y=max(contiguous)
        value=scale['slope_value_per_pixel']*top_y+scale['intercept']
        if value<=0:continue
        halfwidth=(3+scale['max_tick_residual_px'])*scale['slope_value_per_pixel']
        out.append({'bar_ordinal_left_to_right':len(out)+1,
            'pixel_bbox':[x0,baseline+2,x1-x0+1,top_y-baseline-1],
            'top_y_pixel':top_y,'axis_estimate':value,'axis_estimate_halfwidth':halfwidth,
            'axis_literal':chart['axis_text_ocr_literal'],
            'unit_semantics':'AUTHOR_RELATIVE_AXIS_DENOMINATOR_UNRESOLVED',
            'arm_assignment':'HOLD_UNASSIGNED','comparator_assignment':'HOLD_UNASSIGNED',
            'numeric_origin':'GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE',
            'uncertainty_kind':'PIXEL_CALIBRATION_BOUND_NOT_BIOLOGICAL_SD_OR_SEM'})
    if not out:raise ValueError('ATP_RELATIVE_AXIS_NO_UNAMBIGUOUS_FILLED_BARS')
    return out


def _matrix(image,tokens,chart,aliases):
    w,h=image['width'],image['height'];left,bottom,right,top=chart['roi_pixel_bounds']
    labels=[]
    # A row label must be left of these bars and physically below this axis.
    for i,t in enumerate(tokens):
        x,y,a,b=_box(t,w,h)
        if not left-.18*w<x<left and not left-.18*w<x+a<left:continue
        if x+a>left+4 or not 0<=y+b/2<bottom:continue
        m=re.fullmatch(r'\s*([A-Za-z][\w-]*)\s*\(([^)]+)\)\s*',t['text'])
        if m:labels.append({'agent':m[1],'unit_or_dose_ocr_literal':m[2],
                           'token_id':i,'y_pixel':y+b/2,'bbox':t['bbox']})
    target=[r for r in labels if r['agent'].casefold() in {a.casefold() for a in aliases} and re.fullmatch(r'[uµμ]M|mM|nM',r['unit_or_dose_ocr_literal'])]
    if len(target)!=1:raise ValueError('ATP_GRAPH_UNIQUE_TARGET_DOSE_ROW_REQUIRED')
    target=target[0]
    if len(labels)<3:raise ValueError('ATP_GRAPH_CONDITION_MATRIX_INCOMPLETE')
    gaps=[b['center_x']-a['center_x'] for a,b in zip(chart['bars'],chart['bars'][1:])]
    halfwidth=max(5,min(15,int(min(gaps)*.32)));halfheight=9
    for bar in chart['bars']:
        states={};cx=round(bar['center_x'])
        for row in labels:
            if row is target:
                numbers=[]
                for i,t in enumerate(tokens):
                    x,y,a,b=_box(t,w,h)
                    if re.fullmatch(r'\d+(?:\.\d+)?',t['text']) and abs(y+b/2-row['y_pixel'])<max(b,10)*.5 and abs(x+a/2-cx)<min(gaps)*.3:
                        numbers.append({'value':float(t['text']),'token_id':i,'bbox':t['bbox'],'ocr_literal':t['text']})
                if len(numbers)==1:
                    states[row['agent']]={'state':'DOSE_REPORTED_IN_IMAGE','dose':numbers[0],
                        'unit_ocr_literal':row['unit_or_dose_ocr_literal'],'label_token_id':row['token_id']}
                    continue
            sym=dark_symbol(image,cx,round(row['y_pixel']),halfwidth,halfheight)
            states[row['agent']]={'state':sym['symbol'],'glyph_pixels':sym,'label_token_id':row['token_id'],
                                 'row_label_ocr_literal':tokens[row['token_id']]['text']}
        bar['condition_states']=states
    return {'condition_rows':labels,'target_agent':target['agent'],'target_unit_ocr_literal':target['unit_or_dose_ocr_literal']}


def _match_controls(chart,matrix):
    target=matrix['target_agent'];pairs=[]
    ref=re.search(r'fold\s+of\s+([^)]*)',chart['axis_text_ocr_literal'],re.I)
    if not ref:raise ValueError('ATP_GRAPH_REFERENCE_COMPARATOR_NOT_STATED')
    reference=[r['agent'] for r in matrix['condition_rows'] if re.search(r'\b'+re.escape(r['agent'])+r'\b',ref[1],re.I)]
    if not reference:raise ValueError('ATP_GRAPH_REFERENCE_AGENTS_NOT_IN_MATRIX')
    for treated in chart['bars']:
        ts=treated['condition_states'];dose=ts[target]
        if dose['state']!='DOSE_REPORTED_IN_IMAGE':continue
        if any(s['state']=='UNKNOWN' for s in ts.values()):continue
        if any(ts.get(name,{}).get('state')!='PRESENT' for name in reference):continue
        # Exactly one drug changes between the two displayed experimental arms.
        controls=[]
        for control in chart['bars']:
            cs=control['condition_states']
            if cs[target]['state']!='ABSENT':continue
            if all(cs[a]['state']==s['state'] and s['state'] in ('PRESENT','ABSENT') for a,s in ts.items() if a!=target):
                controls.append(control)
        if len(controls)==1:pairs.append({'dose':dose,'treated_bar':treated,'control_bar':controls[0],
            'matched_basis':'IDENTICAL_VISIBLE_CONDITION_MATRIX_EXCEPT_TARGET_COMPOUND',
            'reference_agent_names':reference})
    return pairs


def digitize(figure_packet,figure_packet_sha,relations_packet,relations_packet_sha,compound,parent_candidate_id=None):
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    if digest(figure_packet)!=figure_packet_sha or digest(relations_packet)!=relations_packet_sha:
        raise ValueError('ATP_PLOT_INPUT_PACKET_SHA256_MISMATCH')
    visual=json.loads(Path(figure_packet).read_text());relations=json.loads(Path(relations_packet).read_text())
    verify_relations(relations,relations['source_path'])
    if visual['source_sha256']!=relations['source_sha256'] or digest(visual['image_path'])!=visual['image_sha256']:
        raise ValueError('ATP_PLOT_IMAGE_SOURCE_BINDING_MISMATCH')
    parents=[c for c in relations['candidates'] if c['compound_name']==compound and c['figure_id']==visual['figure_id']]
    if parent_candidate_id is not None:parents=[c for c in parents if c['candidate_id']==parent_candidate_id]
    if len(parents)!=1:raise ValueError('ATP_PLOT_UNIQUE_CONTEXT_PARENT_REQUIRED')
    parent=parents[0];tokens=visual['ocr']['tokens']
    axis_ids=[i for i,t in enumerate(tokens) if re.search(r'ATP\s+(?:level|content).{0,20}fold|relative\s+ATP\b',t['text'],re.I)]
    image=(read_image(visual['image_path']) if axis_ids else {'width':visual['ocr']['width'],'height':visual['ocr']['height']})
    if (image['width'],image['height'])!=(visual['ocr']['width'],visual['ocr']['height']):
        raise ValueError('ATP_PLOT_OCR_PIXEL_DIMENSIONS_MISMATCH')
    charts=[];holds=[] if axis_ids else [{'reason':'UNSUPPORTED_OR_UNSPECIFIED_AXIS_UNIT_NOT_A_SUPPORTED_ATP_PLOT'}];pairs=[];relative_bars=[]
    for i,t in enumerate(tokens):
        is_relative=bool(re.search(r'relative\s+ATP\b',t['text'],re.I))
        if not re.search(r'ATP\s+(?:level|content).{0,20}fold|relative\s+ATP\b',t['text'],re.I):continue
        try:
            chart=_axis_region(image,tokens,i,relative=is_relative)
            if is_relative:
                chart['bars']=[]
                estimates=_relative_bars(image,chart)
                for estimate in estimates:estimate['axis_token_id']=i
                relative_bars.extend(estimates);charts.append(chart);continue
            matrix=_matrix(image,tokens,chart,parent['compound_author_names'])
            matched=_match_controls(chart,matrix);chart.update(matrix=matrix)
            scale=chart['scale'];slope=scale['slope_value_per_pixel'];intercept=scale['intercept']
            # This is pixel/axis uncertainty, not experimental SD/SEM.
            error=(2+scale['max_tick_residual_px'])*slope
            for p in matched:
                tv=p['treated_bar']['top_y_pixel']*slope+intercept
                cv=p['control_bar']['top_y_pixel']*slope+intercept
                if min(tv-error,cv-error)<=0:continue
                p.update(estimated_treated_value=tv,estimated_control_value=cv,
                    estimated_log2fc=math.log2(tv/cv),
                    digitization_value_halfwidth=error,
                    log2fc_pixel_bounds=[math.log2((tv-error)/(cv+error)),math.log2((tv+error)/(cv-error))],
                    numeric_origin='GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE',
                    measurement_unit_ocr_literal=chart['axis_text_ocr_literal'],
                    uncertainty_kind='PIXEL_CALIBRATION_BOUND_NOT_BIOLOGICAL_SD_OR_SEM',
                    axis_token_id=i)
                pairs.append(p)
            charts.append(chart)
        except ValueError as e:holds.append({'axis_token_id':i,'reason':str(e)})
    return {'schema':'asterloom.atp-content.plot-estimates/0.3','compound':compound,
        'source_sha256':relations['source_sha256'],'figure_id':visual['figure_id'],'image_sha256':visual['image_sha256'],
        'figure_packet_path':str(Path(figure_packet).resolve()),'figure_packet_sha256':figure_packet_sha,
        'relations_packet_path':str(Path(relations_packet).resolve()),'relations_packet_sha256':relations_packet_sha,
        'parent_candidate_id':parent['candidate_id'],'requested_parent_candidate_id':parent_candidate_id,
        'image_dimensions':[image['width'],image['height']],
        'charts':charts,'estimated_pairs':pairs,'relative_bar_candidates':relative_bars,'holds':holds,'numeric_origin':'GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE',
        'assumptions':['LINEAR_NONTRUNCATED_AXIS_WITH_THREE_TICKS_AND_VISIBLE_ZERO_LINE',
            'SINGLE_SERIES_FILLED_RECTANGULAR_BARS_COLORED_OR_NEUTRAL_GRAY',
            'RELATIVE_AXIS_ESTIMATES_REMAIN_UNASSIGNED_UNLESS_SEPARATELY_VERIFIED',
            'PLUS_MINUS_VISIBLE_CONDITION_MATRIX_FOR_FOLD_AXIS_PAIRING_ONLY',
            'MATCH_ONLY_IDENTICAL_OBSERVED_CONDITIONS_EXCEPT_TARGET_DOSE'],
        'not_universal_figure_parser':True,'review_tier':'Review-B','training_approved':False,
        'status':'UNASSIGNED_RELATIVE_GRAPH_BAR_ESTIMATES_REVIEW_ONLY' if relative_bars and not pairs else 'GRAPH_ESTIMATES_AND_MATCHED_VISIBLE_ARMS_REQUIRE_SCIENTIFIC_REVIEW'}


def read_plot_packet(path):
    data=json.loads(Path(path).read_text())
    schema=data.get('schema')
    if schema not in ('asterloom.atp-content.plot-estimates/0.2','asterloom.atp-content.plot-estimates/0.3'):
        raise ValueError('ATP_GRAPH_PACKET_PROTOCOL_HOLD')
    rerun=digitize(data['figure_packet_path'],data['figure_packet_sha256'],
                   data['relations_packet_path'],data['relations_packet_sha256'],data['compound'],data.get('requested_parent_candidate_id'))
    if schema.endswith('/0.2'):
        # Preserve exact replay for legacy fold-axis packets. A legacy HOLD on a
        # newly supported relative axis must be regenerated under protocol 0.3.
        if rerun['relative_bar_candidates']:
            raise ValueError('ATP_GRAPH_LEGACY_RELATIVE_PACKET_REBUILD_REQUIRED')
        rerun.pop('relative_bar_candidates',None);rerun['schema']=schema
        for hold in rerun['holds']:
            if hold.get('reason')=='UNSUPPORTED_OR_UNSPECIFIED_AXIS_UNIT_NOT_A_SUPPORTED_ATP_PLOT':
                hold['reason']='UNSUPPORTED_OR_UNSPECIFIED_AXIS_UNIT_NOT_A_FOLD_MATRIX_BAR_PLOT'
        rerun['assumptions']=['LINEAR_NONTRUNCATED_AXIS_WITH_THREE_TICKS_AND_VISIBLE_ZERO_LINE',
            'SINGLE_SERIES_COLORED_RECTANGULAR_BARS','PLUS_MINUS_VISIBLE_CONDITION_MATRIX',
            'MATCH_ONLY_IDENTICAL_OBSERVED_CONDITIONS_EXCEPT_TARGET_DOSE']
    if data!=rerun:raise ValueError('ATP_GRAPH_PIXEL_REPLAY_MISMATCH')
    return data


def apply_plot_candidate(candidate,data,packet_path):
    """Only a caption-corroborated dose can inherit the caption's conditions.

    Other image doses remain in the figure packet; they cannot inherit the
    experimental schedule of a differently dosed caption candidate.
    """
    import copy
    if candidate['candidate_id']!=data['parent_candidate_id']:return candidate
    expected=candidate['fields']['dose_uM']['value'];matches=[]
    for pair in data['estimated_pairs']:
        factor={'uM':1,'µM':1,'μM':1,'mM':1000,'nM':.001}.get(pair['dose']['unit_ocr_literal'])
        if factor is not None and expected is not None and pair['dose']['dose']['value']*factor==expected:
            matches.append(pair)
    if len(matches)!=1:return candidate
    pair=matches[0];out=copy.deepcopy(candidate)
    raw=Path(packet_path).read_bytes();plot_sha=hashlib.sha256(raw).hexdigest()
    out.update(candidate_id='ATP-GRAPH-'+hashlib.sha256((candidate['candidate_id']+plot_sha).encode()).hexdigest()[:22],
        endpoint_kind='ATP_CONTENT_GRAPH_ESTIMATE_CANDIDATE',
        treated_value=pair['estimated_treated_value'],control_value=pair['estimated_control_value'],
        measurement_unit=re.search(r'fold\s+of\s+([^)]*)',pair['measurement_unit_ocr_literal'],re.I)[0],
        numeric_origin='GRAPH_PIXEL_ESTIMATE_NOT_AUTHOR_REPORTED_VALUE',
        graph_estimate_evidence={'plot_packet_path':str(Path(packet_path).resolve()),'plot_packet_sha256':plot_sha,
            'image_sha256':data['image_sha256'],'estimated_pair':pair,'image_dimensions':data['image_dimensions'],
            'author_reported_numeric_value':False},
        graph_parent_candidate_id=candidate['candidate_id'])
    for key in ('dose_uM','treated_arm','comparator_arm'):
        out['fields'][key]['image_evidence']={'image_sha256':data['image_sha256'],
            'plot_packet_sha256':plot_sha,'treated_bar_bbox':pair['treated_bar']['pixel_bbox'],
            'control_bar_bbox':pair['control_bar']['pixel_bbox'],'condition_states':pair['treated_bar']['condition_states']}
        out['fields'][key]['state']='AUTHOR_CAPTION_AND_VISIBLE_MATRIX_BOUND_CANDIDATE'
    out['hold_reasons']=sorted(set([h for h in candidate['hold_reasons'] if h not in
        ('AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED','IMAGE_ARM_ASSIGNMENT_UNVERIFIED')]+
        ['GRAPH_ESTIMATE_NOT_AUTHOR_REPORTED_NUMERIC','GRAPH_PIXEL_METHOD_NEEDS_INDEPENDENT_SCIENCE_REVIEW']))
    return out
