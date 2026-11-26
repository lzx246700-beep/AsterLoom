"""Concordant named Methods exposure and same-panel ATP Results, no arm guesses.

This bounded sentence family requires the same unique cell model, explicit
named dose and exposure, and one matching ATP-result time. Multi-agent list
membership and 'increased by N times' semantics remain unapproved/ambiguous.
"""
import copy
import re


def bind_method_protocol(parent,methods,linked,sha):
    from .atp_content_relations import CELL,TIME,CONTENT,RATE,_name_pattern,_doses,_hours,_citation,_field
    from .atp_content_cross_context import model_class,references
    f=parent['fields'];model=f['cell_model']['value']
    names='|'.join(re.escape(n) for n in parent['compound_author_names'])
    combined=re.search(rf'(?<![\w-])(?:{names})[- ]stimulated\b[^.]*\bcells\s+treated\s+with\s+(?P<other>[^.]+)',
        parent.get('scope_evidence',{}).get('quote',''),re.I)
    if combined:
        out=copy.deepcopy(parent);out['endpoint_kind']='ATP_CONTENT_MULTI_INTERVENTION_CONTEXT_CANDIDATE'
        out['method_protocol_exclusion']={'reason':'DRUG_STIMULATED_CELLS_WITH_ADDITIONAL_NAMED_INTERVENTIONS',
            'additional_intervention_author_literal':combined['other'].strip(),
            'evidence':parent['scope_evidence'],'single_compound_causal_effect_assigned':False}
        out['hold_reasons']=sorted(set(out['hold_reasons']+['MULTI_INTERVENTION_PROTOCOL_REQUIRES_SEPARATE_ARMS','TESTED_CHEMICAL_FORM_UNVERIFIED']))
        return out
    if (parent['numeric_origin']!='NOT_EXTRACTED' or parent['endpoint_kind']!='ATP_CONTENT_CANDIDATE'
        or model is None or f['dose_uM']['value'] is not None or f['total_compound_exposure_h']['value'] is not None
        or any('CONFLICT' in h or 'AMBIGU' in h for h in parent['hold_reasons'])):return parent
    number=re.search(r'\d+',parent['figure_label'] or parent['figure_id'] or '')
    if not number or parent['panel'] is None:return parent
    name=_name_pattern(parent['compound_author_names']);wanted=model_class(model);results=[]
    for block in linked:
        sentence=block.get('reference_sentence','')
        if ((number[0],parent['panel']) not in references(sentence) or not re.search(name,sentence,re.I)
            or not CONTENT.search(sentence) or RATE.search(sentence)
            or {model_class(m.group()) for m in CELL.finditer(sentence)}!={wanted}):continue
        times=list(re.finditer(TIME,sentence,re.I))
        if len(times)==1:results.append((block,sentence,_hours(times[0].group())))
    if len(results)!=1:return parent
    result_block,result_sentence,result_h=results[0];matches=[]
    pattern=(rf'(?P<model>{CELL.pattern})\s+cells?.{{0,100}}?\s+were\s+'
        rf'(?:stimulated|treated|incubated)\s+with\s+(?P<agents>.+?)\s+for\s+(?P<time>{TIME})(?:[.;]|$)')
    for block in methods:
        heading=' '.join(block['headings'])
        if not re.search(r'cell culture|cell.{0,20}treatment|drug treatment',heading,re.I):continue
        if re.search(r'transfect|knockdown|overexpress',heading,re.I):continue
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',block['text']):
            if re.search(r'transfect|knockdown|overexpress|\bbefore\b|\bafter\b|pretreat',sentence,re.I):continue
            models={model_class(m.group()) for m in CELL.finditer(sentence)}
            if models!={wanted}:continue
            for match in re.finditer(pattern,sentence,re.I):
                doses=_doses(match['agents'],parent['compound_author_names'],literal_molar=True)
                if len(doses)!=1 or doses[0]['uM'] is None:continue
                hours=_hours(match['time'])
                if hours==result_h:matches.append((block,sentence,match,doses[0]))
    if len(matches)!=1:return parent
    block,sentence,match,dose=matches[0];out=copy.deepcopy(parent);f=out['fields']
    method_evidence=_citation(block,sha,sentence);result_evidence=_citation(result_block,sha,result_sentence)
    ev=[method_evidence,result_evidence]
    f['dose_original']=_field(dose['literal'],ev,'NAMED_METHOD_DOSE_SAME_MODEL_ATP_RESULT_CANDIDATE')
    f['dose_uM']=_field(dose['uM'],ev,'EXPLICIT_MOLAR_LITRE_NOTATION_CONVERSION')
    f['total_compound_exposure_h']=_field(result_h,ev,'CONCORDANT_NAMED_METHOD_AND_SAME_PANEL_ATP_RESULT_INTERVAL')
    out['treatment_stages']=[{'role':'NAMED_COMPOUND_PROTOCOL_EXPOSURE','duration_h':result_h,
        'author_literal':sentence,'evidence':ev,'intervention':out['compound_name'],
        'method_agent_list_literal':match['agents'],'cointervention_membership':'UNRESOLVED_NOT_ASSIGNED_FROM_LIST'}]
    holds=[h for h in out['hold_reasons'] if h not in ('DOSE_BINDING_UNRESOLVED','TREATMENT_SEQUENCE_UNRESOLVED','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED')]
    holds.append('SOURCE_PROTOCOL_BINDING_REQUIRES_INDEPENDENT_REVIEW')
    holds.append('TESTED_CHEMICAL_FORM_UNVERIFIED')
    if re.search(r',|\band\b|\bplus\b|\+',match['agents'],re.I):holds.append('METHOD_MULTI_AGENT_LIST_ARMS_REQUIRE_REVIEW')
    reported=None
    effect=re.search(rf'{name}\s+treatment\s+(?P<verb>increased|decreased)\s+(?:the\s+)?ATP\s+(?:content|levels?)\s+by\s+(?P<number>\d+(?:\.\d+)?)\s+times\b',result_sentence,re.I)
    if effect:
        f['effect_direction']=_field('INCREASE' if effect['verb'].casefold()=='increased' else 'DECREASE',[result_evidence],
            'AUTHOR_QUALITATIVE_EFFECT_FOLD_INTERPRETATION_UNRESOLVED')
        literal=effect['number']+' times';offset=result_sentence.index(literal)
        reported={'literal':literal,'value':float(effect['number']),'unit_author_literal':'times',
            'semantics':'BY_TIMES_AMBIGUOUS_NOT_AUTHOR_RAW_ATP_MEAN_OR_VERIFIED_FINAL_FOLD',
            'evidence':_citation(result_block,sha,literal,result_block['text'].index(result_sentence)+offset),
            'final_treated_control_fold':None,'raw_means_invented':False}
        holds.append('AUTHOR_BY_TIMES_EFFECT_INTERPRETATION_UNRESOLVED')
    out['method_protocol_evidence']={'schema':'asterloom.atp-content.named-method-protocol/0.1',
        'method_statement':method_evidence,'same_panel_atp_result':result_evidence,
        'author_named_dose':dose,'exposure_h':result_h,'conversion_version':'r52.molar-litre-notation/1',
        'reported_effect_token':reported,'automatic_control_assignment':False,'scientific_approval':False}
    out['hold_reasons']=sorted(set(holds))
    return out


