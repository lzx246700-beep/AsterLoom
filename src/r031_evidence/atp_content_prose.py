"""Author-signed relative ATP changes for explicitly defined cell variants.

This bounded sentence family binds one named dose/time, ATP endpoint, figure
panel and each signed percent/variant to the stated untreated comparison.
It never turns percent change into raw means or a genotype into a drug control.
"""
import copy
import hashlib
import re


def relative_changes(parent,linked,sha):
    from .atp_content_relations import TIME,NUMBER,UNIT,_name_pattern,_doses,_hours,_citation,_field
    from .atp_content_cross_context import references
    variants={v['label'].casefold():v for v in parent.get('cell_model_variant_candidates',[])}
    f=parent['fields']
    if len(variants)<2 or f['dose_uM']['value'] is None or f['total_compound_exposure_h']['value'] is None:return []
    number=re.search(r'\d+',parent['figure_label'] or parent['figure_id'] or '')
    if not number:return []
    names=parent['compound_author_names'];name=_name_pattern(names);found=[]
    prefix=rf'\b(?:A\s+)?(?P<time>{TIME})\s+treatment\s+with\s+{NUMBER}\s*{UNIT}\s+{name}\s+(?:(?:also|significantly)\s+)*(?P<verb>increased|decreased)\s+(?:the\s+)?ATP\s+(?:levels?|content|concentration)\b'
    for b in linked:
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',b['text']):
            if (number[0],parent['panel']) not in references(sentence):continue
            head=re.search(prefix,sentence,re.I)
            if not head or _hours(head['time'])!=f['total_compound_exposure_h']['value']:continue
            doses=_doses(sentence,names)
            if len(doses)!=1 or doses[0]['uM']!=f['dose_uM']['value']:continue
            comparator=re.search(r'compared\s+(?:with|to)\s+untreated\s+(?:cells|groups)',sentence,re.I)
            if not comparator:continue
            matches=list(re.finditer(r'\bin\s+([A-Za-z0-9_-]+)\s+cells\s*\(\s*([+-]\d+(?:\.\d+)?)%\s*\)',sentence[head.end():comparator.start()],re.I))
            if len(matches)!=len(variants) or {m[1].casefold() for m in matches}!=set(variants):continue
            if any((float(m[2])<=0 if head['verb'].casefold()=='increased' else float(m[2])>=0) for m in matches):continue
            for m in matches:
                variant=variants[m[1].casefold()];label=variant['label'];delta=float(m[2]);out=copy.deepcopy(parent)
                offset=b['text'].index(sentence)
                effect=_citation(b,sha,m.group(),offset+head.end()+m.start())
                statement=_citation(b,sha,sentence,offset)
                comparison=_citation(b,sha,comparator.group(),offset+comparator.start())
                key=parent['candidate_id']+label+str(delta)+statement['source_anchor']+str(statement['start'])
                out.update(candidate_id='ATP-PROSE-'+hashlib.sha256(key.encode()).hexdigest()[:22],
                    numeric_origin='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE',endpoint_kind='ATP_CONTENT_AUTHOR_RELATIVE_CHANGE_CANDIDATE',
                    author_reported_percent=delta,percentage_semantics='SIGNED_PERCENT_CHANGE_VS_MATCHED_UNTREATED_VARIANT',
                    treated_value=None,control_value=None,measurement_unit='% change versus untreated same cell variant',
                    relative_prose_evidence={'parent_candidate_id':parent['candidate_id'],'variant_label':label,
                        'variant_definition':variant['evidence'],'variant_effect':effect,'author_statement':statement,
                        'comparison':comparison,'reported_percent_literal':m[2]+'%','raw_control_value_invented':False})
                out['treatment_stages'].append({'role':'AUTHOR_DEFINED_CELL_VARIANT','label':label,'evidence':[variant['evidence']]})
                ev=[variant['evidence'],effect,statement,comparison]
                out['fields']['treated_arm']=_field(parent['compound_name']+' '+f['dose_original']['value']+' | '+label+' cells',ev,
                    'AUTHOR_PROSE_VARIANT_TREATMENT_CANDIDATE')
                out['fields']['comparator_arm']=_field('untreated | '+label+' cells',ev,
                    'AUTHOR_PROSE_VARIANT_UNTREATED_COMPARISON_REQUIRES_REVIEW')
                out['fields']['effect_direction']=_field('INCREASE' if delta>0 else 'DECREASE',[effect,statement],
                    'AUTHOR_SIGNED_PERCENT_CHANGE_NOT_PERCENT_OF_CONTROL')
                out['hold_reasons']=sorted(set([h for h in out['hold_reasons'] if h not in
                    ('CELL_VARIANT_EXPERIMENT_BINDING_UNRESOLVED','MATCHED_COMPARATOR_UNRESOLVED','AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED','IMAGE_ARM_ASSIGNMENT_UNVERIFIED')]+
                    ['AUTHOR_PROSE_VARIANT_COMPARISON_REQUIRES_INDEPENDENT_REVIEW','RAW_MEANS_NOT_EXTRACTED_FROM_THIS_RELATIVE_STATEMENT']))
                found.append(out)
    # Duplicate or conflicting clauses for one variant are not reconciled by
    # accepting the last value. Keep the unsplit source parent for review.
    labels=[c['relative_prose_evidence']['variant_label'] for c in found]
    return found if len(labels)==len(set(labels)) else []


