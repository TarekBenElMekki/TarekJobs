
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

app_path = UI / "app.js"
html_path = UI / "index.html"
css_path = UI / "styles.css"

app = app_path.read_text(encoding="utf-8")
html = html_path.read_text(encoding="utf-8")
css = css_path.read_text(encoding="utf-8")

marker = "/* V0.4.5.1 RUNTIME NULL-SAFETY */"
if marker not in app:
    helper = r'''
/* V0.4.5.1 RUNTIME NULL-SAFETY */
function byId(id){return document.getElementById(id);}
function fieldValue(id,fallback=''){const e=byId(id);return e&&('value' in e)?e.value:fallback;}
'''
    anchor = "function scoreClass(v){ return Number(v)>=65?'score-high':Number(v)>=45?'score-mid':'score-low'; }"
    if anchor not in app:
        raise RuntimeError("Could not locate scoreClass() insertion point")
    app = app.replace(anchor, anchor + helper, 1)

replacements = {
    "document.getElementById('jobsSearch').value": "fieldValue('jobsSearch','')",
    "document.getElementById('jobsMinScore').value": "fieldValue('jobsMinScore','0')",
    "document.getElementById('jobsReview').value": "fieldValue('jobsReview','unreviewed')",
    "document.getElementById('jobsVerdict').value": "fieldValue('jobsVerdict','reviewable')",
    "document.getElementById('jobsSource').value": "fieldValue('jobsSource','all')",
    "document.getElementById('jobsLocation').value": "fieldValue('jobsLocation','all')",
    "document.getElementById('jobsSort').value": "fieldValue('jobsSort','verdict')",
    "document.getElementById('runScope').value": "fieldValue('runScope','all')",
    "document.getElementById('runLimit').value": "fieldValue('runLimit','100')",
}
for old, new in replacements.items():
    app = app.replace(old, new)

pattern = re.compile(r"function populateSourceFilter\(\)\{.*?\}\n", re.S)
match = pattern.search(app)
if not match:
    raise RuntimeError("populateSourceFilter() was not found")

safe_populate = r'''function populateSourceFilter(){
  const active=state.sources.filter(s=>s.state==='active');
  const jobsSource=byId('jobsSource');
  if(jobsSource){
    const current=jobsSource.value;
    jobsSource.innerHTML='<option value="all">ALL ACTIVE SOURCES</option>'+
      active.map(s=>`<option value="${esc(s.name)}">${esc(s.name.toUpperCase())}</option>`).join('');
    if([...jobsSource.options].some(o=>o.value===current))jobsSource.value=current;
  }
  const runScope=byId('runScope');
  if(runScope){
    const currentScope=runScope.value;
    runScope.innerHTML='<option value="all">ALL READY SOURCES</option>'+
      active.filter(s=>s.enabled).map(s=>`<option value="${esc(s.name)}">${esc(s.name.toUpperCase())}</option>`).join('');
    if([...runScope.options].some(o=>o.value===currentScope))runScope.value=currentScope;
  }
}
'''
app = app[:match.start()] + safe_populate + app[match.end():]

app = app.replace(
    "document.getElementById('jobsSearch').oninput=scheduleJobs;",
    "if(byId('jobsSearch'))byId('jobsSearch').oninput=scheduleJobs;"
)
app = app.replace(
    "['jobsVerdict','jobsReview','jobsLocation','jobsSource','jobsMinScore','jobsSort'].forEach(id=>document.getElementById(id).onchange=scheduleJobs);",
    "['jobsVerdict','jobsReview','jobsLocation','jobsSource','jobsMinScore','jobsSort'].forEach(id=>{const e=byId(id);if(e)e.onchange=scheduleJobs;});"
)
app = app.replace(
    "document.getElementById('runScope').onchange=updateRunEligibility;",
    "if(byId('runScope'))byId('runScope').onchange=updateRunEligibility;"
)

if "bootstrapRetries" not in app:
    app = app.replace(
        "jobsMode: 'review', runTimer: null, currentJobId: null,",
        "jobsMode: 'review', runTimer: null, currentJobId: null, bootstrapRetries: 0,",
        1
    )

old_bootstrap = re.search(r"async function bootstrap\(\)\{.*?\}\n", app, re.S)
if old_bootstrap:
    safe_bootstrap = r'''async function bootstrap(){
  try{
    const [h,p,s]=await Promise.all([api('/api/health'),api('/api/profile'),api('/api/sources')]);
    state.bootstrapRetries=0;
    const dot=byId('connectionDot');
    const connection=byId('connectionText');
    const version=byId('appVersion');
    if(dot){dot.classList.remove('offline');dot.classList.add('online');}
    if(connection)connection.textContent='LOCAL API / ONLINE';
    if(version)version.textContent=`VERSION ${h.version}`;
    state.profile=p.profile;
    state.settings=p.settings;
    state.reviewStatuses=p.review_statuses||[];
    state.applicationStatuses=p.application_statuses||[];
    state.sources=s.items||[];
    populateSourceFilter();
    await loadOverview();
    const r=await api('/api/run/status');
    renderRun(r);
    if(r.status==='running')beginRunPolling();
  }catch(e){
    const dot=byId('connectionDot');
    const connection=byId('connectionText');
    if(dot){dot.classList.remove('online');dot.classList.add('offline');}
    if(connection)connection.textContent='LOCAL API / OFFLINE';
    state.bootstrapRetries=(state.bootstrapRetries||0)+1;
    if(state.bootstrapRetries<=12){setTimeout(()=>bootstrap(),500);}
    else{handleError(e);}
  }
}
'''
    app = app[:old_bootstrap.start()] + safe_bootstrap + app[old_bootstrap.end():]

version = "0451"
html = re.sub(r'(/assets/styles\.css)(?:\?[^"\']*)?', rf'\1?v={version}', html)
html = re.sub(r'(/assets/app\.js)(?:\?[^"\']*)?', rf'\1?v={version}', html)
html = re.sub(
    r'/assets/tarekjobs-logo(?:-hd)?\.png(?:\?[^"\']*)?',
    f'/assets/tarekjobs-logo-hd.png?v={version}',
    html
)
html = re.sub(
    r'/assets/(?:favicon|tarekjobs-v\d+)\.ico(?:\?[^"\']*)?',
    f'/assets/tarekjobs-v0451.ico?v={version}',
    html
)
if "tarekjobs-icon-64.png" not in html:
    html = html.replace(
        "</head>",
        '  <link rel="icon" type="image/png" sizes="64x64" href="/assets/tarekjobs-icon-64.png?v=0451" />\n</head>',
        1
    )

if "V0.4.5.1 HD BRANDING" not in css:
    css += r'''

/* ==========================================================
   V0.4.5.1 HD BRANDING
   ========================================================== */
.brand-logo,
.brand-logo-block img {
  image-rendering: auto !important;
  object-fit: contain !important;
  max-width: 100%;
  height: auto;
  filter: none !important;
  transform: translateZ(0);
}
.brand-logo {
  width: min(168px, 88%);
}
'''

app_path.write_text(app, encoding="utf-8")
html_path.write_text(html, encoding="utf-8")
css_path.write_text(css, encoding="utf-8")
print("Front-end runtime + cache-busting patch: OK")
