"""Deterministic private static preview. No network access."""
from .config import UID, DISPLAY_NAME
import datetime
import html
import json
import re
from .parser import clean_fragment, soup_of, thread_url, parse_thread
from .store import atomic_write

CSS = """body{font:16px/1.6 system-ui,sans-serif;background:#f5f4f0;color:#26352f;margin:0}main{max-width:1240px;margin:auto;padding:20px 24px}a{color:#176b55;text-decoration:none}a:hover{text-decoration:underline}header{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #176b55;margin-bottom:14px}header p{font-size:12px;color:#66736c}h1{font-size:34px;margin:12px 0}h2{font-size:17px;margin:20px 0 6px}small,.meta{color:#66736c}.article-post{background:white;padding:24px;margin:20px 0;border-radius:8px;overflow-wrap:anywhere}.card,.columns{display:grid;grid-template-columns:140px minmax(180px,1fr) 125px 80px 65px;gap:12px;align-items:center;padding:0 10px;min-height:40px;border-bottom:1px solid #e3e7e1}.card:nth-child(even){background:#fff}.card[hidden]{display:none}.card .title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.columns{min-height:30px;color:#68746d;font-size:12px}.number{text-align:right;font-variant-numeric:tabular-nums}.when,.forum{font-size:13px;color:#66736c}.forum{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}input{padding:8px;width:min(520px,85%);font:inherit;border:1px solid #aaa;border-radius:6px}#count{display:inline;margin-left:12px;font-size:13px}blockquote{border-left:3px solid #ccc;padding-left:20px}table{max-width:100%}pre{white-space:pre-wrap}footer{margin-top:26px;color:#66736c;font-size:12px}.today h2{color:#176b55;font-size:20px}@media(max-width:650px){main{padding:12px}.card{grid-template-columns:1fr 70px 55px;gap:2px 8px;padding:7px 4px}.card .title{grid-column:1/-1;grid-row:1;white-space:normal}.when{grid-column:1}.forum{grid-column:1;grid-row:3}.views{grid-column:2;grid-row:2}.replies{grid-column:3;grid-row:2}.columns{display:none}header p{display:none}}"""
CSS += """:root{--reader-size:18px}[data-reader-size="16"]{--reader-size:16px}[data-reader-size="20"]{--reader-size:20px}[data-reader-size="22"]{--reader-size:22px}[data-reader-size="24"]{--reader-size:24px}.reader-body{font-size:var(--reader-size);line-height:1.78}.article-post .meta{font-size:14px}.reader-body img{display:block;max-width:100%;height:auto;margin:14px 0}.reader-controls{display:flex;align-items:center;gap:8px;margin:14px 0;font-size:14px}.reader-controls button{font:inherit;padding:4px 10px;background:white;border:1px solid #b7c6bd;border-radius:5px;cursor:pointer}.missing-image{font-size:14px;color:#66736c}button:focus-visible{outline:2px solid #176b55}"""

# oursteps-sort-v1
CSS += """.columns .sort-button{font-weight:800;cursor:pointer;user-select:none}.columns .sort-button:hover{text-decoration:underline}.columns .sort-button:focus-visible{outline:2px solid #176b55;outline-offset:2px}.columns .sort-time{color:#24527a}.columns .sort-title{color:#176b55}.columns .sort-forum{color:#6b4c8a}.columns .sort-views{color:#9a5a00}.columns .sort-replies{color:#9b2c2c}.columns .sort-button[data-arrow]::after{content:attr(data-arrow);margin-left:5px;font-size:10px}"""


# oursteps-global-range-v2
CSS += """.range-controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:14px 0 12px}.range-controls strong{margin-right:4px}.range-btn{font:inherit;font-size:13px;font-weight:700;padding:5px 10px;border:1px solid #b7c6bd;border-radius:6px;background:#fff;color:#40534b;cursor:pointer}.range-btn:hover{background:#eef4f0}.range-btn.active{background:#176b55;color:#fff;border-color:#176b55}.range-btn:focus-visible{outline:2px solid #176b55;outline-offset:2px}.range-note{width:100%;font-size:12px;color:#66736c;margin-top:2px}.global-results h2{color:#176b55;font-size:20px}.global-results[hidden]{display:none}@media(max-width:650px){.range-controls{gap:5px}.range-btn{font-size:12px;padding:5px 8px}}"""

READER_JS = """(()=>{const key='oursteps.reader.fontSize';const sizes=[16,18,20,22,24];let size=18;try{const saved=Number(localStorage.getItem(key));if(sizes.includes(saved))size=saved;}catch(e){}const apply=()=>{document.documentElement.dataset.readerSize=String(size);document.getElementById('reader-size').textContent=size+'px';};document.querySelectorAll('[data-font]').forEach(b=>b.addEventListener('click',()=>{const action=b.dataset.font;size=action==='reset'?18:sizes[Math.max(0,Math.min(sizes.length-1,sizes.indexOf(size)+(action==='up'?1:-1)))];try{localStorage.setItem(key,String(size));}catch(e){}apply();}));apply();})();"""