def reported_fold_changes(parent,paragraphs,sha,caption):
    """Preserve explicit fold ratios without reconstructing absolute means.

    Requires one Results sentence naming both cell models, the compound,
    dose, time, endpoint, and fold; a second sentence in that paragraph must
    map each model to a distinct panel; and the caption must define an
    untreated same-timepoint denominator. Ambiguous or repeated matches hold.
    """
    from .atp_content_relations import TIME,NUMBER,UNIT,_name_pattern,_hours,_citation,_field
    f=parent['fields'];panel=parent.get('panel')
    number=re.search(r'\d+',parent.get('figure_label') or parent.get('figure_id') or '')
    if (not number or panel not in ('A','B') or f['dose_uM']['value'] is None or
            f['assay_method']['value'] is None):return []
    caption_text=caption.get('text','')
    norm=re.search(r'[^.]{0,130}(?:normalized|normalised)[^.]{0,200}untreated[^.]{0,100}',caption_text,re.I)
    if not norm:return []
    names=parent['compound_author_names'];name=_name_pattern(names)
    fold_pattern=rf'\bBoth\s+(?P<cell1>[A-Za-z0-9][A-Za-z0-9 -]*?)\s+and\s+(?P<cell2>[A-Za-z0-9][A-Za-z0-9 -]*?)\s+(?:cells?\s+)?showed\s+a\s+(?P<fold>{NUMBER}|one|two|three|four|five)[ -]fold\s+increase\s+in\s+ATP\s+(?:production|content|levels?)\s+after\s+(?P<time>{TIME})\s+treatment\s+with\s+(?P<dose>{NUMBER})\s*(?P<unit>{UNIT})\s+{name}\b'
    found=[]
    for block in paragraphs:
        if block.get('role')!='RESULTS' or not re.search(name,block.get('text',''),re.I):continue
        sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z])',block['text'])
        matches=[]
        for sentence in sentences:
            head=re.search(fold_pattern,sentence,re.I)
            if head:matches.append((sentence,head))
        if len(matches)!=1:continue
        sentence,head=matches[0]
        unit_factor={'nM':.001,'µM':1,'μM':1,'uM':1,'mM':1000}.get(head['unit'])
        if unit_factor is None or float(head['dose'])*unit_factor!=f['dose_uM']['value']:continue
        # Anchor the panel mapping at the named cell. This avoids an earlier
        # phrase such as "increase in ATP" being mistaken for a cell subject.
        mapping_pattern=rf'\bIn\s+(?P<m1>{re.escape(head["cell1"])})\b(?P<c1>.{{0,180}}?\(\s*Fig(?:ure)?\.?\s*{number[0]}\s*(?P<p1>[A-Z])\s*\))\s+whereas\s+in\s+(?P<m2>{re.escape(head["cell2"])})\b(?P<c2>.{{0,180}}?\(\s*Fig(?:ure)?\.?\s*{number[0]}\s*(?P<p2>[A-Z])\s*\))'
        mappings=list(re.finditer(mapping_pattern,block['text'],re.I))
        if len(mappings)!=1:continue
        mapping=mappings[0]
        if mapping['p1'].upper()==mapping['p2'].upper():continue
        mapped_cell=mapping['m1'] if mapping['p1'].upper()==panel else mapping['m2'] if mapping['p2'].upper()==panel else None
        if mapped_cell is None or mapped_cell.casefold()!=str(f['cell_model']['value'] or '').casefold():continue
        if not re.search(r'\b(?:untreated|control)\b',norm.group(),re.I):continue
        factor={'one':1.0,'two':2.0,'three':3.0,'four':4.0,'five':5.0}.get(head['fold'].casefold())
        if factor is None:factor=float(head['fold'])
        if factor<=0:continue
        statement_start=block['text'].index(sentence)
        map_start=block['text'].index(mapping.group())
        norm_cite=_citation(caption,sha,norm.group(),caption_text.index(norm.group()))
        statement=_citation(block,sha,sentence,statement_start)
        fold_cite=_citation(block,sha,head.group(),statement_start+head.start())
        panel_cite=_citation(block,sha,mapping.group(),map_start)
        key='|'.join((parent['candidate_id'],str(factor),panel,str(statement['start'])))
        out=copy.deepcopy(parent)
        out.update(candidate_id='ATP-FOLD-'+hashlib.sha256(key.encode()).hexdigest()[:22],
            numeric_origin='AUTHOR_PROSE_REPORTED_FOLD_CHANGE',
            author_reported_fold_change=factor,
            fold_change_semantics='AUTHOR_REPORTED_RATIO_TO_UNTREATED_SAME_CELL_TIMEPOINT',
            measurement_unit='fold versus untreated same cell at matched timepoint',
            treated_value=None,control_value=None,
            fold_change_evidence={'parent_candidate_id':parent['candidate_id'],
                'author_statement':statement,'fold_clause':fold_cite,
                'panel_mapping':panel_cite,'normalization_caption':norm_cite,
                'cell_model_literal':mapped_cell,'reported_fold_literal':head['fold']+'-fold',
                'dose_literal':head['dose']+' '+head['unit'],'time_literal':head['time'],
                'raw_treated_or_control_means_invented':False})
        out['fields']['total_compound_exposure_h']=_field(_hours(head['time']),
            [fold_cite,statement],'AUTHOR_PROSE_TIME_BOUND_TO_REPORTED_FOLD_CANDIDATE')
        out['fields']['treated_arm']=_field(parent['compound_name']+' '+head['dose']+' '+head['unit']+' '+head['time'],
            [fold_cite,panel_cite],'AUTHOR_PROSE_REPORTED_TREATMENT_REQUIRES_REVIEW')
        out['fields']['comparator_arm']=_field('untreated '+str(f['cell_model']['value'])+' at matched timepoint',
            [norm_cite,panel_cite],'CAPTION_NORMALIZATION_COMPARATOR_CANDIDATE')
        out['fields']['effect_direction']=_field('INCREASE',[fold_cite,statement],
            'AUTHOR_REPORTED_FOLD_CHANGE_NOT_RAW_MEANS')
        out['hold_reasons']=sorted(set(out['hold_reasons']+
            ['AUTHOR_PROSE_FOLD_CHANGE_REQUIRES_INDEPENDENT_REVIEW',
             'RAW_TREATED_AND_CONTROL_MEANS_NOT_REPORTED_HERE']))
        found.append(out)
    # A second competing prose statement for the same panel is a conflict,
    # not another independent value to append.
    return found if len(found)==1 else []


