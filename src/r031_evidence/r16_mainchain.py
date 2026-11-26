"""R16 integrates the completed Worker source adapters into the ONE cumulative SQLite.
Runs the independently installed r15_adapters Python3.12 environment as a JSON
subprocess; does not fork a new scientific database or infer treatment pairs.
"""
from __future__ import annotations
import hashlib,json,os,sqlite3,subprocess
from pathlib import Path
from . import repository as repo

def worker_settings(workspace,worker_python=None):
    """Independent packaged Worker paths, no developer private absolutes."""
    home=Path(workspace).expanduser().resolve()
    root=home.parent if home.name=="state" else home
    config_path=root/"config"/"config.json"
    try:config=json.loads(config_path.read_text())
    except FileNotFoundError:config={}
    plugin=config.get("worker") or {}
    raw=worker_python or os.getenv("R031_SOURCE_ADAPTER_PY") or plugin.get("python") or str(root/"worker"/".venv"/"bin"/"python")
    binary=Path(raw).expanduser().absolute()
    models=os.getenv("R031_DOCLING_MODELS") or plugin.get("models_path") or str(root/"models"/"docling")
    return binary,Path(models).expanduser().absolute()

def sha(raw):return hashlib.sha256(raw if isinstance(raw,bytes) else raw.encode("utf-8")).hexdigest()
def _paths(workspace):
    home=Path(workspace).resolve()
    db=home/"science.sqlite3"
    if not db.exists():raise ValueError("R16_EXISTING_CUMULATIVE_SQLITE_REQUIRED")
    return home,db

def _migrate(home,db):
    repo.configure(home)
    c=repo.connect(db)
    try:repo.migrate(c)
    finally:c.close()

def _worker_python(worker_python=None,workspace=None):
    p,_=worker_settings(workspace or os.getenv("R031_HOME") or str(Path.home()/".r031"),worker_python)
    if not p.is_file():raise RuntimeError("R16_WORKER_ADAPTER_PYTHON_NOT_INSTALLED")
    return p

def _ir_file(home,paper,author_sha,backend_suffix):
    dest=home/"r16_worker_ir"
    dest.mkdir(parents=True,exist_ok=True)
    return dest/(paper+"_"+author_sha[:20]+"_"+backend_suffix+".json")