JS = r"""(()=>{
const search=document.getElementById('search');
const count=document.getElementById('count');

if(!search || !count)return;

const groupSections=[
  ...document.querySelectorAll(
    'section.today, section.date-group'
  )
];

if(!groupSections.length)return;

const allCards=[
  ...document.querySelectorAll('.card')
];

const original=new Map();

const collator=new Intl.Collator('zh-CN',{
  numeric:true,
  sensitivity:'base'
});

const keys=[
  'time',
  'title',
  'forum',
  'views',
  'replies'
];

const labels=[
  '发布时间',
  '标题',
  '版块',
  '浏览',
  '回复'
];

const ranges=[
  ['group','按日期分组'],
  ['1m','1个月'],
  ['3m','3个月'],
  ['6m','半年'],
  ['1y','1年'],
  ['3y','3年'],
  ['5y','5年'],
  ['10y','10年'],
  ['all','全部']
];

function sectionDay(section){
  const heading=section.querySelector('h2');
  const text=heading ? heading.textContent : '';
  const match=text.match(/\d{4}-\d{2}-\d{2}/);

  return match
    ? match[0]
    : '0000-00-00';
}

groupSections.forEach(section=>{
  const day=sectionDay(section);

  [...section.querySelectorAll('.card')]
    .forEach((card,index)=>{
      const text=
        card.querySelector('.when')
          ?.textContent.trim() || '';

      const match=text.match(
        /(\d{2}:\d{2})$/
      );

      const clock=
        match ? match[1] : '00:00';

      const stamp=
        new Date(
          day+'T'+clock+':00'
        ).getTime();

      original.set(card,{
        section,
        index,
        day,
        stamp
      });
    });
});

function metric(card,selector){
  const text=
    card.querySelector(selector)
      ?.textContent || '';

  const digits=
    text.replace(/[^\d]/g,'');

  return digits
    ? Number(digits)
    : null;
}

function valueFor(card,key){
  const meta=original.get(card);

  if(key==='time'){
    return meta ? meta.stamp : 0;
  }

  if(key==='title'){
    return card.querySelector('.title')
      ?.textContent.trim() || '';
  }

  if(key==='forum'){
    return card.querySelector('.forum')
      ?.textContent.trim() || '';
  }

  if(key==='views'){
    return metric(card,'.views');
  }

  if(key==='replies'){
    return metric(card,'.replies');
  }

  return '';
}

function compareCards(a,b,key,direction){
  const av=valueFor(a,key);
  const bv=valueFor(b,key);

  const aMissing=
    av===null ||
    av===undefined ||
    av==='';

  const bMissing=
    bv===null ||
    bv===undefined ||
    bv==='';

  if(aMissing || bMissing){
    if(aMissing && bMissing){
      return 0;
    }

    // Unknown values always stay at the bottom.
    return aMissing ? 1 : -1;
  }

  let result;

  if(
    key==='time' ||
    key==='views' ||
    key==='replies'
  ){
    result=av-bv;
  }else{
    result=collator.compare(
      String(av),
      String(bv)
    );
  }

  if(direction==='desc'){
    result=-result;
  }

  if(result===0){
    const am=original.get(a);
    const bm=original.get(b);

    result=
      (bm?.stamp || 0) -
      (am?.stamp || 0);
  }

  return result;
}

function clearHeader(columns){
  [...columns.children]
    .slice(0,5)
    .forEach(control=>{
      delete control.dataset.arrow;
      control.removeAttribute(
        'aria-current'
      );
    });
}

function markHeader(
  columns,
  key,
  direction
){
  clearHeader(columns);

  const control=[
    ...columns.children
  ].slice(0,5).find(
    c=>c.dataset.sort===key
  );

  if(!control)return;

  control.dataset.arrow=
    direction==='asc'
      ? '▲'
      : '▼';

  control.setAttribute(
    'aria-current',
    'true'
  );
}

function decorateColumns(
  columns,
  activate
){
  const controls=[
    ...columns.children
  ].slice(0,5);

  controls.forEach(
    (control,index)=>{
      const key=keys[index];

      control.classList.add(
        'sort-button',
        'sort-'+key
      );

      control.dataset.sort=key;

      control.setAttribute(
        'role',
        'button'
      );

      control.setAttribute(
        'tabindex',
        '0'
      );

      control.setAttribute(
        'aria-label',
        '按'+labels[index]+'排序'
      );

      const run=()=>{
        activate(
          key,
          columns
        );
      };

      control.addEventListener(
        'click',
        run
      );

      control.addEventListener(
        'keydown',
        event=>{
          if(
            event.key==='Enter' ||
            event.key===' '
          ){
            event.preventDefault();
            run();
          }
        }
      );
    }
  );
}

groupSections.forEach(section=>{
  const columns=
    section.querySelector('.columns');

  if(!columns)return;

  decorateColumns(
    columns,
    key=>{
      globalSortKey=key;
      globalSortDir=
        [
          'time',
          'views',
          'replies'
        ].includes(key)
          ? 'desc'
          : 'asc';

      setActiveRange('all');
      enterGlobalRange('all');
    }
  );
});

/* -------------------------------------------------
   Time-range toolbar
------------------------------------------------- */

const toolbar=
  document.createElement('div');

toolbar.className='range-controls';

const rangeLabel=
  document.createElement('strong');

rangeLabel.textContent='时间范围：';

toolbar.appendChild(rangeLabel);

const rangeButtons=new Map();

ranges.forEach(([value,label])=>{
  const button=
    document.createElement('button');

  button.type='button';
  button.className='range-btn';
  button.dataset.range=value;
  button.textContent=label;

  toolbar.appendChild(button);
  rangeButtons.set(value,button);
});

const note=
  document.createElement('div');

note.className='range-note';
note.textContent=
  '选择时间范围后，可按发布时间、标题、版块、浏览或回复对整个范围排序。';

toolbar.appendChild(note);


/* -------------------------------------------------
   Global result section
------------------------------------------------- */

const globalSection=
  document.createElement('section');

globalSection.className=
  'global-results';

globalSection.hidden=true;

const globalHeading=
  document.createElement('h2');

const globalColumns=
  document.createElement('div');

globalColumns.className='columns';

[
  ['发布时间','sort-time'],
  ['标题','sort-title'],
  ['版块','sort-forum'],
  ['浏览','sort-views number'],
  ['回复','sort-replies number']
].forEach(([label,classes])=>{
  const span=
    document.createElement('span');

  span.textContent=label;

  classes.split(' ')
    .filter(Boolean)
    .forEach(
      c=>span.classList.add(c)
    );

  globalColumns.appendChild(span);
});

globalSection.appendChild(
  globalHeading
);

globalSection.appendChild(
  globalColumns
);

groupSections[0].before(toolbar);

toolbar.insertAdjacentElement(
  'afterend',
  globalSection
);


/* -------------------------------------------------
   Range / sorting state
------------------------------------------------- */

let mode='group';
let selectedRange='group';

let globalSortKey='time';
let globalSortDir='desc';

let globalCards=[];

let preSearchState=null;

function cutoffFor(range){
  if(range==='all'){
    return -Infinity;
  }

  const d=new Date();

  if(range.endsWith('m')){
    const months=
      Number(range.slice(0,-1));

    d.setMonth(
      d.getMonth()-months
    );

    return d.getTime();
  }

  if(range.endsWith('y')){
    const years=
      Number(range.slice(0,-1));

    d.setFullYear(
      d.getFullYear()-years
    );

    return d.getTime();
  }

  return -Infinity;
}

function rangeName(value){
  const found=ranges.find(
    r=>r[0]===value
  );

  return found
    ? found[1]
    : value;
}

function setActiveRange(value){
  rangeButtons.forEach(
    (button,key)=>{
      const active=key===value;

      button.classList.toggle(
        'active',
        active
      );

      button.setAttribute(
        'aria-pressed',
        active ? 'true' : 'false'
      );
    }
  );
}

function restoreOriginalOrder(){
  groupSections.forEach(section=>{
    const cards=allCards
      .filter(
        card=>
          original.get(card)
            ?.section===section
      )
      .sort(
        (a,b)=>
          original.get(a).index -
          original.get(b).index
      );

    cards.forEach(
      card=>section.appendChild(card)
    );

    section.hidden=false;

    delete section.dataset.sortKey;
    delete section.dataset.sortDir;

    const columns=
      section.querySelector(
        '.columns'
      );

    if(columns){
      clearHeader(columns);
    }
  });

  globalCards=[];
  globalSection.hidden=true;
}

function sortGlobal(){
  globalCards.sort(
    (a,b)=>compareCards(
      a,
      b,
      globalSortKey,
      globalSortDir
    )
  );

  globalCards.forEach(
    card=>
      globalSection.appendChild(card)
  );

  markHeader(
    globalColumns,
    globalSortKey,
    globalSortDir
  );
}

decorateColumns(
  globalColumns,
  (key,header)=>{
    if(globalSortKey===key){
      globalSortDir=
        globalSortDir==='asc'
          ? 'desc'
          : 'asc';
    }else{
      globalSortKey=key;

      globalSortDir=
        [
          'time',
          'views',
          'replies'
        ].includes(key)
          ? 'desc'
          : 'asc';
    }

    sortGlobal();
    applyVisibility();
  }
);

function enterGlobalRange(range){
  restoreOriginalOrder();

  mode='global';
  selectedRange=range;

  const cutoff=cutoffFor(range);

  globalCards=
    allCards.filter(card=>{
      const meta=original.get(card);

      return (
        meta &&
        meta.stamp>=cutoff
      );
    });

  groupSections.forEach(
    section=>section.hidden=true
  );

  globalSection.hidden=false;

  globalHeading.textContent=
    rangeName(range)+
    ' · '+
    globalCards.length+
    ' 篇';

  sortGlobal();
  applyVisibility();
}

function enterGrouped(){
  mode='group';
  selectedRange='group';

  restoreOriginalOrder();
  applyVisibility();
}

let fullSearchIndex=null;
let searchIndexPromise=null;
let searchSerial=0;

function cardTid(card){
  if(card.dataset.tid){
    return card.dataset.tid;
  }

  const href=
    card.querySelector('.title')
      ?.getAttribute('href') || '';

  const match=
    href.match(/^\/(\d+)\.html$/);

  return match ? match[1] : '';
}

async function ensureSearchIndex(){
  if(fullSearchIndex){
    return fullSearchIndex;
  }

  if(!searchIndexPromise){
    searchIndexPromise=
      fetch(
        '/search-index.json',
        {cache:'no-store'}
      )
      .then(response=>{
        if(!response.ok){
          throw new Error(
            'search-index HTTP '+
            response.status
          );
        }

        return response.json();
      })
      .then(data=>{
        fullSearchIndex=data;
        return data;
      })
      .catch(error=>{
        searchIndexPromise=null;
        throw error;
      });
  }

  return searchIndexPromise;
}

function matchesSearch(card){
  const q=
    search.value
      .toLocaleLowerCase()
      .trim();

  if(!q){
    return true;
  }

  const tid=cardTid(card);

  const full=
    (
      fullSearchIndex &&
      tid &&
      fullSearchIndex[tid]
    ) || '';

  const lightweight=
    card.dataset.search || '';

  return (
    full || lightweight
  ).includes(q);
}

function applyVisibility(){
  let visible=0;

  if(mode==='group'){
    allCards.forEach(card=>{
      card.hidden=
        !matchesSearch(card);

      if(!card.hidden){
        visible++;
      }
    });

  }else{
    const selected=
      new Set(globalCards);

    allCards.forEach(card=>{
      if(selected.has(card)){
        card.hidden=
          !matchesSearch(card);

        if(!card.hidden){
          visible++;
        }

      }else{
        card.hidden=false;
      }
    });
  }

  count.textContent=
    visible+' 篇';

  if(mode==='global'){
    const q=
      search.value
        .toLocaleLowerCase()
        .trim();

    globalHeading.textContent=
      q
        ? rangeName(selectedRange)+
          ' · 搜索结果 · '+
          visible+
          ' 篇'
        : rangeName(selectedRange)+
          ' · '+
          globalCards.length+
          ' 篇';
  }
}

rangeButtons.forEach(
  (button,value)=>{
    button.addEventListener(
      'click',
      ()=>{
        const q=
          search.value
            .toLocaleLowerCase()
            .trim();

        if(q && value==='group'){
          setActiveRange('all');
          enterGlobalRange('all');
          return;
        }

        setActiveRange(value);

        if(value==='group'){
          enterGrouped();
        }else{
          enterGlobalRange(value);
        }
      }
    );
  }
);

async function handleSearchInput(){
  const serial=++searchSerial;

  const q=
    search.value
      .toLocaleLowerCase()
      .trim();

  if(!q){
    if(preSearchState){
      const saved=preSearchState;
      preSearchState=null;

      globalSortKey=
        saved.globalSortKey;

      globalSortDir=
        saved.globalSortDir;

      setActiveRange(
        saved.selectedRange
      );

      if(saved.mode==='group'){
        enterGrouped();
      }else{
        enterGlobalRange(
          saved.selectedRange
        );
      }

    }else{
      applyVisibility();
    }

    return;
  }

  const firstSearch=
    !preSearchState;

  if(firstSearch){
    preSearchState={
      mode,
      selectedRange,
      globalSortKey,
      globalSortDir
    };

    globalSortKey='time';
    globalSortDir='desc';
  }

  count.textContent=
    '正在载入全文索引…';

  try{
    await ensureSearchIndex();

  }catch(error){
    console.error(
      'Full-text search index failed to load',
      error
    );
  }

  if(serial!==searchSerial){
    return;
  }

  if(mode==='group'){
    setActiveRange('all');
    enterGlobalRange('all');

  }else{
    if(firstSearch){
      sortGlobal();
    }

    applyVisibility();
  }
}

search.addEventListener(
  'input',
  handleSearchInput
);

setActiveRange('group');
applyVisibility();

})();"""