def reported_percent_of_control(candidates,paragraphs,methods,captions,sha):
    """Bind explicit author percent-of-control values to named single agents.

    This handles only a Results clause that names two author-defined agents,
    assigns two values with ``respectively``, names a cell model, and points to
    a specific ATP panel. It stores a relative percentage, never fabricated
    absolute means. Combo arms and other cell lines remain separate/HOLD.
    """
    from .atp_content_relations import NUMBER,TIME,_doses,_hours,_citation,_field
    from .atp_content_cross_context import references
    out=[]
    atp=[c for c in candidates if c.get('endpoint_kind','').startswith('ATP_CONTENT')
         and c.get('figure_id') and c.get('panel')]
    if not atp:return out
    # Author aliases must be unique in this panel. Do not infer names from
    # unanchored chemical dictionaries or from another figure.
    aliases={}
    for c in atp:
        for alias in c.get('compound_author_names',[]):
            if len(alias.strip())<3:continue
            aliases.setdefault(alias.casefold(),[]).append((alias,c))
    alternatives=sorted((re.escape(items[0][0]) for items in aliases.values()),key=len,reverse=True)
    if len(alternatives)<2:return out
    agent=rf'(?P<agent1>(?:{"|".join(alternatives)}))\s+and\s+(?P<agent2>(?:{"|".join(alternatives)}))'
    pattern=rf'\bFigure\s*(?P<fig>\d+)\s*(?P<panel>[A-Z])\s+shows\s+that\s+{agent}\s+treatment\b(?P<mid>.{{0,100}}?)\b(?P<verb>reduced|increased)\s+ATP\s+(?:levels?|content|concentration)\s+in\s+(?P<cell>[A-Za-z0-9-]+)\s+cells\s+to\s*\(\s*(?P<value1>{NUMBER})\s+and\s+(?P<value2>{NUMBER})\s*\)\s*%\s+of\s+(?:the\s+)?control\s+values?,\s*respectively\b'
    for block in paragraphs:
        if block.get('role')!='RESULTS':continue
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',block.get('text','')):
            match=re.search(pattern,sentence,re.I)
            if not match:continue
            try:ref=(match['fig'],match['panel'].upper())
            except Exception:continue
            if ref not in references(sentence):continue
            panel_candidates=[c for c in atp if re.search(r'\d+',c.get('figure_label') or c['figure_id']).group()==match['fig'] and c.get('panel','').upper()==match['panel'].upper()]
            if len({c['compound_name'].casefold() for c in panel_candidates})<2:continue
            chosen=[]
            for group in ('agent1','agent2'):
                alias=match[group]
                hits=[c for raw,cs in aliases.items() if raw==alias.casefold() for _,c in cs if c in panel_candidates]
                # A candidate can have several aliases; the resolved author
                # token must select exactly one chemical entity in this panel.
                hits=list({c['candidate_id']:c for c in hits}.values())
                if len(hits)!=1:chosen=[];break
                chosen.append((hits[0],alias,match['value1'] if group=='agent1' else match['value2']))
            if len(chosen)!=2 or chosen[0][0]['candidate_id']==chosen[1][0]['candidate_id']:continue
            if re.search(r'co[- ]?treatment|combination',match['mid'],re.I):continue
            cell=match['cell']
            caption=captions.get(chosen[0][0]['figure_id'])
            if not caption or captions.get(chosen[1][0]['figure_id']) is not caption:continue
            caption_text=caption.get('text','')
            panel_cap=re.search(rf'\(\s*{match["panel"]}\s*\)(?P<text>[^.]*\bATP\b[^.]{{0,180}})',caption_text,re.I)
            cap_time=re.search(rf'\bafter\s+(?P<t>{TIME})\s+exposure\b',panel_cap['text'],re.I) if panel_cap else None
            if not cap_time:continue
            cap_cite=_citation(caption,sha,cap_time.group(),caption_text.index(cap_time.group()))
            # Methods must explicitly connect the named agent and its dose to
            # treated cells, and name the vehicle control in the same block.
            for parent,alias,literal in chosen:
                matching=[]
                for method in methods:
                    txt=method.get('text','')
                    if not re.search(r'\b(?:cells\s+were|cells\s+are)\s+treated\s+with\b',txt,re.I):continue
                    if not re.search(r'\bvehicle[- ]treated\s+cells?\s+served\s+as\s+controls?\b',txt,re.I):continue
                    doses=_doses(txt,[alias,*parent.get('compound_author_names',[])])
                    if len(doses)==1 and doses[0].get('agent','').casefold()==alias.casefold():matching.append((method,doses[0]))
                if len(matching)!=1:continue
                method,dose=matching[0]
                val=float(literal)
                if val<=0:continue
                verb=match['verb'].casefold()
                if (verb=='reduced' and val>=100) or (verb=='increased' and val<=100):continue
                start=block['text'].index(sentence)
                statement=_citation(block,sha,sentence,start)
                value_group='value1' if alias.casefold()==match['agent1'].casefold() else 'value2'
                value_cite=_citation(block,sha,match.group(value_group),start+match.start(value_group))
                agent_cite=_citation(block,sha,match[group],start+match.start(group))
                dose_cite=_citation(method,sha,dose['quote'])
                control_match=re.search(r'\bvehicle[- ]treated\s+cells?\s+served\s+as\s+controls?\b',method['text'],re.I)
                control_cite=_citation(method,sha,control_match.group())
                key='|'.join((parent['candidate_id'],match['fig'],match['panel'],cell,str(val),str(statement['start'])))
                child=copy.deepcopy(parent)
                child.update(candidate_id='ATP-REL-'+hashlib.sha256(key.encode()).hexdigest()[:22],
                    numeric_origin='AUTHOR_PROSE_PERCENT_OF_CONTROL',
                    author_reported_percent_of_control=val,
                    percentage_semantics='AUTHOR_REPORTED_PERCENT_OF_MATCHED_CONTROL',
                    treated_value=None,control_value=None,
                    measurement_unit='% of matched vehicle control',
                    percent_of_control_evidence={'parent_candidate_id':parent['candidate_id'],
                        'author_statement':statement,'agent_alias_evidence':agent_cite,
                        'value_evidence':value_cite,'dose_evidence':dose_cite,
                        'control_evidence':control_cite,'figure_caption_time_evidence':cap_cite,
                        'chemical_identity_state':'AUTHOR_EXPLICIT_NAME_AND_ALIAS; STRUCTURE_NOT_VERIFIED',
                        'author_value_literal':literal+'% of control','raw_means_invented':False})
                child['fields']['cell_model']=_field(cell,[_citation(block,sha,cell,start+match.start('cell'))],
                    'AUTHOR_RESULTS_EXPLICIT_CELL_MODEL')
                child['fields']['dose_original']=_field(dose['literal'],[dose_cite],
                    'AUTHOR_METHODS_AGENT_SPECIFIC_DOSE')
                child['fields']['dose_uM']=_field(dose['uM'],[dose_cite],
                    'AUTHOR_METHODS_AGENT_SPECIFIC_DOSE')
                child['fields']['total_compound_exposure_h']=_field(_hours(cap_time['t']),[cap_cite],
                    'AUTHOR_FIGURE_CAPTION_EXPOSURE_TIME')
                assay_norm=[m for m in methods if re.search(r'\bATP\b',m.get('text',''),re.I)
                            and re.search(r'normaliz\w*\s+to\s+protein\s+concentration',m.get('text',''),re.I)]
                if len(assay_norm)==1:
                    norm_match=re.search(r'normaliz\w*\s+to\s+protein\s+concentration',assay_norm[0]['text'],re.I)
                    child['fields']['normalization']=_field('PROTEIN',
                        [_citation(assay_norm[0],sha,norm_match.group())],
                        'AUTHOR_ATP_ASSAY_PROTEIN_NORMALIZATION')
                child['fields']['treated_arm']=_field(parent['compound_name']+' '+dose['literal']+' '+cap_time['t'],
                    [agent_cite,dose_cite,cap_cite],'AUTHOR_REPORTED_SINGLE_AGENT_ARM_REQUIRES_REVIEW')
                child['fields']['comparator_arm']=_field('same-cell vehicle-treated control',
                    [control_cite,cap_cite],'AUTHOR_REPORTED_CONTROL_DENOMINATOR_REQUIRES_REVIEW')
                child['fields']['effect_direction']=_field('DECREASE' if val<100 else 'INCREASE',
                    [value_cite,statement],'DIRECTION_FROM_AUTHOR_PERCENT_OF_CONTROL')
                child['hold_reasons']=sorted(set([h for h in child['hold_reasons'] if h not in
                    ('CELL_BINDING_UNRESOLVED','DOSE_BINDING_UNRESOLVED','MATCHED_COMPARATOR_UNRESOLVED',
                     'NORMALIZATION_NOT_BOUND','TREATMENT_SEQUENCE_UNRESOLVED','AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED',
                     'IMAGE_ARM_ASSIGNMENT_UNVERIFIED')]+
                    ['AUTHOR_REPORTED_PERCENT_OF_CONTROL_NOT_RAW_MEANS',
                     'AUTHOR_PROSE_RELATIVE_VALUE_REQUIRES_INDEPENDENT_REVIEW',
                     'AUTHOR_NAME_TO_CHEMICAL_STRUCTURE_NOT_INDEPENDENTLY_VALIDATED']))
                out.append(child)
    ids=[c['candidate_id'] for c in out]
    return out if len(ids)==len(set(ids)) else []