def bind_author_polyphenol_protocol(parent,methods,sha):
    """Bind a listed molecule's dose and pretreatment only with ATP-method corroboration.

    This path requires a unique same-model Methods list that puts the exact
    candidate name inside a dose-bearing polyphenol list, plus one direct ATP
    assay paragraph that independently repeats the polyphenol pretreatment
    interval and declares multiple OA challenge durations. The challenge
    alternatives remain unassigned to any individual result sentence.
    """
    from .atp_content_cross_context import model_class
    from .atp_content_relations import CELL,_citation,_field,_hours
    if (parent.get('endpoint_kind') not in ('ATP_CONTENT_CANDIDATE',
            'ATP_CONTENT_UNCALIBRATED_CELLTITER_GLO_REVIEW')
            or not parent.get('linked_author_prose')):
        return parent
    fields=parent.get('fields',{});model=fields.get('cell_model',{}).get('value')
    if not model or fields.get('dose_uM',{}).get('value') is not None:
        return parent
    wanted=model_class(model)
    names=parent.get('compound_author_names',[]) or [parent.get('compound_name','')]
    cultures=[b for b in methods if re.search(r'cell\s+culture',' '.join(b.get('headings',())),re.I)]
    culture_models={model_class(m.group()) for b in cultures for m in CELL.finditer(b.get('text',''))}
    if culture_models and culture_models!={wanted}:
        return parent

    treatment_matches=[]
    listing_pattern=re.compile(
        r'(?P<literal>(?P<dose>\d+(?:\.\d+)?\s*[µμu]M)\s+'
        r'(?:polyphenol|polyphenols|compound|compounds)\s*\('
        r'(?P<agents>.*?)\)\s+for\s+(?P<pretreatment>\d+(?:\.\d+)?\s*'
        r'(?:h|hours?)))\s+followed\s+by\s+',re.I|re.S)
    for block in methods:
        heading=' '.join(block.get('headings',()))
        if not re.search(r'treat|polyphenol',' '.join(block.get('headings',())),re.I):
            continue
        block_models={model_class(m.group()) for m in CELL.finditer(block.get('text',''))}
        if block_models!={wanted}:
            continue
        for match in listing_pattern.finditer(block.get('text','')):
            members=[]
            for name in names:
                if not name:
                    continue
                if re.search(r'(?<![A-Za-z0-9-])'+re.escape(name)+r'(?![A-Za-z0-9-])',
                             match['agents'],re.I):
                    members.append(name)
            # The requested molecule must be an explicit list member. A
            # shorter name cannot match a longer derivative/analogue token.
            if len(members)==1:
                treatment_matches.append((block,match,members[0]))
    if len(treatment_matches)!=1:
        return parent

    treatment_block,treatment,named_member=treatment_matches[0]
    assay_matches=[]
    for block in methods:
        heading=' '.join(block.get('headings',()))
        if not re.search(r'ATP',heading,re.I) or not re.search(
                r'assay|quantif|measurement|determination|analysis',heading,re.I):
            continue
        assay_models={model_class(m.group()) for m in CELL.finditer(block.get('text',''))}
        if assay_models and assay_models!={wanted}:
            continue
        text=block.get('text','')
        direct_measurement=bool(re.search(
            r'(?:\bATP\s+(?:content|levels?|concentration)\b.{0,100}'
            r'\b(?:measured|quantified|determined|assayed)\b'
            r'|\b(?:measured|quantified|determined|assayed)\b.{0,100}\bATP\b)',
            text,re.I))
        direct_assay=bool(re.search(
            r'CellTiter[- ]Glo|ATP\s+(?:Assay\s+Kit|reagent)|lucifer|bioluminescen',
            text,re.I))
        if not direct_measurement or not direct_assay:
            continue
        schedule=re.search(
            r'(?P<literal>treated\s+with\s+polyphenols?\s+for\s+'
            r'(?P<pre>\d+(?:\.\d+)?\s*(?:h|hours?))\s+and\s+'
            r'(?P<challenge>[^.;]{0,100}?\bfor\s+'
            r'(?P<t1>\d+(?:\.\d+)?\s*(?:h|hours?))\s+or\s+'
            r'(?P<t2>\d+(?:\.\d+)?\s*(?:h|hours?))))',text,re.I)
        if schedule:
            assay_matches.append((block,schedule))
    if len(assay_matches)!=1:
        return parent
    assay_block,schedule=assay_matches[0]
    # The two author paragraphs must agree on the pretreatment clock.
    if _hours(treatment['pretreatment']) != _hours(schedule['pre']):
        return parent

    out=copy.deepcopy(parent);f=out['fields']
    treatment_ev=_citation(treatment_block,sha,treatment.group('literal'))
    assay_ev=_citation(assay_block,sha,schedule.group('literal'))
    evidence=[treatment_ev,assay_ev]
    f['dose_original']=_field(treatment['dose'],evidence,
        'AUTHOR_NAMED_DOSE_LIST_MEMBER_ATP_ASSAY_CORROBORATED_REVIEW')
    dose_value=float(re.search(r'\d+(?:\.\d+)?',treatment['dose'])[0])
    f['dose_uM']=_field(dose_value,evidence,
        'AUTHOR_EXPLICIT_MICROMOLAR_DOSE_LIST_MEMBER_REVIEW')
    pretreatment_h=_hours(treatment['pretreatment'])
    f['pretreatment_h']=_field(pretreatment_h,evidence,
        'AUTHOR_POLYPHENOL_PRETREATMENT_ATP_METHOD_CORROBORATED')
    out['treatment_stages']=[{
        'role':'NAMED_POLYPHENOL_PRETREATMENT','duration_h':pretreatment_h,
        'intervention':out['compound_name'],'author_list_member':named_member,
        'author_literal':treatment['literal'],'evidence':evidence,
        'total_compound_exposure_after_challenge':'UNKNOWN'}]
    options=[_hours(schedule['t1']),_hours(schedule['t2'])]
    out['author_method_challenge_options']={
        'stressor_author_literal':schedule['challenge'].strip(),
        'duration_options_h':options,
        'evidence':assay_ev,
        'state':'AUTHOR_ATP_ASSAY_ALTERNATIVES_NOT_ASSIGNED_TO_INDIVIDUAL_RESULT',
        'compound_exposure_h':None}
    holds=[h for h in out.get('hold_reasons',[]) if h not in
           ('DOSE_BINDING_UNRESOLVED','TREATMENT_SEQUENCE_UNRESOLVED')]
    holds.extend(['AUTHOR_CHALLENGE_DURATION_OPTIONS_REQUIRE_PANEL_TIMEPOINT_REVIEW',
                  'TOTAL_COMPOUND_EXPOSURE_UNRESOLVED',
                  'SOURCE_NAMED_LIST_DOSE_REQUIRES_INDEPENDENT_REVIEW'])
    out['hold_reasons']=sorted(set(holds))
    out['method_protocol_evidence']={
        'schema':'asterloom.atp-content.named-list-dose-protocol/0.1',
        'treatment_list_statement':treatment_ev,'atp_assay_schedule_statement':assay_ev,
        'named_member_literal':named_member,'dose_literal':treatment['dose'],
        'pretreatment_h':pretreatment_h,'challenge_options_h':options,
        'total_compound_exposure_h':None,'independent_review_required':True,
        'scientific_approval':False,'training_approved':False}
    return out
