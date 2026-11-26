"""Author aliases plus a pinned, offline external structure-name crosswalk.

Author-defined names remain retrieval evidence. Exact name variants can share
an external structure identifier only when the local pinned crosswalk contains
both names and the author source does not violate its specificity constraints.
The crosswalk never approves training and cannot supply experiment conditions.
References are excluded from author name declarations.
"""
from __future__ import annotations
import json
import re
from pathlib import Path


def _external_structure_records():
    """Load pinned public structure crosswalk data; never query the network."""
    path = Path(__file__).parent / 'data' / 'atp_content_external_structure_aliases.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 'asterloom.atp-content.external-structure-name-map/1':
        raise ValueError('ATP_EXTERNAL_STRUCTURE_MAP_SCHEMA_HOLD')
    return data.get('records', [])


def _external_structure_match(requested, author_name, declarations, records=None,
                              source_names=None):
    records = _external_structure_records() if records is None else records
    author_literals = (list(source_names) if source_names is not None else
                       [author_name, *(x.get('full_name', '') for x in declarations)])
    requested_key = requested.strip().casefold()
    author_keys = {x.strip().casefold() for x in author_literals if x}
    matches = [r for r in records if requested_key in {x.casefold() for x in r.get('aliases', [])}
               and author_keys.intersection(x.casefold() for x in r.get('aliases', []))]
    if len(matches) != 1:
        return None
    record = matches[0]
    requirement = record.get('required_author_specificity')
    if requirement == 'E_STEREOCHEMISTRY' and not re.search(
            r'(?i)(?:\btrans[- ]pterostilbene\b|\(E\)|\(E\)-|\bE-pterostilbene\b)',
            ' '.join(author_literals)):
        return {
            'state': 'HOLD_REQUIRED_STEREOCHEMISTRY_NOT_IN_AUTHOR_SOURCE',
            'external_id_candidate': record['external_id'],
            'required_author_specificity': requirement,
            'canonical_structure_verified': False,
            'identity_approved_for_training': False,
            'source_url': record['source_url'],
            'source_snapshot_sha256': record.get('source_snapshot_sha256'),
        }
    return {
        'state': 'AUTHOR_AND_REQUESTED_NAMES_MATCH_PINNED_EXTERNAL_STRUCTURE',
        'external_id': record['external_id'],
        'database': record['database'],
        'cid': record['cid'],
        'molecular_formula': record['molecular_formula'],
        'inchikey': record['inchikey'],
        'iupac_name': record['iupac_name'],
        'source_url': record['source_url'],
        'source_snapshot_sha256': record.get('source_snapshot_sha256'),
        'source_query_response_sha256': record.get('source_query_response_sha256'),
        'canonical_structure_verified': True,
        'identity_approved_for_training': False,
    }


def name_pattern(names):
    return r'(?<![\w@-])(?:' + '|'.join(re.escape(n) for n in names) + r')(?![\w@-])'