def reported_by_times_effects(candidates,paragraphs,captions,sha):
    """Preserve ambiguous author ``increased by N times`` ATP statements.

    The wording may mean a final ratio of N or an increment of N times the
    baseline. Preserve its literal and scope only; do not convert it to a
    fold ratio or model target without independent adjudication.
    """
    from .atp_content_relations import NUMBER,TIME,_name_pattern,_hours,_citation,_field
    from .atp_content_cross_context import references
    out=[]
    for parent in candidates:
        if not parent.get('endpoint_kind','').startswith('ATP_CONTENT') or not parent.get('figure_id') or not parent.get('panel'):
            continue
        if parent.get('numeric_origin') in ('AUTHOR_PROSE_REPORTED_FOLD_CHANGE','AUTHOR_PROSE_PERCENT_OF_CONTROL','AUTHOR_PROSE_BY_TIMES_SEMANTICS_HOLD'):
            continue
        f=parent['fields'];fig=re.search(r'\d+',parent.get('figure_label') or parent['figure_id'])
        if not fig or f['dose_uM']['value'] is None or f['assay_method']['value'] is None:continue
        name=_name_pattern(parent.get('compound_author_names',[]))
        pattern=rf'\b(?:the\s+)?(?P<agent>{name})\s+treatment\s+(?P<verb>increased|decreased)\s+(?:the\s+)?ATP\s+(?:content|levels?)\s+by\s+(?P<value>{NUMBER})\s+times?\s+in\s+(?P<time>{TIME})\s+in\s+(?P<cell>[A-Za-z0-9_-]+)\s+cells?\b'
        for block in paragraphs:
            if block.get('role')!='RESULTS':continue
            for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',block.get('text','')):
                match=re.search(pattern,sentence,re.I)
                if not match or (fig[0],parent['panel'].upper()) not in references(sentence):continue
                if f['cell_model']['value'] is not None and match['cell'].casefold()!=str(f['cell_model']['value']).casefold():continue
                cell=match['cell']
                caption=captions.get(parent['figure_id'])
                if not caption:continue
                panel_cap=re.search(rf'\(\s*{parent["panel"]}\s*\)(?P<text>[^.]*\bATP\b[^.]{{0,180}})',caption.get('text',''),re.I)
                cap_time=re.search(rf'\bafter\s+(?P<t>{TIME})\s+exposure\b',panel_cap['text'],re.I) if panel_cap else None
                statement_hours=_hours(match['time'])
                if cap_time and _hours(cap_time['t'])!=statement_hours:continue
                if f['total_compound_exposure_h']['value'] is not None and statement_hours!=f['total_compound_exposure_h']['value']:continue
                if not cap_time and f['total_compound_exposure_h']['value'] is None:continue
                offset=block['text'].index(sentence)
                statement=_citation(block,sha,sentence,offset)
                value_cite=_citation(block,sha,match['value'],offset+match.start('value'))
                agent_cite=_citation(block,sha,match['agent'],offset+match.start('agent'))
                cell_cite=_citation(block,sha,match['cell'],offset+match.start('cell'))
                time_cite=_citation(block,sha,match['time'],offset+match.start('time'))
                cap_cite=(_citation(caption,sha,cap_time.group(),caption['text'].index(cap_time.group()))
                          if cap_time else f['total_compound_exposure_h']['evidence'][0])
                key='|'.join((parent['candidate_id'],match['value'],parent['panel'],str(statement['start'])))
                child=copy.deepcopy(parent)
                child.update(candidate_id='ATP-BYTIMES-'+hashlib.sha256(key.encode()).hexdigest()[:22],
                    numeric_origin='AUTHOR_PROSE_BY_TIMES_SEMANTICS_HOLD',
                    author_reported_by_times_literal=match['value']+' times',
                    by_times_semantics='AMBIGUOUS_RATIO_VERSUS_INCREMENT',
                    measurement_unit='author-reported times phrase; ratio semantics unresolved',
                    treated_value=None,control_value=None,
                    by_times_evidence={'parent_candidate_id':parent['candidate_id'],
                        'author_statement':statement,'value_evidence':value_cite,
                        'agent_alias_evidence':agent_cite,'cell_evidence':cell_cite,
                        'time_evidence':time_cite,'caption_time_evidence':cap_cite,
                        'author_value_literal':match['value']+' times',
                        'ratio_conversion_performed':False})
                child['fields']['cell_model']=_field(cell,[cell_cite],
                    'AUTHOR_RESULTS_EXPLICIT_CELL_MODEL')
                child['fields']['effect_direction']=_field('INCREASE' if match['verb'].casefold()=='increased' else 'DECREASE',[statement,value_cite],
                    'AUTHOR_DIRECTION_ONLY_MAGNITUDE_SEMANTICS_UNRESOLVED')
                child['fields']['total_compound_exposure_h']=_field(statement_hours,
                    [time_cite,cap_cite,statement],'AUTHOR_RESULTS_TIME_CONCORDANT_WITH_PANEL_CONTEXT')
                child['hold_reasons']=sorted(set(child['hold_reasons']+
                    ['AUTHOR_BY_TIMES_RATIO_VERSUS_INCREMENT_AMBIGUITY',
                     'AUTHOR_REPORTED_RELATIVE_MAGNITUDE_REQUIRES_INDEPENDENT_REVIEW']))
                child['hold_reasons']=[h for h in child['hold_reasons'] if h!='IMAGE_ARM_ASSIGNMENT_UNVERIFIED']
                out.append(child)
    ids=[c['candidate_id'] for c in out]
    return out if len(ids)==len(set(ids)) else []


