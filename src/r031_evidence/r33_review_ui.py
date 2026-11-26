"""Loopback-only bounded review/competition preview; separate annotation store.
No science DB writes, no external resources, no Gold approval endpoint.
"""
from pathlib import Path
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib,json,mimetypes,os,secrets,sqlite3,threading,time,urllib.parse
from .r33_evaluation import load_packet,FIELDS,ERRORS,evaluate
from .r33_review_store import annotate,history,draft_reference
from .r30_ledger import db_path,audit,digest
HTML=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AsterLoom 星织 · 科学审核</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f1f5f6;color:#19343c;font:15px -apple-system,BlinkMacSystemFont,sans-serif}header{background:#102e38;color:#fff;padding:20px 30px;display:flex;justify-content:space-between}h1{font-size:23px;margin:0}small{color:#91b8bf}nav{padding:12px 28px;background:#fff;border-bottom:1px solid #cedbdd;display:flex;gap:10px}button,select,input,textarea{font:inherit;border:1px solid #bed0d4;border-radius:7px;padding:9px;background:#fff;color:#163a45}button{cursor:pointer}button.active,.save{background:#087d82;color:#fff;border-color:#087d82}.warning{margin:16px 28px;padding:13px;background:#fff2d9;border-left:4px solid #c68b25}.grid{display:grid;grid-template-columns:265px 1fr;gap:18px;margin:18px 28px}.card{background:white;border:1px solid #d5e0e1;border-radius:12px;padding:18px}.list{height:70vh;overflow:auto}.case{width:100%;text-align:left;margin:0 0 8px;font-size:13px}.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}pre{white-space:pre-wrap;word-break:break-word;font:12px/1.65 ui-monospace,monospace;background:#f3f7f7;padding:12px;max-height:56vh;overflow:auto}.field{display:grid;grid-template-columns:105px 1fr;gap:9px;margin-bottom:9px;align-items:center}textarea{width:100%;min-height:65px}#overview,#quality{margin:18px 28px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.num{font-size:28px;color:#087d82}.hidden{display:none}#notice{padding:10px;color:#8a471a}label{font-size:13px}footer{margin:20px 28px;color:#58767e;font-size:12px}@media(max-width:1100px){.split{grid-template-columns:1fr}.grid{grid-template-columns:200px 1fr}.stats{grid-template-columns:repeat(2,1fr)}}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:11px;border-bottom:1px solid #dce7e8;vertical-align:top}th{color:#50737c;background:#f5f9f9}.pill{display:inline-block;padding:4px 9px;border-radius:12px;background:#e5f4f1;color:#116c64}.muted{color:#69858b}.case{overflow-wrap:anywhere;line-height:1.6}.case.selected{border-color:#087d82;background:#e7f5f3}#anchor{overflow-wrap:anywhere;color:#617e85;font:12px/1.7 ui-monospace,monospace}.field>div{overflow-wrap:anywhere}details{margin-top:15px}</style>
<header><div><h1>AsterLoom · 星织</h1><small>Scientific Evidence to Structured Data</small></div><div>科研证据 · 来源绑定 · 人工审阅<br><small id="mode"></small></div></header>
<nav><button data-page="overview" class="active">采集与覆盖</button><button data-page="evidence">科学证据浏览器</button><button data-page="review">科学审核工作台</button><button data-page="quality">数据集与质量报告</button><button data-page="pilot" id="pilotTab" class="hidden">ATP课题数据库</button></nav>
<div class="warning" id="banner">正在核对冻结来源……</div><section id="overview"><div class="stats" id="stats"></div><div class="card" style="margin-top:15px"><h3>来源与缺口</h3><div id="queue"></div><details><summary>查看队列完整性审计</summary><pre id="ledger"></pre></details><p>缓存元数据、在线获取、解析和完整科学记录分别统计。SOURCE_ONLY / HOLD 不等于已批准训练数据。</p></div></section>
<section id="workspace" class="grid hidden"><aside class="card list" id="cases"></aside><main class="card"><h3 id="paper"></h3><p id="anchor"></p><div class="split"><section><h3>作者原始证据</h3><select id="asset"></select><button id="loadSource">查看原文</button><pre id="evidence"></pre></section><section><h3 id="candidateTitle">人工标注字段</h3><div id="fields"></div><div id="annotation"><div class="field"><label>标注来源定位</label><input id="evidenceLocator" placeholder="XPath / Sheet A1 / 页码等真实锚点"></div><div class="field"><label>审阅者标识</label><input id="reviewer" placeholder="由真实审阅者填写"></div><div class="field"><label>标注字段</label><select id="field"></select></div><div class="field"><label>结论</label><select id="decision"><option value="UNVERIFIED">待核验</option><option value="CORRECT">正确</option><option value="INCORRECT">错误</option><option value="MISSING">作者未报告（需核对）</option></select></div><div class="field"><label>标注值</label><input id="value" placeholder="保留原始值；缺失不填0"></div><div class="field"><label>错误分类</label><select id="error"></select></div><label>依据与修改原因</label><textarea id="reason"></textarea><button class="save" id="save">保存评测标注 · 保留修改历史</button><button id="export">导出待复核标注</button><p id="notice"></p><pre id="history"></pre></div></section></div></main></section>
<section id="quality" class="hidden"><div class="card"><h3>可重复质量评价</h3><p>人工评测答案与模型训练Gold分离。缺少真实人工答案及发布确认时，Precision / Recall / F1保持null。</p><div id="metrics"></div><button id="refresh">刷新真实状态</button></div></section>
<section id="pilot" class="hidden" style="margin:18px 28px"><div class="card"><h3>R031 · ATP含量课题</h3><p id="pilotDescription">正在核对课题范围与原始来源……</p><label>视图 </label><select id="pilotView"><option value="atp_content_candidates">ATP含量候选（排除已知时间坐标/相对百分比）</option><option value="source_only">全部来源与HOLD记录</option><option value="profile_complete_candidates">既有Profile字段完整候选（未科学批准）</option><option value="reviewed">独立科学Reviewed</option></select><input id="pilotPaper" placeholder="论文身份筛选，如PMC10787699"><button id="pilotSearch">查询</button><button id="pilotCSV">导出当前视图CSV</button><p id="pilotCounts"></p><div id="pilotRows" style="overflow:auto"></div><details><summary>来源范围与任务预算</summary><pre id="pilotScope"></pre></details></div><div class="card" style="margin-top:16px"><h3>创建Profile任务与预算草案</h3><select id="taskProfile"><option value="small_molecule_atp_content_v1">ATP含量</option><option value="small_molecule_atp_rate_v1">ATP生成速率</option><option value="bioactive_polymer_v1">共价活性高分子</option></select><input id="taskCompounds" value="quercetin OR resveratrol OR curcumin" aria-label="化合物检索词"><label>目标论文 <input id="taskN" type="number" min="20" max="50" value="20" style="width:80px"></label><label>请求上限 <input id="taskReq" type="number" min="1" max="60" value="40" style="width:80px"></label><button id="taskCreate">保存任务草案</button><p>草案显示检索式与预算，通过CLI显式执行有界采集；人工审核和训练批准保持独立。</p><pre id="taskPreview"></pre></div></section>
<footer>该本机页面仅写入评测标注侧文件；没有批准Gold、训练导出或迁移正式数据库的操作。服务有到期时间。</footer>
<script nonce="__TOKEN__">const TOKEN='__TOKEN__';let cases=[],assets={},current=null,events=[],view='overview',meta={};const $=s=>document.getElementById(s);const LABEL={entity:'材料/分子',treatment:'处理组',control:'对照组',model:'细胞/材料模型',dose:'剂量',time:'暴露时间',method:'实验方法',endpoint:'测量终点',value:'原始数值候选',unit:'单位',source_locator:'证据位置'};const GAP={PARSED_DECLARED_SI_NOT_ACQUIRED:'已解析，声明的附件尚未取得',PARSED_TARGET_NUMERIC_NOT_EXTRACTED:'已解析，目标数值尚未入科学记录',NUMERIC_WITHOUT_COMPLETE_PROFILE_EXPERIMENT_RELATION:'已有数值，完整实验关系待核验',COMPLETE_XY_CANDIDATE_SCI_REVIEW_HOLD:'完整候选，独立科学审阅待完成'};
async function api(path,body){let r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Review-Token':TOKEN,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});let d=await r.json();if(!r.ok)throw Error(d.reason||d.error||r.status);return d;}
function text(id,t){$(id).textContent=typeof t==='string'?t:JSON.stringify(t,null,2)}
function navigate(p){view=p;document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===p));$('overview').classList.toggle('hidden',p!=='overview');$('quality').classList.toggle('hidden',p!=='quality');$('pilot').classList.toggle('hidden',p!=='pilot');if(p==='pilot')loadPilot();$('workspace').classList.toggle('hidden',!['evidence','review'].includes(p));$('annotation').classList.toggle('hidden',p!=='review');if(p==='quality')refresh();if(current)show(current)}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>navigate(b.dataset.page));
function show(c){current=c;document.querySelectorAll('.case').forEach(b=>b.classList.toggle('selected',b.dataset.caseId===c.case_id));text('candidateTitle',meta.blind?'人工盲标字段':'系统候选 · 待科学核验');text('paper',c.paper_key+' · '+c.case_id);text('anchor','作者SHA '+c.source_sha256+'\n定位 '+c.source_locator);$('fields').replaceChildren();for(const f of meta.fields){let d=document.createElement('div');d.className='field';let k=document.createElement('label');k.textContent=LABEL[f]||f;let v=document.createElement('div');v.textContent=meta.blind?'盲标：系统候选隐藏':c.field_candidates?.[f]??'未知 / 待核验';d.append(k,v);$('fields').append(d)}$('asset').replaceChildren();Object.entries(assets).filter(([s,a])=>a.paper_key===c.paper_key).forEach(([s,a])=>{let o=document.createElement('option');o.value=s;o.textContent=a.format+' · '+s.slice(0,14);o.selected=s===c.source_sha256;$('asset').append(o)});$('evidenceLocator').value=c.source_locator;text('evidence','原文单元格字面值：'+c.source_literal+'\n\n请选择“查看原文”打开来源内容。');updateHistory()}
function updateHistory(){text('history',events.filter(e=>current&&e.case_id===current.case_id).map(e=>({field:e.field,revision:e.revision,reviewer:e.reviewer_identity,decision:e.decision,reason:e.reason,actor:e.actor_kind})))}
$('asset').onchange=()=>{$('evidenceLocator').value=$('asset').value===current.source_sha256?current.source_locator:''};
$('loadSource').onclick=async()=>{try{let d=await api('/api/evidence?sha='+encodeURIComponent($('asset').value)+'&case_id='+encodeURIComponent(current.case_id)+'&locator='+encodeURIComponent($('evidenceLocator').value));text('evidence',d.text);if(d.download_supported){let a=document.createElement('a');a.textContent='下载原件用于独立核对';a.href='/raw/'+$('asset').value+'?token='+TOKEN;a.target='_blank';$('evidence').prepend(a,document.createTextNode('\n'))}}catch(e){text('evidence',e.message)}};
$('save').onclick=async()=>{if(!current)return;try{let name=$('reviewer').value.trim(),field=$('field').value;let prior=events.filter(e=>e.case_id===current.case_id&&e.field===field&&e.reviewer_identity===name);let val=$('value').value;let row=await api('/api/annotations',{case_id:current.case_id,field,decision:$('decision').value,proposed_value:field==='value'&&val!==''?Number(val):val||null,error_code:$('error').value||null,reviewer_identity:name,source_sha256:$('asset').value,source_locator:$('evidenceLocator').value,reason:$('reason').value,expected_revision:prior.length});events.push(row);text('notice','已保存评测标注，第'+row.revision+'版；科学批准仍为HOLD。');updateHistory()}catch(e){text('notice',e.message)}};
$('export').onclick=async()=>{let d=await api('/api/reference-draft');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='human-reference-PENDING.json';a.click()};function makeTable(headers,rows){let t=document.createElement('table'),head=document.createElement('tr');headers.forEach(h=>{let el=document.createElement('th');el.textContent=h;head.append(el)});t.append(head);rows.forEach(row=>{let tr=document.createElement('tr');row.forEach(v=>{let td=document.createElement('td');td.textContent=v??'待核验';tr.append(td)});t.append(tr)});return t}
function gaps(s,g){let intro=document.createElement('p');intro.textContent='当前采集账本：'+s.queue.work_items+' 个文献身份 · '+s.queue.pending_or_inflight+' 个待处理 · '+(s.queue.conservation_pass?'分页与任务数量守恒通过':'需核对任务完整性');$('queue').replaceChildren(intro,makeTable(['论文','原文解析','已取SI / 已声明引用','当前缺口'],g.papers.map(p=>[p.paper_key,p.parsed_assets+' 份来源',p.acquired_SI+' / '+p.declared_SI,GAP[p.stage]||p.stage])));text('ledger',s.queue)}
async function refresh(){let e=await api('/api/evaluation');let p=document.createElement('p');p.className='pill';p.textContent='待真实人工标准答案 · 不报告模型准确率';$('metrics').replaceChildren(p,makeTable(['指标','当前结果','解释'],[['字段 Precision / Recall / F1',e.F1??'null','真实人工答案未发布'],['完整科学 X/Y 产出率',e.complete_XY_yield??'null','来源候选仍需字段和关系核验'],['训练 Gold','0','没有科学准入变更'],['真实模型调用',meta.candidate_trace?.actual_model_calls??'未运行','离线协议回放不作为模型效果']]));if(meta.candidate_trace){let h=document.createElement('h4');h.textContent='本次真实来源候选';let t=document.createElement('p');t.textContent='36 个单元格候选；12 个作者布局歧义值隔离。'+meta.candidate_trace.source_conflict;let n=document.createElement('p');n.className='muted';n.textContent='单位、受试化学形态与独立科学审阅仍为HOLD。';$('metrics').append(h,t,n)}}$('refresh').onclick=refresh;
async function loadPilot(){try{let d=await api('/api/pilot?view='+encodeURIComponent($('pilotView').value)+'&paper='+encodeURIComponent($('pilotPaper').value));text('pilotDescription',d.scope.task.task_id+' · '+d.scope.papers.length+' 篇任务范围 · '+d.scope.papers.filter(p=>p.source_path).length+' 篇原文可用。独立科学审核待完成；正文候选与原生表格分列。');text('pilotCounts','当前视图 '+d.total+' 条 · Reviewed '+d.manifest.counts.reviewed+' 条 · Gold0 · 版本 '+d.manifest.version);let table=makeTable(['论文 / 来源类型','分子 / 条件','原始值 / 单位','审核与缺口','证据'],d.rows.map(r=>[r.paper_key+' / '+(r.row_kind==='CANONICAL_R17_SOURCE_ONLY'?'原生表格':'正文数值候选'),(r.compound||'未知')+' / '+(r.dose||'剂量待核验')+' / '+(r.exposure_time||'时间待核验'),r.author_literal+' '+(r.unit||'单位待核验'),r.review_state+' · '+JSON.parse(r.hold_reasons).slice(0,2).join('; '),'查看']));Array.from(table.querySelectorAll('tr')).slice(1).forEach((tr,i)=>{let td=tr.lastChild;td.textContent='';let b=document.createElement('button');b.textContent='查看原件';b.onclick=()=>{let c=cases.find(x=>x.case_id===d.rows[i].row_id);if(c){navigate('evidence');show(c)}};td.append(b)});$('pilotRows').replaceChildren(table);text('pilotScope',d.scope)}catch(e){text('pilotCounts',e.message)}}
$('pilotSearch').onclick=loadPilot;$('pilotView').onchange=loadPilot;
$('pilotCSV').onclick=async()=>{try{let d=await api('/api/pilot/csv?view='+encodeURIComponent($('pilotView').value)+'&paper='+encodeURIComponent($('pilotPaper').value));let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([d.csv],{type:'text/csv;charset=utf-8'}));a.download='ATP_CONTENT_'+d.view+'_source_only.csv';a.click()}catch(e){text('pilotCounts',e.message)}};
$('taskCreate').onclick=async()=>{try{text('taskPreview',await api('/api/pilot/task',{profile:$('taskProfile').value,max_papers:Number($('taskN').value),max_requests:Number($('taskReq').value),compound_terms:$('taskCompounds').value}))}catch(e){text('taskPreview',e.message)}};
(async()=>{meta=await api('/api/meta');$('pilotTab').classList.toggle('hidden',!meta.pilot_enabled);text('mode',meta.blind?'盲标模式 · 候选隐藏':'候选对照模式 · 非盲标');text('banner',(meta.test_mode?'自动化演示模式：所有事件排除人工真值。 ':'')+(meta.reference_state==='DEVELOPMENT_DEMO_NOT_HUMAN_BENCHMARK'?'新增SI候选演示（不属于冻结评测集）':'人工答案待建立')+' · Gold0 · '+meta.expires_at);let s=await api('/api/summary');$('stats').replaceChildren();for(const [k,v]of Object.entries(s.cards)){let d=document.createElement('div');d.className='card';let n=document.createElement('div');n.className='num';n.textContent=v;let l=document.createElement('div');l.textContent=k;d.append(n,l);$('stats').append(d)}gaps(s,await api('/api/gaps'));assets=meta.assets;cases=await api('/api/cases');events=await api('/api/annotations');for(const f of meta.fields){let o=document.createElement('option');o.value=f;o.textContent=LABEL[f]||f;$('field').append(o)}for(const f of ['',...meta.error_codes]){let o=document.createElement('option');o.value=f;o.textContent=f||'未分类';$('error').append(o)}for(const c of cases){let b=document.createElement('button');b.className='case';b.dataset.caseId=c.case_id;b.textContent=c.paper_key+' · '+c.source_locator.split('!').slice(-1)[0]+'\nSOURCE ONLY · '+c.case_id.slice(-8);b.onclick=()=>show(c);$('cases').append(b)}if(cases.length)show(cases[0]);await refresh()})().catch(e=>text('banner',e.message));</script></html>'''

def make_server(home,packet_file,packet_sha,store,port=0,test_mode=False,blind=True,seconds=900,pilot_dataset=None,pilot_manifest_sha=None):
 if not 10<=seconds<=1800:raise ValueError('R33_UI_BOUNDED_TTL_10_TO_1800_SECONDS')
 if pilot_dataset:
  if blind:raise ValueError('R38_PILOT_DATA_NOT_ALLOWED_IN_BLIND_SESSION')
  from .r38_dataset import query as pilot_query
  pilot_query(pilot_dataset,pilot_manifest_sha,limit=1)
 packet=load_packet(packet_file,packet_sha);token=secrets.token_urlsafe(30);started=time.time();db=db_path(home)
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,d,status=200,mime='application/json; charset=utf-8'):
   b=d if isinstance(d,bytes) else d.encode() if isinstance(d,str) else json.dumps(d,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'nonce-"+token+"'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'");self.end_headers();self.wfile.write(b)
  def check(self,raw=False):
   expected={'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
   if self.headers.get('Host') not in expected:raise PermissionError('R33_HOST_NOT_LOOPBACK_AUTHORITY')
   origin=self.headers.get('Origin')
   if origin and origin not in {'http://'+h for h in expected}:raise PermissionError('R33_CROSS_ORIGIN_REQUEST_REJECTED')
   supplied=self.headers.get('X-Review-Token')
   if raw:supplied=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('token',[''])[0]
   if not supplied or not secrets.compare_digest(supplied,token):raise PermissionError('R33_REVIEW_SESSION_TOKEN_REQUIRED')
  def do_GET(self):
   try:
    path=urllib.parse.urlsplit(self.path).path
    if path=='/':
     if self.headers.get('Sec-Fetch-Site')=='cross-site':raise PermissionError('R33_CROSS_SITE_PAGE_REJECTED')
     return self.reply(HTML.replace('__TOKEN__',token),mime='text/html; charset=utf-8')
    self.check(raw=path.startswith('/raw/'))
    if path in ('/api/pilot','/api/pilot/csv'):
     if not pilot_dataset:return self.reply(dict(error='PILOT_DATASET_NOT_CONFIGURED'),404)
     from .r38_dataset import query as query_pilot
     args=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query);view=args.get('view',['atp_content_candidates'])[0];paper=args.get('paper',[None])[0] or None;result=query_pilot(pilot_dataset,pilot_manifest_sha,view,paper,500)
     if path=='/api/pilot/csv':
      if result['total']!=len(result['rows']):raise ValueError('R38_UI_EXPORT_LIMIT_USE_FULL_VERSIONED_EXPORT')
      import io,csv
      text=io.StringIO();cols=[k for k in result['rows'][0] if k!='record_json'] if result['rows'] else ['row_id','review_state'];writer=csv.DictWriter(text,fieldnames=cols,extrasaction='ignore');writer.writeheader();writer.writerows(result['rows']);return self.reply(dict(csv=text.getvalue(),rows=result['total'],view=view,Gold=0))
     scope=json.loads((Path(pilot_dataset)/'TASK_AND_SOURCE_SCOPE.json').read_text());return self.reply(dict(result,scope=scope))
    if path=='/api/meta':return self.reply(dict(blind=blind,test_mode=test_mode,expires_at=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(started+seconds)),fields=FIELDS,error_codes=ERRORS,packet_sha256=packet_sha,pilot_enabled=bool(pilot_dataset),reference_state=packet.get('reference_state'),candidate_trace=packet.get('candidate_trace') if not blind else None,assets={s:{k:v for k,v in a.items() if k!='path' and k!='IR_path'} for s,a in packet['source_assets'].items()}))
    if path=='/api/cases':return self.reply([{k:v for k,v in x.items() if not blind or k not in ('field_candidates','field_state')} for x in packet['cases']])
    if path=='/api/annotations':return self.reply(history(store))
    if path=='/api/reference-draft':return self.reply(draft_reference(packet_file,packet_sha,store))
    if path=='/api/evaluation':return self.reply(evaluate(packet_file,packet_sha,Path(packet_file).parent/'candidate_baseline.json'))
    if path=='/api/gaps':
     from .r34_gap_diagnostics import attribute
     with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:
      rows=[]
      for paper in packet['papers']:
       key=paper['identity']['paper_key'];parsed=c.execute('select count(*) from r16_parsed_asset where paper_key=?',(key,)).fetchone()[0];declared=c.execute('select count(*) from r16_source_supplement where paper_key=?',(key,)).fetchone()[0];si=c.execute('select count(*) from r17_si_attachment where parent_parsed_asset_id in (select parsed_asset_id from r16_parsed_asset where paper_key=?)',(key,)).fetchone()[0];num=c.execute('select count(*) from r17_measurement where paper_key=?',(key,)).fetchone()[0]
       rows.append(dict(paper_key=key,parsed_assets=parsed,declared_SI=declared,acquired_SI=si,raw_numeric_candidates=num,stage=attribute(metadata_found=True,lawful_fulltext=bool(parsed),parsed=bool(parsed),si_declared=bool(declared),si_acquired=si,target_numeric=num,complete_XY=0),scope='FROZEN24_SOURCE_STAGES_ALL_ENDPOINT_CANDIDATES_NOT_PROFILE_TARGET_COVERAGE',profile_XY_verified=None))
     return self.reply(dict(scope='Frozen development review papers; not population retrieval recall',papers=rows,Gold=0))
    if path=='/api/summary':
     with closing(sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)) as c:papers=c.execute('select count(*) from paper_ref').fetchone()[0];measure=c.execute('select count(*) from r17_measurement').fetchone()[0];gold=c.execute('select count(*) from qualified_training_labels').fetchone()[0]
     q=audit(home,initialize=False);q={k:v for k,v in q.items() if k!='pages'}
     return self.reply(dict(cards={'来源登记':papers,'原始数值':measure,'待标注论文':packet['paper_count'],'科学Gold':gold},queue=q))
    if path=='/api/evidence' or path.startswith('/raw/'):
     s=path.split('/')[-1] if path.startswith('/raw/') else urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('sha',[''])[0];a=packet['source_assets'].get(s)
     if not a:raise ValueError('R33_SOURCE_NOT_IN_FROZEN_PACKET')
     p=Path(a['path']);b=p.read_bytes()
     if len(b)>20000000 or digest(b)!=s:raise ValueError('R33_SOURCE_SHA_OR_SIZE_HOLD')
     if path.startswith('/raw/'):return self.reply(b,mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
     if a['format'] in ('.xml','.nxml','.xlsx'):
      from .r36_native_evidence import preview as source_preview
      case_id=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('case_id',[''])[0];case=next((c for c in packet['cases'] if c['case_id']==case_id and c['paper_key']==a['paper_key']),None)
      requested_locator=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('locator',[''])[0]
      if len(requested_locator)>2000:raise ValueError('R38_SOURCE_LOCATOR_LENGTH')
      return self.reply(source_preview(p,s,requested_locator or (case['source_locator'] if case and case['source_sha256']==s else None)))
     preview=b.decode('utf-8',errors='replace')[:200000] if a['format']=='.csv' else 'PDF/图像原件：下载后独立核对；图像估读未获科学批准。SHA256 '+s
     return self.reply(dict(sha256=s,text=preview,preview_truncated=len(b)>200000,download_supported=True))

    self.reply(dict(error='NOT_FOUND'),404)
   except PermissionError as e:self.reply(dict(reason=str(e)),403)
   except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:self.reply(dict(reason=str(e)),409)
  def do_POST(self):
   try:
    self.check()
    if urllib.parse.urlsplit(self.path).path=='/api/pilot/task':
     if not pilot_dataset:return self.reply(dict(error='PILOT_NOT_CONFIGURED'),404)
     size=int(self.headers.get('Content-Length','0'))
     if not 0<size<=10000:raise ValueError('R38_TASK_REQUEST_SIZE')
     d=json.loads(self.rfile.read(size))
     if set(d)-{'profile','max_papers','max_requests','compound_terms'}:raise ValueError('R38_TASK_FIELDS_NOT_ALLOWED')
     from .r38_dataset import create_task
     return self.reply(create_task(home,d.get('profile','small_molecule_atp_content_v1'),d.get('max_papers',20),d.get('max_requests',40),d.get('compound_terms','quercetin OR resveratrol OR curcumin')),201)
    if urllib.parse.urlsplit(self.path).path!='/api/annotations':return self.reply(dict(error='NO_SCIENCE_WRITE_ENDPOINT'),404)
    size=int(self.headers.get('Content-Length','0'))
    if not 0<size<=100000:raise ValueError('R33_ANNOTATION_REQUEST_SIZE')
    entry=json.loads(self.rfile.read(size));r=annotate(packet_file,packet_sha,store,entry,test_mode=test_mode,candidates_exposed=not blind)
    self.reply(r,201)
   except PermissionError as e:self.reply(dict(reason=str(e)),403)
   except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:self.reply(dict(reason=str(e)),409)
 server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True
 timer=threading.Timer(seconds,server.shutdown);timer.daemon=True
 return server,token,timer

def serve(home,packet_file,packet_sha,store,port=0,minutes=15,test_mode=False,blind=True,pilot_dataset=None,pilot_manifest_sha=None):
 if not 1<=minutes<=30:raise ValueError('R33_UI_MAX30_MINUTES_NO_BACKGROUND_DAEMON')
 server,token,timer=make_server(home,packet_file,packet_sha,store,port,test_mode,blind,minutes*60,pilot_dataset,pilot_manifest_sha);timer.start();state=Path(store)/'server.json';state.parent.mkdir(parents=True,exist_ok=True);state.write_text(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port)+'/',pid=os.getpid(),port=server.server_port,expires_unix=time.time()+minutes*60,packet_sha256=packet_sha,test_mode=test_mode,blind=blind),indent=2)+'\n');os.chmod(state,0o600);print(state.read_text(),flush=True)
 try:server.serve_forever(poll_interval=.25)
 finally:server.server_close();timer.cancel()
 return dict(status='BOUNDED_LOCAL_REVIEW_SESSION_STOPPED',Gold=0)
