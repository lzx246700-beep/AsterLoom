"""Native ATP numbers joined to table-local arms and original source cells.

No data labels, paper-specific conditions, or numeric reference answers are
embedded here. Only a unique control in the same metric/block can be paired.
"""
from __future__ import annotations
import hashlib
import json
import re

from .source_io import jats_tables, local, plain
from .atp_content_relations import _field, _citation, _names, _name_pattern, _doses, _hours, TIME

VALUE = re.compile(r'^\s*(?P<mean>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*'
                   r'(?:±\s*(?P<error>\d+(?:\.\d+)?))?\s*[a-zA-Z#*∗]*\s*$')
CONTROL = re.compile(r'^(?:control|vehicle(?: control)?|untreated(?: control)?)(?:\s*\(.*\))?$', re.I)


def _endpoint(text):
    if re.search(r'ATPase|ATP\s*/|ATP[- ]linked|\b(?:rate|synthesis|production)\b|/\s*min|\bmin[−-]1', text, re.I):
        return False
    return bool(re.fullmatch(
        r'(?:initial\s+|cellular\s+|intracellular\s+)?ATP'
        r'(?:\s+(?:content|levels?|concentration))?\s*(?:\(.*\))?',
        text.strip(), re.I))


def _unit(metric, headers):
    m=re.search(r'ATP(?:\s+(?:content|levels?|concentration))?\s*\((.*)\)',metric,re.I)
    if m:return m[1]
    # Preserve a merged original unit header verbatim; never convert units.
    found=[h for h in headers if re.search(r'(?:[nµμu]mol|mol)\b',h)]
    return found[0] if len(set(found))==1 else None


def _cover(cells, row, col):
    found=[c for c in cells if c['row']<=row<c['row']+c['rowspan'] and c['col']<=col<c['col']+c['colspan']]
    return found[0] if len(found)==1 else None


def _cell_map(wrap, table, index):
    paths={id(b['element']):b for b in index.values()}
    node=next(e for e in wrap if local(e.tag)=='table');rows=[]
    for e in node:
        if local(e.tag) in ('thead','tbody','tfoot'):
            rows.extend(x for x in e if local(x.tag)=='tr')
        elif local(e.tag)=='tr':rows.append(e)
    elements=[e for row in rows for e in row if local(e.tag) in ('td','th')]
    if len(elements)!=len(table['cells']):raise ValueError('ATP_NATIVE_CELL_GRID_MAPPING_HOLD')
    mapping={}
    for c,e in zip(table['cells'],elements):
        if c['raw']!=plain(e):raise ValueError('ATP_NATIVE_CELL_LITERAL_MAPPING_HOLD')
        mapping[(c['row'],c['col'])]=paths[id(e)]
    return mapping


def _model(caption):
    # Table-local biological material precedes any article-wide method model.
    patterns=[r'perfused livers of (?:fasted|fed) rats',r'(?:cultured\s+)?(?:primary\s+)?(?:rat\s+)?astrocytes',
              r'(?:RAW\s*264\.?7|H9C2|HepG2|SKOV3|T24|5637|Chub-S7)\s*(?:cells|macrophages)?',r'post-thaw\s+(?:bull\s+)?sperm']
    hits=[m.group() for p in patterns for m in re.finditer(p,caption,re.I)]
    return hits[0] if len(set(x.lower() for x in hits))==1 else None


def _reported_significance(point, control, foot, sha):
    """Bind a cell's star code only to one same-table control definition.

    A symbol without an explicit p-threshold and control comparator remains
    unresolved. No significance is inferred from numeric differences.
    """
    marker=re.search(r'(?P<marker>[*∗#]+)\s*$',point['source_literal'])
    if not marker or not foot or not CONTROL.fullmatch(control['author_group_literal'].strip()):
        return None
    symbol=marker['marker'].replace('∗','*')
    pattern=re.compile(
        rf'(?<![A-Za-z0-9*∗#]){re.escape(symbol)}(?![*∗#])\s*'
        rf'p\s*[<≤=>≥]\s*\d+(?:\.\d+)?'
        rf'(?:\s*(?:vs\.?|versus|compared\s+(?:to|with))\s+'
        rf'(?:the\s+)?(?P<target>[A-Za-z][A-Za-z-]*))?',re.I)
    definitions=list(pattern.finditer(foot['text']))
    if len(definitions)!=1 or (definitions[0].group('target') or '').casefold()!='control':return None
    match=definitions[0]
    return {'literal':match.group(),'evidence':_citation(foot,sha,match.group())}


