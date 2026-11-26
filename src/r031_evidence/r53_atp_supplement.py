"""Source-only text view of author-declared OOXML supplementary documents.

This adapter preserves the supplementary asset's independent SHA and locators.
It does not silently join methods to figure panels or experimental arms.
"""
from __future__ import annotations

import hashlib
import copy
import io
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
ATP_METHOD = re.compile(
    r"\b(?:intracellular|cellular|total)\s+ATP\s+(?:levels?|content|concentration)\s+"
    r"(?:were|was)\s+(?:measured|determined|quantified|assayed)\b"
    r"|\bATP\s+(?:levels?|content|concentration)\s+"
    r"(?:were|was)\s+(?:measured|determined|quantified|assayed)\b",
    re.I,
)
ACQUISITION_CONTEXT_KEYS = (
    'parent_source_sha256', 'declared_filename', 'native_supplement_id',
    'native_supplement_href', 'permission_status', 'official_source_url', 'http_status',
)


def _text(element):
    return ''.join(node.text or '' for node in element.iter(W + 't')).strip()


def extract_docx_blocks(path, expected_sha256):
    """Read paragraph and table-cell literals from a SHA-pinned DOCX.

    External relationships and embedded media are never followed. The return
    values are source excerpts only; no experimental fields are inferred.
    """
    source = Path(path)
    raw = source.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError('R53_SUPPLEMENT_SHA_MISMATCH')
    if len(raw) > 256_000_000:
        raise ValueError('R53_SUPPLEMENT_PACKAGE_SIZE_HOLD')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if ('word/document.xml' not in names or len(names) > 5000
                    or len(names) != len(set(names))
                    or sum(info.file_size for info in infos) > 256_000_000):
                raise ValueError('R53_SUPPLEMENT_OOXML_PACKAGE_HOLD')
            for info in infos:
                member = PurePosixPath(info.filename.replace('\\', '/'))
                if (member.is_absolute() or '..' in member.parts
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.flag_bits & 1
                        or info.filename.lower().endswith('vbaproject.bin')):
                    raise ValueError('R53_SUPPLEMENT_UNSAFE_OOXML_MEMBER')
            document = archive.read('word/document.xml')
    except zipfile.BadZipFile as exc:
        raise ValueError('R53_SUPPLEMENT_DOCX_SIGNATURE_HOLD') from exc
    if b'<!DOCTYPE' in document.upper() or b'<!ENTITY' in document.upper():
        raise ValueError('R53_SUPPLEMENT_XML_ENTITY_HOLD')
    root = ET.fromstring(document)
    body = root.find('.//' + W + 'body')
    if body is None:
        raise ValueError('R53_SUPPLEMENT_DOCUMENT_BODY_MISSING')

    blocks = []
    paragraph_number = 0
    table_number = 0
    for child in body:
        if child.tag == W + 'p':
            paragraph_number += 1
            text = _text(child)
            if not text:
                continue
            style = child.find('./' + W + 'pPr/' + W + 'pStyle')
            blocks.append({
                'kind': 'PARAGRAPH',
                'locator': f'word/document.xml#/w:document/w:body/w:p[{paragraph_number}]',
                'paragraph_index': paragraph_number,
                'style': style.get(W + 'val') if style is not None else None,
                'text': text,
                'source_sha256': actual,
            })
        elif child.tag == W + 'tbl':
            table_number += 1
            for row_number, row in enumerate(child.findall('./' + W + 'tr'), 1):
                for cell_number, cell in enumerate(row.findall('./' + W + 'tc'), 1):
                    text = ' '.join(_text(p) for p in cell.findall('./' + W + 'p')).strip()
                    if text:
                        blocks.append({
                            'kind': 'TABLE_CELL',
                            'locator': (f'word/document.xml#/w:document/w:body/w:tbl[{table_number}]'
                                        f'/w:tr[{row_number}]/w:tc[{cell_number}]'),
                            'table_index': table_number,
                            'row_index': row_number,
                            'cell_index': cell_number,
                            'text': text,
                            'source_sha256': actual,
                        })
    direct_atp_methods = []
    latest_heading = None
    for block in blocks:
        is_heading = (re.search(r'heading', block.get('style') or '', re.I)
                      or (block['kind'] == 'PARAGRAPH'
                          and len(block['text']) <= 100
                          and re.search(r'\b(?:assay|measurement|analysis|determination|protocol)\s*$',
                                        block['text'], re.I)))
        if block['kind'] == 'PARAGRAPH' and is_heading:
            latest_heading = {key: block[key] for key in ('locator', 'text', 'source_sha256')}
        if block['kind'] == 'PARAGRAPH' and ATP_METHOD.search(block['text']):
            direct_atp_methods.append({**block, 'preceding_heading_context': latest_heading})
    return {
        'schema': 'asterloom.r53-author-supplement-text/1',
        'source_path': str(source.resolve()),
        'source_sha256': actual,
        'blocks': blocks,
        'direct_atp_method_blocks': direct_atp_methods,
        'experimental_fields_inferred': False,
        'figure_or_arm_links_created': False,
        'scientific_approval': False,
        'training_approved': False,
    }


