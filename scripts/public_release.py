"""NAS-only public release publisher. No network, crawler, Docker or writable SQLite."""
import datetime
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'.deps')]
from oursteps.deployment import public_access
from oursteps.dates import sydney_today
from oursteps.preview import parsed_date, READER_JS
from oursteps.deployment import deployment
from bs4 import BeautifulSoup

FIXED = {'index.html', 'style.css', 'search.js', 'reader.js', 'search-index.json', 'robots.txt'}
PUBLIC_GENERATED = {'article-views.json'}
from oursteps.control_center import OWNER_FILES, files as control_center_files
from oursteps.analytics import payload as analytics_payload
ARTICLE = re.compile(r'([0-9]+)\.html\Z')
RELEASE = re.compile(r'releases/[0-9a-f]{64}\Z')
STATIC_PRIVATE_MARKERS = (b'.secrets', b'session.json', b'archive.sqlite3', b'/data/raw')
MARKERS = STATIC_PRIVATE_MARKERS + tuple(dict.fromkeys(
    path.encode('utf-8') for path in (str(ROOT), deployment()[1]) if path
))
ROBOTS = b'User-agent: *\nDisallow: /\n'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def directory(path):
    require(not path.is_symlink(), 'symlink directory refused: '+path.name)
    path.mkdir(exist_ok=True)
    path.chmod(0o755)


