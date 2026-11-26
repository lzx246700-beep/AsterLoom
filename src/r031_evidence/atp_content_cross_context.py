"""Source-scoped figure references and explicit named-drug protocols.

All joins are proposals with original quotes. A whole-paper dose, unrelated
model, neighbouring panel, or endpoint number is never enough to join.
"""
import copy
import re

_SENTENCE_BOUNDARY=re.compile(
    r'(?:(?<=[.!?])|(?<=\)[.!?])|(?<=\][.!?])|(?<=[.!?][)\]]))\s+(?=[A-Z])')


def model_class(text):
    t=re.sub(r'\s+',' ',text).casefold()
    if re.search(r'\b(?:mii\s+|mature\s+)?oocytes?\b',t):return 'OOCYTES'
    if 'myotub' in t:
        if 'immortalized' in t:return 'IMMORTALIZED_MYOTUBES'
        if 'primary' in t:return 'PRIMARY_MYOTUBES'
        return 'MYOTUBES_UNSPECIFIED'
    return re.sub(r'\s+','',t).upper()


def references(text):
    # Author forms "Figure 2I", "Figures 2I,J" and "Figs. 2E,F"
    # refer to distinct panels. A shared figure number does NOT grant a
    # treatment protocol to other panels.
    pattern=r'\bFig(?:ure)?s?\.?\s*(\d+)(?:\s*([A-Za-z])(?=$|[\s,.;:)–-]))?'
    out=[(m[1],m[2].upper() if m[2] else None) for m in re.finditer(pattern,text,re.I)]
    multi=r'\bFig(?:ure)?s?\.?\s*(\d+)\s*([A-Za-z](?:\s*,\s*[A-Za-z])+)'
    for m in re.finditer(multi,text,re.I):
        out.extend((m[1],p.strip().upper()) for p in m[2].split(','))
    return list(dict.fromkeys(out))


def linked_prose(paragraphs,label,figure_id,panel,names,strict_roles=False):
    from .atp_content_relations import _name_pattern
    number=re.search(r'\d+',label or figure_id or '')
    if not number:return []
    out=[];seen=set()
    for b in paragraphs:
        if b['role']!='RESULTS' or not re.search(_name_pattern(names),b['text'],re.I):continue
        for sentence in _SENTENCE_BOUNDARY.split(b['text']):
            if not re.search(_name_pattern(names),sentence,re.I):continue
            if strict_roles:
                # Mentioning a drug as motivation does not make it the actual
                # intervention in a subsequent explicit co-treatment clause.
                active=list(re.finditer(r'\bwe\s+co[- ]?treated\s+',sentence,re.I))
                if active and not re.search(_name_pattern(names),sentence[active[-1].end():],re.I):continue
            refs=references(sentence)
            if any(n==number[0] and (panel is None or p==panel) for n,p in refs):
                key=(b.get('path'),sentence)
                if key in seen:continue
                seen.add(key)
                out.append({**b,'reference_sentence':sentence,
                            'scope_state':'NAMED_DRUG_AND_EXPLICIT_FIGURE_REFERENCE_SAME_SENTENCE'})
    return out


def assay_blocks(methods):
    from .atp_content_relations import CONTENT,RATE
    out=[]
    for b in methods:
        if re.search(r'chemicals|reagents? and cell culture|materials$',b['headings'][-1] if b['headings'] else '',re.I):continue
        heading=' '.join(b['headings'])
        specific=bool(re.search(r'ATP',heading,re.I) and re.search(r'assay|measurement|determination|levels?|analysis',heading,re.I))
        best=[]
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])',b['text']):
            if 'ATP' not in sentence or RATE.search(sentence):continue
            measured=bool(re.search(r'measur|determin|quantif|analy[sz]|check|assay',sentence,re.I))
            catalogued_atp=bool(re.search(
                r'ATP\s*\(\s*[A-Z0-9][A-Z0-9-]{2,}(?:\s*,[^)]{0,100})?\)\s*'
                r'(?:content|levels?|concentration)\b',sentence,re.I))
            detector=bool(re.search(
                r'ATP\s+(?:Assay\s+Kit|reagent)|'
                r'ATP\s*\(\s*[A-Z0-9][A-Z0-9-]{2,}(?:\s*,[^)]{0,100})?\)\s*'
                r'(?:content|levels?|concentration)\b|'
                r'Cell\s*Titer\s*Glo|lucifer|luminescen|luminometric|HPLC|'
                r'enzymatic\s+method|spectrophotometr',sentence,re.I))
            if measured and detector and (specific or catalogued_atp or CONTENT.search(sentence) or re.search(r'ATP\s+in\s+(?:the\s+)?lysate',sentence,re.I)):
                score=5*bool(CONTENT.search(sentence))+3*detector+2*specific
                best.append((score,sentence))
        if best:
            best.sort(key=lambda x:-x[0]);out.append({**b,'assay_quote':best[0][1]})
    return out