def resolve_author_entity(requested, blocks, sha):
    from .atp_content_relations import _citation
    tokens = re.split(r'[\s-]+', requested.strip())
    # Preserve every difference as a HOLD, not as chemical equivalence.
    retrieval = r'(?:[- ]\d+)?[- ]'.join(map(re.escape, tokens))
    definition = re.compile(r'(?<![\w@-])(?P<full>'+retrieval+
        r')\s*\((?P<abbr>[A-Za-z][A-Za-z0-9′’\'-]{1,12}(?:\s+[A-Z0-9]{1,3})?)(?:\s*;[^)]{1,65})?\)', re.I)
    names={requested}; declarations=[]; variants=set()
    for b in blocks:
        if '/ref-list[' in b['path'] or '/back[' in b['path']:continue
        for m in definition.finditer(b['text']):
            abbr=m['abbr'];full=m['full']
            if not abbr.isupper() and not (abbr[0].isupper() and len(re.sub(r'\s+','',abbr))<=4):continue
            names.update((full,abbr));variants.add(full)
            declarations.append({'full_name':full,'abbreviation':abbr,'evidence':_citation(b,sha,m.group())})
    # The same short token explicitly expanded differently is ambiguous.
    conflicts=set()
    # Do not interpret ordinary prose immediately before an abbreviation as
    # another author-defined name.  For example, ``cotreatment with FXL`` is
    # not a definition competing with ``fucoxanthinol (FXL)``.  Keep genuine
    # compact definitions such as ``microtubules (MT)`` so a short alias is
    # withheld when the source explicitly assigns it to another entity.
    definition_context_words={
        'a','an','the','and','or','of','in','on','at','to','for','by','via',
        'with','without','after','before','following','upon','during','from',
        'was','were','is','are','be','been','being','using','used','treated',
        'treatment','cotreatment','co-treatment','combination','combined',
        'administered','exposure','dose','dosed','cell','cells','group',
        'model','control','assay','measured','measurements','value','values',
        'level','levels','sample','samples','effect','effects','respectively',
    }
    for d in declarations:
        rx=re.compile(r'([A-Za-z][A-Za-z0-9′’ -]{2,70})\s*\('+re.escape(d['abbreviation'])+r'\)')
        for b in blocks:
            if '/ref-list[' in b['path'] or '/back[' in b['path']:continue
            for m in rx.finditer(b['text']):
                prefix=m[1].strip()
                words=re.findall(r"[A-Za-z][A-Za-z0-9′’'-]*",prefix.casefold())
                if not words or len(words)>8 or definition_context_words.intersection(words):
                    continue
                if not any(prefix.lower().endswith(v.lower()) for v in variants):conflicts.add(d['abbreviation'])
    names-=conflicts
    nonexact=[v for v in variants if v.casefold()!=requested.casefold()]
    literal=sorted(nonexact,key=lambda s:(len(s),s))[0] if nonexact else requested
    formulations=[]
    # Preserve author-named parent-plus-carrier constructs as distinct from
    # the free parent. This is only a formulation flag: it does not assert a
    # chemical bond, loading ratio, nanoparticle composition, or equivalence.
    parent_names=sorted(names,key=len,reverse=True)
    parent_alt='|'.join(re.escape(n) for n in parent_names)
    suffix_formulation=re.compile(
        r'(?<![\w@-])(?P<parent>'+parent_alt+r')\s*[- ]\s*'
        r'(?P<marker>PNTs?|nanoparticles?|nanotubes?|liposomes?|micelles?|nanocarriers?|conjugates?|carriers?)'
        r'(?:-?\d+)?(?![\w@-])',re.I) if parent_alt else None
    seen_formulations=set()
    for b in blocks:
        for m in re.finditer(r'\b[A-Za-z][\w-]*@(?:'+'|'.join(re.escape(n) for n in sorted(names,key=len,reverse=True))+r')(?![\w@-])',b['text'],re.I):
            item={'author_literal':m.group(),'parent_author_alias':None,
                  'formulation_marker':m.group().split('@',1)[-1],
                  'evidence':_citation(b,sha,m.group()),'state':'FORMULATION_NOT_FREE_PARENT'}
            key=item['author_literal'].casefold()
            if key not in seen_formulations:formulations.append(item);seen_formulations.add(key)
        if suffix_formulation:
            for m in suffix_formulation.finditer(b['text']):
                item={'author_literal':m.group(),'parent_author_alias':m['parent'],
                      'formulation_marker':m['marker'],'evidence':_citation(b,sha,m.group()),
                      'state':'AUTHOR_NAMED_FORMULATION_NOT_FREE_PARENT'}
                key=item['author_literal'].casefold()
                if key not in seen_formulations:formulations.append(item);seen_formulations.add(key)
    # A requested profile name is not evidence that the author used that
    # chemical. Crosswalk only names that are actually present in the source,
    # including author-defined full-name/abbreviation declarations.
    source_names = [d['full_name'] for d in declarations]
    searchable = '\n'.join(b['text'] for b in blocks
                           if '/ref-list[' not in b['path'] and '/back[' not in b['path'])
    for name in (requested, literal):
        if name and re.search(r'(?<![\w@-])' + re.escape(name) + r'(?![\w@-])',
                              searchable, re.I):
            source_names.append(name)
    structure_match = _external_structure_match(requested, literal, declarations,
                                                 source_names=source_names)
    if structure_match and structure_match.get('canonical_structure_verified'):
        state = structure_match['state']
    elif nonexact:
        state = 'ENTITY_IDENTITY_HOLD'
    else:
        state = 'AUTHOR_NAME_MATCH_STRUCTURE_NOT_VERIFIED'
    return {'requested_name':requested,'author_name':literal,'retrieval_names':sorted(names,key=lambda s:(-len(s),s)),
            'state':state,'canonical_structure_verified':bool(structure_match and structure_match.get('canonical_structure_verified')),
            'external_structure_identity':structure_match,'declarations':declarations,
            'ambiguous_abbreviations':sorted(conflicts),'formulations':formulations,
            'human_gold':False,'training_approved':False}
