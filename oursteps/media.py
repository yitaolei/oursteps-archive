"""Serial, resumable image archive for already-complete owner posts only."""
from .config import UID
import hashlib,ipaddress,json,random,re,socket,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urljoin
from urllib.request import Request,build_opener,HTTPCookieProcessor
from urllib.error import HTTPError,URLError
from urllib.robotparser import RobotFileParser
from .fetch import AGENT,NoRedirect,session,retry_after,longest_rule_allowed
from .store import atomic_write

SCHEMA='''CREATE TABLE IF NOT EXISTS media_files(url TEXT PRIMARY KEY,state TEXT DEFAULT 'pending',path TEXT,mime TEXT,size INTEGER,sha256 TEXT,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,error TEXT,tid INTEGER,pid INTEGER);
CREATE TABLE IF NOT EXISTS media_refs(pid INTEGER,position INTEGER,url TEXT,PRIMARY KEY(pid,position));'''

def image_url(img):
    value=img.get('file') or img.get('data-src') or img.get('src') or ''
    u=urlsplit(value)
    if u.scheme not in ('http','https'):return None
    return urlunsplit((u.scheme,u.netloc,u.path,u.query,''))

def irrelevant(url,attrs):
    path=urlsplit(url).path.lower()
    return bool(re.search(r'(?:/static/image/(?:common|smiley)/|/uc_server/avatar(?:\.php|/)|/(?:avatars?|ads?)/)',path) or attrs.get('smilieid'))

def discover(store):
    store.db.executescript(SCHEMA)
    if 'reported_mime' not in [r[1] for r in store.db.execute('PRAGMA table_info(media_files)')]:
        store.db.execute('ALTER TABLE media_files ADD COLUMN reported_mime TEXT')
        store.db.commit()
    with store.db:
        for row in store.db.execute("SELECT url,path FROM media_files WHERE state='success'").fetchall():
            if not row['path'] or not (store.root/row['path']).is_file():
                store.db.execute("UPDATE media_files SET state='pending',error='local_file_missing' WHERE url=?",(row['url'],))
    # assets were parsed directly from the configured owner postmessage nodes in saved raw.
    with store.db:
        for a in store.db.execute("SELECT a.*,p.tid FROM assets a JOIN posts p ON p.pid=a.pid JOIN threads t ON t.tid=p.tid WHERE p.author_uid=? AND t.status='complete' AND a.kind='image'", (UID,)).fetchall():
            attrs=json.loads(a['attributes']);url=image_url(dict(attrs,file=a['url'])) if a['url'] else None
            if not url or irrelevant(url,attrs):continue
            store.db.execute('INSERT OR IGNORE INTO media_files(url,tid,pid) VALUES(?,?,?)',(url,a['tid'],a['pid']))
            store.db.execute('INSERT OR REPLACE INTO media_refs VALUES(?,?,?)',(a['pid'],a['position'],url))

def sniff(data):
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg','jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png','png'
    if data[:6] in (b'GIF87a',b'GIF89a'):return 'image/gif','gif'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp','webp'
    raise ValueError('unsupported_or_non_image_response')

def public_url(url):
    u=urlsplit(url)
    if u.scheme not in ('http','https') or u.username or u.password or u.port not in (None,80,443):raise ValueError('unsafe_image_url')
    addresses=socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('non_public_image_host')
    return u.scheme+'://'+u.netloc

class MediaFailure(Exception):
    def __init__(self,state,reason,delay=0):self.state,self.reason,self.delay=state,reason,delay