def caption_schedule(scope,names):
    from .atp_content_relations import TIME,_hours,_name_pattern
    if not re.search(r'cells|myotubes|macrophages|HepG2|RAW264',scope,re.I):return None
    if re.search(r'lysis|lysed|centrifug|reaction buffer',scope,re.I):return None
    hits=list(re.finditer(TIME,scope,re.I))
    if len(hits)==1 and (re.search(r'after\s+'+TIME+r'\s+(?:incubation|treatment)',scope,re.I) or
                         re.search(r'(?:cells|myotubes).{0,50}incubated\s+for\s+'+TIME,scope,re.I)):
        return {'total_h':_hours(hits[0].group()),'state':'CAPTION_TREATMENT_INTERVAL_BOUND',
                'stages':[{'role':'COMPOUND_TREATMENT','duration_h':_hours(hits[0].group())}]}
    # An author can describe alternative interventions in one common
    # treatment clause: "cultured in the presence of compound (range) or
    # stressor (range) for 6 h". Bind the sole interval to the named compound
    # only when the treatment phrase itself contains it.
    treatment=re.search(
        r'\b(?:cultured|incubated|treated)\s+in\s+(?:the\s+)?presence\s+of\s+'
        r'(?P<agents>[^.]{1,240}?)\s+for\s+(?P<time>'+TIME+r')\b',scope,re.I)
    if (len(hits)==1 and treatment and re.search(_name_pattern(names),treatment['agents'],re.I)
            and not re.search(r'\b(?:before|after|followed by|then)\b',treatment['agents'],re.I)):
        return {'total_h':_hours(treatment['time']),'state':'AUTHOR_SHARED_ALTERNATIVE_TREATMENT_INTERVAL',
                'stages':[{'role':'COMPOUND_TREATMENT','duration_h':_hours(treatment['time'])}]}
    if (len(hits)==2 and re.search(r'continuous presence of\s+'+_name_pattern(names),scope,re.I)
        and re.search(r'followed by',scope,re.I)):
        return {'total_h':sum(_hours(h.group()) for h in hits),'state':'DERIVED_SUM_EXPLICIT_CONTINUOUS_PRESENCE_STAGES',
                'stages':[{'role':f'CONTINUOUS_COMPOUND_STAGE_{i+1}','duration_h':_hours(h.group())} for i,h in enumerate(hits)]}
    return None


def concentration_lists(methods,names,model,caption_time):
    from .atp_content_relations import CELL,NUMBER,UNIT,_name_pattern,_citation
    if model is None or caption_time is None:return []
    name=_name_pattern(names);found=[]
    value_list=rf'(?P<list>(?:{NUMBER}\s*,\s*)+(?:(?:and|or)\s+)?{NUMBER})\s*(?P<unit>{UNIT})\s+concentrations?'
    for b in methods:
        if not re.search(r'treatments?', ' '.join(b['headings']),re.I):continue
        models={model_class(m.group()) for m in CELL.finditer(b['text'])}
        if models!={model_class(model)}:continue
        # Explicit ALL-treated interval links this named protocol to the figure.
        time=re.search(r'all treated cells were incubated for\s+(\d+(?:\.\d+)?)\s*h\b',b['text'],re.I)
        if not time or float(time[1])!=caption_time:continue
        for clause in b['text'].split(';'):
            for m in re.finditer(value_list,clause):
                prefix=clause[:m.start()]
                if not re.search(name,prefix,re.I) or not re.search(r'\b(?:in|at)\s*$',prefix,re.I):continue
                if re.search(r'compared|versus|whereas|\bwhile\b|followed|except|\bnot\b',prefix,re.I):continue
                numbers=[float(x) for x in re.findall(NUMBER,m['list'])]
                factor={'nM':.001,'µM':1,'μM':1,'uM':1,'mM':1000}.get(m['unit'])
                found.append({'values':numbers,'unit':m['unit'],'factor':factor,'block':b,'quote':clause.strip()})
    # Conflicting named lists are not resolved by choosing the first.
    signatures={(tuple(x['values']),x['unit']) for x in found}
    return found[:1] if len(signatures)==1 else []