def parse_source(workspace,source,paper,worker_python=None,standard_pdf=False):
    home,db=_paths(workspace)
    source=Path(source).resolve()
    if not source.is_file():raise ValueError("R16_ORIGINAL_SOURCE_NOT_FOUND")
    if source.suffix.lower() not in (".xml",".nxml",".pdf",".xlsx",".csv"):
        raise ValueError("R16_UNSUPPORTED_SOURCE_FORMAT")
    if not paper or len(paper)>100 or any(ch not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-" for ch in paper):
        raise ValueError("R16_PAPER_ID_INVALID")
    original_sha=sha(source.read_bytes())
    _migrate(home,db)
    suffix=source.suffix.lower()
    expected_backend=(
        "docling-standard-cpu-no-ocr" if suffix==".pdf" and standard_pdf
        else "docling-native-pdf-no-models" if suffix==".pdf"
        else "native-jats-lxml" if suffix in (".xml",".nxml")
        else "openpyxl-raw-cells" if suffix==".xlsx"
        else "stdlib-csv"
    )
    c=repo.connect(db)
    try:
        old=c.execute("SELECT parsed_asset_id,cell_count,table_count,backend FROM r16_parsed_asset WHERE paper_key=? AND original_sha=? AND backend=?",
          (paper,original_sha,expected_backend)).fetchone()
        if old:return {"state":"ALREADY_INDEXED","paper":paper,"original_sha":original_sha,"cells":old[1],"tables":old[2],"backend":old[3]}
    finally:c.close()
    output=_ir_file(home,paper,original_sha,"pdf_standard" if standard_pdf else "native")
    worker=_worker_python(worker_python,home)
    # The checked worker command also passed -I -B. Those flags did not create
    # tables for table-wrap/alternatives/table, so an empty IR table list is kept.
    argv=[str(worker),"-m","r15_adapters.cli","parse","--input",str(source),
          "--paper-id",paper,"--output",str(output)]
    if standard_pdf and source.suffix.lower()==".pdf":
        # Docling model weights were already pinned and downloaded by the Worker.
        # Use them offline; do not silently download arbitrary new weights.
        model_root=worker_settings(home,worker_python)[1]
        if not model_root.is_dir():raise RuntimeError("R16_STANDARD_PDF_MODELS_NOT_INSTALLED")
        argv.extend(["--standard-pdf","--models",str(model_root)])
    child_env=os.environ.copy()
    if standard_pdf and suffix==".pdf":
        child_env["HF_HUB_OFFLINE"]="1"
        child_env["TRANSFORMERS_OFFLINE"]="1"
        child_env["DOCLING_ARTIFACTS_PATH"]=str(model_root)
    process=subprocess.run(argv,capture_output=True,text=True,timeout=240,env=child_env)
    if process.returncode and suffix==".csv":
        # Narrow original-byte fallback when Worker cannot decode a lawful
        # native SI CSV (e.g. cp1252 semicolon); preserve the real file SHA.
        try:
            from .r17_csv_codec import original_csv_ir
            recovered=original_csv_ir(source,paper,original_sha)
            output.write_text(json.dumps(recovered,ensure_ascii=False,sort_keys=True))
            process=None
        except (ValueError,UnicodeError) as exc:
            return {"state":"ADAPTER_PARSE_HOLD","paper":paper,"original_sha":original_sha,
              "reason":str(exc)[:600],"backend":"r15_adapters+source_csv_codec"}
    elif process.returncode:
        return {"state":"ADAPTER_PARSE_HOLD","paper":paper,"original_sha":original_sha,
          "reason":(process.stderr or process.stdout)[-900:],"backend":"r15_adapters"}
    ir_bytes=output.read_bytes()
    ir=json.loads(ir_bytes)
    if ir.get("asset",{}).get("sha256")!=original_sha:
        raise RuntimeError("R16_WORKER_IR_ASSET_SHA_NOT_ORIGINAL")
    backend=str(ir["backend"])
    tables=ir.get("tables",[])
    methods=ir.get("methods",[])
    supplements=ir.get("supplements",[])
    parsed_id="r16:asset:"+sha(paper+"|"+original_sha+"|"+backend)[:32]
    c=repo.connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            repo.put(c,"r16_parsed_asset",{
               "parsed_asset_id":parsed_id,"paper_key":paper,"original_sha":original_sha,
               "original_path":str(source),"source_format":source.suffix.lower(),
               "backend":backend,"ir_sha":sha(ir_bytes),"ir_path":str(output),
               "table_count":len(tables),"cell_count":sum(len(t.get("cells",[])) for t in tables),
               "method_count":len(methods),"supplement_count":len(supplements),
               "science_state":"SOURCE_ONLY","training_approved":0})
            for t in tables:
                table_id=str(t["table_id"])
                table_key="r16:table:"+sha(parsed_id+"|"+table_id)[:32]
                anc=t.get("anchor") or {}
                repo.put(c,"r16_source_table",{
                    "table_key":table_key,"parsed_asset_id":parsed_id,"paper_key":paper,
                    "table_id":table_id,"caption_original":t.get("caption"),
                    "footnotes_json":json.dumps(t.get("footnotes",[]),ensure_ascii=False,sort_keys=True),
                    "locator":anc.get("locator")})
                for cell in t.get("cells",[]):
                    anchor=cell.get("anchor") or {}
                    locator=anchor.get("locator")
                    if not locator or anchor.get("asset_sha256")!=original_sha:
                        raise RuntimeError("R16_WORKER_CELL_MISSING_ORIGINAL_SHA_OR_ANCHOR")
                    key="r16:cell:"+sha(table_key+"|"+str(cell.get("row"))+"|"+str(cell.get("column"))+"|"+locator)[:32]
                    repo.put(c,"r16_source_cell",{
                      "cell_key":key,"table_key":table_key,"parsed_asset_id":parsed_id,
                      "paper_key":paper,"cell_row":int(cell["row"]),"cell_column":int(cell["column"]),
                      "cell_role":cell.get("role","data"),
                      "original_literal":str(cell.get("raw_text","")),"original_locator":locator,
                      "page_number":anchor.get("page"),"bbox_json":json.dumps(anchor.get("bbox")),
                      "author_sha":original_sha,
                      "metadata_json":json.dumps(cell.get("metadata",{}),ensure_ascii=False,sort_keys=True)})
            for index,m in enumerate(methods):
                anchor=m.get("anchor") or {}
                locator=anchor.get("locator") or f"method-{index+1}"
                repo.put(c,"r16_source_method",{
                  "method_key":"r16:method:"+sha(parsed_id+"|"+locator)[:32],
                  "parsed_asset_id":parsed_id,"paper_key":paper,
                  "heading_original":m.get("heading"),
                  "text_original":str(m.get("raw_text") or ""),
                  "original_locator":locator,"page_number":anchor.get("page")})
            for index,s in enumerate(supplements):
                anchor=s.get("href_anchor") or {}
                locator=anchor.get("locator") or f"supplement-{index+1}"
                repo.put(c,"r16_source_supplement",{
                  "supplement_key":"r16:supp:"+sha(parsed_id+"|"+locator)[:32],
                  "parsed_asset_id":parsed_id,"paper_key":paper,"href_literal":s.get("href_literal"),
                  "original_locator":locator,"state":s.get("state","ACCESS_HOLD")})
            c.commit()
        except BaseException:c.rollback();raise
    finally:c.close()
    return {"state":"SOURCE_IR_INDEXED","paper":paper,"original_sha":original_sha,
            "backend":backend,"tables":len(tables),
            "cells":sum(len(t.get("cells",[])) for t in tables),"methods":len(methods),
            "supplements":len(supplements),"ir_sha":sha(ir_bytes),"ir_path":str(output),
            "scientific_pairs_added":0,"Gold":0}

def backlog(workspace,max_items=20,worker_python=None):
    if not 1<=max_items<=100:raise ValueError("R16_BACKLOG_MAX_1_TO_100")
    home,db=_paths(workspace)
    _migrate(home,db)
    c=repo.connect(db)
    try:
        sources=c.execute("""SELECT p.paper_key,a.original_path,a.original_sha
          FROM asset_ref a JOIN paper_ref p ON a.paper_id=p.paper_id
          WHERE NOT EXISTS(SELECT 1 FROM r16_parsed_asset x WHERE x.paper_key=p.paper_key AND x.original_sha=a.original_sha)
          ORDER BY p.paper_key LIMIT ?""",(max_items,)).fetchall()
    finally:c.close()
    done=[]
    for paper,path,expected_sha in sources:
        try:
            original=Path(path)
            # R07 asset_ref paths are immutable, even after an explicit move.
            # A user-approved import copies exact source SHA bytes to the new
            # private home; try that mirror only after the old path is absent.
            if not original.is_file():
                original=home/"r15_author_raw"/(expected_sha+Path(path).suffix.lower())
            if not original.is_file() or sha(original.read_bytes())!=expected_sha:
                raise ValueError("ORIGINAL_SOURCE_SHA_CHANGED_OR_MIRROR_NOT_IMPORTED")
            done.append(parse_source(home,original,paper,worker_python))
        except Exception as exc:
            done.append({"paper":paper,"state":"SOURCE_IR_HOLD","reason":str(exc)[:350]})
    return {"status":"R16_MAINLINE_WORKER_ADAPTER_INDEX","processed":len(done),
            "new_source_assets":sum(x.get("state")=="SOURCE_IR_INDEXED" for x in done),
            "cells_indexed":sum(x.get("cells",0) for x in done if x.get("state")=="SOURCE_IR_INDEXED"),
            "results":done,"Gold":0}

def status(workspace):
    home,db=_paths(workspace)
    c=repo.connect(db)
    try:
        return {"status":"R16_CUMULATIVE_AUTHOR_IR","db":str(db),
           "papers":c.execute("select count(distinct paper_key) from r16_parsed_asset").fetchone()[0],
           "assets":c.execute("select count(*) from r16_parsed_asset").fetchone()[0],
           "tables":c.execute("select count(*) from r16_source_table").fetchone()[0],
           "cells":c.execute("select count(*) from r16_source_cell").fetchone()[0],
           "methods":c.execute("select count(*) from r16_source_method").fetchone()[0],
           "supplements":c.execute("select count(*) from r16_source_supplement").fetchone()[0],
           "science_pairs":c.execute("select count(*) from contrast_proposal").fetchone()[0],
           "schema":c.execute("pragma user_version").fetchone()[0],"Gold":0}
    finally:c.close()

def search(workspace,term,limit=25):
    if not term or len(term)>120 or not 1<=limit<=200:raise ValueError("R16_IR_SEARCH_QUERY_INVALID")
    home,db=_paths(workspace)
    c=repo.connect(db)
    try:
        sql="""SELECT c.paper_key,t.table_id,t.caption_original,c.cell_row,c.cell_column,
          c.original_literal,c.original_locator,c.author_sha
          FROM r16_source_cell c JOIN r16_source_table t ON c.table_key=t.table_key
          WHERE lower(c.original_literal) LIKE ? OR lower(coalesce(t.caption_original,'')) LIKE ?
          ORDER BY c.paper_key,c.table_key,c.cell_row,c.cell_column LIMIT ?"""
        needle="%"+term.lower()+"%"
        rows=c.execute(sql,(needle,needle,limit)).fetchall()
        return {"status":"R16_AUTHOR_ORIGINAL_CELL_SEARCH","term":term,"results":[
           dict(zip(("paper","table","caption","row","column","original_literal","locator","source_sha"),r)) for r in rows],"Gold":0}
    finally:c.close()

def harvest(workspace,query,provider="EUROPE_PMC",max_hits=20,max_sources=8,parse_max=25,worker_python=None):
    from . import provider_discovery,provider_acquisition
    home,db=_paths(workspace)
    _migrate(home,db)
    d=provider_discovery.discover(home,db,query=query,provider=provider,max_results=max_hits)
    a=provider_acquisition.acquire(home,db,d["batch_id"],max_sources=max_sources) if d["hits"] else {}
    ir=backlog(home,max_items=parse_max,worker_python=worker_python)
    return {"status":"R16_REAL_QUERY_TO_AUTHOR_CELL_DATABASE",
            "discovery":{"batch_id":d["batch_id"],"provider":provider,"metadata_hits":d["metadata_hits"]},
            "acquisition":a,
            "source_ir":ir,
            "total":status(home),"Gold":0}
