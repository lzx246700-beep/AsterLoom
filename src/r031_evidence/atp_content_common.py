"""Explicit common figure protocol and source-declared model variants.

An explicit common treatment can support a panel; a protocol naming other
panels, multiple doses or a conflicting local dose cannot. Challenge time,
analysis time and drug exposure remain separate source roles.
"""
import re


def explicit_protein_normalization(text):
    """An explicit same-sentence ATP referent, not a neighbouring assay."""
    found=[]
    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',text):
        if re.search(r'glucose|\bROS\b|viability|ATPase|OCR|\bnot\b',sentence,re.I):continue
        if re.search(r'\bATP\s+(?:content|levels?|concentration)\s+in\s+(?:the\s+)?samples,\s*which\s+(?:was|were)\s+(?:then\s+)?normalized\s+to\s+(?:the\s+)?protein\s+concentration',sentence,re.I):
            found.append(sentence)
    return found[0] if len(found)==1 else None


def common_protocol(cap,names,sha):
    from .atp_content_relations import PANEL,NUMBER,UNIT,TIME,_name_pattern,_doses,_hours,_citation
    mark=PANEL.search(cap['text'])
    if not mark:return None
    preamble=cap['text'][:mark.start()].strip()
    if re.search(r'panel|except|respectively|depending|either|different\s+(?:dose|concentr)|with or without',preamble,re.I):return None
    pattern=rf'(?:^|(?<=[.!?])\s+)(?:all\s+)?cells\s+were\s+(?:treated|incubated)\s+with\s+{NUMBER}\s*{UNIT}\s+{_name_pattern(names)}\s+for\s+(?P<time>{TIME})\s*[.;]'
    hits=list(re.finditer(pattern,preamble,re.I));doses=_doses(preamble,names)
    if len(hits)!=1 or len(doses)!=1:return None
    return {'quote':hits[0].group(),'dose':doses[0],'time_h':_hours(hits[0]['time']),
            'evidence':_citation(cap,sha,hits[0].group())}