def enrich_relations_with_supplements(relations, supplement_sources):
    """Attach a uniquely scoped ATP method excerpt without rewriting JATS facts.

    A link is proposed only when the selected supplements collectively contain
    one direct ATP method block and the paper has one panel-level ATP candidate.
    Ambiguous links remain paper-level HOLD context.
    """
    if not isinstance(supplement_sources, list) or len(supplement_sources) > 5:
        raise ValueError('R53_SUPPLEMENT_SOURCE_SCOPE_HOLD')
    enriched = copy.deepcopy(relations)
    supplements = []
    methods = []
    for spec in supplement_sources:
        allowed_keys = {'path', 'source_path', 'sha256', 'source_sha256',
                        'acquisition_context', *ACQUISITION_CONTEXT_KEYS}
        if not isinstance(spec, dict) or set(spec) - allowed_keys:
            raise ValueError('R53_SUPPLEMENT_SOURCE_SPEC_HOLD')
        source_path = spec.get('path') or spec.get('source_path')
        source_sha = spec.get('sha256') or spec.get('source_sha256')
        if (not isinstance(source_path, (str, Path))
                or not isinstance(source_sha, str)
                or not re.fullmatch(r'[0-9a-f]{64}', source_sha)):
            raise ValueError('R53_SUPPLEMENT_SOURCE_PIN_REQUIRED')
        context = dict(spec.get('acquisition_context') or {})
        context.update({key: spec[key] for key in ACQUISITION_CONTEXT_KEYS if key in spec})
        if set(context) - set(ACQUISITION_CONTEXT_KEYS):
            raise ValueError('R53_SUPPLEMENT_ACQUISITION_CONTEXT_HOLD')
        view = extract_docx_blocks(source_path, source_sha)
        supplements.append({**{key: view[key] for key in (
            'source_path', 'source_sha256', 'direct_atp_method_blocks',
            'scientific_approval', 'training_approved')},
            'acquisition_context': context})
        methods.extend({**method, 'supplement_source_sha256': view['source_sha256']}
                       for method in view['direct_atp_method_blocks'])

    candidates = enriched.get('candidates', [])
    atp_candidates = [candidate for candidate in candidates
                      if 'ATP_CONTENT' in str(candidate.get('endpoint_kind', ''))
                      and candidate.get('figure_id') and candidate.get('panel')]
    unique_link = len(methods) == 1 and len(atp_candidates) == 1
    if unique_link:
        candidate = atp_candidates[0]
        method_text = methods[0]['text']
        assay_match = re.search(
            r'(?P<literal>(?:chemiluminescence|luminescence)[- ]based\s+'
            r'ATP\s+Assay\s+Kit(?:\s*\([^)]{1,120}\))?)',
            method_text, re.I)
        if assay_match:
            candidate['supplementary_field_proposals'] = {
                'assay_method': {
                    'value': assay_match['literal'],
                    'state': 'SOURCE_LINKED_METHOD_PROPOSAL_REVIEW_REQUIRED',
                    'evidence': {
                        'source_sha256': methods[0]['supplement_source_sha256'],
                        'locator': methods[0]['locator'],
                        'quote': assay_match['literal'],
                        'start': assay_match.start('literal'),
                        'end': assay_match.end('literal'),
                        'offset_basis': 'concatenated WordprocessingML w:t text in source paragraph',
                    },
                    'independent_scientific_approval': False,
                    'training_approved': False,
                }
            }
        candidate['supplementary_method_context'] = [{
            'source_sha256': methods[0]['supplement_source_sha256'],
            'locator': methods[0]['locator'],
            'preceding_heading_context': methods[0]['preceding_heading_context'],
            'author_literal': methods[0]['text'],
            'link_state': 'UNIQUE_DIRECT_ATP_METHOD_AND_PANEL_CANDIDATE_REVIEW_REQUIRED',
            'field_values_promoted': False,
        }]
        candidate['supplementary_method_review'] = {
            'proposal': 'ATP_METHOD_BINDING_UNRESOLVED',
            'status': 'SOURCE_LINKED_REVIEW_REQUIRED',
            'independent_scientific_approval': False,
            'training_approved': False,
        }
    enriched['supplementary_documents'] = supplements
    enriched['supplementary_link_audit'] = {
        'direct_atp_method_count': len(methods),
        'panel_level_atp_candidate_count': len(atp_candidates),
        'unique_paper_scope_link_proposed': unique_link,
        'unlinked_method_count': 0 if unique_link else len(methods),
        'field_values_promoted': False,
        'scientific_approval': False,
        'training_approved': False,
    }
    return enriched