class Transport:
    def __init__(self,store):
        self.store=store;jar,_=session(store)
        self.opener=build_opener(NoRedirect(),HTTPCookieProcessor(jar));self.policies={};self.blocked={}
    def request(self,url,limit):
        public_url(url)
        delay=max(5,random.uniform(2,5),float(self.store.setting('adaptive_delay','0')),getattr(self,'robot_delay',0))
        time.sleep(max(0,delay-(time.time()-float(self.store.setting('last_request','0')))))
        start=time.time();self.store.set_setting('last_request',start)
        try:r=self.opener.open(Request(url,headers={'User-Agent':AGENT,'Accept':'image/*,text/plain;q=0.1'}),timeout=30)
        except HTTPError as e:r=e
        with r:
            body=r.read(limit+1);code=r.code;headers=r.headers
        latency=time.time()-start
        self.store.set_setting('adaptive_delay',min(120,max(latency*2,float(self.store.setting('adaptive_delay','0'))*.9)))
        if code==429:raise MediaFailure('retry_later','http_429',retry_after(headers.get('Retry-After')))
        if code in (401,403):raise MediaFailure('permission_denied','http_'+str(code))
        if code>=500:raise MediaFailure('retry_later','http_'+str(code),300)
        if len(body)>limit:raise MediaFailure('unsupported','image_size_limit_20MB')
        return code,headers,body
    def policy(self,url):
        origin=public_url(url)
        if origin in self.blocked:raise self.blocked[origin]
        if origin not in self.policies:
            key='media_robots:'+hashlib.sha256(origin.encode()).hexdigest()
            cached=json.loads(self.store.setting(key,'{}'))
            if cached.get('until',0)>time.time():text=cached['text']
            else:
                code,headers,body=self.request(origin+'/robots.txt',512*1024)
                if code==404:text='User-agent: *\nAllow: /'
                elif code==200:text=body.decode('utf-8','replace')
                else:raise MediaFailure('retry_later','robots_unavailable_http_'+str(code),3600)
                if 'user-agent:' not in text.lower():raise MediaFailure('retry_later','robots_unrecognized',3600)
                self.store.set_setting(key,json.dumps(dict(text=text,until=time.time()+86400)))
            robot=RobotFileParser();robot.parse(text.splitlines());rate=robot.request_rate(AGENT)
            self.policies[origin]=(text,max(robot.crawl_delay(AGENT) or 0,rate.seconds/rate.requests if rate else 0))
        text,self.robot_delay=self.policies[origin]
        if not longest_rule_allowed(text.splitlines(),url):raise MediaFailure('robots_denied','robots_disallow')
    def get(self,url):
        try:
            for _ in range(4):
                self.policy(url)
                code,headers,body=self.request(url,20*1024*1024)
                if code in (301,302,303,307,308):
                    url=urljoin(url,headers.get('Location',''));public_url(url);continue
                if code==404:raise MediaFailure('not_found','http_404')
                if code!=200:raise MediaFailure('retry_later','http_'+str(code),300)
                return body,headers.get_content_type()
            raise MediaFailure('unsupported','redirect_limit')
        except MediaFailure as e:
            if e.state in ('permission_denied','retry_later'):self.blocked[urlsplit(url).scheme+'://'+urlsplit(url).netloc]=e
            raise

def run(store,limit=100,tids=None):
    discover(store);transport=Transport(store);downloaded=0
    scope='' if tids is None else ' AND EXISTS (SELECT 1 FROM media_refs r JOIN posts p ON p.pid=r.pid WHERE r.url=media_files.url AND p.tid IN ('+','.join(str(int(t)) for t in tids or [0])+'))'
    rows=store.db.execute("SELECT * FROM media_files WHERE state IN ('pending','retry_later') AND retry_at<=?"+scope+" ORDER BY tid,pid,url LIMIT ?",(time.time(),limit)).fetchall()
    for row in rows:
        url=row['url']
        try:
            with store.db:store.db.execute('UPDATE media_files SET attempts=attempts+1 WHERE url=?',(url,))
            body,reported=transport.get(url);mime,ext=sniff(body)
            if reported not in ('image/jpeg','image/png','image/gif','image/webp','application/octet-stream','binary/octet-stream'):raise ValueError('mime_signature_mismatch:'+reported)
            sha=hashlib.sha256(body).hexdigest()
            existing=store.db.execute("SELECT path FROM media_files WHERE sha256=? AND state='success'",(sha,)).fetchone()
            path=existing['path'] if existing else 'media/'+sha[:2]+'/'+sha+'.'+ext
            if not (store.root/path).exists():atomic_write(store.root/path,body)
            with store.db:store.db.execute("UPDATE media_files SET state='success',path=?,mime=?,size=?,sha256=?,reported_mime=?,error=NULL,retry_at=0 WHERE url=?",(path,mime,len(body),sha,reported,url))
            downloaded+=1
        except (MediaFailure,URLError,TimeoutError,ConnectionError,OSError,ValueError) as e:
            state=e.state if isinstance(e,MediaFailure) else ('unsupported' if isinstance(e,ValueError) else 'retry_later')
            reason=e.reason if isinstance(e,MediaFailure) else str(e)
            delay=max(getattr(e,'delay',0),min(3600,60*2**min(row['attempts'],6))*random.uniform(1,1.3))
            with store.db:store.db.execute('UPDATE media_files SET state=?,error=?,retry_at=? WHERE url=?',(state,reason,time.time()+delay if state=='retry_later' else 0,url))
    states={r[0]:r[1] for r in store.db.execute('SELECT state,count(*) FROM media_files GROUP BY state')}
    result=dict(detected=sum(states.values()),references=store.db.execute('SELECT count(*) FROM media_refs').fetchone()[0],downloaded_this_run=downloaded,states=states,failures=[dict(r) for r in store.db.execute("SELECT url,state,error FROM media_files WHERE state!='success'")])
    atomic_write(store.root/'media-report.json',json.dumps(result,ensure_ascii=False,indent=2).encode())
    return result
