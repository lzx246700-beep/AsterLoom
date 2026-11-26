"""Bounded ATP experiment binding from immutable JATS, independent of LLMs.

Fields are proposals, not scientific truth. No graph estimates, blanket Methods
duration inheritance, cross-panel joins, or training approval. All locators use
indexed local-name XML paths; offsets refer to source_io.plain-normalized text.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .source_io import local, plain
from .atp_content_source_evidence import extract as source_evidence

PROTOCOL = 'asterloom.atp-content.relations/0.9'
PREVIOUS_PROTOCOL = 'asterloom.atp-content.relations/0.8'
ANCHOR_PROTOCOL = 'asterloom.atp-content.relations/0.7'
PANEL_PROTOCOL = 'asterloom.atp-content.relations/0.6'
LEGACY_PROTOCOL = 'asterloom.atp-content.relations/0.5'
NUMBER = r'\d+(?:\.\d+)?'
UNIT = r'(?:nM|[µμu]M|mM|mg/[mM][lL]|[µμu]g/[mM][lL]|ng/[mM][lL])'
TIME = rf'{NUMBER}\s*(?:h|hours?|min(?:ute)?s?|days?)\b'
ATP = re.compile(r'\bATP\b', re.I)
RATE = re.compile(r'ATP[- ]?linked|ATPase|ATP.{0,25}(?:production|generation)\s+rate|OCR[- ]derived', re.I)
CONTENT = re.compile(r'(?:intracellular|cellular|total)\s+ATP|ATP\s+(?:content|levels?|concentration)'
                     r'|ATP\s+stores?\b|intracellular\s+glucose,\s*ATP,', re.I)
CELL = re.compile(r'\b(?:RAW\s*264\.?7|AML12|3T3-L1|TM3|TM4|ELT3|NHDF|HaCaT|HGEpiC|OECM-1|Chub-S7|T24|5637|HepG2|H9c2|SH-SY5Y|Min6|THP-1|C2C12|HT22|HK-?2|1321N1|JIMT-1'
                  r'|(?:(?:MII|mature)\s+)?oocytes?'
                  r'|(?:differentiated\s+)?immortalized(?:\s+human)?\s+myotubes'
                  r'|(?:differentiated\s+)?primary(?:\s+human|\s+differentiated)?\s+myotubes)\b', re.I)
PANEL = re.compile(r'(?<!\w)\(([A-Za-z])\)\s+(?!(?:was|were|is|are)\b)', re.I)


def _calibrated_ctg_atp_content(scope: str, methods: list, sha: str):
    """Prove that a CellTiter-Glo signal was calibrated as ATP content.

    The product's viability branding is insufficient. Accept only when this
    ATP panel explicitly reports ATP content and one Methods paragraph both
    describes direct ATP measurement with CellTiter-Glo and an ATP standard
    curve. Explicit viability interpretation outside the product name keeps
    the endpoint on HOLD.
    """
    if not re.search(r'\bATP\s+(?:content|levels?|concentration|amount|stores?)\b',scope,re.I):
        return None
    product = re.compile(
        r'CellTiter[- ]Glo(?:®|™)?(?:\s+[A-Za-z-]+){0,4}\s+'
        r'Cell\s+Viability\s+Assay(?:\s+Kit)?', re.I)
    residual=product.sub('',scope)
    if re.search(r'\b(?:cell\s+)?viability\b|\bcell\s+survival\b',residual,re.I):
        return None
    matches=[]
    for method in methods:
        text=method['text']
        direct=re.search(
            r'(?P<literal>Cell\s+ATP\s+content\s+was\s+'
            r'(?:determined|measured|quantified)\s+.{0,180}?CellTiter[- ]Glo)',text,re.I)
        standard=re.search(
            r'(?P<literal>(?:a|the)\s+standard\s+curve\s+was\s+'
            r'(?:prepared|generated|constructed)\s+from\s+ATP'
            r'(?:\s*\([^)]*\))?)',text,re.I)
        if direct and standard and direct.start()<standard.start():
            matches.append((method,direct,standard))
    if len(matches)!=1:
        return None
    method,direct,standard=matches[0]
    return {
        'status':'AUTHOR_DIRECT_ATP_CONTENT_CALIBRATED_WITH_ATP_STANDARD_CURVE',
        'assay_product_author_literal':direct['literal'],
        'assay_evidence':_citation(method,sha,direct['literal']),
        'calibration_author_literal':standard['literal'],
        'calibration_evidence':_citation(method,sha,standard['literal']),
        'review_required':True,
        'scientific_approval':False,
        'training_approved':False,
    }


def _viability_is_endpoint(scope: str, methods=(), sha=None) -> bool:
    """Reject viability proxies unless the author calibrated the ATP pool.

    A branded CellTiter-Glo kit is not itself an ATP-content measurement;
    explicit same-method ATP calibration is required to override that default.
    """
    if re.search(r'CellTiter[- ]Glo', scope, re.I):
        return _calibrated_ctg_atp_content(scope,methods,sha) is None
    product = re.search(
        r'ATP[- ]?Glo(?:™|®)?\s+(?:\w+\s+){0,3}Cell\s+Viability\s+Assay\s+Kit',
        scope, re.I)
    amount_measured = re.search(
        r'\bATP\s+(?:content|levels?|concentration|amount)\b.{0,100}'
        r'\b(?:measured|determined|quantified|assayed)\b', scope, re.I)
    if product and amount_measured:
        residual = scope[:product.start()] + scope[product.end():]
        # The product's marketing name is the only viability mention. An
        # independent author statement that this was a viability endpoint
        # remains a fail-closed exclusion.
        if not re.search(r'\b(?:cell\s+)?viability\b|\bcell\s+survival\b', residual, re.I):
            return False
    return bool(re.search(r'CellTiter[- ]Glo|cell\s+viability', scope, re.I))


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _field(value=None, evidence=(), state=None):
    return {'value': value, 'state': state or ('SOURCE_BOUND_CANDIDATE' if value is not None
            else 'SOFTWARE_UNRESOLVED_NOT_AUTHOR_NONDISCLOSURE'), 'evidence': list(evidence)}


def _index(root):
    index = {}
    def walk(e, path, role='UNRESOLVED', headings=(), excluded=False):
        kind = local(e.tag)
        if kind == 'sec':
            title = next((plain(x) for x in e if local(x.tag) == 'title'), '')
            headings = (*headings, title)
            if re.search(r'\b(methods?|experimental|protocol)\b', title, re.I):
                role = 'METHODS'
            elif re.search(r'\bresults?\b', title, re.I):
                role = 'RESULTS'
            elif re.search(r'\b(discussion|introduction|conclusions?)\b', title, re.I):
                role = 'CONTEXT'
        excluded = excluded or kind in ('ref-list', 'abstract', 'back', 'table-wrap', 'fig')
        index[path] = {'element': e, 'text': plain(e), 'path': path, 'role': role,
                       'headings': headings, 'excluded': excluded}
        counts = {}
        for child in e:
            k = local(child.tag); counts[k] = counts.get(k, 0) + 1
            walk(child, f'{path}/{k}[{counts[k]}]', role, headings, excluded)
    walk(root, '/article')
    return index


def _body_paragraphs(index):
    """Read actual prose around nested figures/tables without caption leakage.

    Each fragment keeps its original paragraph locator and exact offset in
    source_io.plain(parent). Repeated caption/tail text cannot steal offsets.
    """
    output=[]
    for block in index.values():
        element=block['element']
        if local(element.tag)!='p' or block['excluded']:continue
        if not any(local(x.tag) in ('fig','table-wrap') for x in element.iter()):
            output.append(block);continue
        raw=''.join(element.itertext());position=0;fragments=[{'start':0,'parts':[]}]
        def add(text):
            nonlocal position
            if text:fragments[-1]['parts'].append(text);position+=len(text)
        def visit(e):
            nonlocal position
            add(e.text)
            for child in e:
                if local(child.tag) in ('fig','table-wrap'):
                    position+=len(''.join(child.itertext()))
                    fragments.append({'start':position,'parts':[]})
                else:visit(child)
                add(child.tail)
        visit(element)
        if position!=len(raw):raise ValueError('ATP_PROSE_RAW_OFFSET_HOLD')
        for fragment in fragments:
            literal=''.join(fragment['parts']);text=' '.join(literal.split())
            if not text:continue
            start=fragment['start']+len(literal)-len(literal.lstrip())
            prefix=raw[:start];offset=len(' '.join(prefix.split()))
            if prefix.strip() and prefix[-1:].isspace():offset+=1
            if block['text'][offset:offset+len(text)]!=text:raise ValueError('ATP_PROSE_NORMALIZED_OFFSET_HOLD')
            output.append({**block,'text':text,'source_text':block['text'],'source_offset':offset,
                           'scope_state':'AUTHOR_PROSE_FRAGMENT_EXCLUDES_NESTED_ASSET'})
    return output


def _citation(block, sha, quote=None, local_start=None):
    quote = quote or block['text']
    if local_start is None:
        scope=block.get('citation_scope_text')
        if scope is not None and quote in scope:
            local_start=block['citation_scope_start']+scope.index(quote)
        else:local_start=block['text'].index(quote)
    if block['text'][local_start:local_start+len(quote)]!=quote:raise ValueError('ATP_CITATION_LOCAL_SPAN_HOLD')
    start = block.get('source_offset',0)+local_start
    if block.get('source_text',block['text'])[start:start+len(quote)]!=quote:
        raise ValueError('ATP_CITATION_FRAGMENT_OFFSET_HOLD')
    return {'source_sha256': sha, 'source_anchor': block['path'],
            'quote': quote, 'start': start, 'end': start + len(quote),
            'offset_basis': 'source_io.plain normalized Unicode characters'}


_ALIAS_STOP = frozenset('ATP ADP AMP OCR ECAR MMP ROS SD SEM SE CI PBS DMSO FBS BSA '
    'EDTA SDS PCR RT WT KO NS ANOVA ELISA HPLC LC MS NMR IR UV OD AUC LPS TNF IL '
    'MRNA DNA RNA SIRNA CDNA GFP RFP FACS BCA DMEM RPMI'.split())


def _names(compound, paragraphs, sha,allow_spaced=False):
    """Only an explicit author expansion supplies an acronym; never guess TYP."""
    names = {compound}; evidence = []
    # Parenthesized author acronyms may carry a purity note, e.g.
    # Genistein (GEN; ≥98% purity). Keep the entire source declaration.
    # A digit-leading abbreviation such as 2-DG is still an author acronym.
    pattern = re.compile(r'(?<!\w)' + re.escape(compound) +
        r"\s*\(([A-Z0-9][A-Z0-9′’'-]{1,12})(?:\s*;\s*[^)]{1,65})?\)", re.I)
    # Author abbreviation lists may put the acronym first, as in
    # "SPD: spermidine" or the table note "2-DG 2-deoxy-D-glucose". The
    # acronym is matched case-sensitively so ordinary prose such as
    # "control spermidine" cannot become an alias.
    reverse = re.compile(r'(?<![\w@-])([A-Z0-9][A-Z0-9′’\'-]{1,11})\s*[:,]?\s*'
                         r'(?i:' + re.escape(compound) + r')(?![\w@-])')
    for b in paragraphs:
        for match in pattern.finditer(b['text']):
            acronym = match.group(1)
            if not re.search(r'[A-Za-z]', acronym): continue
            if acronym.upper() == acronym or (acronym[0].isupper() and len(acronym)<=4):
                names.add(acronym); evidence.append(_citation(b, sha, match.group()))
        for match in reverse.finditer(b['text']):
            acronym = match.group(1)
            if not re.search(r'[A-Za-z]', acronym) or acronym.upper() in _ALIAS_STOP: continue
            names.add(acronym); evidence.append(_citation(b, sha, match.group()))
        if allow_spaced:
            spaced=re.compile(r'(?<!\w)'+re.escape(compound)+r'\s*\(([A-Za-z]{1,4}\s+[A-Z])\)',re.I)
            for match in spaced.finditer(b['text']):
                if re.fullmatch(r'[A-Z][A-Za-z]{0,3}\s+[A-Z]',match[1]):
                    names.add(match[1]);evidence.append(_citation(b,sha,match.group()))
    return sorted(names, key=lambda n: (-len(n), n)), evidence


def _name_pattern(names, protect_formulation=True):
    # Avoid a parent name inside a salt, conjugate, or delivery-system identity.
    boundary=r'[\w@-]' if protect_formulation else r'[\w-]'
    return r'(?<!'+boundary+r')(?:' + '|'.join(re.escape(n) for n in names) + r')(?!'+boundary+r')'


def _doses(text, names,literal_molar=False):
    name = _name_pattern(names)
    units=rf'(?:{UNIT}|[µμu]mol/[lL]|nmol/[lL]|mmol/[lL])' if literal_molar else UNIT
    patterns = [rf'(?P<agent>{name})\s*\(\s*(?P<dose>{NUMBER}\s*{units})\s*\)',
                rf'(?P<dose>{NUMBER}\s*{units})\s+(?P<agent>{name})']
    found = []
    for pattern in patterns:
        # Names case-insensitive, dose units explicitly case-sensitive.
        pattern = pattern.replace(name, '(?i:' + name + ')')
        for m in re.finditer(pattern, text):
            d = re.fullmatch(rf'({NUMBER})\s*({units})', m.group('dose'))
            factor = {'nM': .001, 'µM': 1, 'μM': 1, 'uM': 1, 'mM': 1000}.get(d[2])
            if literal_molar and factor is None:
                factor=.001 if re.fullmatch(r'nmol/[lL]',d[2]) else 1000 if re.fullmatch(r'mmol/[lL]',d[2]) else 1 if re.fullmatch(r'[µμu]mol/[lL]',d[2]) else None
            found.append({'literal': m.group('dose'), 'uM': float(d[1]) * factor if factor else None,
                          'quote': m.group(), 'agent': m.group('agent')})
    return found


def _hours(text):
    m = re.match(rf'({NUMBER})\s*(\w+)', text)
    unit = m[2].lower()
    return float(m[1]) * (24 if unit.startswith('day') else 1/60 if unit.startswith('min') else 1)


def _legacy_panels(caption,anchored=False):
    """Panel labels delimit local scope, including lowercase clause openings.

    Mid-sentence 'ATP content (B) was checked' labels the measured panel; it
    does not sever the preceding shared protocol from that assay.
    """
    text = caption['text']; marks = list(PANEL.finditer(text))
    if not marks:
        m = re.search(r'(?:intracellular\s+)?ATP\s+(?:content|levels?)\s*\(([A-Za-z])\)', text, re.I)
        meta=caption
        if anchored:
            meta={**caption,'scope_start':0}
            if m:meta.update(panel_label_literal='('+m[1]+')',panel_label_start=m.start(1)-1)
        return [(m[1].upper() if m else None, text, meta)]
    output = []
    for n, mark in enumerate(marks):
        end = marks[n+1].start() if n+1 < len(marks) else len(text)
        chunk=text[mark.end():end];meta=caption
        if anchored:meta={**caption,'scope_start':mark.end()+len(chunk)-len(chunk.lstrip()),
            'panel_label_literal':mark.group().strip(),'panel_label_start':mark.start()}
        output.append((mark[1].upper(), chunk.strip(), meta))
    return output


def _panels(caption,legacy=False,anchored=True):
    """Explicit postfix labels own preceding descriptions, prefix labels next.

    Mixed/duplicate placement is not resolved by guessing. Inline continuation
    captions keep their existing shared-protocol rule; no new dose inheritance.
    """
    old=_legacy_panels(caption,anchored=anchored and not legacy)
    if legacy:return old
    text=caption['text']
    # Some captions group panel labels and then enumerate distinct endpoints,
    # e.g. "(C,D) ROS and ATP levels in RAW264.7 cells". A single-label
    # tokenizer otherwise lets the preceding (B) scope swallow the grouped
    # clause and can falsely associate an earlier Figure 6B Results reference
    # with ATP. Split only an explicit two-label/two-endpoint construction
    # where exactly one endpoint is direct ATP content and the other is a
    # recognized distinct measurement. Ambiguous lists remain on the legacy
    # source inventory path.
    grouped=re.search(r'\((?P<labels>[A-J]\s*,\s*[A-J])\)\s*(?P<body>.*?)(?=\.(?:\s|$))',text,re.I)
    if grouped:
        labels=[x.strip().upper() for x in grouped['labels'].split(',')]
        clauses=re.fullmatch(r'\s*(?P<left>[^,;]+?)\s+and\s+(?P<right>[^,;]+?)\s*',grouped['body'])
        if len(labels)==2 and labels[0]!=labels[1] and clauses:
            left,right=clauses['left'],clauses['right']
            atp_pattern=r'\b(?:intracellular|cellular|total)?\s*ATP\s+(?:content|levels?|concentration|stores?)\b'
            left_atp=bool(re.search(atp_pattern,left,re.I));right_atp=bool(re.search(atp_pattern,right,re.I))
            other=right if left_atp and not right_atp else left if right_atp and not left_atp else ''
            distinct_endpoint=bool(re.search(
                r'\b(?:ROS|reactive oxygen species|cell(?:ular)? viability|viability|'
                r'cell survival|membrane potential|OCR|ECAR|ATPase)\b',other,re.I))
            model_matches=list(CELL.finditer(grouped['body']))
            model_classes={m.group().casefold() for m in model_matches}
            if (left_atp != right_atp and distinct_endpoint and len(model_classes)==1):
                result=[]
                group_letter_offsets={labels[0]:grouped.start('labels'),
                                      labels[1]:grouped.start('labels')+grouped['labels'].upper().find(labels[1])}
                for idx,(panel,clause) in enumerate(zip(labels,(left,right))):
                    clause_key='left' if idx==0 else 'right'
                    # Keep the exact author clause as scope. The shared model
                    # remains separately anchored in the grouped source span.
                    meta={**caption,'panel_scope_state':'AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS',
                          'panel_cell_model':model_matches[0].group(),
                          'panel_cell_quote':model_matches[0].group(),
                          'panel_endpoint_literal':clause,
                          'panel_endpoint_start':grouped.start('body')+clauses.start(clause_key)}
                    if anchored:
                        meta.update(scope_start=grouped.start('body')+clauses.start(clause_key),
                                    panel_label_literal=panel,
                                    panel_label_start=group_letter_offsets[panel])
                    result.append((panel,clause,meta))
                # Keep independent scopes before and after this grouped label.
                # Truncate the preceding scope at the grouped-label boundary so
                # it cannot inherit the ATP endpoint from a later panel pair.
                retained=[]
                for panel,scope,meta in old:
                    label_start=meta.get('panel_label_start')
                    if label_start is not None and label_start>=grouped.start():
                        retained.append((panel,scope,meta));continue
                    scope_start=meta.get('scope_start')
                    if scope_start is not None and scope_start<grouped.start():
                        end=min(grouped.start(),scope_start+len(scope))
                        truncated=text[scope_start:end].strip()
                        retained.append((panel,truncated,meta))
                    else:
                        retained.append((panel,scope,meta))
                retained.extend(result)
                return sorted(retained,key=lambda item:(
                    item[2].get('panel_label_start',len(text)),
                    item[0]))
    # A figure heading can define a direct ATP endpoint while the panels
    # identify two ordered culture conditions. Share only the endpoint and
    # the unique heading model; retain each panel's own literal as a separate
    # scope. Never share intervention dose, exposure, or comparison arms.
    heading_endpoint = re.match(
        rf'\s*(?P<heading>(?:Effects?\s+on\s+)?ATP\s+(?:content|levels?|concentration|stores?)\b'
        rf'.{{0,180}}?\b(?P<cell>{CELL.pattern})\s+cells?\b'
        rf'.{{0,100}}?\b(?P<condition1>glucose|galactose)\s+and\s+'
        rf'(?P<condition2>glucose|galactose)\s+medium\b[^.]*)\.', text, re.I)
    marks = list(PANEL.finditer(text))
    if (heading_endpoint and len(marks) == 2
            and heading_endpoint['condition1'].casefold() != heading_endpoint['condition2'].casefold()
            and not re.search(r'\b(?:OCR|ECAR|ATPase|viability|membrane\s+potential)\b', text, re.I)):
        result=[]
        expected=(heading_endpoint['condition1'].casefold(), heading_endpoint['condition2'].casefold())
        for idx, mark in enumerate(marks):
            end=marks[idx+1].start() if idx+1<len(marks) else len(text)
            raw_chunk=text[mark.end():end]
            chunk=raw_chunk.strip()
            condition=re.match(r'(glucose|galactose)\b',chunk,re.I)
            if not condition or condition[1].casefold()!=expected[idx]:
                break
            model=CELL.search(heading_endpoint['cell'])
            if model is None:
                break
            scope=chunk
            meta={**caption,'panel_scope_state':'AUTHOR_FIGURE_LEVEL_ATP_ENDPOINT_SHARED_ACROSS_CONDITION_PANELS',
                  'panel_cell_model':model.group(),'panel_cell_quote':heading_endpoint['cell'],
                  'panel_endpoint_literal':heading_endpoint['heading'],
                  'panel_endpoint_start':heading_endpoint.start('heading'),
                  'panel_condition_literal':condition.group(),'panel_condition_quote':mark.group().strip()+' '+condition.group(),
                  'panel_conditions_expected':expected}
            if anchored:
                meta.update(scope_start=mark.end()+len(raw_chunk)-len(raw_chunk.lstrip()),
                            panel_label_literal=mark.group().strip(),
                            panel_label_start=mark.start())
            result.append((mark[1].upper(),scope,meta))
        if len(result)==2:
            return result
    # A publisher caption may place the endpoint phrase before its panel-owned
    # cell models ("ATP level of (E) cell-A and (F) cell-B after ..."). The
    # preceding panel then inherits a dangling "ATP level of" token, while
    # the true E/F local slices contain no endpoint word. Split the two named
    # owners explicitly and mark only the dangling preceding scope ambiguous.
    shared_atp_cells=re.search(
        rf'(?P<statement>ATP\s+(?:content|levels?|concentration)\s+of\s+'
        rf'\((?P<panel1>[A-J])\)\s*(?P<cell1>{CELL.pattern}(?:-(?:MOCK|APP695))?)\s+cells?\s+'
        rf'and\s+\((?P<panel2>[A-J])\)\s*'
        rf'(?P<cell2>{CELL.pattern}(?:-(?:MOCK|APP695))?)\s+cells?\s+'
        rf'(?P<tail>.{{0,260}}?)(?:\.(?=\s|$)|$))',text,re.I)
    if shared_atp_cells and shared_atp_cells['panel1'].upper()!=shared_atp_cells['panel2'].upper():
        result=[]
        for panel,scope,meta in old:
            if re.search(r'ATP\s+(?:content|levels?|concentration)\s+of\s*$',scope,re.I):
                result.append((panel,scope,{**caption,'panel_scope_state':'PANEL_LABEL_PLACEMENT_AMBIGUOUS'}))
            else:
                result.append((panel,scope,meta))
        for idx in (1,2):
            model=CELL.search(shared_atp_cells[f'cell{idx}'])
            if model is None:raise ValueError('ATP_SHARED_PANEL_MODEL_LITERAL_HOLD')
            author_model=shared_atp_cells[f'cell{idx}']
            panel=shared_atp_cells[f'panel{idx}'].upper()
            meta={**caption,'panel_scope_state':'AUTHOR_SHARED_ATP_ENDPOINT_DISTINCT_MODEL_PANELS',
                  'panel_cell_model':author_model,
                  'panel_cells_expected':(shared_atp_cells['cell1'],shared_atp_cells['cell2']),
                  'panel_cell_quote':f"({shared_atp_cells[f'panel{idx}']}) {author_model} cells"}
            if anchored:
                meta.update(scope_start=shared_atp_cells.start('statement'),
                            panel_label_literal=f"({shared_atp_cells[f'panel{idx}']})",
                            panel_label_start=shared_atp_cells.start(f'panel{idx}')-1)
            result.append((panel,shared_atp_cells['statement'],meta))
        return result
    # Some captions assign cell models with postfix panel markers before the
    # shared treatment/method description: "1321N1 cells (A) and JIMT-1 cells
    # (B) ... ATP content ...". Split only when exactly two recognized,
    # distinct models own distinct panel labels in one ATP caption.
    cell_name_pattern=CELL.pattern[:-2]  # remove terminal word boundary for e.g. "JIMT-1cells"
    cell_label_pattern=rf'(?P<cell>{cell_name_pattern})(?=\s*(?:cells?)?\s*\([A-J]\))\s*(?:cells?)?\s*\((?P<panel>[A-J])\)'
    cell_labels=list(re.finditer(cell_label_pattern,text,re.I))
    unowned_models=[m for m in CELL.finditer(text)
                    if m.group().casefold() not in {x['cell'].casefold() for x in cell_labels}]
    if (ATP.search(text) and len(cell_labels)==2
            and cell_labels[0]['panel'].upper()!=cell_labels[1]['panel'].upper()
            and cell_labels[0]['cell'].casefold()!=cell_labels[1]['cell'].casefold()
            and not unowned_models):
        result=[]
        for match in cell_labels:
            panel=match['panel'].upper(); model=CELL.search(match['cell'])
            if model is None: raise ValueError('ATP_PANEL_MODEL_LITERAL_HOLD')
            meta={**caption,'panel_scope_state':'AUTHOR_MODEL_LABELS_MAP_DISTINCT_PANELS',
                  'panel_cell_model':model.group(),
                  'panel_cells_expected':tuple(x['cell'] for x in cell_labels),
                  'panel_cell_quote':match.group()}
            if anchored:
                meta.update(scope_start=0,panel_label_literal=f'({match["panel"]})',
                            panel_label_start=match.start('panel')-1)
            result.append((panel,text,meta))
        return result
    # Explicit same-endpoint, two-cell figure ownership: "ATP content of
    # cell-A (I) and cell-B (J) was determined using an ATP ... assay kit".
    # The surrounding 4h glucose-uptake panels are NOT in this assay scope.
    cell_literal=rf'{CELL.pattern}(?:\s+(?:adipocytes|myotubes|cells))?'
    pair=re.search(rf'(?P<statement>ATP\s+content\s+of\s+'
                   rf'(?P<cell1>{cell_literal})\s*\((?P<panel1>[A-J])\)\s+and\s+'
                   rf'(?P<cell2>{cell_literal})\s*\((?P<panel2>[A-J])\)\s+'
                   rf'was\s+(?:determined|measured)\s+using\s+an?\s+ATP\s+'
                   rf'(?:colorimetric|calorimetric|luminescent|bioluminescent)\s+assay\s+kit\.)',
                   text,re.I)
    if pair and pair['panel1'].upper()!=pair['panel2'].upper():
        result=[]
        for idx in (1,2):
            panel=pair[f'panel{idx}'].upper()
            cell=pair[f'cell{idx}']
            model=CELL.search(cell)
            assert model is not None
            meta={**caption,
                  'panel_scope_state':'AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS',
                  'panel_cell_model':model.group(),
                  'panel_cells_expected':tuple(sorted(CELL.search(pair[f'cell{j}']).group() for j in (1,2))),
                  'panel_cell_quote':f'{cell} ({pair[f"panel{idx}"]})'}
            if anchored:
                meta.update(scope_start=pair.start('statement'),
                  panel_label_literal=f'({pair[f"panel{idx}"]})',
                  panel_label_start=pair.start(f'panel{idx}')-1)
            result.append((panel,pair['statement'],meta))
        return result
    # "ATP levels were measured in (C) TM4 and (D) ELT3 cells" and
    # "(C) TM4-ATP levels, and (D) ELT3-ATP levels" explicitly own
    # each ATP panel. Adjacent JC-1 membrane-potential panels do not.
    name_cell=CELL.pattern
    source_patterns=[
        rf'(?P<statement>(?:intracellular|cellular|total)\s+ATP\s+levels?\s+'
        rf'were\s+measured\s+in\s+\((?P<panel1>[A-J])\)\s*'
        rf'(?P<cell1>{name_cell})\s+and\s+\((?P<panel2>[A-J])\)\s*'
        rf'(?P<cell2>{name_cell})\s+cells?)',
        rf'(?P<statement>\((?P<panel1>[A-J])\)\s*(?P<cell1>{name_cell})'
        rf'[-–‐]ATP\s+levels?\s*,?\s+and\s+\((?P<panel2>[A-J])\)\s*'
        rf'(?P<cell2>{name_cell})[-–‐]ATP\s+levels?)'
    ]
    owners=[m for patt in source_patterns for m in re.finditer(patt,text,re.I)]
    if len(owners)==1:
        match=owners[0]
        if match['panel1'].upper()!=match['panel2'].upper() and                 match['cell1'].casefold()!=match['cell2'].casefold():
            result=[]
            for idx in (1,2):
                panel=match[f'panel{idx}'].upper()
                cell=match[f'cell{idx}']
                original=match.group()
                marker=re.search(r'\('+re.escape(match[f'panel{idx}'])+r'\)\s*'+re.escape(cell),original)
                if marker is None:
                    raise ValueError('ATP_PANEL_CELL_OWNER_SOURCE_MATCH_HOLD')
                # Source quote and anchor must resolve to this same caption.
                label_index=match.start('statement')+marker.start()
                meta={**caption,
                    'panel_scope_state':'AUTHOR_DIRECT_ATP_EXPLICIT_TWO_CELL_PANELS',
                    'panel_cell_model':cell,
                    'panel_cells_expected':(match['cell1'],match['cell2']),
                    'panel_cell_quote':text[label_index:label_index+len(marker.group())]}
                if anchored:
                    meta.update(scope_start=match.start('statement'),
                        panel_label_literal='('+match[f'panel{idx}']+')',
                        panel_label_start=label_index)
                result.append((panel,match['statement'],meta))
            return result
    suffix=list(re.finditer(r'\bATP\s+(?:content|levels?|concentration)\s*\(([A-Za-z])\)',text,re.I))
    if not suffix:return old
    raw=list(re.finditer(r'(?<!\w)\(([A-Za-z](?:\s*,\s*[A-Za-z])*)\)',text))
    labels=[label.strip().upper() for m in raw for label in m[1].split(',')]
    postfix=bool(raw) and len(labels)==len(set(labels)) and all(
        text[:m.start()].rstrip() and text[:m.start()].rstrip()[-1] not in '.;:!?'
        for m in raw)
    # "ATP content (B) was measured" with no prefix-style delimiters already
    # has explicit inline ownership and may have a common treatment protocol.
    if (postfix and not list(PANEL.finditer(text)) and
        all(re.match(r'\s+(?:was|were|is|are)\b',text[m.end():],re.I) for m in raw)):return old
    if postfix:
        result=[];start=0
        for mark in raw:
            chunk=text[start:mark.start()];scope=chunk.strip()
            for label in mark[1].split(','):
                meta={**caption,'panel_scope_state':'AUTHOR_POSTFIX_LABEL_BOUNDARY',
                    'postfix_preamble':text[:raw[0].start()].strip()}
                if anchored:meta.update(scope_start=start+len(chunk)-len(chunk.lstrip()),
                    panel_label_literal=mark.group(),panel_label_start=mark.start())
                result.append((label.strip().upper(),scope,meta))
            start=mark.end()
        return result
    # An apparent prefix owner cannot overrule an explicit different postfix
    # owner. Retain the figure in inventory for adjudication, without conditions.
    result=[]
    for panel,scope,meta in old:
        conflicts=[s for s in suffix if panel!=s[1].upper() and s.group() in scope]
        if conflicts:result.append((None,scope,{**caption,'panel_scope_state':'PANEL_LABEL_PLACEMENT_AMBIGUOUS'}))
        else:result.append((panel,scope,meta))
    return result


def extract_relations(source_path, expected_sha256, compounds,protocol=PROTOCOL,
                      supplementary_sources=None):
    if protocol not in (PROTOCOL,PREVIOUS_PROTOCOL,ANCHOR_PROTOCOL,PANEL_PROTOCOL,LEGACY_PROTOCOL):raise ValueError('ATP_RELATION_PROTOCOL_VERSION_HOLD')
    # The request manifest is canonicalized in the output; candidate ordering
    # must use that same canonical order so source replay is permutation-safe.
    compounds=sorted(set(compounds))
    current=protocol!=LEGACY_PROTOCOL
    anchored=protocol in (PROTOCOL,PREVIOUS_PROTOCOL,ANCHOR_PROTOCOL)
    named_methods=protocol in (PROTOCOL,PREVIOUS_PROTOCOL)
    if not expected_sha256:
        raise ValueError('ATP_EXPECTED_SHA256_REQUIRED')
    if Path(source_path).suffix.casefold() in ('.html', '.htm'):
        if protocol != PROTOCOL:raise ValueError('PMC_HTML_CURRENT_PROTOCOL_REQUIRED')
        if supplementary_sources:
            raise ValueError('ATP_SUPPLEMENTARY_DOCX_CURRENTLY_REQUIRES_AUTHOR_JATS')
        from .atp_content_pmc_html import extract_pmc_html_relations
        return extract_pmc_html_relations(source_path,expected_sha256,compounds,protocol)
    source = source_evidence(source_path, expected_sha256)
    raw = Path(source_path).read_bytes()
    # Guard against replacement between the evidence read and this read.
    if _hash(raw) != expected_sha256:
        raise ValueError('ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH')
    root = ET.fromstring(raw); index = _index(root)
    paragraphs = _body_paragraphs(index)
    methods = [b for b in paragraphs if b['role'] == 'METHODS']
    from .atp_content_cross_context import assay_blocks,linked_prose,enrich,model_class
    assays = assay_blocks(methods)
    from .atp_content_endpoint_reconciliation import direct_atp_from_author_methods
    # A cell-culture Methods block can resolve generic "macrophages" only if
    # it names one unique model; mixed cultures and model alternatives HOLD.
    cultures = [b for b in methods if re.search(r'cell culture', ' '.join(b['headings']), re.I)]
    culture_models = {m.group() for b in cultures for m in CELL.finditer(b['text'])}
    candidates = []; inventory = []; unmapped_interventions = []
    for figpath, fig in index.items():
        if local(fig['element'].tag) != 'fig':
            continue
        cap = next((b for p,b in index.items() if p.startswith(figpath+'/caption[')
                    and local(b['element'].tag) == 'caption'), None)
        if not cap or not ATP.search(cap['text']):
            continue
        label = next((plain(x) for x in fig['element'] if local(x.tag) == 'label'), '')
        figure_id = fig['element'].get('id')
        assets = [{'href': e.get('{http://www.w3.org/1999/xlink}href'),
                   'content_type': e.get('content-type'), 'state': 'DECLARED_NOT_ACQUIRED'}
                  for e in fig['element'].iter() if local(e.tag) == 'graphic']
        figure_caption_evidence = _citation(cap, expected_sha256)
        for panel, scope, scope_meta in _panels(cap,legacy=not current,anchored=anchored):
            if anchored and 'scope_start' in scope_meta:
                cap={**cap,'citation_scope_start':scope_meta['scope_start'],'citation_scope_text':scope}
            panel_endpoint=scope_meta.get('panel_endpoint_literal','')
            semantic_scope=(panel_endpoint+' '+scope).strip()
            if not ATP.search(scope) and not CONTENT.search(panel_endpoint):
                continue
            base = {'figure_id': figure_id, 'figure_label': label, 'panel': panel,
                    'scope_evidence': _citation(cap, expected_sha256, scope),
                    'figure_caption_evidence': figure_caption_evidence, 'assets': assets}
            if scope_meta.get('panel_scope_state'):
                base['panel_scope_state']=scope_meta['panel_scope_state']
                if scope_meta['panel_scope_state'] in ('AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS',
                        'AUTHOR_DIRECT_ATP_EXPLICIT_TWO_CELL_PANELS',
                        'AUTHOR_MODEL_LABELS_MAP_DISTINCT_PANELS',
                        'AUTHOR_SHARED_ATP_ENDPOINT_DISTINCT_MODEL_PANELS'):
                    base['panel_cells_expected']=scope_meta['panel_cells_expected']
            if scope_meta.get('panel_condition_literal'):
                base['panel_condition_literal']=scope_meta['panel_condition_literal']
                panel_mark=re.search(r'\([A-J]\)\s*'+re.escape(scope_meta['panel_condition_literal']),
                                     cap['text'],re.I)
                if panel_mark:
                    base['panel_condition_evidence']=_citation(cap,expected_sha256,
                        panel_mark.group(),panel_mark.start())
            if panel_endpoint:
                base['panel_endpoint_evidence']=_citation(cap,expected_sha256,
                    panel_endpoint,scope_meta['panel_endpoint_start'])
            if anchored and 'panel_label_literal' in scope_meta:
                base['panel_label_evidence']=_citation(cap,expected_sha256,scope_meta['panel_label_literal'],scope_meta['panel_label_start'])
            if scope_meta.get('panel_scope_state')=='PANEL_LABEL_PLACEMENT_AMBIGUOUS':
                inventory.append({**base,'state':'ATP_PANEL_OWNERSHIP_UNRESOLVED'});continue
            calibrated_ctg = (_calibrated_ctg_atp_content(semantic_scope,methods,expected_sha256)
                              if protocol==PROTOCOL else None)
            endpoint_claim_without_viability_proxy = re.sub(
                r'CellTiter[- ]Glo(?:®|™)?(?:\s+[A-Za-z-]+){0,4}\s+'
                r'Cell\s+Viability\s+Assay(?:\s+Kit)?', '', semantic_scope, flags=re.I)
            panel_model=scope_meta.get('panel_cell_model')
            from .atp_content_cross_context import model_class
            compatible_ctg_assays=[]
            for assay in assays:
                if not re.search(r'CellTiter[- ]Glo',assay['text'],re.I):
                    continue
                assay_models={model_class(m.group()) for m in CELL.finditer(assay['text'])}
                if not assay_models or panel_model is None or assay_models=={model_class(panel_model)}:
                    compatible_ctg_assays.append(assay)
            explicit_uncalibrated_ctg_claim = bool(
                protocol==PROTOCOL and CONTENT.search(semantic_scope) and
                not re.search(r'\b(?:cell\s+)?viability\b|\bcell\s+survival\b',
                              endpoint_claim_without_viability_proxy,re.I) and
                len(compatible_ctg_assays)==1 and
                calibrated_ctg is None)
            if (protocol==PROTOCOL and not explicit_uncalibrated_ctg_claim and
                    _viability_is_endpoint(semantic_scope,methods,expected_sha256)):
                # A whole-caption scope may mix comma-labelled ATP and
                # viability panels. Record the explicitly labelled ATP panel
                # separately so the inventory does not contradict the
                # source-only recovery path below.
                from .atp_content_experiment_recall import panel_scopes
                comma_panels=panel_scopes(cap)
                if comma_panels and all(x['state']=='AUTHOR_COMMA_LABEL_ATP_PANEL' for x in comma_panels):
                    for x in comma_panels:
                        xev=_citation(cap,expected_sha256,x['text'],x['scope_start'])
                        inventory.append({**base,'panel':x['panel'],'scope_evidence':xev,
                            'panel_scope_state':x['state'],'state':'ATP_PANEL_REVIEW_METHOD_UNBOUND'})
                    continue
                inventory.append({**base,'state':'ATP_SIGNAL_VIABILITY_NOT_CONTENT'});continue
            reconciled = direct_atp_from_author_methods(scope,methods,expected_sha256)
            if RATE.search(semantic_scope) or (not CONTENT.search(semantic_scope) and reconciled is None):
                inventory.append({**base, 'state': 'ATP_ENDPOINT_NEEDS_SEMANTIC_REVIEW'}); continue
            if re.search(r'volcano plot|Venn diagram|screening strategy|all (?:\d+\s+)?(?:tested|screened) compounds',scope,re.I):
                inventory.append({**base,'state':'AGGREGATE_SCREENING_FIGURE_COMPOUND_POINT_UNBOUND'});continue
            inventory.append({**base, 'state': (
                'ATP_CONTENT_AUTHOR_DIRECT_LYSATE_METHOD_CONCORDANCE_REVIEW'
                if reconciled else 'ATP_CONTENT_CONTEXT_CANDIDATE')})
            # A task registry must not make an author-reported intervention
            # disappear merely because it was not among the requested names.
            # This is an untyped source inventory only: it cannot create a
            # molecule identity, experiment arm, numeric value, or observation.
            refnum = re.search(r'\d+', label or figure_id or '')
            if refnum and panel:
                result_ref = rf'Fig(?:ure)?\.?\s*{refnum[0]}\s*{panel}\b'
                for result_block in paragraphs:
                    if result_block['role'] != 'RESULTS':
                        continue
                    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])', result_block['text']):
                        if not re.search(result_ref, sentence, re.I) or not CONTENT.search(sentence):
                            continue
                        for intervention in re.finditer(
                            r'\bwith\s+(?P<agent>[A-Za-z][A-Za-z0-9-]*'
                            r'(?:\s+[A-Za-z][A-Za-z0-9-]*){0,3})'
                            r'(?:\s*\([A-Z][A-Z0-9-]{0,8}\))?\s+'
                            r'(?P<role>ingestion|treatment|administration|supplementation|feeding)\b',
                            sentence, re.I):
                            author_literal = intervention['agent'].strip()
                            unmapped_interventions.append({
                                'figure_id': figure_id, 'figure_label': label, 'panel': panel,
                                'author_intervention_literal': author_literal,
                                'author_role_literal': intervention['role'],
                                'result_evidence': _citation(result_block, expected_sha256, sentence),
                                'state': 'AUTHOR_NAMED_INTERVENTION_UNMAPPED_REVIEW',
                                'entity_type': None, 'canonical_compound_id': None,
                                'identity_approved': False, 'candidate_observation_created': False,
                                'independent_gold': 0, 'training_approved': 0,
                            })
            for compound in sorted(set(n.strip().lower() for n in compounds if n.strip())):
                names, alias_evidence = _names(compound, paragraphs, expected_sha256,allow_spaced=named_methods)
                # Author caption may define "Abbreviation: NAR, naringenin"
                # without repeating the expansion in a prose paragraph.
                # This alias is scoped to this very figure, never paper-wide.
                if scope_meta.get('panel_scope_state')=='AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS':
                    reverse=rf'\bAbbreviations?:\s*(?P<abbr>[A-Za-z][A-Za-z0-9-]{{1,12}})\s*,\s*{re.escape(compound)}\b'
                    for abbrev in re.finditer(reverse,cap['text'],re.I):
                        literal=abbrev['abbr']
                        if literal.isupper() and len(literal)<=8:
                            names=sorted(set([*names,literal]),key=lambda s:(-len(s),s))
                            alias_evidence.append(_citation(cap,expected_sha256,abbrev.group()))
                name = _name_pattern(names,protect_formulation=protocol==PROTOCOL)
                # Only the author-written COMMON treatment preceding the
                # first panel may identify the compound in an ATP subpanel.
                # No drug dose or time can be borrowed from other subpanels.
                first_panel=re.search(r'\([A-Z]\)',cap['text'])
                preamble=cap['text'][:first_panel.start()] if first_panel else ''
                shared=re.search(
                    rf'\bCells\s+were\s+pretreated\s+with\s+'
                    rf'{name}\s+for\s+(?P<t>{TIME})\b',
                    preamble,re.I)
                from .atp_content_common import common_protocol
                common=common_protocol(cap,names,expected_sha256)
                linked=linked_prose(paragraphs,label,figure_id,panel,names,strict_roles=current)
                in_caption=bool(re.search(name,scope,re.I))
                figure_only=bool(current and scope_meta.get('panel_scope_state')=='AUTHOR_POSTFIX_LABEL_BOUNDARY' and
                    re.search(name,scope_meta.get('postfix_preamble',''),re.I) and not in_caption and not linked and not common)
                if not in_caption and not linked and not common and not figure_only and not shared:
                    continue
                ev = [_citation(cap, expected_sha256, scope)]
                fields = {k: _field() for k in ('compound', 'cell_model', 'dose_original', 'dose_uM',
                    'pretreatment_h', 'challenge_h', 'total_compound_exposure_h', 'assay_method',
                    'normalization', 'treated_arm', 'comparator_arm', 'effect_direction')}
                fields['compound'] = _field(compound,
                    [_citation(cap,expected_sha256,scope_meta['postfix_preamble'])] if figure_only else
                    [*alias_evidence,*ev] if in_caption else [common['evidence']] if common else
                    [_citation(cap,expected_sha256,shared.group())] if shared else
                    [*alias_evidence,*[_citation(b,expected_sha256) for b in linked]],
                    'FIGURE_NAMED_COMPOUND_PANEL_ARMS_UNRESOLVED' if figure_only else
                    'CAPTION_NAMED_COMPOUND' if in_caption else
                    'COMMON_FIGURE_PROTOCOL_NAMED_COMPOUND' if common else
                    'AUTHOR_FIGURE_PREAMBLE_NAMED_DRUG_PANEL_ARM_UNVERIFIED' if shared else
                    'EXPLICIT_FIGURE_REFERENCED_AUTHOR_RESULT_COMPOUND')
                holds = ['INDEPENDENT_SCIENTIFIC_REVIEW_REQUIRED', 'AUTHOR_ATP_NUMERIC_PAIR_UNRESOLVED',
                         'IMAGE_ARM_ASSIGNMENT_UNVERIFIED']
                if figure_only:holds.append('FIGURE_COMPOUND_PANEL_ARM_BINDING_UNRESOLVED')
                protocol_scope = re.split(r'\b(?:versus|compared\s+with|Each\s+column)\b', scope, maxsplit=1, flags=re.I)[0]
                doses = _doses(protocol_scope, names,literal_molar=True)
                # A parenthesized series is a source-level treatment protocol,
                # NOT five individually paired ATP measurements. Preserve the
                # original author span without fabricating panel-arm values.
                series_match=re.search(
                    rf'{name}\s*\(\s*(?:{NUMBER}\s*{UNIT}\s*,\s*){{2,}}'
                    rf'{NUMBER}\s*{UNIT}\s*\)',scope,re.I)
                dose_series_evidence={
                    'author_literal':series_match.group(),
                    'evidence':_citation(cap,expected_sha256,series_match.group()),
                    'individual_arm_dose_uM':None,
                    'state':'MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED'
                } if series_match else None
                if dose_series_evidence:holds.append('MULTIDOSE_FIGURE_PANEL_ARMS_UNASSIGNED')
                values = {(d['uM'], d['literal'].split()[-1]) for d in doses}
                range_pattern=(rf'(?:{name}\s*\(\s*(?P<range_paren>{NUMBER}\s*[–—-]\s*{NUMBER}\s*{UNIT})\s*\)'
                               rf'|(?P<range_suffix>{NUMBER}\s*[–—-]\s*{NUMBER}\s*{UNIT})\s+{name})')
                ranges=list(re.finditer(range_pattern,scope,re.I))
                if len(ranges)==1:
                    dose_range=ranges[0]
                    dose_literal=dose_range['range_paren'] or dose_range['range_suffix']
                    fields['dose_original']=_field(dose_literal,
                        [_citation(cap,expected_sha256,dose_literal)],
                        'AUTHOR_REPORTED_DOSE_INTERVAL_NOT_INDIVIDUAL_ARM_DOSE')
                    holds.append('DOSE_RANGE_ARM_VALUE_UNRESOLVED')
                elif len(values) == 1:
                    d = doses[0]; de = [_citation(cap, expected_sha256, d['quote'])]
                    fields['dose_original'] = _field(d['literal'], de)
                    fields['dose_uM'] = _field(d['uM'], de)
                elif len(values) > 1:
                    holds.append('CONFLICTING_COMPOUND_DOSES')
                else:
                    holds.append('DOSE_BINDING_UNRESOLVED')
                local_model_matches = list(CELL.finditer(scope))
                local_models = {model_class(m.group()) for m in local_model_matches}
                if scope_meta.get('panel_scope_state') in ('AUTHOR_SAME_ATP_ASSAY_DISTINCT_CELL_PANELS','AUTHOR_DIRECT_ATP_EXPLICIT_TWO_CELL_PANELS','AUTHOR_MODEL_LABELS_MAP_DISTINCT_PANELS','AUTHOR_SHARED_ATP_ENDPOINT_DISTINCT_MODEL_PANELS','AUTHOR_FIGURE_LEVEL_ATP_ENDPOINT_SHARED_ACROSS_CONDITION_PANELS','AUTHOR_ORDERED_ENDPOINTS_WITH_GROUPED_PANEL_LABELS'):
                    mapped=scope_meta['panel_cell_model']
                    model_quote=scope_meta['panel_cell_quote']
                    fields['cell_model']=_field(mapped,[_citation(cap,expected_sha256,model_quote)],
                        'AUTHOR_ATP_ASSAY_PANEL_SPECIFIC_CELL_MODEL')
                elif len(local_models) == 1:
                    # Multiple grammatical mentions (e.g. mature oocytes,
                    # mature oocyte, and oocytes in one caption) can denote
                    # one author-named sample. Keep the longest source literal
                    # only when the normalized model class is unique.
                    found=max(local_model_matches,key=lambda m:len(m.group()))
                    fields['cell_model'] = _field(found.group(), [_citation(cap,expected_sha256,found.group())])
                elif not local_models and preamble:
                    # A single model explicitly named in the shared FIGURE
                    # heading belongs to ATP panels of that figure only if
                    # no other cell model occurs anywhere in that figure.
                    # Models of neighboring panels or figures are not borrowed.
                    figure_models={m.group() for m in CELL.finditer(cap['text'])}
                    heading_models={m.group() for m in CELL.finditer(preamble)}
                    if len(heading_models)==1 and figure_models==heading_models:
                        model=next(iter(heading_models))
                        fields['cell_model']=_field(model,
                            [_citation(cap,expected_sha256,model)],
                            'SINGLE_AUTHOR_MODEL_IN_SAME_FIGURE_HEADING')
                if fields['cell_model']['value'] is None and not local_models and shared and len(culture_models)==1 and re.search(
                        r'\bCells\s+were\s+pretreated\b',shared.group(),re.I):
                    model=next(iter(culture_models))
                    matches=[b for b in cultures if model in b['text']]
                    if len(matches)==1:
                        fields['cell_model']=_field(model,
                            [_citation(cap,expected_sha256,shared.group()),
                             _citation(matches[0],expected_sha256,model)],
                            'UNIQUE_AUTHOR_CELL_CULTURE_AND_COMMON_FIGURE_DRUG')
                if fields['cell_model']['value'] is None and not local_models and len(culture_models) == 1 and re.search(r'\bmacrophages\b', scope, re.I):
                    model = next(iter(culture_models))
                    matches = [b for b in cultures if model in b['text'] and re.search('macrophages', b['text'], re.I)]
                    if len(matches) == 1:
                        fields['cell_model'] = _field(model, [*ev, _citation(matches[0], expected_sha256)])
                if fields['cell_model']['value'] is None:
                    holds.append('CELL_BINDING_UNRESOLVED')
                stages = []
                seq = re.search(rf'(?P<pret>pretreated\s+with\s+.+?)\s+for\s+(?P<t1>{TIME})\s+'
                    rf'before\s+(?:treatment|stimulation)\s+with\s+(?P<stress>.+?)\s+for\s+(?P<t2>{TIME})', scope, re.I)
                if seq and re.search(name, seq['pret'], re.I):
                    se = [_citation(cap, expected_sha256, seq.group())]
                    fields['pretreatment_h'] = _field(_hours(seq['t1']), se)
                    fields['challenge_h'] = _field(_hours(seq['t2']), se)
                    stages = [{'role': 'PRETREATMENT', 'duration_h': _hours(seq['t1']),
                               'author_literal': seq['pret'], 'evidence': se},
                              {'role': 'CHALLENGE', 'duration_h': _hours(seq['t2']),
                               'author_literal': seq['stress'], 'evidence': se}]
                    # Neither washout nor continued presence is inferred.
                    holds.append('TOTAL_COMPOUND_EXPOSURE_UNRESOLVED')
                    # Comparator-context proposal requires an author-written
                    # vs-clause with the same priming/challenge agents+doses.
                    priming = _doses(seq['pret'], ['LPS'])
                    challenge = re.fullmatch(rf'\s*(?P<name>[A-Za-z][\w-]*)\s*\(\s*(?P<dose>{NUMBER}\s*{UNIT})\s*\)\s*', seq['stress'])
                    if len(priming) == 1 and challenge:
                        base_agents = [priming[0]['quote'], challenge.group().strip()]
                        for m in re.finditer(r'(?:versus|compared with)\s+([^;]+)', scope, re.I):
                            comparator = m[1].strip().rstrip('.')
                            # Exactly these two named agents, no third drug.
                            found = re.findall(rf'[A-Za-z][\w-]*\s*\(\s*{NUMBER}\s*{UNIT}\s*\)', comparator)
                            if sorted(found) == sorted(base_agents):
                                fields['comparator_arm'] = _field(comparator, [_citation(cap, expected_sha256, m.group())],
                                    'COMPARATOR_CONTEXT_CANDIDATE_IMAGE_ARM_UNVERIFIED')
                                drug = fields['dose_original']['value']
                                if drug:
                                    fields['treated_arm'] = _field(f'{names[-1]} ({drug}) + {comparator}', [*ev, *se],
                                        'TREATMENT_CONTEXT_CANDIDATE_IMAGE_ARM_UNVERIFIED')
                                break
                # Shared author protocol can assign *separate* pretreatment
                # and challenge clocks to a figure subpanel. It does not prove
                # continuation of drug presence or select +/- stressor arms.
                if not stages and shared:
                    sq=re.search(
                        rf'(?P<pre>Cells\s+were\s+pretreated\s+with\s+{name}'
                        rf'\s+for\s+(?P<t1>{TIME}))\s*,?\s+and\s+then\s+'
                        rf'stimulated\s+with\s+or\s+without\s+'
                        rf'(?P<stress>{NUMBER}\s*{UNIT}\s+[A-Za-z0-9-]+)'
                        rf'\s+for\s+(?P<t2>{TIME})',preamble,re.I)
                    if sq:
                        proof=[_citation(cap,expected_sha256,sq.group())]
                        fields['pretreatment_h']=_field(_hours(sq['t1']),proof,
                            'AUTHOR_COMMON_FIGURE_DRUG_PRETREATMENT')
                        fields['challenge_h']=_field(_hours(sq['t2']),proof,
                            'AUTHOR_COMMON_FIGURE_STRESSOR_CHALLENGE')
                        stages=[
                            {'role':'DRUG_PRETREATMENT','duration_h':_hours(sq['t1']),
                             'author_literal':sq['pre'],'evidence':proof},
                            {'role':'STRESSOR_WITH_OR_WITHOUT_VARIANTS',
                             'duration_h':_hours(sq['t2']),
                             'author_literal':sq['stress'],'evidence':proof}]
                        holds.extend(['MULTIPLE_STRESSOR_VARIANTS_REQUIRE_ARM_REVIEW',
                                      'TOTAL_COMPOUND_EXPOSURE_UNRESOLVED'])
                if not stages:
                    # Only explicit local named-drug duration; never borrow an
                    # assay lysis/incubation time or another panel's duration.
                    tm = re.search(rf'{name}\s+for\s+(?P<t>{TIME})', scope, re.I)
                    if tm:
                        fields['pretreatment_h'] = _field(_hours(tm['t']), [_citation(cap, expected_sha256, tm.group())],
                            'LOCAL_DRUG_INTERVAL_SEQUENCE_ROLE_UNRESOLVED')
                    else:
                        # Some author captions order the same explicit
                        # treatment as "after 24 h incubation with dose drug".
                        # Bind time only when that interval, dose interval and
                        # named intervention all occur in this panel scope.
                        after_incubation=re.search(
                            rf'after\s+(?P<t>{TIME})\s+incubation\s+with\s+'
                            rf'.{{0,90}}?{name}',scope,re.I)
                        if after_incubation:
                            duration=_hours(after_incubation['t'])
                            evidence=[_citation(cap,expected_sha256,after_incubation.group())]
                            fields['total_compound_exposure_h']=_field(duration,evidence,
                                'AUTHOR_PANEL_LOCAL_TREATMENT_INCUBATION_INTERVAL')
                            stages=[{'role':'COMPOUND_TREATMENT','duration_h':duration,
                                     'author_literal':after_incubation.group(),'evidence':evidence}]
                    if not stages:holds.append('TREATMENT_SEQUENCE_UNRESOLVED')
                variants = re.search(r'left panel|right panel|with or without|2-DG|poly\s*\(I:C\)', scope, re.I)
                if variants:
                    fields['treated_arm'] = _field()
                    fields['comparator_arm'] = _field()
                    holds.append('MULTIPLE_STIMULUS_VARIANTS')
                if fields['comparator_arm']['value'] is None:
                    holds.append('MATCHED_COMPARATOR_UNRESOLVED')
                model = fields['cell_model']['value']
                compatible_assays = []
                for b in assays:
                    models = {model_class(m.group()) for m in CELL.finditer(b['text'])}
                    if not models or model and models == {model_class(str(model))}:
                        compatible_assays.append(b)
                if len(compatible_assays) == 1:
                    b = compatible_assays[0]
                    fields['assay_method'] = _field(b.get('assay_quote',b['text']), [_citation(b, expected_sha256,b.get('assay_quote',b['text']))],
                        'UNIQUE_ARTICLE_ATP_METHOD_SCOPE_CANDIDATE')
                    if re.search(r'ATP.{0,25}normaliz\w*.{0,35}protein', b['text'], re.I):
                        fields['normalization'] = _field('PROTEIN', [_citation(b, expected_sha256)])
                    elif current:
                        from .atp_content_common import explicit_protein_normalization
                        norm=explicit_protein_normalization(b['text'])
                        if norm:fields['normalization']=_field('PROTEIN',[_citation(b,expected_sha256,norm)],'EXPLICIT_SAME_ATP_ASSAY_RELATIVE_CLAUSE')
                    if re.search(r'CellTiter[- ]Glo',b['text'],re.I) and not calibrated_ctg:
                        holds.append('CELLTITER_GLO_SIGNAL_NOT_CALIBRATED_AS_ATP_CONTENT')
                elif len(compatible_assays) > 1:
                    holds.append('MULTIPLE_ATP_METHODS_SCOPE_UNRESOLVED')
                else:
                    holds.append('ATP_METHOD_BINDING_UNRESOLVED')
                # Explicit direct-ATP same-figure protein normalization is
                # credible even if an assay-specific Methods paragraph omitted
                # the normalization statement. Do not borrow normalization
                # from neighbouring OCR, viability or protein-expression axes.
                if fields['normalization']['value'] is None:
                    norm_match=re.search(
                        r'\bATP\s+levels?\s+were\s+measured\b.{0,160}?'
                        r'\bnormalized\s+to\s+protein\s+content\b',
                        scope,re.I)
                    if norm_match and not re.search(r'\b(?:OCR|ECAR|ATPase)\b',
                                                    norm_match.group(),re.I):
                        fields['normalization']=_field('PROTEIN',
                            [_citation(cap,expected_sha256,norm_match.group())],
                            'AUTHOR_SAME_ATP_PANEL_PROTEIN_NORMALIZATION')
                # An author figure term "ATP production" that is proven to
                # refer to a direct intracellular lysate ATP method is a
                # source-only semantic REVIEW; no numeric or rate is assumed.
                if reconciled:
                    fields['assay_method']=_field(
                        reconciled['assay_method_author_literal'],
                        [reconciled['method_evidence']],
                        'AUTHOR_DIRECT_LYSATE_ATP_METHOD_CONCORDANCE_REVIEW')
                    holds.append('AUTHOR_FIGURE_PRODUCTION_TERM_DIRECT_LYSATE_ENDPOINT_REVIEW')
                # Qualitative effect requires an explicit reference to this
                # figure/panel AND a named-drug subject in the same sentence.
                refnum = re.search(r'\d+', label or figure_id or '')
                for b in paragraphs:
                    if b['role'] != 'RESULTS' or not refnum:
                        continue
                    ref = rf'Fig(?:ure)?\.?\s*{refnum[0]}\s*{panel or ""}\b'
                    if not re.search(ref, b['text'], re.I):
                        continue
                    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])', b['text']):
                        if not re.search(name, sentence, re.I) or not re.search(ref,sentence,re.I):
                            continue
                        effects = list(re.finditer(r'\b(increased|decreased)\s+(?:intracellular\s+)?ATP\s+(?:content|levels?|production)', sentence, re.I))
                        if len(effects) != 1:
                            continue  # Multi-effect sentence needs clause/arm adjudication.
                        effect = effects[0]
                        subjects = list(re.finditer(name, sentence[:effect.start()], re.I))
                        if not subjects:
                            continue
                        bridge = sentence[subjects[-1].end():effect.start()]
                        if re.search(r';|\b(?:but|whereas|while|AICAR|LPS|PA|PTF)\b', bridge, re.I):
                            continue
                        if not re.search(r'\b(?:not|no|neither)\s+(?:\w+\s+){0,2}' + re.escape(effect[0]), sentence, re.I):
                            # No interpretation of sign as a numeric change.
                            fields['effect_direction'] = _field('INCREASE' if effect[1].lower() == 'increased' else 'DECREASE',
                                [_citation(b, expected_sha256, sentence)], 'QUALITATIVE_AUTHOR_STATEMENT_CANDIDATE')
                key = {'paper': source['paper'], 'figure_id': figure_id, 'panel': panel,
                       'compound': compound, 'source_sha256': expected_sha256, 'protocol': protocol}
                candidate_id = 'ATP-' + _hash(json.dumps(key, sort_keys=True).encode())[:22]
                candidate={**base, 'candidate_id': candidate_id, 'compound_name': compound,
                    'compound_author_names': names, 'fields': fields, 'treatment_stages': stages,
                    'endpoint_kind': (
                        'ATP_CONTENT_AUTHOR_METHOD_CONCORDANCE_REVIEW'
                        if reconciled else
                        'ATP_CONTENT_UNCALIBRATED_CELLTITER_GLO_REVIEW'
                        if len(compatible_assays)==1 and re.search(r'CellTiter[- ]Glo',compatible_assays[0]['text'],re.I) and not calibrated_ctg
                        else 'ATP_CONTENT_CANDIDATE'),
                    'endpoint_reconciliation':reconciled,
                    'calibrated_atp_content_proof':calibrated_ctg,
                    'dose_series_author_evidence':dose_series_evidence,
                    'treated_value': None, 'control_value': None,
                    'measurement_unit': None, 'numeric_origin': 'NOT_EXTRACTED',
                    'review_tier': 'Review-B', 'train_eligible': False,
                    'hold_reasons': sorted(set(holds)), 'independent_gold': 0, 'training_approved': 0}
                formulation=re.search(r'\b(?:nano[- ]?crystals?|nanoparticles?|nano[- ]form|HstN)\b',scope,re.I)
                if formulation:
                    candidate['author_formulation_literal']=formulation.group()
                    candidate['hold_reasons']=sorted(set(candidate['hold_reasons']+[
                        'FORMULATION_AND_FREE_PARENT_ARMS_MUST_REMAIN_DISTINCT',
                        'AUTHOR_NAMED_FORMULATION_IDENTITY_REQUIRES_REVIEW']))
                    candidate['fields']['compound']['state']='AUTHOR_NAMED_FORMULATION_PARENT_IDENTITY_HOLD'
                from .atp_content_prose import relative_changes
                for enriched in enrich(candidate,scope,cap,linked,methods,expected_sha256,common):
                    if named_methods:
                        from .atp_content_method_protocol import (bind_author_polyphenol_protocol,
                                                                  bind_method_protocol)
                        enriched=bind_method_protocol(enriched,methods,linked,expected_sha256)
                        enriched=bind_author_polyphenol_protocol(enriched,methods,expected_sha256)
                    candidates.append(enriched)
                    candidates.extend(relative_changes(enriched,linked,expected_sha256))
    from .atp_content_native import extract_native
    native = extract_native(source_path, expected_sha256, compounds, root, index, paragraphs, protocol, source['paper'])
    candidates.extend(native['candidates'])
    entity_audit = []
    if protocol == PROTOCOL:
        from .atp_content_author_identity import resolve_author_entity
        identity_blocks=[b for b in index.values() if local(b['element'].tag) in ('p','article-title')]
        entity_audit=[resolve_author_entity(n,identity_blocks,expected_sha256) for n in compounds]
        for candidate in candidates:
            entity=next((e for e in entity_audit if
                candidate['compound_name'].casefold()==e['requested_name'].casefold() or
                candidate['compound_name'].casefold() in {n.casefold() for n in e.get('retrieval_names',[])}),None)
            if entity and entity.get('external_structure_identity'):
                candidate['chemical_identity_resolution']=entity['external_structure_identity']
                if entity['external_structure_identity'].get('canonical_structure_verified'):
                    candidate['hold_reasons']=[h for h in candidate.get('hold_reasons',[]) if h!='ENTITY_IDENTITY_HOLD']
                    candidate['fields']['compound']['state']=entity['state']
                elif entity['external_structure_identity'].get('state','').startswith('HOLD_'):
                    candidate['hold_reasons']=sorted(set(candidate.get('hold_reasons',[])+['ENTITY_IDENTITY_HOLD']))
                    candidate['fields']['compound']['state']='ENTITY_IDENTITY_HOLD'
            if entity and entity['formulations']:
                candidate['chemical_identity_resolution']=entity
                candidate['hold_reasons']=sorted(set(candidate['hold_reasons']+['FORMULATION_AND_FREE_PARENT_ARMS_MUST_REMAIN_DISTINCT','COMPOUND_SPECIFIC_EFFECT_UNRESOLVED']))
                candidate['fields']['effect_direction']=_field()
        from .atp_content_experiment_recall import recall
        candidates.extend(recall(index,paragraphs,entity_audit,expected_sha256,source['paper'],protocol,candidates))
    # Relative fold effects may be stated in a Results block that does not
    # repeat the figure reference in the same sentence as the compound. Run
    # the conservative panel-mapped matcher over final source candidates only
    # after ordinary and recall candidates have been assembled.
    if protocol==PROTOCOL:
        from .atp_content_prose import (reported_fold_changes,reported_percent_of_control,
            reported_by_times_effects,expand_author_caption_dose_series)
        captions={}
        for figpath,fig in index.items():
            if local(fig['element'].tag)!='fig':continue
            figid=fig['element'].get('id')
            cap=next((b for p,b in index.items() if p.startswith(figpath+'/caption[') and local(b['element'].tag)=='caption'),None)
            if figid and cap:captions[figid]=cap
        fold_candidates=[]
        for parent in list(candidates):
            cap=captions.get(parent.get('figure_id'))
            if cap:fold_candidates.extend(reported_fold_changes(parent,paragraphs,expected_sha256,cap))
        seen_fold=set()
        for candidate in fold_candidates:
            if candidate['candidate_id'] not in seen_fold:
                candidates.append(candidate);seen_fold.add(candidate['candidate_id'])
        relative_candidates=reported_percent_of_control(candidates,paragraphs,methods,captions,expected_sha256)
        seen_relative=set()
        for candidate in relative_candidates:
            if candidate['candidate_id'] not in seen_relative:
                candidates.append(candidate);seen_relative.add(candidate['candidate_id'])
        ambiguous_magnitudes=reported_by_times_effects(candidates,paragraphs,captions,expected_sha256)
        seen_magnitudes=set()
        for candidate in ambiguous_magnitudes:
            if candidate['candidate_id'] not in seen_magnitudes:
                candidates.append(candidate);seen_magnitudes.add(candidate['candidate_id'])
        source_only_arms=expand_author_caption_dose_series(candidates,captions,expected_sha256)
        seen_arms=set()
        for candidate in source_only_arms:
            if candidate['candidate_id'] not in seen_arms:
                candidates.append(candidate);seen_arms.add(candidate['candidate_id'])
    licenses = [plain(e) for e in root.iter() if local(e.tag) == 'license']
    author_reported_effect_series=[]
    if protocol==PROTOCOL:
        from .atp_content_prose import unassigned_author_percent_series
        author_reported_effect_series=unassigned_author_percent_series(candidates,paragraphs,expected_sha256)
    if protocol==PROTOCOL:
        for candidate in candidates:
            entity=next((e for e in entity_audit if
                candidate['compound_name'].casefold()==e['requested_name'].casefold() or
                candidate['compound_name'].casefold() in {n.casefold() for n in e.get('retrieval_names',[])}),None)
            if entity and entity.get('external_structure_identity'):
                candidate['chemical_identity_resolution']=entity['external_structure_identity']
                if entity['external_structure_identity'].get('canonical_structure_verified'):
                    candidate['hold_reasons']=[h for h in candidate.get('hold_reasons',[]) if h!='ENTITY_IDENTITY_HOLD']
                    candidate['fields']['compound']['state']=entity['state']
                elif entity['external_structure_identity'].get('state','').startswith('HOLD_'):
                    candidate['hold_reasons']=sorted(set(candidate.get('hold_reasons',[])+['ENTITY_IDENTITY_HOLD']))
                    candidate['fields']['compound']['state']='ENTITY_IDENTITY_HOLD'
            if entity and entity['formulations']:
                candidate['chemical_identity_resolution']=entity
                candidate['hold_reasons']=sorted(set(candidate['hold_reasons']+[
                    'FORMULATION_AND_FREE_PARENT_ARMS_MUST_REMAIN_DISTINCT','COMPOUND_SPECIFIC_EFFECT_UNRESOLVED']))
                candidate['fields']['effect_direction']=_field()
        from .atp_content_arm_context import attach_author_arm_context
        candidates=[attach_author_arm_context(c,methods,expected_sha256) for c in candidates]
        from .atp_content_method_panel_scope import attach_method_panel_scope
        candidates=[attach_method_panel_scope(c,methods,expected_sha256) for c in candidates]
        from .atp_content_panel_interventions import attach_panel_interventions
        candidates=[attach_panel_interventions(c,expected_sha256) for c in candidates]
        from .atp_content_author_effect_summary import attach_author_panel_effect_summary
        candidates=[attach_author_panel_effect_summary(c,expected_sha256,methods) for c in candidates]
        from .atp_content_prose import explicit_panel_percent_decreases
        candidates.extend(explicit_panel_percent_decreases(candidates,expected_sha256))
    # Collapse duplicate author sentences that repeat a single figure result.
    unique_unmapped = {}
    for item in unmapped_interventions:
        key=(item['figure_id'],item['panel'],item['author_intervention_literal'].casefold(),
             item['result_evidence']['source_anchor'],item['result_evidence']['start'])
        unique_unmapped[key]=item
    result = {'schema': protocol, 'paper': source['paper'], 'source_path': str(Path(source_path).resolve()),
            'source_sha256': expected_sha256, 'requested_compounds': sorted(set(compounds)),
            'license_author_literal': licenses, 'license_training_approval': False,
            'unmapped_author_interventions': list(unique_unmapped.values()),
            **({'author_entity_resolution':entity_audit} if protocol==PROTOCOL else {}),
            **({'author_reported_effect_series':author_reported_effect_series} if author_reported_effect_series else {}),
            'candidates': candidates, 'native_numeric_points':native['native_numeric_points'],
            'figure_inventory': inventory, 'original_evidence_stats': source['stats'],
            'scientific_metrics': {'precision': None, 'recall': None, 'f1': None, 'human_gold': 0},
            'status': 'SOURCE_BOUND_CONTEXT_CANDIDATES_NOT_SCIENTIFIC_APPROVAL'}
    if supplementary_sources:
        from .r53_atp_supplement import enrich_relations_with_supplements
        result = enrich_relations_with_supplements(result, supplementary_sources)
    return result


def verify_relations(data, source_path):
    """Fail closed on changed source, modified output, or altered field role.

    Deterministic replay verifies software consistency, not independent science.
    It rejects a valid source dose deliberately relabelled as another agent.
    """
    if data.get('schema') not in (PROTOCOL,PREVIOUS_PROTOCOL,ANCHOR_PROTOCOL,PANEL_PROTOCOL,LEGACY_PROTOCOL):raise ValueError('ATP_RELATION_PROTOCOL_VERSION_HOLD')
    if data.get('source_format')=='PMC_HTML':
        if data.get('schema')!=PROTOCOL:raise ValueError('PMC_HTML_CURRENT_PROTOCOL_REQUIRED')
        from .atp_content_pmc_html import verify_pmc_html_relations
        return verify_pmc_html_relations(data,source_path,extract_relations)
    raw = Path(source_path).read_bytes()
    if _hash(raw) != data['source_sha256']:
        raise ValueError('ATP_AUTHOR_ORIGINAL_SHA256_MISMATCH')
    supplement_specs = []
    for item in data.get('supplementary_documents', []):
        spec = {'path': item['source_path'], 'sha256': item['source_sha256']}
        spec.update(item.get('acquisition_context', {}))
        supplement_specs.append(spec)
    rerun = extract_relations(source_path, data['source_sha256'], data['requested_compounds'],
                              data['schema'], supplementary_sources=supplement_specs or None)
    if json.loads(json.dumps(data,sort_keys=True)) != json.loads(json.dumps(rerun,sort_keys=True)):
        raise ValueError('ATP_DETERMINISTIC_REPLAY_MISMATCH')
    index = _index(ET.fromstring(raw)); count = 0
    for candidate in data['candidates']:
        citations = [candidate['scope_evidence']]
        if candidate.get('panel_label_evidence'):citations.append(candidate['panel_label_evidence'])
        citations.extend(e for f in candidate['fields'].values() for e in f['evidence'])
        citations.extend(candidate.get('numeric_pair_evidence',[]))
        citations.extend(candidate.get('linked_author_prose',[]))
        arm_context=candidate.get('author_method_arm_context')
        if arm_context:citations.extend(arm_context.get('source_evidence',[]))
        panel_summary=candidate.get('author_reported_panel_effect_summary')
        if panel_summary:citations.extend(panel_summary.get('author_literal_evidence',[]))
        prose=candidate.get('relative_prose_evidence')
        if prose:citations.extend(prose[k] for k in ('variant_definition','variant_effect','author_statement','comparison'))
        method=candidate.get('method_protocol_evidence')
        if method:
            if method.get('schema')=='asterloom.atp-content.named-list-dose-protocol/0.1':
                citations.extend(method[k] for k in ('treatment_list_statement','atp_assay_schedule_statement'))
            else:
                citations.extend(method[k] for k in ('method_statement','same_panel_atp_result'))
            if method.get('reported_effect_token'):citations.append(method['reported_effect_token']['evidence'])
        for conflict in candidate.get('source_conflicts',[]):
            citations.extend([conflict['caption_evidence'],conflict['prose_evidence']])
        for e in citations:
            text = index[e['source_anchor']]['text']
            if text[e['start']:e['end']] != e['quote'] or e['source_sha256'] != data['source_sha256']:
                raise ValueError('ATP_SOURCE_ANCHOR_REPLAY_MISMATCH')
            count += 1
    if data['schema']==PROTOCOL:
        # Include author definitions, formulation inventory and scope metadata,
        # not just scalar fields. A locatable quote still is NOT science truth.
        def citations(value):
            if isinstance(value,dict):
                if 'source_anchor' in value and 'quote' in value:yield value
                for v in value.values():yield from citations(v)
            elif isinstance(value,(list,tuple)):
                for v in value:yield from citations(v)
        checked=set()
        for e in citations(data):
            text=index[e['source_anchor']]['text']
            if text[e['start']:e['end']]!=e['quote'] or e['source_sha256']!=data['source_sha256']:
                raise ValueError('ATP_SOURCE_ANCHOR_REPLAY_MISMATCH')
            checked.add((e['source_anchor'],e['start'],e['end']))
        count=len(checked)
    return {'source_replay_pass': True, 'field_citation_checks': count,
            'independent_scientific_validation': False, 'training_approved': False}
