"""Generate sanitized owner-only Control Center assets."""
import json
from pathlib import Path
import sqlite3
from oursteps.analytics import counts as analytics_counts

OWNER_FILES={"control-center.html","control-center.css","control-center.js","control-center.json"}

HTML='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Control Center · 足迹私藏</title><link rel="stylesheet" href="/control-center.css"></head><body><main><header><div><a href="/">← 返回文章归档</a><h1>OurSteps Archive Control Center</h1><p>状态快照 + 固定安全动作</p></div><span class="badge">SAFE ACTIONS</span></header><section id="summary" class="grid" aria-live="polite"></section><section><h2>Archive Worker</h2><div id="worker" class="panel" aria-live="polite">正在读取…</div></section><section><h2>安全动作</h2><div class="panel"><p class="hint">所有动作都经过固定 allowlist；没有任意命令输入。重复运行中的同一动作不会再次入队。</p><div id="actions" class="actions"></div><div id="jobs" class="jobs" aria-live="polite"></div></div></section><section><h2>Guide Discovery V2</h2><div id="guide" class="panel"></div></section><section><h2>自动任务</h2><div id="schedules" class="panel"></div></section><section><h2>安全边界</h2><div class="panel safety"><p>Web API 没有 SSH、Keychain、Cookie、Session、SQLite 或 Docker 权限，只能写入专用动作队列。</p><p>Mac mini runner 仅执行硬编码 allowlist。Rollback 仍为 Terminal-only，不提供网页按钮。</p></div></section><p id="error" class="error" hidden></p></main><script src="/control-center.js" defer></script></body></html>'''

CSS='''*{box-sizing:border-box}body{margin:0;background:#f6f7f9;color:#18202a;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}header a{color:#315f91;text-decoration:none}h1{font-size:30px;margin:8px 0 2px}h2{font-size:19px;margin:26px 0 10px}.badge{font-size:12px;font-weight:700;letter-spacing:.08em;border:1px solid #8a9aab;border-radius:999px;padding:6px 10px;background:#fff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,.panel{background:#fff;border:1px solid #dfe4ea;border-radius:10px;padding:16px}.value{font-size:28px;font-weight:700;margin-top:4px}.label{color:#637083;font-size:13px}.rows{display:grid;grid-template-columns:minmax(150px,220px) 1fr;gap:7px 16px}.rows div:nth-child(odd){color:#637083}.ok{color:#24713f}.warn{color:#8a5b00}.running{color:#a14b00;font-weight:700}.idle{color:#24713f;font-weight:700}.unknown{color:#8a5b00;font-weight:700}.safety{border-left:4px solid #7c8da1}.error{background:#fff0f0;border:1px solid #dca5a5;padding:14px;border-radius:8px}.actions{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}.actions button{border:1px solid #506882;background:#f8fbff;border-radius:7px;padding:9px 12px;font:inherit;cursor:pointer}.actions button:hover{background:#edf5ff}.actions button:disabled{opacity:.5;cursor:not-allowed}.hint{color:#637083;margin-top:0}.jobs{margin-top:14px}.job{display:grid;grid-template-columns:160px 100px 1fr;gap:10px;padding:7px 0;border-top:1px solid #edf0f3;font-size:13px}.job:first-child{border-top:0}.state-running,.state-pending{color:#8a5b00}.state-succeeded{color:#24713f}.state-failed{color:#a12b2b}@media(max-width:600px){header{display:block}.badge{display:inline-block;margin-top:12px}.rows,.job{grid-template-columns:1fr}.rows div:nth-child(odd){margin-top:8px}}'''