def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, content):
    fd, tmp = tempfile.mkstemp(prefix='.'+path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def expected_tids(root):
    dbpath = root/'data/archive.sqlite3'
    require(sys.platform != 'darwin' and not str(dbpath).startswith('/Volumes/'), 'query SQLite on NAS native filesystem only')
    db = sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        return {str(r[0]) for r in db.execute("SELECT tid FROM threads WHERE status='complete'")}
    finally:
        db.close()


def publication_dates(root):
    dbpath = root/'data/archive.sqlite3'
    require(sys.platform != 'darwin' and not str(dbpath).startswith('/Volumes/'), 'query SQLite on NAS native filesystem only')
    db = sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        # Exactly the precedence used by preview.build.date; never parse post raw data.
        return {str(tid): value for tid, value in db.execute("SELECT t.tid, CASE WHEN p.tid IS NOT NULL THEN p.sydney_time ELSE t.created_at_raw END FROM threads t LEFT JOIN publication_times p ON p.tid=t.tid WHERE t.status='complete'")}
    finally:
        db.close()


def recent_policy(dates, today=None):
    today = today or sydney_today()
    cutoff = (datetime.date.fromisoformat(today)-datetime.timedelta(days=365)).isoformat()
    allowed = set(); missing = 0
    for tid, value in dates.items():
        try:
            day = parsed_date(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            missing += 1
            continue
        if cutoff <= day <= today:
            allowed.add(str(tid))
    return dict(cutoff=cutoff, as_of=today, excluded_missing_dates=missing, allowed=sorted(allowed))


def owner_index(data):
    soup = BeautifulSoup(data, 'html.parser')
    if soup.select_one('.owner-control-link') is None:
        count = soup.select_one('#count')
        anchor = soup.new_tag('a', href='/control-center.html')
        anchor['class'] = ['range-btn', 'owner-control-link']
        anchor.string = 'Control Center'
        if count is not None:
            count.insert_after(anchor)
        else:
            heading = soup.select_one('h1')
            require(heading is not None, 'homepage heading missing')
            heading.insert_after(anchor)
    return str(soup).encode('utf-8')


def stage_recent(stage, allowed):
    recent = stage/'recent-1y'; directory(recent)
    for name in (FIXED-{'index.html','search-index.json'}) | PUBLIC_GENERATED | {tid+'.html' for tid in allowed}:
        # Stage files are already durable, validated 0644 public content.
        # Link only within the immutable release, never to private preview/raw.
        os.link(stage/name, recent/name)
    soup = BeautifulSoup((stage/'index.html').read_bytes(), 'html.parser')
    for link in soup.select('.owner-control-link'):
        link.decompose()
    for card in soup.select('.card'):
        if card.get('data-tid') not in allowed:
            card.decompose()
    for section in soup.select('section'):
        cards = section.select('.card')
        if not cards:
            section.decompose()
        elif section.h2:
            section.h2.string = re.sub(r' · [0-9]+ 篇$', ' · %s 篇' % len(cards), section.h2.get_text())
    if soup.select_one('#count'):
        soup.select_one('#count').string = '%s 篇' % len(allowed)
    index = json.loads((stage/'search-index.json').read_text())
    for name, data in [('index.html', str(soup)), ('search-index.json', json.dumps({tid:index[tid] for tid in sorted(allowed)}, ensure_ascii=False))]:
        write_atomic(recent/name, data.encode('utf-8')); (recent/name).chmod(0o644)
    sync_dir(recent)


def validate(path, expected, recent=None, allow_empty=False, owner=False, require_control=False, require_generated=False, require_owner_link=False):
    require(path.is_dir() and not path.is_symlink(), 'release directory missing or symlink')
    require(stat.S_IMODE(path.stat().st_mode) == 0o755, 'release directory permissions must be 755')
    hashes = {}; tids = set()
    for item in path.iterdir():
        if item.name == 'recent-1y' and recent is not None:
            continue
        require(item.is_file() and not item.is_symlink(), 'non-file/symlink in release: '+item.name)
        match = ARTICLE.fullmatch(item.name)
        require(item.name in FIXED or item.name in PUBLIC_GENERATED or match or (owner and item.name in OWNER_FILES), 'unexpected public file: '+item.name)
        require(stat.S_IMODE(item.stat().st_mode) == 0o644, 'file permissions must be 644: '+item.name)
        data = item.read_bytes()
        require(not any(marker in data for marker in MARKERS), 'private marker in '+item.name)
        hashes[item.name] = hashlib.sha256(data).hexdigest()
        if match: tids.add(match[1])
    require(FIXED <= set(hashes), 'missing fixed assets')
    if require_generated: require(PUBLIC_GENERATED <= set(hashes), 'missing generated public assets')
    if require_control: require(OWNER_FILES <= set(hashes), 'missing owner control-center assets')
    require(tids == set(expected) and (tids or allow_empty), 'article TIDs differ from expected complete threads')
    require((path/'robots.txt').read_bytes() == ROBOTS, 'robots must remain Disallow /')
    soup = BeautifulSoup((path/'index.html').read_bytes(), 'html.parser')
    owner_links = soup.select('a.owner-control-link[href="/control-center.html"]')
    if require_owner_link:
        require(len(owner_links) == 1, 'owner homepage control-center link missing/duplicate')
    elif not owner:
        require(not owner_links, 'owner control-center link leaked outside full scope')
    cards = soup.select('.card')
    card_ids = [c.get('data-tid') for c in cards]
    require(len(card_ids) == len(tids) and set(card_ids) == tids, 'homepage card/data-tid mismatch')
    index = json.loads((path/'search-index.json').read_text())
    require(isinstance(index, dict) and set(index) == tids and all(isinstance(v,str) for v in index.values()), 'search-index TIDs mismatch')
    # Homepage card payload must be only title/category, never author body text.
    for card in cards:
        title = card.select_one('.title'); forum = card.select_one('.forum')
        require(title is not None and forum is not None, 'missing card title/category')
        allowed = ' '.join((title.get_text(), forum.get_text())).lower()
        require(card.get('data-search','').strip() == allowed.strip(), 'full body or unexpected inline search payload')
        require(title.get('href') == '/'+card['data-tid']+'.html', 'card link mismatch')
        require(not card.select('.post, .body, .article-body, .reader-body, .article-post'), 'article body embedded in homepage')
    require(not soup.select('.post, .article-body, .reader-body, .article-post'), 'article body embedded in homepage')
    require(all(s.get('src') for s in soup.select('script')), 'inline script in homepage')
    js = (path/'search.js').read_text()
    for token in ('ensureSearchIndex', '/search-index.json', 'handleSearchInput', 'matchesSearch', 'enterGlobalRange', 'sort', 'rangeButtons'):
        require(token in js, 'search/sort/range contract missing: '+token)
    require('await ensureSearchIndex()' in js, 'lazy search trigger missing')
    result = dict(articles=len(tids), cards=len(cards), search_entries=len(index), hashes=hashes)
    if require_owner_link: result['owner_home_link'] = True
    if recent is not None:
        require(set(recent['allowed']) <= tids, 'recent TIDs outside full scope')
        scoped = validate(path/'recent-1y', recent['allowed'], allow_empty=True, owner=False, require_generated=require_generated)
        for name, digest in scoped['hashes'].items():
            if name not in {'index.html', 'search-index.json'}:
                require(digest == hashes.get(name), 'scoped article/static differs from full source: '+name)
        recent_index = json.loads((path/'recent-1y/search-index.json').read_text())
        require(recent_index == {tid:index[tid] for tid in recent['allowed']}, 'scoped search content differs from allowed source')
        hashes.update({'recent-1y/'+name: value for name,value in scoped['hashes'].items()})
        result['recent'] = recent
        result['recent_articles'] = scoped['articles']
    return result


def validate_recent_from_full(path, full, recent, require_generated=False):
    """Validate only recent-1y after an already validated immutable top-level stage."""
    tids={ARTICLE.fullmatch(name)[1] for name in full['hashes'] if ARTICLE.fullmatch(name)}
    require(set(recent['allowed']) <= tids, 'recent TIDs outside full scope')
    scoped=validate(path/'recent-1y',recent['allowed'],allow_empty=True,owner=False,require_generated=require_generated)
    for name,digest in scoped['hashes'].items():
        if name not in {'index.html','search-index.json'}:
            require(digest == full['hashes'].get(name), 'scoped article/static differs from full source: '+name)
    index=json.loads((path/'search-index.json').read_text())
    recent_index=json.loads((path/'recent-1y/search-index.json').read_text())
    require(recent_index == {tid:index[tid] for tid in recent['allowed']}, 'scoped search content differs from allowed source')
    report=dict(full)
    report['hashes']=dict(full['hashes'])
    report['hashes'].update({'recent-1y/'+name:value for name,value in scoped['hashes'].items()})
    report['recent']=recent
    report['recent_articles']=scoped['articles']
    return report


def config_check(root):
    compose = (root/'compose.public.yaml').read_text()
    block = compose.split('    volumes:',1)[1].split('    read_only:',1)[0]
    mounts = [line.strip()[2:] for line in block.splitlines() if line.strip().startswith('- ')]
    require(set(mounts) == {'./public-site:/srv/public:ro', './nginx-public-stable.conf:/etc/nginx/nginx.conf:ro', './.secrets/oursteps.htpasswd:/etc/nginx/oursteps.htpasswd:ro', './data/public-analytics:/srv/analytics:rw'}, 'unexpected nginx mounts')
    require('docker.sock' not in compose and 'privileged:' not in compose, 'unsafe Docker configuration')
    for token in ('read_only: true', 'ALL', 'no-new-privileges:true', '18080:8080'):
        require(token in compose, 'missing container safeguard: '+token)
    require('\n  action-api:' in compose, 'missing internal action-api')
    action = compose.split('\n  action-api:',1)[1]
    require('ports:' not in action, 'action-api must not publish a host port')
    require('.secrets' not in action and 'archive.sqlite3' not in action and 'docker.sock' not in action, 'action-api secret/private mount refused')
    for token in ('control_action_api.py:/app/scripts/control_action_api.py:ro', 'control_actions.py:/app/oursteps/control_actions.py:ro', 'tool_registry.json:/app/config/tool_registry.json:ro', 'data/control-actions:/state/data/control-actions:rw', 'read_only: true', 'no-new-privileges:true'):
        require(token in action, 'missing action-api safeguard: '+token)
    nginx = (root/'nginx-public-stable.conf').read_text()
    recent_user, full_user = public_access()
    for token in ('root $archive_root;'
                  , 'default /dev/null;', f'~^{recent_user}$ recent-1y;', f'~^{full_user}$ full;', 'auth_basic_user_file /etc/nginx/oursteps.htpasswd;', 'server_tokens off;', 'connect-src \'self\'', 'limit_except GET HEAD', 'gzip on;', 'open_file_cache off;', 'control-center\\.json', 'article-views\\.json', '/srv/analytics/article-views.log', 'article_reads', 'proxy_pass http://action-api:8081;', 'X-Oursteps-Action-Request'):
        require(token in nginx, 'missing nginx contract: '+token)


@contextlib.contextmanager
def locks(root):
    # Same order for publish/rollback. No lock files live in nginx's mount.
    with (root/'data/public-publish.lock').open('a') as pub:
        fcntl.flock(pub, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (root/'data/worker.lock').open('a') as worker:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


def pointer(site, name):
    path = site/name
    if not path.exists() and not path.is_symlink(): return None
    require(path.is_symlink(), name+' must be a relative release symlink')
    target = os.readlink(str(path))
    require(RELEASE.fullmatch(target), 'unsafe release pointer')
    require((site/target).is_dir() and not (site/target).is_symlink(), 'release target missing')
    return target


def switch(site, name, target):
    temp = site/('.'+name+'-'+str(os.getpid()))
    try:
        temp.symlink_to(target)
        os.replace(str(temp), str(site/name))
        sync_dir(site)
    finally:
        if temp.is_symlink(): temp.unlink()


def metadata(root, target):
    return root/'data/public-releases'/(Path(target).name+'.json')


def validate_saved(root, target):
    report = json.loads(metadata(root,target).read_text())
    expected = {ARTICLE.fullmatch(n)[1] for n in report['hashes'] if ARTICLE.fullmatch(n)}
    require_control = OWNER_FILES <= set(report.get('hashes', {}))
    require_generated = PUBLIC_GENERATED <= set(report.get('hashes', {}))
    actual = validate(root/'public-site'/target, expected, report.get('recent'), owner=True, require_control=require_control, require_generated=require_generated, require_owner_link=bool(report.get('owner_home_link')))
    require(actual['hashes'] == report['hashes'], 'release manifest checksum mismatch')
    digest = hashlib.sha256(json.dumps(actual['hashes'],sort_keys=True).encode()).hexdigest()
    require(Path(target).name == digest, 'release identity checksum mismatch')
    return actual


def post_publish_check(root, result):
    """Cheap immediate check after publish; full public_healthcheck remains independent."""
    root = Path(root)
    site = root/'public-site'
    target = result.get('release')
    require(target is not None, 'post-publish release missing')
    require(pointer(site, 'current') == target, 'post-publish current pointer mismatch')
    require(pointer(site, 'previous') is not None, 'post-publish previous pointer missing')
    report = json.loads(metadata(root, target).read_text())
    hashes = report.get('hashes')
    require(isinstance(hashes, dict) and hashes, 'post-publish manifest missing hashes')
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    require(Path(target).name == digest, 'post-publish release identity mismatch')
    require(report.get('articles') == result.get('articles'), 'post-publish article count mismatch')
    if 'recent_articles' in result:
        require(report.get('recent_articles') == result.get('recent_articles'), 'post-publish recent count mismatch')
    return {'status':'pass','release':target,'articles':report.get('articles'),'recent_articles':report.get('recent_articles')}


def publish(root=ROOT, expected=None, rollback=False, dates=None, today=None, preview_hashes=None):
    root = Path(root)
    started = time.monotonic()
    performance = {}
    with locks(root):
        config_check(root)
        site = root/'public-site'; directory(site); directory(site/'releases')
        old = pointer(site, 'current')
        phase = time.monotonic()
        old_report = validate_saved(root, old) if old else None
        performance['previous_validation_seconds'] = round(time.monotonic()-phase, 6)
        if rollback:
            previous = pointer(site,'previous')
            require(previous is not None, 'no previous release')
            validate_saved(root, previous)
            # Leave previous intact until current is switched; rollback remains retryable.
            switch(site,'current',previous)
            if old: switch(site,'previous',old)
            return {'status':'rolled_back', 'release':previous}
        require(not (root/"data/preview-incremental-pending.json").exists(), "incremental preview pending; rerun preview before publishing")
        if preview_hashes is not None:
            require(bool(preview_hashes), "empty batch preview")
            for name, digest in preview_hashes.items():
                require(ARTICLE.fullmatch(name) is not None or name in FIXED-{'robots.txt'}, "invalid batch preview file")
                require(hashlib.sha256((root/"data/preview"/name).read_bytes()).hexdigest() == digest, "batch preview changed; rerun batch")
        expected = expected_tids(root) if expected is None else expected
        dates = publication_dates(root) if dates is None else dates
        require(set(dates) == set(expected), 'publication dates TIDs mismatch')
        recent = recent_policy(dates, today)
        source = root/'data/preview'
        require(source.is_dir() and not source.is_symlink(), 'preview missing/symlink')
        selected = []
        source_hashes = {'robots.txt':hashlib.sha256(ROBOTS).hexdigest()}
        for item in source.iterdir():
            require(not item.is_symlink(), 'preview symlink refused')
            if item.name in FIXED-{'robots.txt'} or ARTICLE.fullmatch(item.name):
                require(item.is_file(), 'invalid preview file')
                selected.append(item)
                source_hashes[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
        source_tids = {ARTICLE.fullmatch(n)[1] for n in source_hashes if ARTICLE.fullmatch(n)}
        require(source_tids == set(expected), 'preview does not match complete TIDs')
        owner_index_content = owner_index((source/'index.html').read_bytes())
        source_hashes['index.html'] = hashlib.sha256(owner_index_content).hexdigest()
        # Shared reader JS comes from current code so frontend-only changes do not require a 5k+ article rebuild.
        reader_content = READER_JS.encode()
        source_hashes['reader.js'] = hashlib.sha256(reader_content).hexdigest()
        analytics_content = analytics_payload(root, expected)
        source_hashes['article-views.json'] = hashlib.sha256(analytics_content).hexdigest()
        owner_content = control_center_files(root, len(expected), len(recent['allowed']))
        source_hashes.update({name:hashlib.sha256(data).hexdigest() for name,data in owner_content.items()})
        performance['prepare_seconds'] = round(time.monotonic()-started-sum(performance.values()), 6)
        if (old_report and old_report.get('recent', {}).get('allowed') == recent['allowed']
                and source_hashes == {name:digest for name,digest in old_report['hashes'].items() if '/' not in name}):
            performance['total_seconds'] = round(time.monotonic()-started, 6)
            return {'status':'unchanged', 'release':old, 'articles':old_report['articles'],
                    'recent_articles':len(recent['allowed']), 'cutoff':recent['cutoff'],
                    'excluded_missing_dates':recent['excluded_missing_dates'], 'performance':performance}
        directory(root/'data/public-releases')
        stage = Path(tempfile.mkdtemp(prefix='.public-release-', dir=str(root)))
        try:
            phase = time.monotonic()
            stage.chmod(0o755)
            for item in selected:
                if old_report and old_report['hashes'].get(item.name) == source_hashes[item.name]:
                    # Link only immutable PUBLIC releases, never private preview/raw files.
                    os.link(str(site/old/item.name),str(stage/item.name))
                else:
                    with item.open('rb') as src, (stage/item.name).open('wb') as dst:
                        shutil.copyfileobj(src,dst); dst.flush(); os.fsync(dst.fileno())
                    (stage/item.name).chmod(0o644)
            write_atomic(stage/'index.html', owner_index_content); (stage/'index.html').chmod(0o644)
            write_atomic(stage/'robots.txt', ROBOTS); (stage/'robots.txt').chmod(0o644)
            write_atomic(stage/'reader.js', reader_content); (stage/'reader.js').chmod(0o644)
            write_atomic(stage/'article-views.json', analytics_content); (stage/'article-views.json').chmod(0o644)
            for name,data in owner_content.items():
                write_atomic(stage/name,data); (stage/name).chmod(0o644)
            performance['full_stage_seconds'] = round(time.monotonic()-phase, 6)
            phase = time.monotonic()
            full_report=validate(stage,expected,owner=True,require_control=True,require_generated=True,require_owner_link=True)
            performance['full_validation_seconds'] = round(time.monotonic()-phase, 6)
            phase = time.monotonic()
            stage_recent(stage, set(recent['allowed']))
            performance['recent_stage_seconds'] = round(time.monotonic()-phase, 6)
            phase = time.monotonic()
            report=validate_recent_from_full(stage,full_report,recent,require_generated=True)
            performance['final_validation_seconds'] = round(time.monotonic()-phase, 6)
            phase = time.monotonic()
            release_id = hashlib.sha256(json.dumps(report['hashes'],sort_keys=True).encode()).hexdigest()
            target = 'releases/'+release_id
            dest = site/target
            if dest.exists():
                require(validate(dest,expected,recent,owner=True,require_control=True,require_generated=True,require_owner_link=True)['hashes'] == report['hashes'], 'existing release changed')
            else:
                sync_dir(stage); os.rename(str(stage),str(dest)); sync_dir(site/'releases')
            write_atomic(metadata(root,target),json.dumps(report,sort_keys=True).encode())
            if old != target:
                # Persist rollback pointer before the single atomic live switch.
                switch(site,'previous',old or target)
                switch(site,'current',target)
            performance['finalize_seconds'] = round(time.monotonic()-phase, 6)
            performance['total_seconds'] = round(time.monotonic()-started, 6)
            status = 'unchanged' if old == target else 'published'
            return {'status':status,'release':target, 'articles':report['articles'], 'recent_articles':report['recent_articles'], 'cutoff':recent['cutoff'], 'excluded_missing_dates':recent['excluded_missing_dates'], 'performance':performance}
        finally:
            if stage.exists(): shutil.rmtree(stage)
