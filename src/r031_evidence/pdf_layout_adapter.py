"""R15 lightweight PDF text-layer geometry adapter, never synthetic table cells."""
import hashlib,shutil,subprocess,time,xml.etree.ElementTree as ET
from pathlib import Path

def page_geometry(path,expected_sha,max_pages=2):
    p=Path(path).resolve()
    if not p.is_file() or p.stat().st_size>30000000:
        raise ValueError("R15_PDF_SIZE_OR_SOURCE_HOLD")
    if not 1<=max_pages<=3:raise ValueError("R15_PDF_PAGE_LIMIT")
    raw=p.read_bytes()
    if not raw.startswith(b"%PDF-") or hashlib.sha256(raw).hexdigest()!=expected_sha:
        raise ValueError("R15_AUTHOR_PDF_ORIGINAL_SHA_HOLD")
    executable=shutil.which("pdftotext")
    if not executable:
        return {"state":"PDF_ADAPTER_MISSING_HOLD","source_sha":expected_sha,"words":[]}
    started=time.monotonic()
    cmd=[executable,"-f","1","-l",str(max_pages),"-bbox-layout",str(p),"-"]
    result=subprocess.run(cmd,capture_output=True,timeout=45)
    duration_ms=round((time.monotonic()-started)*1000)
    if result.returncode or len(result.stdout)>7000000:
        return {"state":"PDF_TEXT_LAYER_UNREADABLE_HOLD","source_sha":expected_sha,
                "words":[],"elapsed_ms":duration_ms}
    root=ET.fromstring(result.stdout)
    pages=[n for n in root.iter() if n.tag.rsplit("}",1)[-1]=="page"]
    out=[]
    for i,page in enumerate(pages,1):
        if i>max_pages:break
        for j,word in enumerate(n for n in page.iter() if n.tag.rsplit("}",1)[-1]=="word"):
            rawword=" ".join("".join(word.itertext()).split())
            try:box=[float(word.get(k)) for k in ("xMin","yMin","xMax","yMax")]
            except (TypeError,ValueError):continue
            if rawword and box[2]>=box[0] and box[3]>=box[1]:
                out.append({"anchor":f"pdf/page-{i}/word-{j+1}","page":i,
                            "bbox_pdf_points":box,"author_pdf_text_layer":rawword})
            if len(out)>=12000:break
    return {"state":"PDF_TEXT_GEOMETRY_ONLY_TABLE_NOT_VERIFIED",
            "source_sha":expected_sha,"words":out,
            "word_count":len(out),"pages_scanned":len(pages),
            "elapsed_ms":duration_ms,
            "table_cell_verification":"HOLD",
            "sci_approved":False,"Gold":0}
