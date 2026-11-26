"""Conservative direct-ATP experiment recall into the existing relation packet.

This fallback preserves experiments missed by the detailed binder. It never
pairs arms, estimates Y, inherits a neighbouring panel's dose/time, or approves
an endpoint based on the word 'production' alone. All results are Review-B.
"""
from __future__ import annotations
import hashlib
import json
import re

_DIRECT = re.compile(r'ATP\s+(?:content|levels?|concentration|quantification)|intracellular\s+ATP',re.I)
_BAD = re.compile(r'ATP[- ]?(?:production[- ]?)?coupled|ATP[- ]linked|ATPase|ATP.{0,20}(?:production|generation)\s+rate|CellTiter[- ]Glo|cell\s+viability',re.I)
_MODEL = re.compile(r'\b(?:TM3|RAW\s*264\.?7|BV-?2|HepG2|C2C12|HT22|SH-SY5Y|TM4|ELT3|macrophages|neurons|astrocytes)\b',re.I)


def direct_methods(methods):
    """Return author ATP-method clauses, not adjacent MTT/viability prose.

    A JATS paragraph may describe several assays in sequence. Segment an
    explicitly ATP-headed method sentence before evaluating its assay; never
    discard it because a neighboring sentence describes cell viability.
    """
    found=[]
    for b in methods:
        t=b['text']
        direct_patterns=[re.compile(r'(?P<assay>(?:the\s+)?(?:(?:cellular|intracellular|total)\s+)?ATP\s+'
            r'(?:levels?|content|concentration)'
            r'(?:\s+(?:in|of)\s+(?:(?:the\s+)?(?:cells?|cell lysates?)))?\s+'
            r'(?:were|was|are|is)\s+'
            r'(?:tested|measured|determined|quantified)\s+(?:using|with|by)\s+'
            r'.{1,320}?(?=(?<!no)\.\s+(?=[A-Z])|$))',re.I),
            re.compile(r'(?P<assay>(?:assessed|measured|determined|quantified)\s+for\s+'
            r'intracellular\s+(?:total\s+)?ATP\s+(?:content|levels?|production)\s+using\s+'
            r'.{1,320}?(?=(?<!no)\.\s+(?=[A-Z])|$))',re.I),
            re.compile(r'(?P<assay>(?:measured|determined|quantified)\s+intracellular\s+'
            r'ATP\s+(?:content|levels?|concentration)\s+using\s+'
            r'.{1,320}?(?=(?<!no)\.\s+(?=[A-Z])|$))',re.I)]
        for direct in direct_patterns:
          for m in direct.finditer(t):
            clause=m['assay'].strip()
            if _BAD.search(clause) or re.search(r'\b(?:OCR|ECAR|Seahorse|CellTiter[- ]Glo)\b',clause,re.I):continue
            if not re.search(r'ATP|luminescen|lucifer|HPLC|mass spectrom|colorimetric|kit|assay',clause,re.I):continue
            if re.search(r'\bproduction\b',clause,re.I) and not re.search(r'luciferase|luciferin|ATP\s+assay\s+kit',clause,re.I):continue
            start=m.start('assay')+len(m['assay'])-len(m['assay'].lstrip())
            found.append({**b,'assay_quote':clause,'assay_start':start,
                          'assay_scope_state':'AUTHOR_ATP_METHOD_SENTENCE_SEGMENTED_FROM_ADJACENT_ASSAYS'})
    return found


def caption_normalization(text):
    """Read an explicit signal basis and control denominator from one sentence."""
    basis=re.search(r'\bnormalized\s+(?:on|to)\s+(?:the\s+)?'
        r'(?P<basis>quantified\s+[A-Za-z0-9-]+\s+signal)',text,re.I)
    denominator=re.search(r'\b(?:shown|expressed|presented)\s+as\s+(?:a\s+|the\s+)?'
        r'percentage\s+of\s+(?:the\s+)?'
        r'(?P<denominator>control(?:\s*\([^)]{1,100}\))?)',text,re.I)
    if not basis or not denominator or basis.start()>=denominator.start():return None
    # A full stop may mark a neighboring panel or experiment.
    if re.search(r'\.(?=\s|$)',text[basis.end():denominator.start()]):return None
    start=basis.start();end=denominator.end()
    return {'value':f"{basis['basis']}; percentage of {denominator['denominator']}",
        'quote':text[start:end],'start':start,'end':end}


