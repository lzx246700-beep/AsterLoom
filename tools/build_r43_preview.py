"""Build owner-only 0.37.0.dev1 R43 engineering preview, not a release."""
from pathlib import Path
import argparse, hashlib, json, subprocess, zipfile, tomllib

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--lab",required=True,type=Path)
    lab=a.parse_args().lab.resolve()
    repo=Path(__file__).resolve().parents[1]
    def git(*argv):
        return subprocess.check_output(["git",*argv],cwd=repo,text=True).strip()
    head=git("rev-parse","HEAD")
    if git("status","--porcelain") or git("branch","--show-current")!="codex/r43-relation-understanding":
        raise RuntimeError("MUST_BUILD_FROM_CLEAN_R43_GIT_COMMIT")
    version=tomllib.loads((repo/"pyproject.toml").read_text())["project"]["version"]
    if version!="0.37.0.dev1" or '__version__ = "0.37.0.dev1"' not in (repo/"src/r031_evidence/__init__.py").read_text():
        raise RuntimeError("PREVIEW_VERSION_MISMATCH")
    out=lab/"reports/phase1b_r43/preview_dist"
    if out.exists():raise RuntimeError("PREVIEW_OUTPUT_ALREADY_EXISTS")
    out.mkdir(parents=True)
    subprocess.run(["uv","build","--offline","--wheel","--sdist","--out-dir",str(out/"core")],
                   cwd=repo,capture_output=True,text=True,check=True,timeout=200)
    core=out/"core"/("r031_evidence-"+version+"-py3-none-any.whl")
    sdist=out/"core"/("r031_evidence-"+version+".tar.gz")
    worker=lab/"reports/phase1b_r42/release_0360/wheels/worker/r031_source_adapters-0.1.1-py3-none-any.whl"
    sources={
      "core/"+core.name:core,"core/"+sdist.name:sdist,"worker/"+worker.name:worker,
      "install.sh":repo/"product/install.sh","R43_PREVIEW.md":repo/"product/R43_PREVIEW.md",
      "README.md":repo/"README.md","INSTALL.md":repo/"product/INSTALL.md",
      "RELEASE_LICENSE_STATUS.md":repo/"product/RELEASE_LICENSE_STATUS.md",
      "THIRD_PARTY_NOTICE.md":repo/"product/THIRD_PARTY_NOTICE.md",
    }
    folder=out/("AsterLoom_R43_"+version+"_PRIVATE_ENGINEERING_PREVIEW")
    folder.mkdir()
    manifest={"schema":"asterloom.r43.private-preview/1",
      "status":"ENGINEERING_PREVIEW_ONLY_NOT_SCIENTIFICALLY_APPROVED",
      "official_current_core":"0.36.0","preview_version":version,"worker":"0.1.1",
      "source_commit":head,"r43_rule_sha256":sha(repo/"src/r031_evidence/r43_relation.py"),
      "training_Gold":0,"human_science_review":0,"precision":None,"recall":None,
      "official_0_37_release":False,"scheduler_started":False,
      "source_articles_in_bundle":False,"science_sqlite_in_bundle":False,
      "model_weights_in_bundle":False,"files":{}}
    for name,path in sources.items():
        if not path.is_file():raise RuntimeError("BUNDLE_INPUT_MISSING:"+name)
        target=folder/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(path.read_bytes())
        manifest["files"][name]={"sha256":sha(target),"bytes":target.stat().st_size}
    (folder/"install.sh").chmod(0o755)
    manifest_file=folder/"RELEASE_SHA_MANIFEST.json"
    manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    zip_path=out/(folder.name+".zip")
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():continue
            if path.suffix.lower() in (".xml",".nxml",".xlsx",".pdf",".sqlite3"):
                raise RuntimeError("SOURCE_OR_DATABASE_WRONGLY_BUNDLED")
            z.write(path,path.relative_to(out))
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip() is not None or len(z.namelist())!=len(sources)+1:
            raise RuntimeError("PRIVATE_PREVIEW_ZIP_INTEGRITY_OR_MANIFEST_FAIL")
    result={"status":manifest["status"],"source_commit":head,
      "zip":str(zip_path),"zip_sha256":sha(zip_path),
      "core_wheel_sha256":sha(core),"worker_wheel_sha256":sha(worker),
      "manifest_sha256":sha(manifest_file),
      "files":len(sources)+1,"Gold":0,"official_release":False}
    (out/"R43_PRIVATE_PREVIEW_BUILD_RECEIPT.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