def parsed_date(value):
    match=re.search(r'\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}',value or '')
    if not match: raise ValueError('missing_absolute_date')
    return datetime.datetime.strptime(match.group(), '%Y-%m-%d %H:%M')

def esc(x):
    return html.escape(str(x if x is not None else '未记录'), quote=True)


# oursteps-lazy-search-v1
def _write_search_index(store, out):
    """Write full-text search data separately from the homepage."""
    parts = {}

    for row in store.db.execute(
        """
        SELECT tid,title,forum
        FROM threads
        WHERE status='complete'
        ORDER BY tid
        """
    ):
        parts[str(row['tid'])] = [
            str(row['title'] or ''),
            str(row['forum'] or ''),
        ]

    for row in store.db.execute(
        """
        SELECT p.tid,p.body_text
        FROM posts p
        JOIN threads t ON t.tid=p.tid
        WHERE t.status='complete'
          AND p.author_uid=?
        ORDER BY p.tid,p.page,CAST(p.floor AS INTEGER),p.pid
        """, (UID,)
    ):
        key = str(row['tid'])

        if key in parts and row['body_text']:
            parts[key].append(
                str(row['body_text'])
            )

    payload = {
        tid: ' '.join(values).lower()
        for tid, values in parts.items()
    }

    atomic_write(
        out/'search-index.json',
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode('utf-8')
    )


