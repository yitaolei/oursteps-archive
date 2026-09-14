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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'.deps')]
from oursteps.deployment import public_access
from oursteps.dates import sydney_today
from oursteps.preview import parsed_date
from oursteps.deployment import deployment
from bs4 import BeautifulSoup

FIXED = {'index.html', 'style.css', 'search.js', 'reader.js', 'search-index.json', 'robots.txt'}
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


def stage_recent(stage, allowed):
    recent = stage/'recent-1y'; directory(recent)
    for name in FIXED-{'index.html','search-index.json'} | {tid+'.html' for tid in allowed}:
        # Stage files are already durable, validated 0644 public content.
        # Link only within the immutable release, never to private preview/raw.
        os.link(stage/name, recent/name)
    soup = BeautifulSoup((stage/'index.html').read_bytes(), 'html.parser')
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


def validate(path, expected, recent=None, allow_empty=False):
    require(path.is_dir() and not path.is_symlink(), 'release directory missing or symlink')
    require(stat.S_IMODE(path.stat().st_mode) == 0o755, 'release directory permissions must be 755')
    hashes = {}; tids = set()
    for item in path.iterdir():
        if item.name == 'recent-1y' and recent is not None:
            continue
        require(item.is_file() and not item.is_symlink(), 'non-file/symlink in release: '+item.name)
        match = ARTICLE.fullmatch(item.name)
        require(item.name in FIXED or match, 'unexpected public file: '+item.name)
        require(stat.S_IMODE(item.stat().st_mode) == 0o644, 'file permissions must be 644: '+item.name)
        data = item.read_bytes()
        require(not any(marker in data for marker in MARKERS), 'private marker in '+item.name)
        hashes[item.name] = hashlib.sha256(data).hexdigest()
        if match: tids.add(match[1])
    require(FIXED <= set(hashes), 'missing fixed assets')
    require(tids == set(expected) and (tids or allow_empty), 'article TIDs differ from expected complete threads')
    require((path/'robots.txt').read_bytes() == ROBOTS, 'robots must remain Disallow /')
    soup = BeautifulSoup((path/'index.html').read_bytes(), 'html.parser')
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
    if recent is not None:
        require(set(recent['allowed']) <= tids, 'recent TIDs outside full scope')
        scoped = validate(path/'recent-1y', recent['allowed'], allow_empty=True)
        for name, digest in scoped['hashes'].items():
            if name not in {'index.html', 'search-index.json'}:
                require(digest == hashes.get(name), 'scoped article/static differs from full source: '+name)
        recent_index = json.loads((path/'recent-1y/search-index.json').read_text())
        require(recent_index == {tid:index[tid] for tid in recent['allowed']}, 'scoped search content differs from allowed source')
        hashes.update({'recent-1y/'+name: value for name,value in scoped['hashes'].items()})
        result['recent'] = recent
        result['recent_articles'] = scoped['articles']
    return result


def config_check(root):
    compose = (root/'compose.public.yaml').read_text()
    block = compose.split('    volumes:',1)[1].split('    read_only:',1)[0]
    mounts = [line.strip()[2:] for line in block.splitlines() if line.strip().startswith('- ')]
    require(set(mounts) == {'./public-site:/srv/public:ro', './nginx-public-stable.conf:/etc/nginx/nginx.conf:ro', './.secrets/oursteps.htpasswd:/etc/nginx/oursteps.htpasswd:ro'}, 'unexpected nginx mounts')
    require('docker.sock' not in compose and 'privileged:' not in compose, 'unsafe Docker configuration')
    for token in ('read_only: true', 'ALL', 'no-new-privileges:true', '18080:8080'):
        require(token in compose, 'missing container safeguard: '+token)
    nginx = (root/'nginx-public-stable.conf').read_text()
    recent_user, full_user = public_access()
    for token in ('root $archive_root;'
                  , 'default /dev/null;', f'~^{recent_user}$ recent-1y;', f'~^{full_user}$ full;', 'auth_basic_user_file /etc/nginx/oursteps.htpasswd;', 'server_tokens off;', 'connect-src \'self\'', 'limit_except GET HEAD', 'gzip on;', 'open_file_cache off;'):
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
    actual = validate(root/'public-site'/target, expected, report.get('recent'))
    require(actual['hashes'] == report['hashes'], 'release manifest checksum mismatch')
    digest = hashlib.sha256(json.dumps(actual['hashes'],sort_keys=True).encode()).hexdigest()
    require(Path(target).name == digest, 'release identity checksum mismatch')
    return actual


def publish(root=ROOT, expected=None, rollback=False, dates=None, today=None, preview_hashes=None):
    root = Path(root)
    with locks(root):
        config_check(root)
        site = root/'public-site'; directory(site); directory(site/'releases')
        old = pointer(site, 'current')
        old_report = validate_saved(root, old) if old else None
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
        if (old_report and old_report.get('recent', {}).get('allowed') == recent['allowed']
                and source_hashes == {name:digest for name,digest in old_report['hashes'].items() if '/' not in name}):
            return {'status':'unchanged', 'release':old, 'articles':old_report['articles'],
                    'recent_articles':len(recent['allowed']), 'cutoff':recent['cutoff'],
                    'excluded_missing_dates':recent['excluded_missing_dates']}
        directory(root/'data/public-releases')
        stage = Path(tempfile.mkdtemp(prefix='.public-release-', dir=str(root)))
        try:
            stage.chmod(0o755)
            for item in selected:
                if old_report and old_report['hashes'].get(item.name) == source_hashes[item.name]:
                    # Link only immutable PUBLIC releases, never private preview/raw files.
                    os.link(str(site/old/item.name),str(stage/item.name))
                else:
                    with item.open('rb') as src, (stage/item.name).open('wb') as dst:
                        shutil.copyfileobj(src,dst); dst.flush(); os.fsync(dst.fileno())
                    (stage/item.name).chmod(0o644)
            write_atomic(stage/'robots.txt', ROBOTS); (stage/'robots.txt').chmod(0o644)
            validate(stage,expected)
            stage_recent(stage, set(recent['allowed']))
            report = validate(stage,expected,recent)
            release_id = hashlib.sha256(json.dumps(report['hashes'],sort_keys=True).encode()).hexdigest()
            target = 'releases/'+release_id
            dest = site/target
            if dest.exists():
                require(validate(dest,expected,recent)['hashes'] == report['hashes'], 'existing release changed')
            else:
                sync_dir(stage); os.rename(str(stage),str(dest)); sync_dir(site/'releases')
            write_atomic(metadata(root,target),json.dumps(report,sort_keys=True).encode())
            if old == target: return {'status':'unchanged','release':target, 'articles':report['articles'], 'recent_articles':report['recent_articles'], 'cutoff':recent['cutoff'], 'excluded_missing_dates':recent['excluded_missing_dates']}
            # Persist rollback pointer before the single atomic live switch.
            switch(site,'previous',old or target)
            switch(site,'current',target)
            return {'status':'published','release':target, 'articles':report['articles'], 'recent_articles':report['recent_articles'], 'cutoff':recent['cutoff'], 'excluded_missing_dates':recent['excluded_missing_dates']}
        finally:
            if stage.exists(): shutil.rmtree(stage)