def source_conflicts(scope,linked,sha,cap):
    from .atp_content_relations import _doses,_citation
    conflicts=[]
    # These are distinct co-substrates, not alternate spellings of one entity.
    for family in [('glutamine','glutamate')]:
        caption=[(name,d) for name in family for d in _doses(scope,[name])]
        for b in linked:
            prose=[(name,d) for name in family for d in _doses(b['text'],[name])]
            if len(caption)==len(prose)==1:
                a,da=caption[0];z,dz=prose[0]
                if a!=z and da['uM'] is not None and da['uM']==dz['uM'] and z in b.get('reference_sentence',b['text']).lower():
                    conflicts.append({'kind':'AUTHOR_SOURCE_CO_SUBSTRATE_IDENTITY_CONFLICT',
                        'caption_agent':a,'prose_agent':z,'caption_evidence':_citation(cap,sha,da['quote']),
                        'prose_evidence':_citation(b,sha,dz['quote']),'state':'HOLD_DO_NOT_RESOLVE_AS_ALIAS'})
    return conflicts


def enrich(candidate,scope,cap,linked,methods,sha,common=None):
    from .atp_content_relations import _citation,_field
    out=copy.deepcopy(candidate);f=out['fields'];names=out['compound_author_names']
    schedule=caption_schedule(scope,names)
    if schedule:
        ev=[_citation(cap,sha,scope)]
        f['total_compound_exposure_h']=_field(schedule['total_h'],ev,schedule['state'])
        out['treatment_stages']=[{**stage,'author_literal':scope,'evidence':ev} for stage in schedule['stages']]
        out['hold_reasons']=[h for h in out['hold_reasons'] if h not in ('TREATMENT_SEQUENCE_UNRESOLVED','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED')]
    if re.search(r'dose[- ]dependent|dose[- ]response',scope,re.I):
        out['hold_reasons'].append('DOSE_RESPONSE_SERIES_REQUIRES_EXPLICIT_DOSE_MAP')
    if re.search(r'difference.{0,100}ATP.{0,120}oligomycin|difference in ATP content',scope,re.I):
        out['hold_reasons'].append('ATP_COMPONENT_DIFFERENCE_NOT_SINGLE_DRUG_CONTROL_MEASUREMENT')
        out['endpoint_kind']='ATP_CONTENT_COMPONENT_DIFFERENCE_CANDIDATE'
    conflicts=source_conflicts(scope,linked,sha,cap)
    out['source_conflicts']=conflicts
    if conflicts:out['hold_reasons'].append('AUTHOR_SOURCE_CO_SUBSTRATE_IDENTITY_CONFLICT')
    if f['normalization']['value'] is None:
        out['hold_reasons'].append('NORMALIZATION_NOT_BOUND')
        if re.search(r'ATP.{0,35}protein',scope,re.I):
            out['hold_reasons'].append('PARALLEL_PROTEIN_MEASUREMENT_NOT_NORMALIZATION_PROOF')
    out['linked_author_prose']=[_citation(b,sha,b.get('reference_sentence',b['text'])) for b in linked]
    from .atp_content_common import apply_common
    apply_common(out,cap,scope,methods,sha,common)
    if out.get('panel_scope_state')=='AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS':
        # Only the Methods section dedicated to direct cellular ATP can supply
        # an exposure interval for both specifically mapped cell panels.
        # The neighbouring glucose-uptake 4h protocol is an explicit negative.
        from .atp_content_relations import CELL,TIME,_name_pattern,_hours
        named=_name_pattern(names)
        match_pattern=(rf'(?P<cell1>{CELL.pattern})\s+(?:adipocytes|myotubes|cells)\s+and\s+'
                       rf'(?P<cell2>{CELL.pattern})\s+(?:adipocytes|myotubes|cells)\s+'
                       rf'were\s+treated\s+with\s+(?P<drug>{named})\s+for\s+(?P<time>{TIME})')
        matched=[]
        for b in methods:
            heading=' '.join(b['headings'])
            if not re.search(r'\bATP\s+content\s+assay\b',heading,re.I):continue
            for m in re.finditer(match_pattern,b['text'],re.I):
                models={model_class(m['cell1']),model_class(m['cell2'])}
                if (len(models)!=2 or models!={model_class(x) for x in out.get('panel_cells_expected',())} or
                    not re.search(r'Total\s+ATP\s+content\s+was\s+measured\s+using\s+an?\s+ATP\s+colorimetric\s+assay',b['text'],re.I)):
                    continue
                matched.append((b,m))
        if len(matched)==1 and f['total_compound_exposure_h']['value'] is None:
            b,m=matched[0]
            evidence=[_citation(b,sha,m.group()),_citation(cap,sha,scope)]
            exposure=_hours(m['time'])
            f['total_compound_exposure_h']=_field(exposure,evidence,
                'EXPLICIT_ATP_ASSAY_METHOD_TWO_CELL_PANELS_SAME_COMPOUND_INTERVAL')
            assay=re.search(r'Total\s+ATP\s+content\s+was\s+measured\s+using\s+an?\s+ATP\s+colorimetric\s+assay\s+kit',b['text'],re.I)
            f['assay_method']=_field(assay.group(),[_citation(b,sha,assay.group())],
                'AUTHOR_DIRECT_ATP_ASSAY_FOR_BOTH_EXPLICIT_PANELS')
            out['treatment_stages']=[{'role':'COMPOUND_TREATMENT_FOR_DIRECT_ATP_ASSAY',
                'duration_h':exposure,'intervention':out['compound_name'],
                'author_literal':m.group(),'evidence':evidence}]
            out['hold_reasons']=[h for h in out['hold_reasons'] if h not in
                ('TREATMENT_SEQUENCE_UNRESOLVED','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED',
                 'ATP_METHOD_BINDING_UNRESOLVED')]
            out['hold_reasons'].append('AUTHOR_ATP_METHOD_SCOPE_SOURCE_SCREENED_NOT_GOLD')
    if out.get('panel_scope_state')=='AUTHOR_DIRECT_ATP_EXPLICIT_TWO_CELL_PANELS':
        # A figure's common multi-dose duration may be inherited as a duration,
        # but it cannot be inherited as one chosen dose or a matched control.
        # Require exact agreement with the dedicated direct-ATP Methods block,
        # including both cell identities, drug and all stated dose alternatives.
        from .atp_content_relations import CELL,TIME,_name_pattern,_hours
        wanted={model_class(x) for x in out['panel_cells_expected']}
        agent=_name_pattern(names)
        dose_pair=r'\d+(?:\.\d+)?\s+(?:or|and)\s+\d+(?:\.\d+)?\s*[µμu]M'
        figure_pattern=(rf'(?P<cell1>{CELL.pattern})\s+and\s+(?P<cell2>{CELL.pattern})\s+cells\s+'
                        rf'were\s+treated\s+with\s+{agent}\s*\(\s*(?P<doses>{dose_pair})'
                        rf'\s+for\s+(?P<duration>{TIME})\s*\)')
        figure_matches=[m for m in re.finditer(figure_pattern,cap['text'],re.I)
                        if {model_class(m['cell1']),model_class(m['cell2'])}==wanted]
        matching=[]
        if len(figure_matches)==1:
            fm=figure_matches[0]
            for b in methods:
                heading=' '.join(b['headings'])
                if not re.search(r'ATP\s+(?:content|levels?|assay)',heading,re.I):continue
                method_pattern=(rf'(?P<cell1>{CELL.pattern})\s+and\s+(?P<cell2>{CELL.pattern})\s+cells'
                                rf'.{{0,110}}?\bexposed\s+to\s+{agent}\s+at\s+'
                                rf'(?P<doses>{dose_pair})\s+for\s+(?P<duration>{TIME})')
                for mm in re.finditer(method_pattern,b['text'],re.I):
                    if ({model_class(mm['cell1']),model_class(mm['cell2'])}==wanted and
                            re.sub(r'\s+','',mm['doses']).casefold()==re.sub(r'\s+','',fm['doses']).casefold()
                            and _hours(mm['duration'])==_hours(fm['duration'])):
                        matching.append((b,mm))
            if len(matching)==1 and f['total_compound_exposure_h']['value'] is None:
                b,mm=matching[0]
                proof=[_citation(cap,sha,fm.group()),_citation(b,sha,mm.group())]
                exposure=_hours(fm['duration'])
                f['total_compound_exposure_h']=_field(
                    exposure,proof,'MATCHED_AUTHOR_FIGURE_AND_ATP_METHOD_MULTIDOSE_EXPOSURE')
                out['treatment_stages']=[{'role':'MULTIDOSE_COMPOUND_EXPOSURE',
                    'duration_h':exposure,'dose_series_author_literal':fm['doses'],
                    'dose_assignment_state':'HOLD_UNASSIGNED_MULTIDOSE_ARMS',
                    'author_literal':fm.group(),'evidence':proof}]
                out['hold_reasons']=[h for h in out['hold_reasons'] if h!='TREATMENT_SEQUENCE_UNRESOLVED']
                out['hold_reasons'].extend([
                    'MULTIDOSE_ATP_PANEL_ARM_ASSIGNMENT_UNRESOLVED',
                    'AUTHOR_ATP_METHOD_DURATION_SOURCE_SCREENED_NOT_GOLD'])
    if f['dose_original']['value'] is not None:return [out]
    # A figure explicitly reporting PRIMARY-screen activities can inherit an
    # explicitly uniform compound protocol. Dose-response and secondary-screen
    # panels cannot borrow it; named positive-control exceptions also block it.
    if re.search(r'primary screen',scope,re.I) and not re.search(r'dose[- ]dependent|dose[- ]response',scope,re.I):
        from .atp_content_relations import NUMBER,UNIT,TIME,_doses,_hours,CELL
        matches=[]
        for b in methods:
            if not re.search(r'screen', ' '.join(b['headings']),re.I):continue
            models={model_class(m.group()) for m in CELL.finditer(b['text'])}
            if models!={model_class(str(f['cell_model']['value']))}:continue
            primary=re.split(r'in the secondary screen',b['text'],maxsplit=1,flags=re.I)[0]
            if _doses(primary,names):continue  # named control or dose exception
            pattern=rf'(?:stimulated|treated)\s+with\s+(?P<dose>{NUMBER}\s*{UNIT})\s+compounds?\s+for\s+(?P<time>{TIME})'
            for m in re.finditer(pattern,primary):matches.append((b,m))
        if len(matches)==1:
            b,m=matches[0];dosematch=re.fullmatch(rf'({NUMBER})\s*({UNIT})',m['dose'])
            factor={'nM':.001,'µM':1,'μM':1,'uM':1,'mM':1000}.get(dosematch[2]);dose_uM=float(dosematch[1])*factor if factor is not None else None
            ev=[_citation(b,sha,m.group()),_citation(cap,sha,scope)]
            f['dose_original']=_field(m['dose'],ev,'EXPLICIT_UNIFORM_PRIMARY_SCREEN_MEMBERSHIP_CANDIDATE')
            f['dose_uM']=_field(dose_uM,ev,'EXPLICIT_UNIFORM_PRIMARY_SCREEN_MEMBERSHIP_CANDIDATE')
            f['treated_arm']=_field(out['compound_name']+' '+m['dose'],ev,'PRIMARY_SCREEN_MEMBERSHIP_CANDIDATE')
            f['total_compound_exposure_h']=_field(_hours(m['time']),ev,'EXPLICIT_UNIFORM_PRIMARY_SCREEN_MEMBERSHIP_CANDIDATE')
            out['treatment_stages']=[{'role':'PRIMARY_SCREEN_COMPOUND_TREATMENT','duration_h':_hours(m['time']),'author_literal':m.group(),'evidence':ev}]
            if 'DMSO negative control' in b['text']:
                f['comparator_arm']=_field('DMSO negative control',[_citation(b,sha,'DMSO negative control')],
                    'AUTHOR_PRIMARY_SCREEN_REFERENCE_NOT_RAW_CONTROL_VALUE')
                out['hold_reasons']=[h for h in out['hold_reasons'] if h!='MATCHED_COMPARATOR_UNRESOLVED']
                out['hold_reasons'].append('PRIMARY_SCREEN_COMPARATOR_REFERENCE_REQUIRES_REVIEW')
            out['primary_screen_protocol_candidate']=True
            out['hold_reasons']=[h for h in out['hold_reasons'] if h not in ('DOSE_BINDING_UNRESOLVED','TREATMENT_SEQUENCE_UNRESOLVED')]
            out['hold_reasons'].append('UNIFORM_SCREEN_PROTOCOL_REQUIRES_INDEPENDENT_REVIEW')
            return [out]
    lists=concentration_lists(methods,names,f['cell_model']['value'],f['total_compound_exposure_h']['value'])
    if not lists:return [out]
    data=lists[0];variants=[]
    for number in data['values']:
        child=copy.deepcopy(out);literal=f'{number:g} {data["unit"]}';ev=[_citation(data['block'],sha,data['quote'])]
        child['fields']['dose_original']=_field(literal,ev,'EXPLICIT_NAMED_DRUG_CONCENTRATION_LIST')
        child['fields']['dose_uM']=_field(number*data['factor'] if data['factor'] is not None else None,ev,'EXPLICIT_NAMED_DRUG_CONCENTRATION_LIST')
        child['fields']['treated_arm']=_field(f'{child["compound_name"]} {literal}',ev,'NAMED_DRUG_DOSE_CANDIDATE_COMPARATOR_UNRESOLVED')
        child['candidate_id']=child['candidate_id']+'-dose-'+str(number).replace('.','_')
        child['hold_reasons']=sorted(set(h for h in child['hold_reasons'] if h not in ('DOSE_BINDING_UNRESOLVED','DOSE_RESPONSE_SERIES_REQUIRES_EXPLICIT_DOSE_MAP')))
        variants.append(child)
    return variants