def document(title, body):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+' · 足迹私藏</title><link rel="stylesheet" href="/style.css"></head><body><main><header><a href="/">足迹私藏 · '+esc(DISPLAY_NAME)+'</a><p>本机试阅 · 仅展示已完整归档的主题</p></header>'+body+'<footer>私人归档预览 · 正文图片使用原始图片地址 · 附件尚未下载 · 不代表公开发布许可</footer></main></body></html>')

def body_html(value, media=None):
    from .media import image_url,irrelevant

    media=media or {}

    fragment,_ = clean_fragment(
        soup_of(value.encode('utf-8')),
        'https://www.oursteps.com.au/bbs/'
    )

    soup = soup_of(fragment.encode('utf-8'))

    for img in soup.find_all('img'):
        url=image_url(img)

        if url and irrelevant(url,img.attrs):
            img.decompose()
            continue

        alt=img.get('alt','')
        if url and url.startswith(('http://','https://')):
            # URL-only archive mode:
            # keep the original remote image URL instead of downloading it.
            img.attrs={
                'src':url,
                'alt':alt,
                'loading':'lazy',
                'decoding':'async',
                'referrerpolicy':'no-referrer'
            }

        else:
            replacement=soup.new_tag('span')
            replacement['class']='missing-image'
            replacement.string='[图片地址不可用：'+(alt or '原文图片')+']'
            img.replace_with(replacement)

    for tag in soup.find_all(['html','body','td','tbody','tr']):
        tag.unwrap()

    for a in soup.find_all('a'):
        a['rel']='noreferrer noopener'

    return str(soup)