_STATISTIC = re.compile(
    r'\bmeans?\s*±\s*(?P<kind>SD|SEM|SE|standard\s+deviation|standard\s+error'
    r'(?:\s+of\s+(?:the\s+)?mean)?)\b', re.I)
_N_VALUE = re.compile(r'\bn\s*=\s*(?P<n>\d+)\b', re.I)


def _table_statistical_summary(foot, sha):
    """Return table-level dispersion/n only when one footnote paragraph binds both.

    Paragraph boundaries matter: JATS flattens adjacent abbreviation paragraphs
    into strings such as ``n = 62-DG``. Parsing each source paragraph avoids
    interpreting that serialization artifact as n=62 while keeping ambiguous
    or multi-group sample descriptions unresolved.
    """
    if not foot:
        return None
    paragraphs = [plain(node) for node in foot['element'].iter()
                  if local(node.tag) == 'p' and plain(node).strip()]
    if not paragraphs and foot.get('text'):
        paragraphs = [foot['text']]
    qualifying = []
    for paragraph in paragraphs:
        stats = list(_STATISTIC.finditer(paragraph))
        ns = list(_N_VALUE.finditer(paragraph))
        if len(stats) == 1 and len(ns) == 1:
            qualifying.append((paragraph, stats[0], ns[0]))
    if len(qualifying) != 1:
        return None
    paragraph, statistic, sample_n = qualifying[0]
    # A citation is accepted only if the exact literal is replayable in the
    # normalized source block. The source anchor remains the table footnote.
    stat_literal = statistic.group()
    n_literal = sample_n.group()
    if stat_literal not in foot['text'] or n_literal not in foot['text']:
        return None
    raw_kind = statistic.group('kind').casefold()
    kind = ('SD' if raw_kind in ('sd', 'standard deviation') else
            'SEM' if raw_kind == 'sem' else
            'SE' if raw_kind == 'se' else 'STANDARD_ERROR_OF_MEAN')
    return {
        'uncertainty_kind': kind,
        'statistic_literal': stat_literal,
        'statistic_evidence': _citation(foot, sha, stat_literal),
        'n_value': int(sample_n.group('n')),
        'n_literal': n_literal,
        'n_evidence': _citation(foot, sha, n_literal),
        'scope': 'ONE_TABLE_FOOTNOTE_PARAGRAPH_UNIQUE_STATISTIC_AND_N',
    }