def expand_author_caption_dose_series(candidates,captions,sha):
    """Split an explicit caption dose series into source-only treatment arms.

    This records arms the author explicitly says were tested. It does not
    assign bar values, significance, or a result to a dose; image-value and
    comparator review remain separate gates.
    """
    from .atp_content_relations import NUMBER,UNIT,_name_pattern,_citation,_field
    output=[]
    unit_factor={'nM':.001,'µM':1,'μM':1,'uM':1,'mM':1000}
    for parent in candidates:
        series=parent.get('dose_series_author_evidence') or {}
        if (not parent.get('endpoint_kind','').startswith('ATP_CONTENT') or
            series.get('state')!='MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED' or
            not parent.get('figure_id') or not parent.get('panel')):
            continue
        quote=series.get('author_literal','');evidence=series.get('evidence') or {}
        if evidence.get('source_sha256')!=sha or evidence.get('quote')!=quote:continue
        caption=captions.get(parent['figure_id'])
        caption_anchor=(caption.get('source_anchor') or caption.get('path')) if caption else None
        if not caption or evidence.get('source_anchor')!=caption_anchor:continue
        cell=parent.get('fields',{}).get('cell_model',{}).get('value')
        exposure=parent.get('fields',{}).get('total_compound_exposure_h',{}).get('value')
        if not cell or exposure is None:continue
        # Require the ATP result clause to name this exact panel and model,
        # and the dose-series clause itself to name an author-defined alias.
        panel_evidence=parent.get('panel_label_evidence') or {}
        if panel_evidence.get('source_sha256')!=sha or parent['panel'].casefold() not in panel_evidence.get('quote','').casefold():continue
        alias_pattern=_name_pattern(parent.get('compound_author_names',[]))
        if not re.search(rf'\bof\s+{alias_pattern}\b',quote,re.I):continue
        doses=list(re.finditer(rf'(?<![\w.])(?P<value>{NUMBER})\s*(?P<unit>{UNIT})',quote))
        if len(doses)<2:continue
        normalized=[]
        for match in doses:
            unit=match['unit'];factor=unit_factor.get(unit)
            if factor is None:normalized=[];break
            normalized.append((match,float(match['value'])*factor,unit))
        if len(normalized)<2 or len({round(x[1],12) for x in normalized})!=len(normalized):continue
        normalization_match=re.search(r'normalized\s+on\s+the\s+quantified\s+(?P<method>[^.;,]+)',caption.get('text',''),re.I)
        control_match=re.search(r'percentage\s+of\s+the\s+control\s*\(\s*untreated\s+control\s+condition\s*\)',caption.get('text',''),re.I)
        caption_block={**caption,'path':caption_anchor}
        normalization_cite=(_citation(caption_block,sha,normalization_match.group(),normalization_match.start())
                            if normalization_match else None)
        control_cite=(_citation(caption_block,sha,control_match.group(),control_match.start())
                      if control_match else None)
        for index,(match,dose_uM,unit) in enumerate(normalized):
            literal=match.group()
            dose_cite={**evidence,'quote':literal,'start':evidence['start']+match.start(),
                       'end':evidence['start']+match.end()}
            child=copy.deepcopy(parent)
            child['candidate_id']='ATP-ARM-'+hashlib.sha256((parent['candidate_id']+'|'+literal).encode()).hexdigest()[:22]
            child['numeric_origin']='NOT_EXTRACTED'
            child['treated_value']=None;child['control_value']=None
            child['measurement_unit']='author-normalized percentage of control; numeric result not extracted'
            child['caption_dose_arm_evidence']={'parent_candidate_id':parent['candidate_id'],
                'dose_evidence':dose_cite,'series_evidence':evidence,'panel_evidence':panel_evidence,
                'author_panel':parent['panel'],'cell_model':cell,'dose_original':literal,
                'dose_uM':dose_uM,'exposure_h':exposure,
                'result_value_assigned':False,'control_value_assigned':False}
            child['dose_series_author_evidence']={**series,'state':'EXPLICIT_DOSE_ARM_CONTEXT_NO_RESULT_VALUE',
                'selected_dose_literal':literal,'dose_index':index,'dose_count':len(normalized)}
            child['fields']['dose_original']=_field(literal,[dose_cite],'AUTHOR_EXPLICIT_FIGURE_CAPTION_DOSE_ARM')
            child['fields']['dose_uM']=_field(dose_uM,[dose_cite],'AUTHOR_EXPLICIT_FIGURE_CAPTION_DOSE_ARM')
            child['fields']['treated_arm']=_field(f"{parent['compound_name']} {literal}",[dose_cite,panel_evidence],
                'AUTHOR_EXPLICIT_TREATMENT_ARM_NO_MEASUREMENT_BINDING')
            if normalization_cite:
                child['fields']['normalization']=_field('CELLTRACKER_SIGNAL',[normalization_cite],
                    'AUTHOR_FIGURE_CAPTION_NORMALIZATION_METHOD')
            if control_cite:
                child['fields']['comparator_arm']=_field('untreated control',[control_cite,panel_evidence],
                    'AUTHOR_FIGURE_CAPTION_UNTREATED_CONTROL_DENOMINATOR')
                child['measurement_unit']='percentage of untreated control; numeric result not extracted'
            child['hold_reasons']=sorted(set([h for h in child['hold_reasons'] if h not in
                ('DOSE_BINDING_UNRESOLVED','CONFLICTING_COMPOUND_DOSES','MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED')]+
                ['AUTHOR_DOSE_ARM_CONTEXT_EXPANDED_RESULT_VALUE_UNASSIGNED']))
            stage=next((s for s in child['treatment_stages'] if s.get('stage')=='AUTHOR_DRUG_EXPOSURE'),None)
            if stage is not None:
                stage['dose_original']=literal;stage['dose_uM']=dose_uM;stage['dose_evidence']=[dose_cite]
            output.append(child)
    ids=[c['candidate_id'] for c in output]
    return output if len(ids)==len(set(ids)) else []