def apply_common(candidate,cap,scope,methods,sha,protocol):
    from .atp_content_relations import CELL,_citation,_field,_name_pattern
    from .atp_content_cross_context import model_class,assay_blocks
    f=candidate['fields'];holds=candidate['hold_reasons']
    if protocol:
        ev=[protocol['evidence']];dose=protocol['dose'];old=f['dose_original']['value']
        if old is not None and f['dose_uM']['value']!=dose['uM']:
            conflict_ev=f['dose_original']['evidence']
            f['dose_original']=_field(None,ev+conflict_ev,'COMMON_FIGURE_PANEL_SOURCE_CONFLICT')
            f['dose_uM']=_field(None,ev+conflict_ev,'COMMON_FIGURE_PANEL_SOURCE_CONFLICT')
            holds.extend(['COMMON_FIGURE_PANEL_DOSE_CONFLICT','DOSE_BINDING_UNRESOLVED'])
        else:
            if old is None:
                f['dose_original']=_field(dose['literal'],ev,'EXPLICIT_COMMON_FIGURE_PROTOCOL')
                f['dose_uM']=_field(dose['uM'],ev,'EXPLICIT_COMMON_FIGURE_PROTOCOL')
            if f['total_compound_exposure_h']['value'] is None:
                f['total_compound_exposure_h']=_field(protocol['time_h'],ev,'EXPLICIT_COMMON_FIGURE_PROTOCOL')
                candidate['treatment_stages']=[{'role':'COMMON_FIGURE_COMPOUND_TREATMENT','duration_h':protocol['time_h'],
                    'author_literal':protocol['quote'],'evidence':ev}]
            elif f['total_compound_exposure_h']['value']!=protocol['time_h']:
                previous=f['total_compound_exposure_h']
                f['total_compound_exposure_h']=_field(None,ev+previous['evidence'],'COMMON_FIGURE_PANEL_TIME_CONFLICT')
                candidate['treatment_stages']=[{'role':'CONFLICTING_SOURCE_TREATMENT_INTERVALS','duration_h':None,
                    'alternatives':[{'duration_h':previous['value'],'evidence':previous['evidence']},
                                    {'duration_h':protocol['time_h'],'evidence':ev}]}]
                holds.extend(['COMMON_FIGURE_PANEL_TIME_CONFLICT','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED'])
    # Only author parenthetical definitions, not arbitrary model inheritance.
    cultures=[b for b in methods if re.search(r'cell culture',' '.join(b['headings']),re.I)]
    models={m.group() for b in cultures for m in CELL.finditer(b['text'])}
    variants=[]
    for b in cultures:
        for m in re.finditer(r'\(([A-Za-z0-9_-]+)\s+cells\)',b['text']):
            if re.search(r'(?<!\w)'+re.escape(m[1])+r'(?!\w)',scope,re.I):
                variants.append({'label':m[1],'evidence':_citation(b,sha,m.group())})
    variants=list({v['label']:v for v in variants}.values())
    if len(models)==1 and len(variants)>=2:
        if f['cell_model']['value'] is None:
            f['cell_model']=_field(next(iter(models)),[_citation(b,sha) for b in cultures],
                'AUTHOR_BASE_CELL_LINE_VARIANTS_REQUIRE_SEPARATE_ARMS')
        candidate['cell_model_variant_candidates']=variants
        holds.append('CELL_VARIANT_EXPERIMENT_BINDING_UNRESOLVED')
    if f['assay_method']['value'] is None and f['cell_model']['value'] is not None:
        assays=[]
        for b in assay_blocks(methods):
            mm={model_class(m.group()) for m in CELL.finditer(b['text'])}
            if not mm or mm=={model_class(f['cell_model']['value'])}:assays.append(b)
        if len(assays)==1:
            b=assays[0];f['assay_method']=_field(b['assay_quote'],[_citation(b,sha,b['assay_quote'])],
                'UNIQUE_MODEL_COMPATIBLE_ARTICLE_ATP_ASSAY_CANDIDATE')
    if f['normalization']['value'] is None and f['assay_method']['value'] is not None:
        # An explicit ATP-mass/protein-mass unit is normalization evidence;
        # a parallel protein assay is not.
        for b in assay_blocks(methods):
            if b['assay_quote']!=f['assay_method']['value']:continue
            match=re.search(r'ATP\s+(?:levels?|content|concentration)\s+(?:was|were)\s+expressed\s+as\s+(?P<unit>(?:micrograms?|[µμ]g)\s+(?:of\s+)?ATP\s+per\s+(?:micrograms?|[µμ]g)\s+(?:of\s+)?protein)',b['text'],re.I)
            if match:
                f['normalization']=_field('PROTEIN',[_citation(b,sha,match.group())],'EXPLICIT_AUTHOR_ATP_PER_PROTEIN_UNIT')
                if candidate['measurement_unit'] is None:candidate['measurement_unit']=match['unit']
    # OGD damage is NOT drug exposure. Only a unique, matching author Drug
    # Treatment paragraph together with this very ATP panel's reoxygenation
    # measurement time can establish post-OGD compound exposure.
    normalized=scope.translate(str.maketrans({'‐':'-','‑':'-','–':'-'}))
    challenge=re.search(r'(\d+(?:\.\d+)?)\s*-\s*h\s+OGD-exposed cells',normalized,re.I)
    present=re.search(r'in the presence of.{0,70}'+_name_pattern(candidate['compound_author_names']),scope,re.I)
    if challenge and present:
        # The regex matched a normalized dash; citation must preserve the
        # author's original Unicode code points and exact character offsets.
        original_challenge=scope[challenge.start():challenge.end()]
        evidence_cap=_citation(cap,sha,original_challenge)
        f['challenge_h']=_field(float(challenge[1]),[evidence_cap],
            'AUTHOR_OGD_CHALLENGE_NOT_COMPOUND_EXPOSURE')
        eligible=[]
        for b in methods:
            if not re.search(r'drug treatment',' '.join(b['headings']),re.I):continue
            if not re.search(_name_pattern(candidate['compound_author_names']),b['text'],re.I):continue
            administration=re.search(r'was added.{0,110}?immediately after the OGD procedure',b['text'],re.I)
            if not administration:continue
            caption_dose=f['dose_original']['value']
            if caption_dose is None or not re.search(re.escape(caption_dose),b['text']):continue
            from .atp_content_relations import CELL
            method_models={m.group().casefold() for m in CELL.finditer(b['text'])}
            target_model=f['cell_model']['value']
            if target_model is None or method_models and method_models!={target_model.casefold()}:continue
            if not method_models and models!={target_model}:continue
            eligible.append((b,administration))
        times=list(re.finditer(r'Analysis was performed after\s+(?P<time>\d+(?:\.\d+)?\s*(?:min|minutes?|h|hours?))\s+of reoxygenation',scope,re.I))
        bound=False
        if len(eligible)==len(times)==1 and f['total_compound_exposure_h']['value'] is None:
            b,administration=eligible[0];t=times[0]
            # The Methods must explicitly declare the measured interval in
            # the same post-OGD maintenance schedule; never borrow other panels.
            after=b['text'][administration.end():]
            interval=t['time']
            if re.search(r'\bmaintained\b.{0,130}(?<!\d)'+re.escape(interval)+r'\b',after,re.I):
                from .atp_content_relations import _hours
                drug_h=_hours(interval)
                method_evidence=_citation(b,sha,administration.group())
                time_evidence=_citation(cap,sha,t.group())
                f['total_compound_exposure_h']=_field(drug_h,
                    [method_evidence,time_evidence],
                    'SAME_PANEL_POST_OGD_COMPOUND_EXPOSURE_AUTHOR_METHOD_MATCH')
                candidate['treatment_stages']=[
                    {'role':'OGD_CHALLENGE','duration_h':float(challenge[1]),
                     'author_literal':challenge.group(),'evidence':[evidence_cap]},
                    {'role':'POST_OGD_COMPOUND_EXPOSURE_AND_REOXYGENATION',
                     'duration_h':drug_h,'intervention':candidate['compound_name'],
                     'author_literal':administration.group()+'; '+t.group(),
                     'evidence':[method_evidence,time_evidence]}]
                candidate['temporal_scope_evidence']={
                    'source_state':'EXPLICIT_AUTHOR_METHOD_AND_SAME_FIGURE_PANEL_TIME',
                    'drug_added_after_challenge':True,
                    'drug_exposure_h':drug_h,'ogd_challenge_h':float(challenge[1]),
                    'assay_time_after_reoxygenation_h':drug_h,
                    'scientific_approval':False}
                holds.append('POST_OGD_DRUG_EXPOSURE_SOURCE_SCREENED_NOT_GOLD')
                bound=True
        if not bound:
            author_evidence=[_citation(b,sha) for b,_ in eligible]
            f['total_compound_exposure_h']=_field(None,
                [evidence_cap,*author_evidence],'AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY')
            candidate['source_conflicts'].append({
                'kind':'AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY',
                'caption_evidence':evidence_cap,'method_evidence':author_evidence,
                'state':'HOLD_TEMPORAL_ADJUDICATION_REQUIRED'})
            holds.extend(['AUTHOR_CHALLENGE_DRUG_TIMING_AMBIGUITY','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED'])
    removals={'cell_model':['CELL_BINDING_UNRESOLVED'],'dose_original':['DOSE_BINDING_UNRESOLVED'],
        'assay_method':['ATP_METHOD_BINDING_UNRESOLVED'],'normalization':['NORMALIZATION_NOT_BOUND','NORMALIZATION_UNRESOLVED','PARALLEL_PROTEIN_MEASUREMENT_NOT_NORMALIZATION_PROOF'],
        'total_compound_exposure_h':['TREATMENT_SEQUENCE_UNRESOLVED','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED']}
    removed={reason for field,reasons in removals.items() if f[field]['value'] is not None for reason in reasons}
    candidate['hold_reasons']=sorted(set(h for h in holds if h not in removed))