def build(store):
    from .media import SCHEMA
    store.db.executescript(SCHEMA)
    media_map={r['url']:r['path'] for r in store.db.execute("SELECT url,path FROM media_files WHERE state='success'") if (store.root/r['path']).is_file()}
    out = store.root/'preview'
    threads = list(store.db.execute("SELECT * FROM threads WHERE status='complete'"))
    def date(t):
        normalized=store.db.execute('SELECT sydney_time FROM publication_times WHERE tid=?',(t['tid'],)).fetchone()
        return parsed_date(normalized[0] if normalized else t['created_at_raw'])
    threads.sort(key=date,reverse=True)
    groups={}
    from .sync import sydney_today
    today=sydney_today()
    for t in threads:
        posts=list(store.db.execute('SELECT * FROM posts WHERE tid=? AND author_uid=? ORDER BY page,CAST(floor AS INTEGER),pid',(t['tid'],UID)))
        m=store.db.execute('SELECT m.* FROM metrics m JOIN snapshots s ON s.id=m.snapshot_id WHERE m.tid=? AND m.views IS NOT NULL AND m.replies IS NOT NULL ORDER BY s.fetched_at DESC,s.id DESC LIMIT 1',(t['tid'],)).fetchone()
        live=store.db.execute('SELECT * FROM listing_metrics WHERE tid=?',(t['tid'],)).fetchone()
        if live is not None:
            latest=store.db.execute('SELECT max(s.fetched_at) FROM metrics m JOIN snapshots s ON s.id=m.snapshot_id WHERE m.tid=? AND m.views IS NOT NULL',(t['tid'],)).fetchone()[0] or 0
            if live['observed_at']>=latest: m=live
        meta='%s · %s · 浏览 %s · 回复 %s' % tuple(esc(x) for x in (date(t).strftime('%Y-%m-%d %H:%M'),t['forum'],m['views'] if m else None,m['replies'] if m else None))
        floors=''.join('<article class="article-post" id="post-%s"><p class="meta">%s · %s · %s · PID %s</p><div class="reader-body">%s</div></article>' % (p['pid'],esc(p['floor']),esc(p['author_name']),esc(p['posted_at_raw']),p['pid'],body_html(p['body_html'],media_map)) for p in posts)
        content='<h1>'+esc(t['title'])+'</h1><p class="meta">'+meta+'</p><a rel="noreferrer" href="'+esc(thread_url(t['tid']))+'">打开新足迹原主题</a>'+'<div class="reader-controls" aria-label="正文字号"><span>正文字号</span><button data-font="down" aria-label="缩小字号">A−</button><button data-font="reset" aria-label="恢复默认字号">A</button><button data-font="up" aria-label="增大字号">A+</button><output id="reader-size" aria-live="polite">18px</output></div>'+floors+'<script src="/reader.js" defer></script>'
        atomic_write(out/('%s.html'%t['tid']),document(t['title'],content).encode())
        searchable=(t['title']+' '+(t['forum'] or '')).lower()
        day=date(t).date().isoformat()
        row='<div class="card" data-tid="'+str(t['tid'])+'" data-search="'+esc(searchable)+'"><time class="when">'+esc(date(t).strftime('%H:%M') if day==today else date(t).strftime('%m-%d %H:%M'))+'</time><a class="title" href="/'+str(t['tid'])+'.html" title="'+esc(t['title'])+'">'+esc(t['title'])+'</a><span class="forum">'+esc(t['forum'])+'</span><span class="number views">'+esc(m['views'] if m else None)+'</span><span class="number replies">'+esc(m['replies'] if m else None)+'</span></div>'
        groups.setdefault(day,[]).append(row)
    sections=[]
    for day in [today]+[d for d in sorted(groups,reverse=True) if d!=today]:
        heading=('TODAY · '+day) if day==today else day
        rows=groups.get(day,[])
        sections.append('<section class="'+('today' if day==today else 'date-group')+'"><h2>'+heading+' · '+str(len(rows))+' 篇</h2><div class="columns"><span>发布时间</span><span>标题</span><span>版块</span><span class="number">浏览</span><span class="number">回复</span></div>'+(''.join(rows) or '<p class="meta">今天还没有已完整归档的文章。</p>')+'</section>')
    index='<h1>我的新足迹文章</h1><label for="search">搜索 </label><input id="search" type="search" placeholder="标题、版块或正文…"><span id="count">%s 篇</span>%s<script src="/search.js" defer></script>'%(len(threads),''.join(sections))
    atomic_write(out/'index.html',document('文章归档',index).encode())
    atomic_write(out/'style.css',CSS.encode())
    atomic_write(out/'search.js',JS.encode())
    atomic_write(out/'reader.js',READER_JS.encode())
    _write_search_index(store,out)
    # Remove stale article exports when a thread is no longer complete.
    allowed={'%s.html'%t['tid'] for t in threads}|{'index.html'}
    for old in out.glob('*.html'):
        if old.name not in allowed:
            old.unlink()
    return dict(articles=len(threads),directory=str(out),url='http://localhost:8080')