def unassigned_author_percent_series(candidates, paragraphs, sha):
    """Keep explicit author percent lists when experiment-arm mapping is open.

    These records are source-fact inventory only: values remain grouped and
    are never assigned to treatment arms, controls, doses, or timepoints.
    """
    from .atp_content_relations import CONTENT, RATE, NUMBER, UNIT, _citation, _name_pattern
    output=[]
    for block in paragraphs:
        if block.get('role') != 'RESULTS':
            continue
        text=block.get('text','')
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',text):
            if not CONTENT.search(sentence) or RATE.search(sentence):
                continue
            # Preserve a numeric ordered series stated as percentages of a
            # control even when the author does not say ``respectively``.
            # The order is evidence, not a mapping to compounds/arms; each
            # value remains in one unassigned source-fact packet.
            level_clause=re.search(
                r'(?P<group>[^;]{1,180}?)\s+(?:improved|increased|decreased|reduced|restored)\s+'
                r'(?:intracellular\s+)?ATP\s+(?:levels?|content|concentration)\s+to\s+'
                r'(?P<values>\d+(?:\.\d+)?(?:\s*±\s*\d+(?:\.\d+)?)?'
                r'(?:\s*(?:,|and|&)\s*\d+(?:\.\d+)?(?:\s*±\s*\d+(?:\.\d+)?)?)+)\s*%\s+'
                r'of\s+(?:the\s+)?control\s+levels?\b',sentence,re.I)
            if level_clause:
                refs=list(re.finditer(r'\bFig(?:ure)?s?\.?\s*(\d+)\s*([A-Za-z])\b',sentence,re.I))
                if refs:
                    for ref in refs:
                        fig_no,panel=ref[1],ref[2].upper()
                        matches=[c for c in candidates
                            if c.get('panel','').upper()==panel
                            and c.get('endpoint_kind','').startswith('ATP_CONTENT')
                            and c.get('figure_id')
                            and re.search(r'\d+',str(c.get('figure_label') or c['figure_id']))
                            and re.search(r'\d+',str(c.get('figure_label') or c['figure_id']))[0]==fig_no]
                        if len({c['candidate_id'] for c in matches})!=1:
                            continue
                        raw_values=re.findall(r'\d+(?:\.\d+)?',level_clause['values'])
                        values=[float(v) for v in raw_values[::2] if v]
                        # The stride selects the mean before each optional
                        # uncertainty term. A separate count guard fails
                        # closed if the literal cannot be parsed consistently.
                        mean_tokens=re.findall(r'\d+(?:\.\d+)?',level_clause['values'])
                        sd_tokens=re.findall(r'±\s*\d+(?:\.\d+)?',level_clause['values'])
                        if len(mean_tokens)!=len(values)+len(sd_tokens) or len(values)<2:
                            continue
                        parent=matches[0]
                        offset=text.index(sentence)
                        clause_cite=_citation(block,sha,level_clause.group(),offset+level_clause.start())
                        value_cite=_citation(block,sha,level_clause['values'],
                                             offset+level_clause.start('values'))
                        figure_cite=_citation(block,sha,ref.group(),offset+ref.start())
                        key='|'.join((parent['candidate_id'],str(value_cite['start']),str(values)))
                        output.append({
                            'series_id':'ATP-PERCENT-SERIES-'+hashlib.sha256(key.encode()).hexdigest()[:20],
                            'parent_candidate_id':parent['candidate_id'],
                            'figure_id':parent.get('figure_id'),'figure_label':parent.get('figure_label'),
                            'panel':panel,'cell_model':parent.get('fields',{}).get('cell_model',{}).get('value'),
                            'endpoint_kind':'ATP_CONTENT_AUTHOR_REPORTED_PERCENT_SERIES_HOLD',
                            'intervention_group_literal':level_clause['group'].strip(),
                            'ordered_effects_percent_of_control':values,
                            'ordered_uncertainty_literals':sd_tokens,
                            'effect_semantics':'AUTHOR_REPORTED_PERCENT_OF_CONTROL_ORDERED_SERIES_UNASSIGNED',
                            'arm_assignment_state':'UNRESOLVED','comparator_assignment_state':'CONTROL_DENOMINATOR_ONLY',
                            'dose_state':'UNKNOWN','exposure_time_state':'UNKNOWN',
                            'numeric_origin':'AUTHOR_PROSE_REPORTED_UNASSIGNED_SERIES',
                            'evidence':clause_cite,'value_evidence':value_cite,
                            'figure_reference_evidence':figure_cite,
                            'state':'Review-B_SOURCE_FACT_NOT_STANDARDIZED_OBSERVATION',
                            'raw_group_means_invented':False,'gold':False,'training_approved':False,
                        })
            clause=re.search(
                r'(?P<group>[^;]{1,120}?)\s+(?:improved|increased|decreased|reduced)\s+'
                r'(?:intracellular\s+)?ATP(?:\s+(?:levels?|content|concentration))?\s+by\s+'
                r'(?P<tail>[^;]{1,600}?\brespectively\b)',sentence,re.I)
            if not clause:
                continue
            values=[float(v) for v in re.findall(r'(\d+(?:\.\d+)?)\s*%',clause['tail'])]
            if len(values)<2 or clause['tail'].count('%')!=len(values):
                continue
            refs=list(re.finditer(r'\bFig(?:ure)?s?\.?\s*(?:(\d+)\s*)?([A-Za-z])\b',sentence,re.I))
            if not refs:
                continue
            for ref in refs:
                sentence_offset=text.index(sentence)
                number=ref[1];panel=ref[2].upper()
                matches=[c for c in candidates
                         if c.get('panel')==panel
                         and c.get('endpoint_kind','').startswith('ATP_CONTENT')
                         and (not number or
                              (re.search(r'\d+',str(c.get('figure_label',''))) is not None and
                               re.search(r'\d+',str(c.get('figure_label','')))[0]==number))]
                # Some JATS captions name two cell models in one figure and
                # the Results text supplies the A/B-to-cell mapping. The
                # figure inventory then has one figure-level candidate with
                # panel=None. Permit that fallback only when the source has
                # one ATP candidate for the cited figure and the current
                # Results clause explicitly names the cell model before the
                # panel reference.
                explicit_cell=None
                explicit_cell_cite=None
                if not matches and number:
                    figure_level=[c for c in candidates
                        if c.get('panel') is None
                        and c.get('endpoint_kind','').startswith('ATP_CONTENT')
                        and re.search(r'\d+',str(c.get('figure_label') or c.get('figure_id') or ''))
                        and re.search(r'\d+',str(c.get('figure_label') or c.get('figure_id') or ''))[0]==number]
                    cell_scope=sentence[clause.start():ref.start()]
                    cell_matches=list(re.finditer(r'\bin\s+([A-Za-z][A-Za-z0-9_-]+)(?=\s*(?:\(|\bcells?\b|\bmyotubes?\b|\bhepatocytes?\b))',cell_scope,re.I))
                    if len(figure_level)==1 and cell_matches:
                        explicit_cell=cell_matches[-1][1]
                        local_start=clause.start()+cell_matches[-1].start(1)
                        explicit_cell_cite=_citation(block,sha,explicit_cell,sentence_offset+local_start)
                        matches=figure_level
                # A letter-only result citation is usable only if it resolves
                # to exactly one ATP candidate panel in this paper.
                if len(matches)!=1:
                    continue
                parent=matches[0]
                prior_refs=[r for r in refs if clause.start()<=r.start()<ref.start()]
                segment_start=prior_refs[-1].end() if prior_refs else clause.start()
                panel_segment=sentence[segment_start:ref.start()]
                panel_values=[float(v) for v in re.findall(r'(\d+(?:\.\d+)?)\s*%',panel_segment)]
                series_values=panel_values if len(panel_values)>=2 else values
                offset=sentence_offset
                clause_start=offset+clause.start()
                evidence=_citation(block,sha,clause.group(),clause_start)
                figure_reference=_citation(block,sha,ref.group(),offset+ref.start())
                panel_effect_evidence=_citation(block,sha,panel_segment.strip(),
                    offset+segment_start+(len(panel_segment)-len(panel_segment.lstrip())))
                ordered_context={}
                caption=parent.get('figure_caption_evidence') or {}
                caption_text=caption.get('quote','')
                precondition=re.search(
                    r'\bpreincubation with\s+(?P<levels>\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*(?:\s*,?\s*(?:and|or)\s*\d+(?:\.\d+)?)?)\s*'
                    r'(?P<unit>mM|μM|µM)\s+(?P<substrate>[A-Za-z][A-Za-z -]+?)\s+for\s+'
                    r'(?P<hours>\d+(?:\.\d+)?)\s*(?:hours?|h)\b',caption_text,re.I)
                if precondition and 'respectively' in sentence[ref.end():].casefold():
                    levels=[float(x) for x in re.findall(r'\d+(?:\.\d+)?',precondition['levels'])]
                    if len(levels)==len(series_values):
                        ordered_context={
                            'ordered_precondition_levels':levels,
                            'precondition_unit':precondition['unit'],
                            'precondition_substrate':precondition['substrate'].strip(),
                            'precondition_h':float(precondition['hours']),
                            'condition_mapping_state':'AUTHOR_RESPECTIVELY_SEQUENCE_MATCHED_TO_SAME_FIGURE_CAPTION',
                            'condition_mapping_evidence':caption,
                        }
                treatment_condition=re.search(
                    r'treated with [^.]{0,100}?\bat\s+(?P<glucose>\d+(?:\.\d+)?)\s*'
                    r'(?P<unit>mM|μM|µM)\s+(?P<substrate>glucose)\s+for\s+'
                    r'(?P<hours>\d+(?:\.\d+)?)\s*(?:hours?|h)\b',caption_text,re.I)
                names=parent.get('compound_author_names') or [parent.get('compound_name','')]
                name_pattern=_name_pattern([n for n in names if n])
                dose_matches=list(re.finditer(
                    rf'(?P<number>{NUMBER})\s*[-‐‑–—]?\s*(?P<unit>{UNIT})\s+(?P<agent>{name_pattern})',
                    text,re.I))
                dose_match=dose_matches[0] if len(dose_matches)==1 else None
                dose_data=None
                if dose_match:
                    dose_data={
                        'value':float(dose_match['number']),'unit':dose_match['unit'],
                        'literal':dose_match.group(),
                        'evidence':_citation(block,sha,dose_match.group(),dose_match.start()),
                    }
                condition_mapped=bool(ordered_context)
                comparator_supported=bool(re.search(
                    r'versus corresponding control\s*\(without [^)]+\)',caption_text,re.I))
                treatment_duration=float(treatment_condition['hours']) if treatment_condition else None
                treatment_name_in_caption=bool(treatment_condition and
                    any(re.search(_name_pattern([n]),caption_text,re.I) for n in names if n))
                key='|'.join((parent['candidate_id'],str(evidence['start']),panel,str(series_values)))
                output.append({
                    'series_id':'ATP-SERIES-'+hashlib.sha256(key.encode()).hexdigest()[:20],
                    'parent_candidate_id':parent['candidate_id'],
                    'figure_id':parent.get('figure_id'),'figure_label':parent.get('figure_label'),
                    'panel':panel,
                    'cell_model':explicit_cell or parent.get('fields',{}).get('cell_model',{}).get('value'),
                    'endpoint_kind':'ATP_CONTENT_AUTHOR_REPORTED_EFFECT_SERIES',
                    'intervention_group_literal':clause['group'].strip(),
                    'ordered_effects_percent':series_values,
                    'effect_semantics':('AUTHOR_REPORTED_PERCENT_CHANGE_BY_ORDERED_PRECONDITION'
                        if condition_mapped else 'AUTHOR_REPORTED_PERCENT_CHANGE_ORDERED_SERIES_UNASSIGNED'),
                    'arm_assignment_state':('AUTHOR_COMPOUND_SPECIFIC_FIGURE_PANEL_EFFECT'
                        if dose_data else 'UNRESOLVED'),
                    'comparator_assignment_state':('AUTHOR_CORRESPONDING_NO_DRUG_CONTROL'
                        if comparator_supported and condition_mapped else 'UNRESOLVED'),
                    'dose_state':('AUTHOR_SINGLE_EXPLICIT_COMPOUND_DOSE_IN_RESULTS_PARAGRAPH'
                        if dose_data else 'UNKNOWN'),
                    **({'compound_dose':dose_data,'treatment_arm_literal':dose_data['literal']} if dose_data else {}),
                    'exposure_time_state':('AUTHOR_FIGURE_CAPTION_DISTINCT_TREATMENT_PHASE'
                        if treatment_duration is not None and treatment_name_in_caption else 'UNKNOWN'),
                    **({'compound_treatment_h':treatment_duration,
                        'treatment_duration_evidence':caption} if treatment_duration is not None and treatment_name_in_caption else {}),
                    **({'comparator_evidence':caption} if comparator_supported and condition_mapped else {}),
                    **ordered_context,
                    **({'treatment_medium_context':{
                        'value':f"{treatment_condition['glucose']} {treatment_condition['unit']} {treatment_condition['substrate']}",
                        'treatment_h':float(treatment_condition['hours']),
                        'evidence':caption,
                        'state':'AUTHOR_FIGURE_CAPTION_TREATMENT_PHASE_CONTEXT'
                    }} if treatment_condition else {}),
                    'numeric_origin':'AUTHOR_PROSE_REPORTED_UNASSIGNED_SERIES',
                    'evidence':evidence,'panel_effect_evidence':panel_effect_evidence,
                    'figure_reference_evidence':figure_reference,
                    **({'cell_model_evidence':explicit_cell_cite,
                        'cell_model_binding_state':'RESULTS_EXPLICIT_FIGURE_PANEL_CELL_MODEL'} if explicit_cell_cite else {}),
                    'state':'Review-B_SOURCE_FACT_NOT_STANDARDIZED_OBSERVATION',
                    'gold':False,'training_approved':False,
                })
    unique={row['series_id']:row for row in output}
    return [unique[key] for key in sorted(unique)]