JS='''const el=id=>document.getElementById(id);const safe=v=>v===null||v===undefined||v===''?'—':String(v);let cfg=null;function card(label,value,cls=''){return `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${safe(value)}</div></div>`}function rows(items){return `<div class="rows">${items.map(([k,v])=>`<div>${k}</div><div>${safe(v)}</div>`).join('')}</div>`}function showError(text){el('error').hidden=!text;el('error').textContent=text||''}function renderJobs(jobs){const labels=Object.fromEntries((cfg.actions||[]).map(x=>[x.id,x.label]));el('jobs').innerHTML=(jobs||[]).slice().reverse().map(j=>`<div class="job"><strong>${safe(labels[j.action]||j.action)}</strong><span class="state-${safe(j.state)}">${safe(j.state)}</span><span>${safe(j.message)}</span></div>`).join('')||'<p class="hint">暂无手动动作记录。</p>'}async function loadJobs(){try{const r=await fetch('/control-action/status',{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);renderJobs((await r.json()).jobs)}catch(e){showError('动作状态读取失败：'+e.message)}}function fmtDuration(v){if(v===null||v===undefined)return '—';const m=Math.floor(Number(v)/60),h=Math.floor(m/60);return h?`${h}小时 ${m%60}分钟`:`${m}分钟`}function fmtTime(v){if(!v)return '—';return new Date(Number(v)*1000).toLocaleString('zh-CN',{hour12:false})}function taskLabel(v){return ({historical_backfill:'Historical Backfill',incremental_sync:'Incremental Sync',guide_discovery:'Guide Discovery',archive_worker:'Archive Worker',manual:'Manual Archive Task',idle:'—',unknown:'Unknown'})[v]||'Unknown'}async function loadWorker(){try{const r=await fetch('/control-action/worker-status',{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);const w=(await r.json()).worker||{};const cls=w.state==='RUNNING'?'running':w.state==='IDLE'?'idle':'unknown';el('worker').innerHTML=rows([['状态',`<span class="${cls}">${safe(w.state)}</span>`],['当前任务',taskLabel(w.task)],['已运行',w.state==='RUNNING'?fmtDuration(w.elapsed_seconds):'—'],['开始时间',w.state==='RUNNING'?fmtTime(w.started_at):'—'],['下一次 Historical Backfill',fmtTime(w.next_historical_backfill)],['状态更新时间',fmtTime(w.sampled_at)]])}catch(e){el('worker').innerHTML='<span class="unknown">UNKNOWN</span> · 状态读取失败'}}async function runAction(a){if(!confirm(`确认执行：${a.label}？\n\n该动作会在 Mac mini 的固定 allowlist runner 中执行。`))return;showError('');const buttons=[...el('actions').querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{const r=await fetch('/control-action/request/'+encodeURIComponent(a.id),{method:'POST',headers:{'X-Oursteps-Action-Request':'1'}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||`HTTP ${r.status}`);await loadJobs()}catch(e){showError('动作提交失败：'+e.message)}finally{buttons.forEach(b=>b.disabled=false)}}fetch('/control-center.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()}).then(d=>{cfg=d;el('summary').innerHTML=[card('公开文章',d.archive.full_public),card('最近 1 年',d.archive.recent_1y),card('待补 / Missing',d.archive.active_missing,d.archive.active_missing?'warn':'ok'),card('需人工复核',d.archive.review_required,d.archive.review_required?'warn':'ok'),card('Preview Pending',d.archive.preview_pending?'YES':'NO',d.archive.preview_pending?'warn':'ok'),card('已知 Inventory',d.archive.inventory_total),card('本站总阅读',d.analytics.total_reads)].join('');const g=d.guide||{};el('guide').innerHTML=rows([['状态',g.state],['下一页',g.next_page],['上次完成页',g.last_end],['上次新增 TID',g.new_tids],['Guide 已发现 TID',g.discovered_tids],['最近成功',g.last_success],['Retry at',g.retry_at],['错误状态',g.has_error?'YES':'NO']]);el('schedules').innerHTML=rows((d.schedules||[]).flatMap(x=>[[x.label,x.schedule]]));el('actions').innerHTML=(d.actions||[]).map(a=>`<button type="button" data-action="${a.id}">${a.label}</button>`).join('');el('actions').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>runAction((d.actions||[]).find(a=>a.id===b.dataset.action))));loadJobs();loadWorker();setInterval(loadJobs,5000);setInterval(loadWorker,5000)}).catch(e=>showError('Control Center 状态读取失败：'+e.message));'''