def build_incremental(store,tids,*,allow_full_fallback=True,out=None):
    from .media import SCHEMA
    store.db.executescript(SCHEMA)

    out = out if out is not None else store.root/'preview'
    out.mkdir(parents=True, exist_ok=True)
    index_path = out/'index.html'

    if not index_path.is_file():
        if not allow_full_fallback:
            raise ValueError('incremental preview cache missing or inconsistent; full rebuild refused')
        return build(store)

    from .sync import sydney_today
    today = sydney_today()
    tids = {int(x) for x in tids}

    media_map = {
        r['url']: r['path']
        for r in store.db.execute(
            "SELECT url,path FROM media_files WHERE state='success'"
        )
        if (store.root/r['path']).is_file()
    }

    soup = soup_of(index_path.read_bytes())
    cards = soup.select('div.card')
    cached = {}

    for section in soup.select('section'):
        h2 = section.find('h2')
        if h2 is None:
            continue

        dm = re.search(
            r'(\d{4}-\d{2}-\d{2})',
            h2.get_text(' ', strip=True)
        )

        if not dm:
            continue

        day = dm.group(1)

        for card in section.select('div.card'):
            link = card.find('a', href=True)

            if link is None:
                continue

            tm = re.fullmatch(
                r'/(\d+)\.html',
                link.get('href', '')
            )

            if not tm:
                continue

            tid = int(tm.group(1))
            time_tag = card.find('time')
            clock = '00:00'

            if time_tag is not None:
                cm = re.search(
                    r'(\d{2}:\d{2})$',
                    time_tag.get_text(strip=True)
                )

                if cm:
                    clock = cm.group(1)

                if day == today:
                    time_tag.string = clock
                else:
                    time_tag.string = day[5:] + ' ' + clock

            title_node = card.select_one('.title')
            forum_node = card.select_one('.forum')

            lightweight = (
                (
                    title_node.get_text(' ', strip=True)
                    if title_node is not None else ''
                )
                + ' ' +
                (
                    forum_node.get_text(' ', strip=True)
                    if forum_node is not None else ''
                )
            ).lower()

            card['data-tid'] = str(tid)
            card['data-search'] = lightweight

            cached[tid] = (
                day,
                day + ' ' + clock + ':00',
                str(card)
            )

    if cards and len(cached) != len(cards):
        if allow_full_fallback:
            print("Incremental cache unreadable; falling back to full build")
        if not allow_full_fallback:
            raise ValueError('incremental preview cache missing or inconsistent; full rebuild refused')
        return build(store)

    def render_one(t):
        normalized = store.db.execute(
            "SELECT sydney_time FROM publication_times WHERE tid=?",
            (t['tid'],)
        ).fetchone()

        dtv = parsed_date(
            normalized[0]
            if normalized
            else t['created_at_raw']
        )

        posts = list(store.db.execute(
            "SELECT * FROM posts WHERE tid=? AND author_uid=? ORDER BY page,CAST(floor AS INTEGER),pid",
            (t['tid'], UID)
        ))

        m = store.db.execute(
            "SELECT m.* FROM metrics m JOIN snapshots s ON s.id=m.snapshot_id WHERE m.tid=? AND m.views IS NOT NULL AND m.replies IS NOT NULL ORDER BY s.fetched_at DESC,s.id DESC LIMIT 1",
            (t['tid'],)
        ).fetchone()

        live = store.db.execute(
            "SELECT * FROM listing_metrics WHERE tid=?",
            (t['tid'],)
        ).fetchone()

        if live is not None:
            latest = store.db.execute(
                "SELECT max(s.fetched_at) FROM metrics m JOIN snapshots s ON s.id=m.snapshot_id WHERE m.tid=? AND m.views IS NOT NULL",
                (t['tid'],)
            ).fetchone()[0] or 0

            if live['observed_at'] >= latest:
                m = live

        meta = '%s · %s · 浏览 %s · 回复 %s' % tuple(
            esc(x)
            for x in (
                dtv.strftime('%Y-%m-%d %H:%M'),
                t['forum'],
                m['views'] if m else None,
                m['replies'] if m else None
            )
        )

        floors = ''.join(
            '<article class="article-post" id="post-%s">'
            '<p class="meta">%s · %s · %s · PID %s</p>'
            '<div class="reader-body">%s</div>'
            '</article>' % (
                p['pid'],
                esc(p['floor']),
                esc(p['author_name']),
                esc(p['posted_at_raw']),
                p['pid'],
                body_html(p['body_html'], media_map)
            )
            for p in posts
        )

        content = (
            '<h1>' + esc(t['title']) + '</h1>'
            '<p class="meta">' + meta + '</p>'
            '<a rel="noreferrer" href="' +
            esc(thread_url(t['tid'])) +
            '">打开新足迹原主题</a>'
            '<div class="reader-controls" aria-label="正文字号">'
            '<span>正文字号</span>'
            '<button data-font="down" aria-label="缩小字号">A−</button>'
            '<button data-font="reset" aria-label="恢复默认字号">A</button>'
            '<button data-font="up" aria-label="增大字号">A+</button>'
            '<output id="reader-size" aria-live="polite">18px</output>'
            '</div>' +
            floors +
            '<script src="/reader.js" defer></script>'
        )

        atomic_write(
            out/('%s.html' % t['tid']),
            document(t['title'], content).encode()
        )

        forum = t['forum'] or ''

        searchable = (
            t['title'] + ' ' + forum
        ).lower()

        day = dtv.date().isoformat()

        displayed_time = (
            dtv.strftime('%H:%M')
            if day == today
            else dtv.strftime('%m-%d %H:%M')
        )

        row = (
            '<div class="card" data-tid="' +
            str(t['tid']) +
            '" data-search="' +
            esc(searchable) +
            '">'
            '<time class="when">' +
            esc(displayed_time) +
            '</time>'
            '<a class="title" href="/' +
            str(t['tid']) +
            '.html" title="' +
            esc(t['title']) +
            '">' +
            esc(t['title']) +
            '</a>'
            '<span class="forum">' +
            esc(forum) +
            '</span>'
            '<span class="number views">' +
            esc(m['views'] if m else None) +
            '</span>'
            '<span class="number replies">' +
            esc(m['replies'] if m else None) +
            '</span>'
            '</div>'
        )

        return (
            day,
            dtv.strftime('%Y-%m-%d %H:%M:%S'),
            row
        )

    for tid in tids:
        cached.pop(tid, None)

        t = store.db.execute(
            "SELECT * FROM threads WHERE tid=? AND status='complete'",
            (tid,)
        ).fetchone()

        if t is None:
            old = out/('%s.html' % tid)

            if old.exists():
                old.unlink()

            continue

        cached[tid] = render_one(t)

    expected = {
        r[0]
        for r in store.db.execute(
            "SELECT tid FROM threads WHERE status='complete'"
        )
    }

    if set(cached) != expected:
        missing = expected - set(cached)
        extra = set(cached) - expected

        print(
            "Incremental cache mismatch:",
            "missing", len(missing),
            "extra", len(extra)
        )

        if allow_full_fallback:
            print("Falling back to full build")
        if not allow_full_fallback:
            raise ValueError('incremental preview cache missing or inconsistent; full rebuild refused')
        return build(store)

    groups = {}

    for tid, (day, sort_key, row) in cached.items():
        groups.setdefault(day, []).append(
            (sort_key, tid, row)
        )

    sections = []

    ordered_days = (
        [today] +
        [
            d
            for d in sorted(groups, reverse=True)
            if d != today
        ]
    )

    for day in ordered_days:
        items = sorted(
            groups.get(day, []),
            key=lambda x: (x[0], x[1]),
            reverse=True
        )

        rows = [x[2] for x in items]

        heading = (
            'TODAY · ' + day
            if day == today
            else day
        )

        sections.append(
            '<section class="' +
            ('today' if day == today else 'date-group') +
            '">'
            '<h2>' +
            heading +
            ' · ' +
            str(len(rows)) +
            ' 篇</h2>'
            '<div class="columns">'
            '<span>发布时间</span>'
            '<span>标题</span>'
            '<span>版块</span>'
            '<span class="number">浏览</span>'
            '<span class="number">回复</span>'
            '</div>' +
            (
                ''.join(rows)
                if rows
                else '<p class="meta">今天还没有已完整归档的文章。</p>'
            ) +
            '</section>'
        )

    total = len(expected)

    index = (
        '<h1>我的新足迹文章</h1>'
        '<label for="search">搜索 </label>'
        '<input id="search" type="search" '
        'placeholder="标题、版块或正文…">'
        '<span id="count">%s 篇</span>%s'
        '<script src="/search.js" defer></script>'
    ) % (
        total,
        ''.join(sections)
    )

    atomic_write(
        out/'index.html',
        document('文章归档', index).encode()
    )

    atomic_write(
        out/'style.css',
        CSS.encode()
    )

    atomic_write(
        out/'search.js',
        JS.encode()
    )

    atomic_write(
        out/'reader.js',
        READER_JS.encode()
    )

    _write_search_index(store, out)

    return dict(
        articles=total,
        updated_articles=len(tids),
        incremental=True,
        directory=str(out),
        url='http://localhost:8080'
    )