def extract_native(path, sha, compounds, root, index, paragraphs, protocol, paper):
    tables=jats_tables(path,sha);wraps=[e for e in root.iter() if local(e.tag)=='table-wrap']
    blocks={id(b['element']):b for b in index.values()};all_points=[];candidates=[]
    for table in tables:
        wrap=wraps[table['table_index']-1];mapping=_cell_map(wrap,table,index)
        caption=next((blocks[id(e)] for e in wrap if local(e.tag)=='caption'),None)
        foot=next((blocks[id(e)] for e in wrap if local(e.tag)=='table-wrap-foot'),None)
        unit_headers=[c for c in table['cells'] if c['is_header']]
        table_citations=[_citation(b,sha) for b in (caption,foot) if b and b['text']]
        statistical_summary = _table_statistical_summary(foot, sha)
        cap=table['caption'];points=[]
        for cell in table['cells']:
            number=VALUE.fullmatch(cell['raw'])
            if cell['is_header'] or not number:continue
            rowlabel=_cover(table['cells'],cell['row'],0)
            rowmetric=rowlabel['raw'] if rowlabel else ''
            columnmetrics=[h for h in cell['header_path'] if _endpoint(h)]
            if _endpoint(rowmetric) and cell['col']>0:
                metric=rowmetric;orientation='ROW';dimension=cell['row']
                group=' '.join(h for h in cell['header_path'] if not _endpoint(h))
                sources=[h for h in unit_headers if h['raw'] in cell['header_path']]
            elif len(columnmetrics)==1:
                metric=columnmetrics[0];orientation='COLUMN';dimension=cell['col']
                group=rowlabel['raw'] if rowlabel else ''
                sources=[rowlabel] if rowlabel else []
            else:continue
            originalgroup=group;group_span=None;dosevalue=None
            row_condition=None;row_condition_evidence=[];dose_header_conflict=False
            # In a metric row spanning several table rows, an adjacent explicit
            # dose column is a row condition. Keep it distinct from compound
            # labels in the column headers: those labels can describe another
            # assay block in the same table (or conflict with the caption).
            if orientation=='ROW' and cell['col']>1:
                condition_cell=_cover(table['cells'],cell['row'],1)
                if condition_cell:
                    dose_match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([µμu]M|nM|mM)\s*',condition_cell['raw'],re.I)
                    metric_cell=_cover(table['cells'],cell['row'],0)
                    if dose_match and metric_cell and metric_cell['rowspan']>1:
                        row_condition=condition_cell['raw'].strip()
                        factor={'nm':0.001,'µm':1,'μm':1,'um':1,'mm':1000}[dose_match[2].casefold()]
                        dosevalue=float(dose_match[1])*factor
                        row_condition_evidence=[_citation(mapping[(condition_cell['row'],condition_cell['col'])],sha)]
                        header_doses=[]
                        for header in cell['header_path']:
                            match=re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s*([µμu]M|nM|mM)\b',header,re.I)
                            if match:
                                header_factor={'nm':0.001,'µm':1,'μm':1,'um':1,'mm':1000}[match[2].casefold()]
                                header_doses.append(float(match[1])*header_factor)
                        if header_doses and any(value!=dosevalue for value in header_doses):
                            # A column heading may label a fixed-dose assay while
                            # the same table's ATP block has its own dose rows.
                            # Keep both literals and their evidence; do not pick
                            # either as the definitive numeric arm dose.
                            dose_header_conflict=True;dosevalue=None
            # Explicit rowspan, not forward-filling arbitrary missing labels.
            if orientation=='COLUMN' and rowlabel and rowlabel['rowspan']>1:
                group_span=[rowlabel['row'],rowlabel['col']]
                dosecell=_cover(table['cells'],cell['row'],1)
                doseheaders=dosecell['header_path'] if dosecell else []
                units=[m[1] for h in doseheaders if (m:=re.search(r'(?:concentrations?|dose)\s*\(([^)]+)\)',h,re.I))]
                if dosecell and len(units)==1 and re.fullmatch(r'\d+(?:\.\d+)?',dosecell['raw']):
                    dosevalue=float(dosecell['raw']);group=f'{group} ({dosecell["raw"]} {units[0]})';sources.append(dosecell)
                    sources.extend(h for h in unit_headers if h['raw'] in doseheaders and units[0] in h['raw'])
            unit=_unit(metric,cell['header_path'])
            metric_cells=[h for h in table['cells'] if h['raw']==metric and
                          (h is rowlabel if orientation=='ROW' else h['is_header'])]
            metric_cells.extend(h for h in unit_headers if unit and h['raw']==unit)
            metric_evidence=[_citation(mapping[(h['row'],h['col'])],sha) for h in metric_cells]
            point_id='ATP-NUM-'+hashlib.sha256(f'{sha}:{table["table_id"]}:{cell["row"]}:{cell["col"]}'.encode()).hexdigest()[:22]
            evid=[_citation(mapping[(cell['row'],cell['col'])],sha)]
            ge=[_citation(mapping[(g['row'],g['col'])],sha) for g in sources if (g['row'],g['col']) in mapping]
            relative_percent=bool(re.search(r'%\s*of\s*control',metric,re.I))
            evidence_state=('AUTHOR_RELATIVE_PERCENT_VALUE_REPLAYABLE_COMPARATOR_MEAN_NOT_REPORTED' if relative_percent
                            else 'AUTHOR_NUMERIC_CELL_REPLAYABLE_EXPERIMENT_BINDING_PENDING')
            if dose_header_conflict:
                evidence_state='AUTHOR_TABLE_ROW_DOSE_CONFLICTS_COLUMN_DOSE_CONTEXT_REVIEW'
            point={'point_id':point_id,'table_id':table['table_id'],'cell_row':cell['row'],'cell_column':cell['col'],
                'source_sha256':sha,'source_literal':cell['raw'],'numeric_value':float(number['mean']),
                'uncertainty':float(number['error']) if number['error'] else None,
                'uncertainty_kind':statistical_summary['uncertainty_kind'] if statistical_summary else None,
                'sample_size_n':statistical_summary['n_value'] if statistical_summary else None,
                'metric_author_literal':metric,'unit_author_literal':unit,'author_group_literal':group,
                'group_author_original':originalgroup,'orientation':orientation,'metric_dimension':dimension,
                'explicit_group_rowspan':group_span,'dose_column_value':dosevalue,
                'row_condition_author_literal':row_condition,'row_condition_evidence':row_condition_evidence,
                'relative_to_control':relative_percent,'dose_header_conflict':dose_header_conflict,
                'value_evidence':evid,'group_evidence':ge,'metric_evidence':metric_evidence,'table_evidence':table_citations,
                'evidence_state':evidence_state}
            points.append(point);all_points.append(point)
        for compound in sorted(set(n.lower() for n in compounds)):
            names,alias_ev=_names(compound,paragraphs,sha);name=_name_pattern(names)
            for pt in points:
                if not re.search(name,pt['author_group_literal'],re.I):continue
                if pt['dose_column_value']==0:continue
                group=pt['author_group_literal']
                if re.search(r'\+|\b(?:plus|and|hydrochloride|sulfate|conjugate|nanoparticle)\b',group,re.I):continue
                dos=_doses(group,names)
                if len({d['literal'] for d in dos})!=1:continue
                compatible=[q for q in points if q['point_id']!=pt['point_id'] and q['orientation']==pt['orientation'] and
                    q['metric_dimension']==pt['metric_dimension'] and q['unit_author_literal']==pt['unit_author_literal']]
                if pt['explicit_group_rowspan']:
                    controls=[q for q in compatible if q['explicit_group_rowspan']==pt['explicit_group_rowspan'] and q['dose_column_value']==0]
                elif re.search(r'experimental block|analyzed separately',table['footnotes'],re.I):
                    controls=[]  # No guessed boundary between independent screens.
                else:controls=[q for q in compatible if CONTROL.fullmatch(q['author_group_literal'].strip())]
                if len(controls)!=1:continue
                ctrl=controls[0];ev=table_citations;fields={k:_field() for k in ('compound','cell_model','dose_original','dose_uM',
                    'pretreatment_h','challenge_h','total_compound_exposure_h','assay_method','normalization','treated_arm','comparator_arm','effect_direction')}
                fields['compound']=_field(compound,[*alias_ev,*pt['group_evidence']])
                fields['dose_original']=_field(dos[0]['literal'],pt['group_evidence']);fields['dose_uM']=_field(dos[0]['uM'],pt['group_evidence'])
                fields['treated_arm']=_field(group,pt['group_evidence']);fields['comparator_arm']=_field(ctrl['author_group_literal'],ctrl['group_evidence'])
                # Same-table author note is authoritative for THIS numeric
                # metric and rows; unrelated article Methods are not.
                # Reject conflicts between caption and table note rather than
                # guessing which model belonged to the table.
                model_cap=_model(cap)
                model_foot=_model(foot['text']) if foot else None
                if model_cap and model_foot and model_cap.casefold()!=model_foot.casefold():
                    model=None
                    model_source=[]
                else:
                    model=model_cap or model_foot
                    model_source=[_citation(caption if model_cap else foot,sha,model)] if model else []
                fields['cell_model']=_field(model,model_source,
                    'EXPLICIT_SAME_TABLE_BIOLOGICAL_MODEL' if model else None)
                duration=None;time_ev=[]
                # "infused 20 min AFTER ..." denotes start time, not exposure.
                match=re.search(rf'{name}\s+(?:was\s+)?(?:incubated|infused|perfused|applied)\s+for\s+(?P<t>{TIME})',cap,re.I)
                if match:duration=_hours(match['t']);time_ev=ev
                else:
                    times=list(re.finditer(rf'\b(?:incubation|incubated|treatment|exposure)\s+for\s+(?P<t>{TIME})',cap,re.I))
                    if len(times)==1 and len(re.findall(TIME,cap,re.I))==1 and re.search(name,cap,re.I):
                        duration=_hours(times[0]['t']);time_ev=ev
                # Scope-safe table-wrap-foot schedule: exactly one time for
                # one explicitly named treatment list with the chosen
                # compound, a unique table-local cell model, and the author
                # word "respectively" spanning listed interventions.
                # Never take time from ATP extraction, assay reagent incubation
                # or a neighboring figure.
                if duration is None and foot and model:
                    schedules=list(re.finditer(
                        rf'(?P<model>\b[A-Za-z0-9-]+\b)\s+cells\s+were\s+'
                        rf'treated\s+with\s+(?P<arms>.{{1,380}}?)\s+for\s+'
                        rf'(?P<time>{TIME})\s*,\s*respectively\b',
                        foot['text'],re.I))
                    if len(schedules)==1:
                        match=schedules[0]
                        found_model,all_arms,interval=match['model'],match['arms'],match['time']
                        if (found_model.casefold() in model.casefold() and
                                re.search(name,all_arms,re.I) and
                                not re.search(r'(?<!\d)\.\s+[A-Z]',all_arms) and
                                len(re.findall(TIME,all_arms,re.I))==0 and
                                len(re.findall(TIME,foot['text'],re.I))==1 and
                                len(set(_hours(m) for m in re.findall(TIME,cap,re.I)))<=1):
                            duration=_hours(interval)
                            time_ev=[_citation(foot,sha,match.group())]
                fields['total_compound_exposure_h']=_field(duration,time_ev,
                    'EXPLICIT_SAME_TABLE_FOOTNOTE_COMPOUND_TREATMENT' if time_ev and foot and
                      any(e.get('source_anchor')==foot['path'] for e in time_ev) else None)
                stages=[{'role':'COMPOUND_TREATMENT','duration_h':duration,
                         'author_literal':time_ev[0]['quote'] if time_ev else cap,'evidence':time_ev}] if duration is not None else []
                # Direct table-caption method takes priority over article-wide
                # Methods containing ATPase, synthesis or converted nucleotides.
                if re.search(r'\bHPLC\b|high.performance liquid chromatography',cap,re.I):
                    fields['assay_method']=_field('HPLC',ev)
                else:
                    methods=[b for b in paragraphs if b['role']=='METHODS' and
                        re.search(r'\bATP\b.{0,45}(?:was\s+)?(?:determined|measured|quantified).{0,140}(?:lucifer|luminometric|luminescen)',b['text'],re.I) and
                        not re.search(r'ATPase|\b(?:convert|converted|conversion)\b',b['text'],re.I)]
                    if len(methods)==1:fields['assay_method']=_field(methods[0]['text'],[_citation(methods[0],sha)])
                    elif len(methods)==0:
                        # Source method for a DIRECT ATP absorbance endpoint.
                        # This clause must name ATP itself, not the neighbouring
                        # hexokinase/LDH spectrophotometric reaction, and the
                        # ATP Methods heading must be unique in this article.
                        direct=[]
                        for b in paragraphs:
                            if b['role']!='METHODS' or not re.search(
                                    r'\b(?:determination|measurement|detection)\b.{0,65}\bATP\b',
                                    ' '.join(b['headings']),re.I):
                                continue
                            match=re.search(
                                r'ATP\s+absorbance\s+values\s+were\s+measured\s+'
                                r'with\s+the\s+multi[- ]plate\s+reader\s+at\s+\d{3,4}\s*nm',
                                b['text'],re.I)
                            if match:direct.append((b,match))
                        if len(direct)==1:
                            b,match=direct[0]
                            fields['assay_method']=_field(match.group(),[_citation(b,sha,match.group())],
                                'AUTHOR_SAME_ATP_METHOD_DIRECT_ABSORBANCE')
                unit=pt['unit_author_literal']
                if unit and (re.search(r'protein',unit,re.I) or
                             re.search(r'/\s*(?:g|mg)\s*prot\b',unit,re.I)):
                    fields['normalization']=_field('PROTEIN',pt['metric_evidence'],
                        'EXPLICIT_ATP_METRIC_PROTEIN_NORMALIZATION_UNIT')
                elif unit and re.search(r'liver weight',unit,re.I):fields['normalization']=_field('LIVER_WEIGHT',pt['metric_evidence'])
                elif unit and re.search(r'/\s*mg',unit,re.I):
                    norms=[b for b in paragraphs if b['role']=='METHODS' and re.search(r'specific contents.{0,50}normaliz.{0,100}protein',b['text'],re.I)]
                    if len(norms)==1:fields['normalization']=_field('PROTEIN',[_citation(norms[0],sha)])
                significance=_reported_significance(pt,ctrl,foot,sha)
                holds=['INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED','TESTED_CHEMICAL_FORM_UNVERIFIED']
                for key,reason in [('cell_model','CELL_BINDING_UNRESOLVED'),('dose_uM','DOSE_BINDING_UNRESOLVED'),
                    ('total_compound_exposure_h','TOTAL_COMPOUND_EXPOSURE_UNRESOLVED'),('assay_method','ATP_METHOD_BINDING_UNRESOLVED'),('normalization','NORMALIZATION_UNRESOLVED')]:
                    if fields[key]['value'] is None:holds.append(reason)
                if unit is None:holds.append('AUTHOR_UNIT_UNRESOLVED')
                if not re.search(r'macrophage|RAW\s*264',str(fields['cell_model']['value']),re.I):holds.append('AUXILIARY_NON_MACROPHAGE_MODEL')
                native_id='ATP-PAIR-'+hashlib.sha256((pt['point_id']+ctrl['point_id']+compound+protocol).encode()).hexdigest()[:22]
                candidates.append({'candidate_id':native_id,'compound_name':compound,'compound_author_names':names,
                    'evidence_kind':'NATIVE_TABLE','figure_id':None,'figure_label':None,'panel':None,'table_id':table['table_id'],
                    'scope_evidence':ev[0] if ev else pt['value_evidence'][0],'assets':[],
                    'fields':fields,'treatment_stages':stages,'endpoint_kind':'ATP_CONTENT_CANDIDATE',
                    'treated_value':pt['numeric_value'],'control_value':ctrl['numeric_value'],'measurement_unit':unit,
                    'numeric_origin':'AUTHOR_TABLE_REPORTED','native_points':{'treated':pt,'control':ctrl},
                    'native_measurement_summary':statistical_summary,
                    'numeric_pair_evidence':pt['value_evidence']+ctrl['value_evidence']+pt['metric_evidence'],
                    'reported_significance':significance['literal'] if significance else None,
                    'reported_significance_evidence':significance['evidence'] if significance else None,
                    'uncertainty_evidence':ev,'review_tier':'Review-B','train_eligible':False,
                    'hold_reasons':sorted(set(holds)),'independent_gold':0,'training_approved':0})
    return {'candidates':candidates,'native_numeric_points':all_points}