def _ro(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True,timeout=5);db.row_factory=sqlite3.Row;db.execute("PRAGMA query_only=ON");return db

def _json(path):
    try:
        value=json.loads(Path(path).read_text());return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def _safe(value): return value if isinstance(value,(str,int,float,bool)) or value is None else None

def snapshot(root,full_public,recent_1y):
    root=Path(root);result={"schema_version":2,"mode":"safe_action_control_center"}
    with _ro(root/'data/archive.sqlite3') as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def scalar(sql,default=0):
            row=db.execute(sql).fetchone();return row[0] if row and row[0] is not None else default
        archive={"full_public":int(full_public),"recent_1y":int(recent_1y),"inventory_total":int(scalar("SELECT count(*) FROM inventory")) if 'inventory' in tables else 0,"complete_threads":int(scalar("SELECT count(*) FROM threads WHERE status='complete'")) if 'threads' in tables else 0,"preview_pending":(root/'data/preview-incremental-pending.json').exists()}
        guide={"state":"not_initialized","next_page":None,"last_end":None,"new_tids":0,"discovered_tids":0,"last_success":None,"retry_at":None,"has_error":False}
        if 'guide_progress' in tables:
            row=db.execute("SELECT state,next_page,last_end,new_tids,last_success,retry_at,error FROM guide_progress WHERE id=1").fetchone()
            if row:
                guide.update({k:_safe(row[k]) for k in ('state','next_page','last_end','new_tids','last_success','retry_at')});guide['has_error']=bool(row['error'])
        if 'guide_tids' in tables: guide['discovered_tids']=int(scalar("SELECT count(*) FROM guide_tids"))
    missing=root/'data/manual-missing-tids.txt'
    try: archive['active_missing']=len({x for x in missing.read_text().splitlines() if x.isdigit()})
    except OSError: archive['active_missing']=0
    archive['review_required']=0;reconcile=root/'data/reconcile.sqlite3'
    if reconcile.is_file():
        try:
            with _ro(reconcile) as db: archive['review_required']=int(db.execute("SELECT count(*) FROM candidates WHERE recovery='review_required'").fetchone()[0])
        except sqlite3.Error: archive['review_required']=0
    registry=_json(root/'config/tool_registry.json');schedules=[];actions=[]
    for tool in registry.get('tools',[]):
        if tool.get('id') in {'guide_discovery_v2','incremental_sync','historical_backfill'} and isinstance(tool.get('schedule'),str): schedules.append({"id":tool['id'],"label":str(tool.get('label',tool['id'])),"schedule":tool['schedule']})
        if tool.get('web_action') is True: actions.append({'id':str(tool['id']),'label':str(tool.get('label',tool['id'])),'safety':str(tool.get('safety','')),'confirmation':True})
    views=analytics_counts(root)
    result.update(archive=archive,guide=guide,schedules=schedules,actions=actions,analytics={'total_reads':sum(views.values()),'tracked_articles':len(views)})
    return result