def validate(store):
    failures=[]
    def check(ok, diagnostic):
        if not ok: failures.append(diagnostic)
    check(store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','database_integrity')
    check(not list(store.db.execute('PRAGMA foreign_key_check')),'foreign_keys')
    check(not list(store.db.execute('SELECT tid FROM threads GROUP BY tid HAVING count(*)>1')),'duplicate_tid')
    for snap in store.db.execute('SELECT * FROM snapshots'):
        try: store.raw(snap)
        except (OSError,ValueError): failures.append('raw_snapshot:%s'%snap['id'])
    pages=list(store.db.execute("SELECT * FROM pages WHERE result='parsed'"))
    for page in pages:
        try:
            snap=store.db.execute('SELECT * FROM snapshots WHERE id=?',(page['snapshot_id'],)).fetchone()
            parsed=parse_thread(store.raw(snap),page['url'])
            t=store.db.execute('SELECT * FROM threads WHERE tid=?',(page['tid'],)).fetchone()
            check(parsed['title']==t['title'] and parsed['forum']==t['forum'],'metadata:%s'%page['url'])
            check(not parsed['gaps'],'parser_gaps:%s'%page['url'])
            m=store.db.execute('SELECT * FROM metrics WHERE tid=? AND snapshot_id=?',(page['tid'],snap['id'])).fetchone()
            check(m is not None and m['views']==parsed['views'] and m['replies']==parsed['replies'] and (page['page'] != 1 or (m['views'] is not None and m['replies'] is not None)),'metrics:%s'%page['url'])
            expected={p['pid'] for p in parsed['posts']}
            rows=list(store.db.execute('SELECT * FROM posts WHERE tid=? AND page=?',(page['tid'],page['page'])))
            check(expected=={r['pid'] for r in rows},'author_pids:%s'%page['url'])
            for p in rows:
                parsed_date(p['posted_at_raw'])
                check(p['author_uid']==UID and bool(p['body_text']),'author_body:%s'%p['pid'])
                original=next(x for x in parsed['posts'] if x['pid']==p['pid'])
                check(original['hash']==p['content_sha256'] and original['html']==p['body_html'] and original['text']==p['body_text'],'body_hash:%s'%p['pid'])
                check(original['floor']==p['floor'] and original['posted_at_raw']==p['posted_at_raw'],'floor_date:%s'%p['pid'])
        except Exception as e:
            failures.append('page:%s:%s'%(page['url'],type(e).__name__))
    complete=list(store.db.execute("SELECT * FROM threads WHERE status='complete'"))
    for t in complete:
        try:
            soup=soup_of((store.root/'preview'/('%s.html'%t['tid'])).read_bytes())
            check(soup.h1.get_text()==t['title'],'render_title:%s'%t['tid'])
            check(soup.find('a',href=thread_url(t['tid'])) is not None,'original_link:%s'%t['tid'])
            for p in store.db.execute('SELECT pid FROM posts WHERE tid=?',(t['tid'],)):
                check(soup.find(id='post-%s'%p['pid']) is not None,'render_floor:%s'%p['pid'])
            check(not soup.find(['iframe','object']),'unsafe_render:%s'%t['tid'])
            check(all(n.get('src')=='/reader.js' and not n.get_text(strip=True) for n in soup.find_all('script')),'unsafe_script:%s'%t['tid'])
            for img in soup.find_all('img'):
                path=img.get('src','').lstrip('/')
                check(bool(re.fullmatch(r'media/[0-9a-f]{2}/[0-9a-f]{64}\.(jpg|png|gif|webp)',path)) and (store.root/path).is_file(),'unsafe_or_missing_image:%s'%t['tid'])
        except Exception as e: failures.append('render:%s:%s'%(t['tid'],type(e).__name__))
    result=dict(ok=not failures,complete_threads=len(complete),parsed_pages=len(pages),author_posts=store.db.execute('SELECT count(*) FROM posts').fetchone()[0],failures=failures)
    atomic_write(store.root/'local-validation.json',json.dumps(result,ensure_ascii=False,indent=2).encode())
    return result