def explicit_panel_percent_decreases(candidates, sha):
    """Create source-only relative-effect candidates for explicit named decreases.

    Only figure-panel-linked Results clauses with percentage magnitudes
    assigned to named compounds are accepted. Multiple values require the
    author's explicit respectively mapping. Denominator, comparator, exposure
    time, and raw group means remain unresolved.
    """
    from .atp_content_relations import _field
    from .atp_content_cross_context import references
    output = []
    seen = set()
    pattern = re.compile(
        r"(?P<values>\(\s*\d+(?:\.\d+)?(?:\s+(?:and|,)\s*\d+(?:\.\d+)?)*\s*%\s*\))"
        r"\s+decreases?\s+by\s+(?P<agents>[^;()]+?)"
        r"(?:,\s*respectively)?(?=\s*\(|\s*$)", re.I
    )
    for parent in candidates:
        if (parent.get('endpoint_kind') not in (
                'ATP_CONTENT_CANDIDATE',
                'ATP_CONTENT_UNCALIBRATED_CELLTITER_GLO_REVIEW',
                'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW')
                or not parent.get('panel')):
            continue
        fig = re.search(r'\d+', parent.get('figure_label') or parent.get('figure_id') or '')
        if not fig:
            continue
        names = [n for n in parent.get('compound_author_names', []) if n]
        aliases = {n.casefold(): n for n in names if n}
        if not aliases and parent.get('compound_name'):
            aliases[parent['compound_name'].casefold()] = parent['compound_name']
        for block in parent.get('linked_author_prose', []):
            text = block.get('quote', '')
            if (not text or block.get('source_sha256') != sha or
                    block.get('source_anchor') is None or block.get('start') is None):
                continue
            for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])', text):
                if (fig.group(), str(parent['panel']).upper()) not in references(sentence):
                    continue
                for match in pattern.finditer(sentence):
                    values = [float(v) for v in re.findall(r'\d+(?:\.\d+)?', match['values'])]
                    agents = [x.strip() for x in re.split(
                        r'\s*,\s*|\s+and\s+', match['agents'], flags=re.I) if x.strip()]
                    respectively = bool(re.search(r',\s*respectively\b', match.group(), re.I))
                    if len(values) != len(agents) or not values:
                        continue
                    if len(values) > 1 and not respectively:
                        continue
                    for index, agent in enumerate(agents):
                        canonical = aliases.get(agent.casefold())
                        if (not canonical or
                                canonical.casefold() != parent.get('compound_name', '').casefold()):
                            continue
                        sentence_start = text.index(sentence)
                        clause_start = sentence_start + match.start()
                        value_matches = list(re.finditer(r'\d+(?:\.\d+)?', match['values']))
                        token = value_matches[index]

                        def cite(quote, local_start):
                            start = int(block['start']) + local_start
                            return {
                                'source_sha256': sha,
                                'source_anchor': block['source_anchor'],
                                'quote': quote,
                                'start': start,
                                'end': start + len(quote),
                                'offset_basis': block.get(
                                    'offset_basis',
                                    'source_io.plain normalized Unicode characters'),
                            }

                        statement = cite(sentence, sentence_start)
                        clause = cite(match.group(), clause_start)
                        value = cite(token.group(),
                                     sentence_start + match.start('values') + token.start())
                        agent_match = re.search(re.escape(agent), match['agents'], re.I)
                        agent_cite = cite(agent, sentence_start + match.start('agents') +
                                          agent_match.start())
                        key = '|'.join((parent['candidate_id'], block['source_anchor'],
                                       str(statement['start']), str(values[index]), agent.casefold()))
                        if key in seen:
                            continue
                        seen.add(key)
                        child = copy.deepcopy(parent)
                        child.update(
                            candidate_id='ATP-REL-HOLD-' + hashlib.sha256(key.encode()).hexdigest()[:20],
                            endpoint_kind='ATP_CONTENT_AUTHOR_REPORTED_RELATIVE_EFFECT_REVIEW',
                            numeric_origin='AUTHOR_PROSE_REPORTED_PERCENT_CHANGE',
                            author_reported_percent=-values[index],
                            percentage_semantics='AUTHOR_REPORTED_PERCENT_DECREASE_DENOMINATOR_UNRESOLVED',
                            measurement_unit='author-reported percent decrease; denominator unresolved',
                            author_effect_literal=match.group(),
                            treated_value=None,
                            control_value=None,
                            descriptive_log2fc=None,
                            training_effect_log2fc=None,
                            author_relative_effect_evidence={
                                'schema': 'asterloom.atp-content.panel-relative-decrease/0.1',
                                'parent_candidate_id': parent['candidate_id'],
                                'author_statement': statement,
                                'effect_clause': clause,
                                'value_evidence': value,
                                'intervention_name_evidence': agent_cite,
                                'figure_panel': f"Figure {fig.group()}{str(parent['panel']).upper()}",
                                'ordered_values_percent': values,
                                'ordered_intervention_names': agents,
                                'author_percent_magnitude': values[index],
                                'author_intervention_name': canonical,
                                'respectively_mapping_explicit': len(values) == 1 or respectively,
                                'denominator_state': 'UNRESOLVED',
                                'raw_means_invented': False,
                            },
                        )
                        child['fields'] = copy.deepcopy(parent['fields'])
                        child['fields']['effect_direction'] = _field(
                            'DECREASE', [value, clause, agent_cite],
                            'AUTHOR_EXPLICIT_PERCENT_DECREASE_SOURCE_REVIEW')
                        child['hold_reasons'] = sorted(set(child.get('hold_reasons', []) + [
                            'AUTHOR_PERCENT_DECREASE_DENOMINATOR_UNRESOLVED',
                            'MATCHED_COMPARATOR_UNRESOLVED',
                            'RAW_TREATED_AND_CONTROL_MEANS_NOT_REPORTED',
                            'INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED',
                        ]))
                        output.append(child)
    return output
