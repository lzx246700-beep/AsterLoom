"""Run real source-custody regressions against a separately installed wheel."""
from pathlib import Path
import argparse,json,os,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--lab',required=True);p.add_argument('--phase',required=True);p.add_argument('--python',required=True);a=p.parse_args();lab=Path(a.lab);out=lab/'reports'/('phase1b_'+a.phase);root=Path(__file__).resolve().parents[1]
env=os.environ.copy();env.update(R031_LAB_ROOT=str(lab),R30_TEST_INSTALLED='1',R31_TEST_INSTALLED='1',R32_TEST_INSTALLED='1',R29_TEST_INSTALLED='1',R33_TEST_INSTALLED='1',R35_TEST_INSTALLED='1',R36_TEST_INSTALLED='1',R38_TEST_INSTALLED='1',R39_TEST_INSTALLED='1');env.pop('PYTHONPATH',None)
commands={'new_acquisition_coverage':[a.python,'-m','unittest','discover','-s',str(root/'tests'),'-p','test_r3[01]*.py','-v'],'R29':[a.python,str(lab/'tests/test_r29_contract_recovery.py'),'-v']}
if a.phase in ('r32','r33','r38','r39','r40','r41'):commands['R32_workflows']=[a.python,'-m','unittest','discover','-s',str(root/'tests'),'-p','test_r32_workflows.py','-v']
if a.phase=='r33':commands['R33_scientific_workbench']=[a.python,'-m','unittest','discover','-s',str(root/'tests'),'-p','test_r3[3456]*.py','-v']
if a.phase in ('r38','r39','r40','r41'):commands['R33_R38_scientific_pilot']=[a.python,'-m','unittest','discover','-s',str(root/'tests'),'-p','test_r3[345678]*.py','-v']
if a.phase in ('r39','r40','r41'):commands['R39_repairs_and_P01_offline']=[a.python,'-m','unittest','discover','-s',str(root/'tests'),'-p','test_r39*.py','-v']
if a.phase in ("r40","r41"):commands["R40_relations_and_supplements"]=[a.python,"-m","unittest","discover","-s",str(root/"tests"),"-p","test_r40*.py","-v"]
if a.phase=="r41":commands["R41_model_data"]=[a.python,"-m","unittest","discover","-s",str(root/"tests"),"-p","test_r41*.py","-v"]
code="""from pathlib import Path
import types,unittest
lab=Path(%r);suite=unittest.TestSuite()
for n in ['test_r28_execution_modes.py','test_r28_real_atprate_author_source.py']:
 p=lab/'tests'/n;s='\\n'.join(l for l in p.read_text().splitlines() if not l.startswith('sys.path.insert(0,'));m=types.ModuleType(n[:-3]);m.__file__=str(p);exec(compile(s,str(p),'exec'),m.__dict__);suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(m))
# The original finite-mode unit test assumed wall AC power; isolate only that
# dependency, then separately exercise the real guard with AC=False. No service.
from unittest.mock import patch
import r031_evidence.r21_scheduler as scheduler
for group in list(suite):
 for case in list(group):
  if case.__class__.__name__=='EvidenceModes':
   cls=case.__class__
   if not getattr(cls,'_r38_power_fixture_installed',False):
    original=cls.test_04_tick_finite_mode_and_reviewed_fail_closed
    def finite_with_power_fixture(self,_original=original):
     with patch.object(scheduler,'power',return_value={'ac_power':True,'pmset_readable':True,'summary':'UNIT_TEST_AC_FIXTURE_NOT_HOST_STATE'}):return _original(self)
    cls.test_04_tick_finite_mode_and_reviewed_fail_closed=finite_with_power_fixture;cls._r38_power_fixture_installed=True
    def battery_guard(self):
     with self.scheduler() as s:
      cfg=s.DEFAULT.copy();cfg['execution_mode']='evidence_production';cfg['requires_ac_power']=True;s.atomic(s.CONFIG,cfg);s.update(enabled=True,paused=False,stop_requested=False,active=None,consecutive_errors=0)
      with patch.object(s,'power',return_value={'ac_power':False,'pmset_readable':True}),patch.object(s,'once') as call:
       result=s.tick();self.assertEqual(result['status'],'NO_AC_POWER_NOT_RUNNING');call.assert_not_called()
    cls.test_09_battery_guard=battery_guard;group.addTest(cls('test_09_battery_guard'))
r=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(not r.wasSuccessful())
"""%str(lab)
commands['R28']=[a.python,'-c',code];results={}
for name,cmd in commands.items():
 r=subprocess.run(cmd,cwd=out,env=env,text=True,capture_output=True);log=out/(a.phase.upper()+'_COLD_'+name+'.log');log.write_text(r.stdout+r.stderr);results[name]=dict(exit_code=r.returncode,log=str(log));print(name,r.returncode,flush=True)
info=subprocess.check_output([a.python,'-c','import json,r031_evidence;print(json.dumps(dict(version=r031_evidence.__version__,module=r031_evidence.__file__)))'],env=env,text=True,cwd=out);receipt=dict(status='PASS' if all(x['exit_code']==0 for x in results.values()) else 'FAIL',installed=json.loads(info),tests=results,R28_loader='Removed only developer sys.path insertion; original __file__ retained for real scientific fixtures',source_mining_not_human_review=True,R28_power_fixture='Finite mocked tick has AC=True fixture; separate AC=False guard test; production power policy unchanged')
(out/(a.phase.upper()+'_INSTALLED_VALIDATION.json')).write_text(json.dumps(receipt,indent=2)+'\n');assert receipt['status']=='PASS'
