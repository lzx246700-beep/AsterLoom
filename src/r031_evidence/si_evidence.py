"""Read author supplementary material as source evidence, never as a value fill.

Why this module exists
----------------------
The D1 task requires the author's **supplementary material** to be part of the
evidence source, not only the main JATS.  The corpus stores SI as
``supplementary.zip`` next to each paper: mostly figure images (that is the
figure-quantification lane), but 16 archives carry docx/xlsx/txt documents that
can hold ATP tables and methods.

What it does
------------
* opens the archive read-only, in memory (nothing is extracted to disk);
* pulls text and table cells out of ``docx`` (``word/document.xml``),
  ``xlsx`` (shared strings + sheet cells) and ``txt`` with the standard library
  only;
* keeps a per-member SHA so any later record can point back to the exact bytes;
* flags members that print ATP together with numbers, because those are the only
  ones that could carry a supplementary numeric Y.

What it deliberately does not do
--------------------------------
It never attaches an SI condition to a main-text experiment.  An SI methods
sentence is a different document; borrowing "treated for 24 h" from it into a
specific dose group would report an inference as an author measurement.  SI
findings are recorded as candidates with their own locator and SHA, and an
explicit ``SI_REQUIRES_EXPERIMENT_LINK`` gap.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

SI_SCHEMA = 'asterloom.supplementary-evidence/1'

TEXT_SUFFIXES = {'.docx', '.xlsx', '.txt'}
UNREADABLE_SUFFIXES = {'.pdf', '.doc'}

_ATP = re.compile(r'(?i)\bATP\b|adenosine\s+triphosphate')
_NUMBER = re.compile(r'(?<!\w)\d+(?:\.\d+)?')
_WS = re.compile(r'\s+')


def _norm(text: str) -> str:
    return _WS.sub(' ', text or '').strip()


def _q(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def docx_blocks(raw: bytes) -> list[dict]:
    """Paragraph and table blocks from a docx, in document order."""
    out: list[dict] = []
    with zipfile.ZipFile(_as_stream(raw)) as z:
        if 'word/document.xml' not in z.namelist():
            return out
        root = ET.fromstring(z.read('word/document.xml'))
    body = next((n for n in root.iter() if _q(n.tag) == 'body'), root)
    table_index = 0
    for node in body:
        tag = _q(node.tag)
        if tag == 'p':
            text = _norm(''.join(t.text or '' for t in node.iter() if _q(t.tag) == 't'))
            if text:
                out.append(dict(kind='SI_PARAGRAPH', text=text))
        elif tag == 'tbl':
            table_index += 1
            rows = []
            for tr in node.iter():
                if _q(tr.tag) != 'tr':
                    continue
                cells = []
                for tc in tr:
                    if _q(tc.tag) != 'tc':
                        continue
                    cells.append(_norm(''.join(t.text or ''
                                               for t in tc.iter()
                                               if _q(t.tag) == 't')))
                if any(cells):
                    rows.append(cells)
            if rows:
                out.append(dict(kind='SI_TABLE', text=' '.join(' '.join(r) for r in rows),
                                rows=rows, table_index=table_index))
    return out


def xlsx_blocks(raw: bytes) -> list[dict]:
    """Sheet rows from an xlsx using shared strings, no third-party library."""
    out: list[dict] = []
    with zipfile.ZipFile(_as_stream(raw)) as z:
        names = z.namelist()
        shared: list[str] = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root:
                shared.append(_norm(''.join(t.text or '' for t in si.iter()
                                            if _q(t.tag) == 't')))
        sheets = sorted(n for n in names
                        if n.startswith('xl/worksheets/sheet') and n.endswith('.xml'))
        for sheet in sheets:
            root = ET.fromstring(z.read(sheet))
            rows = []
            for row in root.iter():
                if _q(row.tag) != 'row':
                    continue
                values = []
                for cell in row:
                    if _q(cell.tag) != 'c':
                        continue
                    kind = cell.get('t')
                    value = ''
                    for child in cell:
                        tag = _q(child.tag)
                        if tag == 'v':
                            value = child.text or ''
                        elif tag == 'is':
                            value = ''.join(t.text or '' for t in child.iter()
                                            if _q(t.tag) == 't')
                    if kind == 's' and value.isdigit():
                        idx = int(value)
                        value = shared[idx] if idx < len(shared) else ''
                    values.append(_norm(value))
                if any(values):
                    rows.append(values)
            if rows:
                out.append(dict(kind='SI_TABLE',
                                text=' '.join(' '.join(r) for r in rows),
                                rows=rows, table_index=len(out) + 1,
                                member=sheet))
    return out


def _as_stream(raw: bytes):
    import io
    return io.BytesIO(raw)


def read_supplementary(path: str | Path) -> dict:
    """Read one ``supplementary.zip`` into locator-stable blocks."""
    path = Path(path)
    if not path.is_file():
        return dict(schema=SI_SCHEMA, path=str(path), present=False, members=[])
    archive_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    members: list[dict] = []
    blocks: list[dict] = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            suffix = Path(info.filename).suffix.lower()
            member = dict(name=info.filename, suffix=suffix,
                          bytes=info.file_size,
                          sha256=hashlib.sha256(z.read(info)).hexdigest())
            member['readable'] = suffix in TEXT_SUFFIXES
            if suffix in UNREADABLE_SUFFIXES:
                member['gap'] = 'SI_FORMAT_NOT_PARSED_IN_THIS_MODULE'
            if member['readable']:
                raw = z.read(info)
                if suffix == '.docx':
                    parsed = docx_blocks(raw)
                elif suffix == '.xlsx':
                    parsed = xlsx_blocks(raw)
                else:
                    text = raw.decode('utf-8', 'ignore')
                    parsed = [dict(kind='SI_PARAGRAPH', text=_norm(text))] if _norm(text) else []
                for i, block in enumerate(parsed):
                    block = dict(block)
                    block.update(locator=f'si:{info.filename}#{i}',
                                 member=info.filename, member_sha256=member['sha256'],
                                 source='AUTHOR_SI')
                    block['atp'] = bool(_ATP.search(block['text']))
                    block['numeric'] = len(_NUMBER.findall(block['text'])) >= 2
                    blocks.append(block)
                member['blocks'] = len(parsed)
                member['atp_blocks'] = sum(1 for b in parsed if _ATP.search(b['text']))
            members.append(member)
    findings = [b for b in blocks if b['atp'] and b['numeric']]
    return dict(schema=SI_SCHEMA, path=str(path), present=True,
                archive_sha256=archive_sha, members=members, blocks=blocks,
                atp_numeric_blocks=len(findings),
                gaps=sorted({m['gap'] for m in members if m.get('gap')}),
                findings=[dict(locator=b['locator'], member=b['member'],
                               member_sha256=b['member_sha256'],
                               kind=b['kind'], table_index=b.get('table_index'),
                               gap='SI_REQUIRES_EXPERIMENT_LINK')
                          for b in findings])


def find_for_paper(paper_dir: str | Path) -> Path | None:
    candidate = Path(paper_dir) / 'supplementary.zip'
    return candidate if candidate.is_file() else None
