"""Private, pinned, source-free release bundle builder. No service or network."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,zipfile,io,tarfile,tomllib
p=argparse.ArgumentParser();p.add_argument('--lab',required=True);p.add_argument('--phase',required=True);a=p.parse_args()
gitroot=Path(__file__).resolve().parents[1];lab=Path(a.lab)
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=gitroot,text=True).strip()
version=tomllib.loads(subprocess.check_output(['git','show',commit+':pyproject.toml'],cwd=gitroot,text=True))['project']['version']
out=lab/'reports'/('phase1b_'+a.phase)/'dist';out.mkdir(parents=True,exist_ok=True)
root=out/'source_from_git'
if root.exists():shutil.rmtree(root)
root.mkdir()
archive=subprocess.check_output(['git','archive',commit],cwd=gitroot)
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(root,filter='data')
subprocess.run(['uv','build','--offline','--out-dir',str(out/'core')],cwd=root,check=True)
name=f'R031_Literature_Mining_{version}_private_release';folder=out/name;folder.mkdir(exist_ok=True)
files={str(p.relative_to(out)):p for p in (out/'core').glob('*') if p.suffix in ('.whl','.gz') and p.name.startswith('r031_evidence-'+version)}
for n in ['install.sh','INSTALL.md','THIRD_PARTY_NOTICE.md','RELEASE_LICENSE_STATUS.md','MODEL_SHA_MANIFEST.json','R26_PROFILE_EXAMPLES.md','R28_OPERATION_MODES.md','R29_CONTRACT_RECOVERY.md','R30_ACQUISITION.md','R31_COVERAGE.md','R32_WORKFLOWS.md','R33_SCIENTIFIC_WORKBENCH.md','R38_ATP_CONTENT_PILOT.md','R39_MACROPHAGE_ATP_PRODUCTION.md','R41_MODEL_DATA.md']:
 files[n]=root/'product'/n
files['README.md']=root/'README.md'
files['worker/r031_source_adapters-0.1.0-py3-none-any.whl']=lab/'reports/phase1b_r21/dist/worker/r031_source_adapters-0.1.0-py3-none-any.whl'
files['worker/requirements.runtime.lock']=lab/'reports/phase1b_r21/dist/R031_Literature_Mining_0.21.0_private_release/worker/requirements.runtime.lock'
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
m=dict(schema='asterloom.private-release/2',core_version=version,worker_version='0.1.0',commit=commit,science_gold=0,distribution='PRIVATE_OWNER_EVALUATION',author_originals_in_bundle=False,scientific_databases_in_bundle=False,model_weights_in_bundle=False,scheduler_enabled=False,files={})
for n,s in files.items():
 d=folder/n;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(s,d)
 if n=='README.md':d.write_text(d.read_text().replace('(product/','('))
 m['files'][n]=dict(sha256=sha(d),bytes=d.stat().st_size)
os.chmod(folder/'install.sh',0o755);(folder/'RELEASE_SHA_MANIFEST.json').write_text(json.dumps(m,indent=2)+'\n')
zpath=out/(name+'.zip')
with zipfile.ZipFile(zpath,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for f in sorted(folder.rglob('*')):
  if f.is_file():
   assert f.suffix not in ('.xml','.pdf','.nxml','.sqlite3','.xlsx');z.write(f,str(f.relative_to(out)))
with zipfile.ZipFile(zpath) as z:assert z.testzip() is None
result=dict(core_version=version,commit=m['commit'],zip=str(zpath),zip_sha256=sha(zpath),bytes=zpath.stat().st_size,core_wheel_sha256=m['files'][f'core/r031_evidence-{version}-py3-none-any.whl']['sha256'],manifest_sha256=sha(folder/'RELEASE_SHA_MANIFEST.json'))
(out.parent/(a.phase.upper()+'_PRIVATE_RELEASE.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