def panel_scopes(cap):
    """Return only an explicit ATP label, or an unresolved figure inventory.

    Preposed 'ATP levels in (a) young and (b) aged neurons' owns a/b, not
    subsequent MMP panels. Postfix '(B)' owns ATP, not lactate's normalization.
    """
    text=cap['text'];out=[]
    # Reuse the primary caption scope parser for ordered grouped labels so the
    # recall fallback cannot reintroduce a neighboring-panel assignment that
    # the main relation path has already disambiguated.
    from .atp_content_relations import _panels
    grouped=[(panel,scope,meta) for panel,scope,meta in _panels(cap)
             if meta.get('panel_scope_state')=='AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS'
             and re.search(r'\bATP\s+(?:content|levels?|concentration|stores?)\b',scope,re.I)]
    if grouped:
        for panel,scope,meta in grouped:
            model=meta.get('panel_cell_model')
            out.append(dict(panel=panel,text=scope,model=model,
                state='AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS',
                label=meta.get('panel_label_literal'),
                label_start=meta.get('panel_label_start'),
                scope_start=meta.get('scope_start'),
                endpoint_literal=meta.get('panel_endpoint_literal'),
                endpoint_start=meta.get('panel_endpoint_start')))
        # The caller creates field-level citations from the source SHA. Retain
        # the exact model literal here; no cross-panel carry.
        return out
    # Parallel multi-panel captions can describe time-series panels first and
    # dose-series panels second, while a neighboring viability series follows.
    # Keep only the explicitly named ATP panels; later result references may
    # bind a cell model, but no single dose is chosen from a concentration range.
    multi=re.search(r'ATP\s+production\s+of\s+(?P<cell1>neurons|astrocytes)\s+and\s+'
        r'(?P<cell2>neurons|astrocytes)\s+after\s+treatment\s+with\s+'
        r'(?P<dose>\d+(?:\.\d+)?\s*[µμu]M)\s+(?P<agent>[A-Za-z][\w-]*)\s+'
        r'at\s+different\s+time\s+points\s+(?P<a>[A-Z])\.?\s*,?\s*(?P<b>[A-Z])\.?\s+'
        r'and\s+with\s+increasing\s+concentrations\s*\((?P<range>[^)]{1,50})\)\s+'
        r'for\s+(?P<hc>\d+(?:\.\d+)?)\s*h\s+(?P<c>[A-Z])\.?\s+or\s+'
        r'(?P<hd>\d+(?:\.\d+)?)\s*h\s+(?P<d>[A-Z])\.?',text,re.I)
    if multi:
        common=multi.group()
        for panel,state,protocol,hours,range_literal in [
            (multi['a'],'AUTHOR_MULTI_PANEL_TIME_SERIES',f"{multi['dose']} {multi['agent']} at different time points",None,None),
            (multi['b'],'AUTHOR_MULTI_PANEL_TIME_SERIES',f"{multi['dose']} {multi['agent']} at different time points",None,None),
            (multi['c'],'AUTHOR_MULTI_PANEL_DOSE_SERIES',f"{multi['range']} for {multi['hc']} h",float(multi['hc']),multi['range']),
            (multi['d'],'AUTHOR_MULTI_PANEL_DOSE_SERIES',f"{multi['range']} for {multi['hd']} h",float(multi['hd']),multi['range'])]:
            out.append(dict(panel=panel,text=common,model=None,state=state,label=panel+'.',
                protocol=protocol,hours=hours,dose_range=range_literal,
                named_agent=multi['agent'],dose_literal=multi['dose']))
        return out
    # Some publishers use comma labels ("h, Intracellular ATP levels ...")
    # in a mixed figure instead of parenthesized labels. Bind only the ATP
    # endpoint clause and stop at the next comma-panel declaration. This keeps
    # neighbouring viability panels out of the ATP scope.
    comma_marks=list(re.finditer(r'(?<![\w])(?P<label>[a-z]),\s+(?=(?:(?:intracellular|cellular|total)\s+)?ATP\s+(?:content|levels?|concentration)\b)',text,re.I))
    if comma_marks:
        for i,mark in enumerate(comma_marks):
            later=re.search(r'(?<![\w])(?:[a-z](?:\s*,\s*[a-z]){0,2}),\s+(?=[A-Z])',text[mark.end():])
            end=mark.end()+later.start() if later else len(text)
            scope=text[mark.end():end].strip()
            models=list(_MODEL.finditer(scope));values={m.group() for m in models}
            out.append(dict(panel=mark['label'].upper(),text=scope,
                model=next(iter(values)) if len(values)==1 else None,
                state='AUTHOR_COMMA_LABEL_ATP_PANEL',label=mark['label']+',',
                label_start=mark.start(),scope_start=mark.end()+len(text[mark.end():end])-len(text[mark.end():end].lstrip())))
        return out
    # Explicit author 'respectively' alignment is source grammar, not nearest
    # number matching. A shared inhibitor-series remains multiple unpaired arms.
    pair=re.search(r'ATP\s+production\s+of\s+(neurons|astrocytes)\s+([A-Z])\.\s+and\s+(neurons|astrocytes)\s+([A-Z])\.\s+after\s+treatment\s+with\s+(.+?)\s+for\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s*h,\s*respectively[^.]*\.',text,re.I)
    if pair:
        for model,panel,hours in [(pair[1],pair[2],pair[6]),(pair[3],pair[4],pair[7])]:
            out.append(dict(panel=panel.upper(),text=pair.group(),model=model,state='AUTHOR_RESPECTIVE_MODEL_TIME_PANELS',label=panel+'.',hours=float(hours),protocol=pair.group()))
        # A subsequent dot-labelled quantification is separate, never a third
        # copy of the preceding cell/time mapping.
        tail=text[pair.end():]
        third=re.search(r'([A-Z])\.\s+(ATP\s+quantification\s+of\s+(neurons|astrocytes)\s+after\s+treatment\s+with\s+.+?\s+for\s+(\d+(?:\.\d+)?)\s*h[^.]*\.)',tail,re.I)
        if third:out.append(dict(panel=third[1].upper(),text=third[2],model=third[3],state='AUTHOR_DOT_LABEL_DIRECT_ATP_PANEL',label=third[1]+'.',hours=float(third[4]),protocol=third[2]))
        return out
    age=re.search(r'(?:relative\s+)?ATP\s+levels?\s+in\s*\(([a-z])\)\s*(young|aged|old)\s+and\s*\(([a-z])\)\s*(young|aged|old)\s+(neurons|astrocytes)',text,re.I)
    if age:
        for panel,ageword in [(age[1],age[2]),(age[3],age[4])]:
            out.append(dict(panel=panel.upper(),text=age.group(),model=ageword+' '+age[5],state='AUTHOR_PREPOSED_ATP_DISTINCT_AGE_PANELS',label='('+panel+')'))
        return out
    postfix=re.search(r'(?:intracellular\s+)?ATP\s+'
        r'(?:levels?|content|concentration|production(?:\s+rate)?)\s*\(([a-z])\)',text,re.I)
    if postfix:
        models={m.group() for m in _MODEL.finditer(text)}
        model=next(iter(models)) if len(models)==1 else None
        return [dict(panel=postfix[1].upper(),text=postfix.group(),model=model,
            state='AUTHOR_POSTFIX_ATP_ENDPOINT_LABEL',label='('+postfix[1]+')')]
    marks=list(re.finditer(r'(?<!\w)\(([a-z])\)\s+(?=[A-Za-z])',text,re.I))
    for i,m in enumerate(marks):
        end=marks[i+1].start() if i+1<len(marks) else len(text);scope=text[m.end():end].strip()
        if re.search(r'ATP\s+(?:content|levels?|concentration|production)\b',scope,re.I) and not _BAD.search(scope):
            models=list(_MODEL.finditer(scope));values={x.group() for x in models}
            out.append(dict(panel=m[1].upper(),text=scope,model=next(iter(values)) if len(values)==1 else None,state='AUTHOR_PREFIX_ATP_PANEL',label=m.group().strip()))
    if out:return out
    if re.search(r'ATP\s+(?:content|levels?|concentration|production)\b',text,re.I) and not re.search(r'schematic|illustration|mechanism|proposed model',text,re.I):
        return [dict(panel=None,text=text,model=None,state='ATP_FIGURE_PANEL_SCOPE_UNRESOLVED',label=None)]
    return []


