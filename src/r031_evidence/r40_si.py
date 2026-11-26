"""Acquire selected author attachments under the calling task's HTTP budget."""
from pathlib import Path
import json
import io
import re
import zipfile

from .r34_si_acquisition import acquire_one, existing_paper_item
from .r35_multi_si import FORMATS
from .r36_native_evidence import xml

SOURCE_FORMATS = (*FORMATS, '.tif', '.tiff', '.doc', '.docx')


def check_document(blob, extension):
    if extension in ('.tif', '.tiff') and blob[:4] not in (b'II*\x00', b'MM\x00*'):
        raise ValueError('SI_TIFF_SIGNATURE_HOLD')
    if extension == '.doc' and not blob.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ValueError('SI_WORD_SIGNATURE_HOLD')
    if extension == '.docx':
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if 'word/document.xml' not in archive.namelist() or any(
                    info.filename.lower().endswith('vbaproject.bin') for info in archive.infolist()):
                raise ValueError('SI_WORD_PACKAGE_HOLD')


def acquire_selected(home, paper_key, selection, reader, worker_python=None):
    item = existing_paper_item(home, paper_key)
    parent = item['parent_asset']
    if selection['parent_sha256'] != parent['original_sha']:
        raise ValueError('SI_PINNED_PARENT_DRIFT')
    root = xml(Path(parent['original_path']).read_bytes())
    results = []
    for filename in selection['filenames']:
        refs = [ref for ref in item['declared_SI'] if ref['href_literal'] == filename]
        record = dict(filename=filename, state='SI_DECLARATION_OR_FORMAT_HOLD')
        if len(refs) == 1 and Path(filename).suffix.lower() in SOURCE_FORMATS:
            locator = refs[0]['original_locator']
            # This path is restricted to exact JATS supplementary declarations.
            wrap_path = re.match(r'^(/article/.*/supplementary-material(?:\[\d+\])?)(?:/|$)', locator)
            wrap = root.find(wrap_path[1].removeprefix('/article/')) if wrap_path else None
            # Local credit/permission text can qualify the article-level license.
            if wrap is None or any(e.tag.rsplit('}', 1)[-1] in ('permissions', 'copyright-statement',
                    'attrib', 'license') for e in wrap.iter()) or re.search(
                    r'copyright|all rights reserved|permission|third.party', ''.join(wrap.itertext()), re.I):
                record['state'] = 'SI_REQUIRES_FILE_SPECIFIC_RIGHTS_REVIEW'
            else:
                try:
                    record.update(acquire_one(home, dict(item, declared_SI=refs), reader,
                                              worker_python, formats=SOURCE_FORMATS))
                except (ValueError, OSError, zipfile.BadZipFile) as exc:
                    record.update(state='SI_HOLD', reason=str(exc))
        results.append(record)
    return results


def validate_selection(selection, allowed_papers):
    if not isinstance(selection, dict) or len(selection) > 5 or selection.keys() - set(allowed_papers):
        raise ValueError('SI_SELECTION_OUTSIDE_FROZEN_PAPER_SCOPE')
    for row in selection.values():
        if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{64}', str(row.get('parent_sha256'))):
            raise ValueError('SI_PARENT_PIN_REQUIRED')
        files = row.get('filenames')
        if not isinstance(files, list) or not 1 <= len(files) <= 5 or not all(
                isinstance(name, str) and re.fullmatch(r'[a-zA-Z0-9_+. -]{3,180}', name)
                for name in files) or len(files) != len(set(files)):
            raise ValueError('SI_SELECTION_FILENAMES_REQUIRED')
    return selection


def read_selection(path, expected_sha, papers):
    from .r30_ledger import digest
    data = Path(path).read_bytes()
    if digest(data) != expected_sha:
        raise ValueError('SI_SELECTION_SHA_DRIFT')
    return validate_selection(json.loads(data), papers)
