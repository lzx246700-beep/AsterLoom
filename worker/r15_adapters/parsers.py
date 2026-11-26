"""Raw source IR, preserving author characters and locators; never infer missing values."""
import re
from pathlib import Path
from lxml import etree
from .transport import asset
from .ir import PaperIR, TableIR, SourceCell, SourceAnchor, SourceMethod, SupplementAsset

def jats_author_tables(wrap):
    """Select one author table without duplicating a nested copy.

    A direct child table is that table. Otherwise exactly one descendant table,
    including table-wrap/alternatives/table, is that table. Several candidates
    are a hold, not a guess.
    """
    direct = wrap.xpath("./table")
    if len(direct) == 1:
        return direct, None
    if direct:
        return [], "AMBIGUOUS_TABLE_CANDIDATES"
    nested = wrap.xpath(".//table")
    if len(nested) == 1:
        return nested, None
    if len(nested) > 1:
        return [], "AMBIGUOUS_TABLE_CANDIDATES"
    return [], None

def parse_jats(path,paper_id=None):
    a = asset(path)
    tree = etree.parse(str(path),etree.XMLParser(resolve_entities=False,no_network=True))
    root = tree.getroot()
    if root.tag != "article": raise ValueError("JATS_ROOT_HOLD")
    ir = PaperIR(paper_id or Path(path).stem,a,"native-jats-lxml")
    def anchor(node): return SourceAnchor(a.sha256,tree.getpath(node))
    def text(node): return "".join(node.itertext())
    for i,wrap in enumerate(root.xpath(".//table-wrap")):
        chosen, problem = jats_author_tables(wrap)
        if problem:
            ir.holds.append(problem + " " + tree.getpath(wrap))
            continue
        for j,tab in enumerate(chosen):
            cells,headers = [],[]
            occupied = set()
            rows = tab.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
            for r,tr in enumerate(rows):
                col = 0
                for cell in tr.xpath("./td|./th"):
                    while (r,col) in occupied: col += 1
                    rs,cs = int(cell.get("rowspan","1")),int(cell.get("colspan","1"))
                    if not 1<=rs<=10000 or not 1<=cs<=10000: raise ValueError("SPAN_LIMIT_HOLD")
                    role = "header" if cell.tag == "th" or tr.getparent().tag == "thead" else "data"
                    c = SourceCell(r,col,text(cell),anchor(cell),rs,cs,role,
                        metadata={'xml_subtree_reserialized':etree.tostring(cell,encoding='unicode',with_tail=False)})
                    cells.append(c)
                    if role == "header": headers.append({"raw_text":c.raw_text,"anchor":c.anchor.__dict__,"row":r,"column":col,"rowspan":rs,"colspan":cs})
                    for rr in range(r,r+rs):
                        for cc in range(col,col+cs): occupied.add((rr,cc))
                    col += cs
            notes = [{"raw_text":text(n),"anchor":anchor(n).__dict__} for n in wrap.xpath("./table-wrap-foot")]
            ir.tables.append(TableIR(wrap.get("id",f"table-{i+1}")+f":{j+1}",cells,headers,notes,anchor(tab)," ".join(text(n) for n in wrap.xpath("./caption|./label"))))
    # Methods descendants retain section path/title; condition matches are candidates only.
    for sec in root.xpath(".//body//sec"):
        headings = sec.xpath("./title")
        heading = " ".join(text(t) for t in headings)
        lineage = " ".join(" ".join(text(t) for t in s.xpath("./title")) for s in [sec]+list(sec.iterancestors("sec")))
        if re.search(r"method|material|experimental",lineage,re.I):
            for p in sec.xpath("./p"):
                ir.methods.append(SourceMethod(text(p),anchor(p),heading))
    for node in root.xpath(".//supplementary-material//*[@xlink:href]|.//supplementary-material[@xlink:href]",namespaces={"xlink":"http://www.w3.org/1999/xlink"}):
        href = node.get("{http://www.w3.org/1999/xlink}href")
        ir.supplements.append(SupplementAsset(ir.paper_id,a.sha256,href,anchor(node)))
    ir.holds.append("SOURCE_ONLY_NO_SCIENTIFIC_PROMOTION")
    return ir