def bind_explicit_scope(candidate, scope, cap, names, sha):
    """Only explicit same-figure protocol; Methods incubation is never exposure."""
    from .atp_content_relations import _citation,_field,_doses
    from .atp_content_author_identity import name_pattern
    text=cap['text'];np=name_pattern(names);protocol=scope.get('protocol')
    if scope['state']=='AUTHOR_PREPOSED_ATP_DISTINCT_AGE_PANELS':
        prefix=text[:text.index(scope['text'])]
        matches=list(re.finditer(r'Neurons\s+were\s+treated\s+for\s+(\d+(?:\.\d+)?)\s*h\s+with\s+.{0,250}?(?:'+np+r')\b\.',prefix,re.I))
        if len(matches)==1:
            protocol=matches[0].group();scope={**scope,'hours':float(matches[0][1])}
            candidate['dose_series_author_evidence']={'author_literal':protocol,'evidence':_citation(cap,sha,protocol),'individual_arm_dose_uM':None,'state':'MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED'}
            candidate['hold_reasons'].append('MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED')
    if protocol and re.search(np,protocol,re.I):
        dose=_doses(protocol,names,literal_molar=True)
        if len({(d['uM'],d['literal']) for d in dose})==1:
            d=dose[0];ev=[_citation(cap,sha,d['quote'])]
            candidate['fields']['dose_original']=_field(d['literal'],ev,'EXPLICIT_SAME_ATP_PANEL_PROTOCOL')
            candidate['fields']['dose_uM']=_field(d['uM'],ev,'EXPLICIT_SAME_ATP_PANEL_PROTOCOL')
        if 'hours' in scope:
            ev=[_citation(cap,sha,protocol)]
            candidate['fields']['total_compound_exposure_h']=_field(scope['hours'],ev,'AUTHOR_EXPLICIT_PANEL_OR_SHARED_SAME_CELL_TREATMENT_INTERVAL')
            candidate['treatment_stages']=[{'stage':'AUTHOR_DRUG_EXPOSURE','duration_h':scope['hours'],'evidence':ev}]
            candidate['experiment_scope']['phase']='AUTHOR_DRUG_EXPOSURE'
    # A caption list establishes distinct interventions, not their effects or
    # their comparator relationships. Lactate/protein statements are not used.
    arms=re.search(r'in\s+(macrophages)\s+treated\s+(?:with\s+)?(.+?)(?:\.|;|$)',text,re.I)
    if arms and re.search(np,arms[2],re.I):
        candidate['intervention_inventory']={'author_literal':arms[2],'evidence':_citation(cap,sha,arms.group()),'state':'AUTHOR_DISTINCT_ARMS_NOT_PAIRED','free_parent_pattern_excludes_formulations':True}
        if len(set(_MODEL.findall(text)))==1:
            candidate['fields']['cell_model']=_field(arms[1],[_citation(cap,sha,arms.group())],'AUTHOR_SAME_FIGURE_MEASURED_ARM_CELL_CONTEXT')
            candidate['experiment_scope']['cell']=arms[1]
    for field,reason in [('dose_uM','DOSE_BINDING_UNRESOLVED'),('cell_model','CELL_BINDING_UNRESOLVED'),('total_compound_exposure_h','EXPOSURE_BINDING_UNRESOLVED')]:
        if candidate['fields'][field]['value'] is not None:
            candidate['hold_reasons']=[h for h in candidate['hold_reasons'] if h!=reason]
    if scope['panel'] is None:candidate['hold_reasons'].append('ATP_FIGURE_PANEL_SCOPE_UNRESOLVED')
    if re.search(r'with\s+or\s+without',scope['text'],re.I):candidate['hold_reasons'].append('MULTI_INTERVENTION_PANEL_REQUIRES_SEPARATE_ARMS')
    candidate['hold_reasons']=sorted(set(candidate['hold_reasons']))
    return candidate