def files(root,full_public,recent_1y):
    data=json.dumps(snapshot(root,full_public,recent_1y),ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    # Studio primary serves static releases: no control-action API is deployed.
    import os
    if os.environ.get('OURSTEPS_STUDIO_PRIMARY_EXPERIMENT') == '1':
        status=json.loads(data)
        status['actions']=[]
        # Sanitized Studio automation receipt, generated from local logs at publish time.
        from pathlib import Path as _Path
        log=_Path.home()/'Library/Logs/oursteps-studio-backfill.log'
        recent=None
        try:
            for line in log.read_text().splitlines():
                if line.startswith('BACKFILL_METRICS '):
                    recent=json.loads(line[len('BACKFILL_METRICS '):])
        except (OSError,ValueError): pass
        health=_json(root/'data/studio-health.json')
        safe_metrics={k:recent.get(k) for k in ('started_at','elapsed_seconds','new_complete','new_snapshots','worker_exit')} if isinstance(recent,dict) else None
        status['studio_automation']={'backfill':safe_metrics,'health':{'status':health.get('status'),'time':health.get('time'),'sqlite':health.get('checks',{}).get('sqlite')},'note':'Snapshot at website publication; not a live worker feed'}
        status['schedules']=[{'id':'studio_primary_cycle','label':'Studio Primary Sync + Publish','schedule':'Every 3600 seconds (LaunchAgent)'}]
        data=json.dumps(status,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
        html=HTML.replace('状态快照 + 固定安全动作','Studio 本地生产状态快照（只读）').replace('SAFE ACTIONS','READ ONLY').replace('Mac mini runner 仅执行硬编码 allowlist。Rollback 仍为 Terminal-only，不提供网页按钮。','Studio 生产任务通过 macOS LaunchAgent 调度；网页动作接口尚未启用。Rollback 仍为 Terminal-only。')
        js=JS.replace("loadJobs();loadWorker();setInterval(loadJobs,5000);setInterval(loadWorker,5000)","el('jobs').textContent='Studio 手动动作接口尚未启用';loadStudioWorker();setInterval(loadStudioWorker,5000)")
        html=html.replace('<section><h2>Archive Worker</h2>', '<section><h2>Studio 自动化运行摘要</h2><div id="studio-automation" class="panel">读取中…</div></section><section><h2>Archive Worker</h2>')
        js=js.replace("cfg=d;el('summary')", "cfg=d;const a=d.studio_automation||{},b=a.backfill||{},h=a.health||{};el('studio-automation').textContent='上轮回填：新增完整 '+(b.new_complete??'—')+' 篇，新增快照 '+(b.new_snapshots??'—')+'，耗时 '+(b.elapsed_seconds??'—')+' 秒；健康检查 '+(h.status??'—')+'；数据库 '+(h.sqlite??'—')+'。数据为最近发布时的快照，并非实时监控。';el('summary')")
        html=html.replace('<section><h2>Archive Worker</h2>', '<section><h2>手动 Incremental Sync Now（Studio）</h2><div class="panel"><p>仅 OSowner 可操作，执行 Studio 本地增量抓取及发布。</p><button id="studio-sync-now" type="button">Incremental Sync Now</button><p id="studio-sync-state">读取状态中…</p></div></section><section><h2>Archive Worker</h2>')
        js += """
async function loadStudioWorker(){try{const r=await fetch('/studio-action/worker-status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const w=(await r.json()).worker||{};const cls=w.state==='RUNNING'?'running':w.state==='IDLE'?'idle':'unknown';el('worker').innerHTML=rows([['状态',`<span class=\"${cls}\">${safe(w.state)}</span>`],['当前任务',taskLabel(w.task)],['已运行',w.state==='RUNNING'?fmtDuration(w.elapsed_seconds):'—'],['开始时间',w.state==='RUNNING'?fmtTime(w.started_at):'—'],['下一次 Historical Backfill',fmtTime(w.next_historical_backfill)],['状态更新时间',fmtTime(w.sampled_at)]])}catch(e){el('worker').textContent='UNKNOWN · 实时状态读取失败：'+e.message}}
"""
        js += """
async function refreshStudioManual(){try{const r=await fetch('/studio-action/status',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const j=(await r.json()).job||{};document.getElementById('studio-sync-state').textContent='状态：'+(j.state||'idle')+'；'+(j.message||'')+'；开始：'+(j.started_at?new Date(j.started_at*1000).toLocaleString('zh-CN'):'—');document.getElementById('studio-sync-now').disabled=j.state==='running'||j.state==='queued'}catch(e){document.getElementById('studio-sync-state').textContent='状态读取失败：'+e.message}}
document.getElementById('studio-sync-now').addEventListener('click',async()=>{if(!confirm('确认立即在 Studio 上执行 Incremental Sync Now？'))return;const b=document.getElementById('studio-sync-now');b.disabled=true;try{const r=await fetch('/studio-action/incremental-sync',{method:'POST',headers:{'X-Oursteps-Action-Request':'1'}});if(!r.ok)throw Error('HTTP '+r.status);await refreshStudioManual()}catch(e){document.getElementById('studio-sync-state').textContent='提交失败：'+e.message;b.disabled=false}});
refreshStudioManual();setInterval(refreshStudioManual,5000);
"""
        html=html.replace('<button id="studio-sync-now" type="button">Incremental Sync Now</button>', '<button id="studio-sync-now" type="button">Incremental Sync Now</button> <button class="studio-extra-action" data-action="guide-discovery" type="button">Guide Discovery V2</button> <button class="studio-extra-action" data-action="historical-backfill" type="button">Historical Backfill</button> <button class="studio-extra-action" data-action="publish-release" type="button">Publish Public Release</button>')
        js += """
for(const b of document.querySelectorAll('.studio-extra-action')){b.addEventListener('click',async()=>{if(!confirm('确认在 Studio 本地执行 '+b.textContent+'？'))return;b.disabled=true;try{const r=await fetch('/studio-action/'+b.dataset.action,{method:'POST',headers:{'X-Oursteps-Action-Request':'1'}});if(!r.ok)throw Error('HTTP '+r.status);await refreshStudioManual()}catch(e){document.getElementById('studio-sync-state').textContent='操作失败：'+e.message}finally{b.disabled=false}})}
"""
        # Poll only the owner-protected JSON alias; no direct access to logs or SQLite.
        js += """
async function refreshStudioAutomation(){try{const r=await fetch('/studio-status/automation.json',{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json(),b=d.backfill||{},m=b.latest||{},c=d.checks||{};const when=x=>x?new Date(Number(x)*1000).toLocaleString('zh-CN',{hour12:false}):'暂无';document.getElementById('studio-automation').textContent='健康状态：'+(d.status||'未知')+'；SQLite：'+(c.sqlite||'未知')+'；Complete：'+(c.complete??'—')+'；历史回填上次执行：'+when(m.started_at)+'；新增完整：'+(m.new_complete??'—')+'；新增快照：'+(m.new_snapshots??'—')+'；连续零增长：'+(b.zero_growth_streak??'—')+' 次；最近文章增长：'+when(b.last_article_growth_at)+'；检查更新：'+when(d.time)+'；告警：'+((d.alerts||[]).join('、')||'无')}catch(e){document.getElementById('studio-automation').textContent='实时状态暂不可用：'+e.message}}
refreshStudioAutomation();setInterval(refreshStudioAutomation,30000);
"""
        studio_css=CSS+'''
/* Studio manual actions: NAS 8448-sized, bold, touch-friendly controls. */
#studio-sync-now,.studio-extra-action{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:17px;font-weight:700;line-height:1.35;min-height:46px;padding:11px 18px;margin:0 9px 10px 0;color:#172b43;background:#f8fbff;border:1.5px solid #536d8a;border-radius:7px;cursor:pointer;white-space:normal;}
#studio-sync-now:hover,.studio-extra-action:hover{background:#eaf3fc;}
#studio-sync-now:focus-visible,.studio-extra-action:focus-visible{outline:3px solid #2878bf;outline-offset:2px;}
#studio-sync-now:disabled,.studio-extra-action:disabled{opacity:.55;cursor:not-allowed;}
@media(max-width:600px){#studio-sync-now,.studio-extra-action{font-size:16px;min-height:46px;margin-bottom:10px;}}
'''
        return {'control-center.html':html.encode(),'control-center.css':studio_css.encode(),'control-center.js':js.encode(),'control-center.json':data.encode()}
    return {"control-center.html":HTML.encode(),"control-center.css":CSS.encode(),"control-center.js":JS.encode(),"control-center.json":data.encode()}