def parse_xlsx(path,paper_id="UNKNOWN"):
    from openpyxl import load_workbook
    from zipfile import ZipFile
    from posixpath import normpath,join
    a = asset(path); ir = PaperIR(paper_id,a,"openpyxl-raw-cells")
    book = load_workbook(path,data_only=False)
    cached = load_workbook(path,data_only=True)
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as z:
        wb=etree.fromstring(z.read('xl/workbook.xml'))
        rels=etree.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        targets={r.get('Id'):r.get('Target') for r in rels}
        raw_sheets={}
        for s in wb.xpath('//s:sheet',namespaces=ns):
            target=targets[s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            member=normpath(target.lstrip('/') if target.startswith('/') else join('xl',target))
            xml=etree.fromstring(z.read(member))
            raw_sheets[s.get('name')]=(member,{c.get('r'):c for c in xml.xpath('//s:sheetData/s:row/s:c',namespaces=ns)})
    for sheet in book:
        cells = []
        member,raw_cells=raw_sheets[sheet.title]
        for row in sheet:
            for c in row:
                if c.value is None: continue
                raw = str(c.value)
                original=raw_cells.get(c.coordinate)
                vv=original.find('s:v',ns) if original is not None else None
                if c.data_type=='n' and vv is not None:raw=vv.text or ''
                cells.append(SourceCell(c.row-1,c.column-1,raw,SourceAnchor(a.sha256,f"sheet={sheet.title!r}!{c.coordinate}"),
                    metadata={"ooxml_member":member,"ooxml_cell_locator":f"/s:worksheet/s:sheetData/s:row/s:c[@r='{c.coordinate}']","ooxml_v":vv.text if vv is not None else None,
                              "value_type":c.data_type,"number_format":c.number_format,"formula":c.value if c.data_type=="f" else None,
                              "cached_value":cached[sheet.title][c.coordinate].value if c.data_type=="f" else None,
                              "hidden_row":bool(sheet.row_dimensions[c.row].hidden),
                              "hidden_column":bool(sheet.column_dimensions[c.column_letter].hidden),
                              "sheet_state":sheet.sheet_state}))
        ir.tables.append(TableIR(sheet.title,cells,[],[],SourceAnchor(a.sha256,f"sheet={sheet.title!r}"),
                                caption=";".join(str(x) for x in sheet.merged_cells.ranges)))
    book.close();cached.close()
    ir.holds += ["SOURCE_ONLY_NO_SCIENTIFIC_PROMOTION","RAW_VALUE_NOT_DISPLAY_FORMAT","ASSAY_TYPE_UNQUALIFIED"]
    return ir

def parse_csv(path,paper_id="UNKNOWN"):
    import csv
    a = asset(path);ir = PaperIR(paper_id,a,"stdlib-csv")
    with open(path,newline="",encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    cells = [SourceCell(r,c,v,SourceAnchor(a.sha256,f"csv-record={r+1};column={c+1}")) for r,row in enumerate(rows) for c,v in enumerate(row)]
    ir.tables.append(TableIR("csv",cells,[],[],SourceAnchor(a.sha256,"csv")))
    ir.holds.append("SOURCE_ONLY_NO_SCIENTIFIC_PROMOTION")
    return ir

def parse_pdf(path,paper_id="UNKNOWN",standard=False,artifacts_path=None):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.pipeline.native_pdf_pipeline import NativePdfPipeline
    from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice
    if standard:
        opts = PdfPipelineOptions(do_ocr=False,artifacts_path=artifacts_path,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU,num_threads=2))
        option = PdfFormatOption(pipeline_options=opts,backend=PyPdfiumDocumentBackend)
    else:
        option = PdfFormatOption(pipeline_cls=NativePdfPipeline,backend=PyPdfiumDocumentBackend)
    converter = DocumentConverter(allowed_formats=[InputFormat.PDF],format_options={InputFormat.PDF:option})
    result = converter.convert(str(path),raises_on_error=True)
    a = asset(path);ir = PaperIR(paper_id,a,"docling-standard-cpu-no-ocr" if standard else "docling-native-pdf-no-models")
    def anchor(node):
        prov = node.prov[0] if node.prov else None
        bbox = prov.bbox if prov else None
        return SourceAnchor(a.sha256,str(node.self_ref),prov.page_no if prov else None,
                            [bbox.l,bbox.t,bbox.r,bbox.b] if bbox else None,
                            str(bbox.coord_origin) if bbox else None)
    for table in result.document.tables:
        ta = anchor(table)
        cells=[]
        for c in table.data.table_cells:
            ca = SourceAnchor(a.sha256,str(table.self_ref)+f"/r{c.start_row_offset_idx}/c{c.start_col_offset_idx}",ta.page,
                              [c.bbox.l,c.bbox.t,c.bbox.r,c.bbox.b] if c.bbox else ta.bbox,
                              str(c.bbox.coord_origin) if c.bbox else ta.coordinate_system)
            cells.append(SourceCell(c.start_row_offset_idx,c.start_col_offset_idx,c.text,ca,c.row_span,c.col_span,"header" if c.column_header else "data"))
        headers = [{"raw_text":c.raw_text,"anchor":c.anchor.__dict__} for c in cells if c.role=="header"]
        ir.tables.append(TableIR(str(table.self_ref),cells,headers,[],ta,caption=" ".join(result.document.texts[int(ref.cref.rsplit('/',1)[-1])].text for ref in table.captions if ref.cref.startswith('#/texts/'))))
    active=False;method_number=None;heading=''
    for item in result.document.texts:
        if not item.text.strip():continue
        label=str(item.label)
        ir.text_blocks.append(SourceMethod(item.text,anchor(item),label,"PDF_TEXT_BLOCK_UNCLASSIFIED"))
        if 'section_header' in label.lower():
            heading=item.text
            number=re.match(r'^(\d+(?:\.\d+)*)\s*[|.]?\s+',heading)
            if re.search(r'materials?\s+and\s+methods?|experimental\s+(procedures?|methods?)|^methods?\b',heading,re.I):
                active=True;method_number=number.group(1).split('.')[0] if number else None
            elif active and ((number and method_number and number.group(1).split('.')[0]!=method_number) or re.search(r'\b(results|discussion|conclusions?|references)\b',heading,re.I)):
                active=False
        if active:
            ir.methods.append(SourceMethod(item.text,anchor(item),heading,"PDF_SECTION_HEADING_CANDIDATE_REVIEW_HOLD"))
    if not ir.tables: ir.holds.append("NO_TABLE_STRUCTURE_HOLD")
    if not ir.text_blocks: ir.holds.append("IMAGE_ONLY_OR_EMPTY_TEXT_OCR_HOLD")
    if not ir.methods: ir.holds.append("METHODS_SECTION_UNRESOLVED_HOLD")
    ir.holds += ["PDF_NUMERIC_FIDELITY_REQUIRES_AUTHOR_CELL_COMPARISON","FIGURE_BARS_EVIDENCE_NOT_NUMERIC","PDF_FOOTNOTE_LINKAGE_HOLD"]
    return ir,result.document.export_to_dict()