def recall(index, paragraphs, entities, sha, paper, protocol, existing):
    from .source_io import local,plain
    from .atp_content_relations import _citation,_field
    from .atp_content_author_identity import name_pattern
    from .atp_content_cross_context import _SENTENCE_BOUNDARY
    methods=direct_methods([b for b in paragraphs if b['role']=='METHODS'])
    has_direct_luminescent_production=any(re.search(r'intracellular\s+(?:total\s+)?ATP\s+production',m['assay_quote'],re.I)
        and re.search(r'luciferase|luciferin',m['assay_quote'],re.I) for m in methods)
    has_direct_atp_method=bool(methods)
    out=[];diagnostics=[]
    for path,fig in index.items():
        if local(fig['element'].tag)!='fig':continue
        cap=next((b for p,b in index.items() if p.startswith(path+'/caption[') and local(b['element'].tag)=='caption'),None)
        if not cap:continue
        if re.search(r'ATP.{0,20}(?:production|generation)\s+rate|ATP[- ]linked|ATP[- ]production[- ]coupled',cap['text'],re.I):
            continue  # do not hide a caption-level endpoint conflict by cutting a short label
        label=next((plain(e) for e in fig['element'] if local(e.tag)=='label'),'');num=re.search(r'\d+',label);fid=fig['element'].get('id')
        for scope in panel_scopes(cap):
            # Explicit current-article Results anchor. Never background/reference prose.
            ref=rf'Fig(?:ure)?\.?\s*{num[0]}\s*{scope["panel"] or ""}\b' if num else r'(?!)'
            linked=[]
            for block in paragraphs:
                if block['role']!='RESULTS':continue
                for sentence in _SENTENCE_BOUNDARY.split(block['text']):
                    if not re.search(ref,sentence,re.I):continue
                    if not (_DIRECT.search(sentence) or ((has_direct_luminescent_production or has_direct_atp_method)
                            and re.search(r'ATP\s+production',sentence,re.I))):continue
                    linked.append({**block,'reference_sentence':sentence})
            if scope['state'] in ('AUTHOR_MULTI_PANEL_TIME_SERIES','AUTHOR_MULTI_PANEL_DOSE_SERIES') and linked:
                # Use the cell named nearest the exact panel reference in the
                # author's own Results clause; never borrow the neighbor arm.
                ref_match=re.search(ref,linked[0]['text'],re.I)
                if ref_match:
                    prefix_start=max(0,ref_match.start()-240)
                    prefix=linked[0]['text'][prefix_start:ref_match.start()]
                    model_mentions=list(_MODEL.finditer(prefix))
                    if model_mentions:
                        model_match=model_mentions[-1]
                        quote_start=prefix_start+model_match.start()
                        quote=linked[0]['text'][quote_start:ref_match.end()]
                        scope={**scope,'model':model_match.group(),
                            'model_evidence':_citation(linked[0],sha,quote,quote_start)}
            if scope['state']=='ATP_FIGURE_PANEL_SCOPE_UNRESOLVED' and (len(methods)!=1 or not linked):continue
            production=not bool(_DIRECT.search(scope['text']))
            if _BAD.search(scope['text']):continue
            if re.search(r'volcano plot|screening strategy|all (?:\d+\s+)?(?:tested|screened) compounds|all \d+ compounds screened',scope['text'],re.I):continue
            if production and (len(methods)!=1 or (not linked and not re.search(r'luminescent intensity',scope['text'],re.I))):continue
            for entity in entities:
                names=entity['retrieval_names'];np=name_pattern(names)
                caption_named=bool(re.search(np,cap['text'],re.I))
                drug_results=[b for b in linked if re.search(np,b['text'],re.I)]
                if not caption_named and not drug_results:continue
                if scope['state']=='AUTHOR_PREFIX_ATP_PANEL' and not re.search(np,scope['text'],re.I) and not drug_results:
                    continue  # a different panel's compound is not this intervention
                literal=entity['author_name']
                if any(c['figure_id']==fid and c['compound_name'].casefold()==literal.casefold() for c in existing):continue
                ev=[_citation(cap,sha,scope['text'])]
                decl=[d['evidence'] for d in entity['declarations']]
                fields={k:_field() for k in ('compound','cell_model','dose_original','dose_uM','pretreatment_h','challenge_h','total_compound_exposure_h','assay_method','normalization','treated_arm','comparator_arm','effect_direction')}
                fields['compound']=_field(literal,[*decl,*([_citation(cap,sha)] if caption_named else [_citation(b,sha) for b in drug_results])],entity['state'])
                # A figure caption may explicitly define both signal basis and
                # relative denominator. Bind that caption-level metadata only
                # to the ATP panels it labels; this does not create absolute
                # values or a matched control measurement.
                normalization=caption_normalization(cap['text'])
                if normalization:
                    fields['normalization']=_field(normalization['value'],[_citation(
                        cap,sha,normalization['quote'],normalization['start'])],
                        'AUTHOR_EXPLICIT_FIGURE_SIGNAL_NORMALIZATION_AND_CONTROL_DENOMINATOR')
                holds=['INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED','AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED','IMAGE_ARM_ASSIGNMENT_UNVERIFIED','SOURCE_LEVEL_EXPERIMENT_NOT_COMPLETE_RELATION','DOSE_BINDING_UNRESOLVED','EXPOSURE_BINDING_UNRESOLVED']
                if scope['state']=='AUTHOR_MULTI_PANEL_TIME_SERIES':holds.append('MULTIPLE_TIMEPOINTS_NOT_COLLAPSED')
                if entity['state']=='ENTITY_IDENTITY_HOLD':holds.append('ENTITY_IDENTITY_HOLD')
                if scope['model']:
                    model_evidence=[scope['model_evidence']] if scope.get('model_evidence') else ev
                    fields['cell_model']=_field(scope['model'],model_evidence,'AUTHOR_EXPLICIT_ATP_PANEL_MODEL')
                else:holds.append('CELL_BINDING_UNRESOLVED')
                if len(methods)==1:
                    fields['assay_method']=_field(methods[0]['assay_quote'],
                        [_citation(methods[0],sha,methods[0]['assay_quote'],methods[0]['assay_start'])],
                        'ARTICLE_DIRECT_ATP_METHOD_PANEL_LINK_REQUIRES_REVIEW')
                    holds.append('METHOD_PANEL_ASSOCIATION_REQUIRES_REVIEW')
                    norm=re.search(r'For the OGD studies, readings were normalized to the average value of the OGD controls\.',methods[0]['text'],re.I)
                    if norm and re.search(r'\bOGD\b',scope['text'],re.I):
                        fields['normalization']=_field('OGD_CONTROL_MEAN',[_citation(methods[0],sha,norm.group(),norm.start())],
                            'AUTHOR_METHOD_OGD_NORMALIZATION_SCOPE_REVIEW')
                        holds.append('ATP_PANEL_NORMALIZATION_METHOD_SCOPE_REQUIRES_REVIEW')
                else:holds.append('ATP_METHOD_BINDING_UNRESOLVED')
                if production:holds.append('AUTHOR_PRODUCTION_TERM_DIRECT_ATP_METHOD_SEMANTIC_REVIEW')
                if entity['formulations']:holds.extend(['FORMULATION_AND_FREE_PARENT_ARMS_MUST_REMAIN_DISTINCT','COMPOUND_SPECIFIC_EFFECT_UNRESOLVED'])
                arm_match=re.search(r'(?:addition of|treated with)\s+(.{1,180}?)\s+compared to\s+(.{1,100}?)(?:\.|;|$)',scope['text'],re.I)
                if arm_match:
                    candidate_arms={'author_literal':arm_match.group(),'evidence':_citation(cap,sha,arm_match.group()),
                        'state':'AUTHOR_INTERVENTION_AND_REFERENCE_CONTEXT_UNPAIRED'}
                    holds.extend(['MULTIPLE_INTERVENTIONS_REQUIRE_SEPARATE_ARMS','MATCHED_COMPARATOR_UNRESOLVED'])
                key=[sha,fid,scope['panel'],literal,'source-experiment-recall-v1']
                candidate=dict(candidate_id='ATP-'+hashlib.sha256(json.dumps(key).encode()).hexdigest()[:22],figure_id=fid,figure_label=label,panel=scope['panel'],scope_evidence=ev[0],assets=[{'href':e.get('{http://www.w3.org/1999/xlink}href'),'state':'DECLARED_NOT_ACQUIRED'} for e in fig['element'].iter() if local(e.tag)=='graphic'],compound_name=literal,compound_author_names=names,chemical_identity_resolution=entity,fields=fields,treatment_stages=[],endpoint_kind='ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW' if production else 'ATP_CONTENT_CANDIDATE',endpoint_reconciliation={'caption_term_conflict':production,'independently_verified':False},dose_series_author_evidence=None,treated_value=None,control_value=None,measurement_unit=None,numeric_origin='NOT_EXTRACTED',review_tier='Review-B',train_eligible=False,hold_reasons=sorted(set(holds)),independent_gold=0,training_approved=0,panel_scope_state=scope['state'],linked_author_prose=[_citation(b,sha,b.get('reference_sentence',b['text'])) for b in drug_results],experiment_scope={'paper':paper,'figure_id':fid,'panel':scope['panel'],'cell':scope['model'],'intervention':literal,'phase':'UNRESOLVED','status':'SOURCE_EXPERIMENT_REVIEW_NOT_PAIRED'})
                if scope.get('dose_range'):
                    candidate['dose_series_author_evidence']={'author_literal':scope['dose_range'],'evidence':_citation(cap,sha,scope['dose_range']),'individual_arm_dose_uM':None,'state':'MULTIDOSE_PANEL_ARMS_UNASSIGNED'}
                    holds.append('MULTIDOSE_PANEL_ARMS_UNASSIGNED')
                    candidate['hold_reasons']=sorted(set(holds))
                    if scope.get('hours') is not None:
                        time_evidence=[_citation(cap,sha,scope['text'])]
                        fields['total_compound_exposure_h']=_field(scope['hours'],time_evidence,'AUTHOR_EXPLICIT_PANEL_TIME_IN_SHARED_DOSE_SERIES_CAPTION')
                        candidate['treatment_stages']=[{'stage':'AUTHOR_DRUG_EXPOSURE','duration_h':scope['hours'],'evidence':time_evidence}]
                elif scope.get('dose_literal'):
                    from .atp_content_relations import _doses
                    dose_rows=_doses(scope['protocol'],names,literal_molar=True)
                    if len(dose_rows)==1:
                        fields['dose_original']=_field(dose_rows[0]['literal'],[_citation(cap,sha,dose_rows[0]['quote'])],'AUTHOR_EXPLICIT_MULTIPANEL_PROTOCOL')
                        fields['dose_uM']=_field(dose_rows[0]['uM'],[_citation(cap,sha,dose_rows[0]['quote'])],'AUTHOR_EXPLICIT_MULTIPANEL_PROTOCOL')
                if arm_match:candidate['arm_inventory']=candidate_arms
                if scope['label']:candidate['panel_label_evidence']=_citation(cap,sha,scope['label'])
                if scope.get('label_start') is not None:candidate['panel_label_evidence']=_citation(cap,sha,scope['label'],scope['label_start'])
                if scope.get('scope_start') is not None:
                    candidate['scope_evidence']=_citation(cap,sha,scope['text'],scope['scope_start'])
                if scope.get('endpoint_literal') and scope.get('endpoint_start') is not None:
                    candidate['panel_endpoint_evidence']=_citation(cap,sha,
                        scope['endpoint_literal'],scope['endpoint_start'])
                out.append(bind_explicit_scope(candidate,scope,cap,names,sha))
    return out
