"""Pure parsers: no network or database access; all input can be replayed."""
from .config import UID
import hashlib
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

VERSION = '1'
BASE = 'https://www.oursteps.com.au'


class ParseError(ValueError):
    pass


class PaginationLimit(ParseError):
    pass


class Blocked(ParseError):
    pass


class NotFound(ParseError):
    pass


class Busy(ParseError):
    pass


def soup_of(html):
    # Pass bytes to BeautifulSoup so meta charset/legacy encodings are honored.
    return BeautifulSoup(html, 'html.parser')


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def thread_url(tid, page=1):
    return BASE + '/bbs/forum.php?' + urlencode(
        {'mod': 'viewthread', 'tid': int(tid), 'page': int(page)})


def directory_url(page=1):
    return BASE + '/bbs/home.php?' + urlencode(dict(
        mod='space', uid=UID, do='thread', view='me', order='dateline',
        **{'from': 'space', 'page': int(page)}))


def guard(soup):
    # Only site-level notices, not arbitrary words in an archived article.
    notices = soup.select('#messagetext, #main_message, .alert_error, .alert_info')
    text = ' '.join(n.get_text(' ', strip=True) for n in notices)
    if not soup.select('[id^=postmessage_], table th a[href*=viewthread]'):
        text += ' ' + soup.get_text(' ', strip=True)[:2500]
    if '分页数不在允许的范围内' in text:
        raise PaginationLimit('pagination_out_of_allowed_range')
    if any(x in text for x in ('指定的主题不存在', '主题不存在或已被删除', '您指定的主题不存在')):
        raise NotFound('thread_not_found')
    if any(x in text for x in ('喝茶时间', '系统暂时繁忙')):
        raise Busy('site_busy')
    if any(x in text for x in ('请先登录', '请登录', '尚未登录', 'Please log in', '分页数不在允许', '无权访问',
                              '没有权限', '阅读权限', '验证码', 'CAPTCHA',
                              'Access denied', 'Just a moment')):
        raise Blocked('login_permission_or_challenge')


def safe_url(value, base):
    if not value:
        return None
    value = urljoin(base, value.strip())
    u = urlsplit(value)
    if u.scheme not in ('https', 'http') or u.username or u.password:
        return None
    return value


def clean_fragment(node, base):
    """Private normalized body, not a public-publishing approval."""
    fragment = BeautifulSoup(str(node), 'html.parser')
    for tag in fragment.select('script, style, iframe, object, embed, form, input, button, textarea'):
        tag.decompose()
    allowed = {'p', 'div', 'span', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'del',
               'blockquote', 'pre', 'code', 'ul', 'ol', 'li', 'a', 'img', 'table',
               'tbody', 'tr', 'td', 'th', 'hr', 'h2', 'h3', 'h4', 'sup', 'sub'}
    for tag in list(fragment.find_all(True)):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        attrs = {}
        for key in ('href', 'src', 'file', 'data-src'):
            if key in tag.attrs:
                value = safe_url(tag.attrs[key], base)
                if value:
                    attrs[key] = value
        for key in ('alt', 'title', 'colspan', 'rowspan'):
            if key in tag.attrs:
                attrs[key] = str(tag.attrs[key])
        tag.attrs = attrs
    return str(fragment), fragment.get_text('\n', strip=True)


def page_links(soup, tid, page):
    """Only observed, unfiltered, forward links belonging to this thread."""
    pages = set()
    for a in soup.select('.pg a[href]'):
        q = query(a['href'])
        if q.get('tid') != str(tid) or q.get('mod') != 'viewthread':
            continue
        if any(k in q for k in ('authorid', 'ordertype', 'action')):
            continue
        n = q.get('page', '')
        if n.isdigit() and int(n) > page:
            pages.add(int(n))
    declared = None
    for pg in soup.select('.pg'):
        m = re.search(r'/\s*(\d+)\s*页', pg.get_text(' ', strip=True))
        if m:
            declared = max(declared or 0, int(m.group(1)))
    return sorted(pages), declared


def parse_thread(html, url):
    s = soup_of(html)
    guard(s)
    q = query(url)
    try:
        tid, page = int(q['tid']), int(q.get('page', 1))
    except (KeyError, ValueError):
        raise ParseError('invalid_thread_url')
    # Trust explicit current-page markers only when all exposed values agree.
    markers = s.select('.pg > strong, .pg input[name="custompage"]')
    values = [node.get('value', '').strip() if node.name == 'input'
              else node.get_text(strip=True) for node in markers]
    if values and all(re.fullmatch(r'[0-9]+', value) and int(value) > 0
                      for value in values):
        current_pages = {int(value) for value in values}
        if len(current_pages) == 1 and page not in current_pages:
            raise ParseError('page_identity_mismatch')
    title = None
    for a in s.select('h1 a[href]'):
        aq = query(a['href'])
        if aq.get('tid') == str(tid) and a.get_text(strip=True) != '[复制链接]':
            title = a.get_text(' ', strip=True)
            break
    crumbs = [a for a in s.select('#pt a[href]')
              if query(a['href']).get('mod') == 'forumdisplay' and
              query(a['href']).get('fid', '').isdigit()]
    if not title or not crumbs:
        raise ParseError('missing_title_or_breadcrumb')
    forum = crumbs[-1]
    counts = None
    for cell in s.select('td.pls'):
        m = re.fullmatch(r'查看:\s*([\d,]+)\s*[|｜]\s*回复:\s*([\d,]+)',
                         cell.get_text('', strip=True))
        if m:
            counts = [int(v.replace(',', '')) for v in m.groups()]
            break
    own, all_pids, gaps = [], [], []
    owner = None
    for container in s.find_all(id=re.compile(r'^post_\d+$')):
        pid = int(container['id'][5:])
        all_pids.append(pid)
        # .pls scopes identity to the author column, never a body/quote link.
        author = container.select_one('.pls .authi a[href*="uid="]')
        if author is None:
            raise ParseError('missing_author_identity:%s' % pid)
        aq = query(author['href'])
        if not aq.get('uid', '').isdigit():
            raise ParseError('invalid_author_uid:%s' % pid)
        uid = int(aq['uid'])
        floor_node = s.find(id='postnum%s' % pid)
        floor = floor_node.get_text('', strip=True) if floor_node else None
        if page == 1 and owner is None:
            owner = uid
            if not floor or not floor.startswith('1#'):
                raise ParseError('first_post_not_floor_one')
        if uid != UID:
            continue
        body = s.find(id='postmessage_%s' % pid)
        when = s.find(id='authorposton%s' % pid)
        if body is None or when is None:
            gaps.append({'pid': pid, 'reason': 'missing_or_hidden_author_body'})
            continue
        raw_time = when.get_text(' ', strip=True)
        if not re.search(r'\d{4}-\d{1,2}-\d{1,2}', raw_time):
            gaps.append({'pid': pid, 'reason': 'unresolved_relative_time'})
        content_html, content_text = clean_fragment(body, url)
        if not content_text and not body.find('img'):
            gaps.append({'pid': pid, 'reason': 'empty_author_body'})
        edit = body.select_one('.pstatus')
        links = [dict(url=safe_url(a.get('href'), url), label=a.get_text(' ', strip=True))
                 for a in body.select('a[href]')]
        links = [a for a in links if a['url']]
        assets = []
        for image in body.select('img'):
            attrs = {k: image.get(k) for k in ('src', 'file', 'data-src', 'aid', 'alt')
                     if image.get(k) is not None}
            candidate = attrs.get('file') or attrs.get('data-src') or attrs.get('src')
            assets.append(dict(kind='image', url=safe_url(candidate, url), attributes=attrs))
        # Attachments may be below the body but within the post content cell.
        for a in container.select('.plc a[href*="attachment"]'):
            assets.append(dict(kind='attachment', url=safe_url(a['href'], url),
                               attributes={'label': a.get_text(' ', strip=True)}))
        own.append(dict(pid=pid, tid=tid, author_uid=uid,
                        author_name=author.get_text(' ', strip=True), floor=floor,
                        page=page, posted_at_raw=raw_time,
                        edited_at_raw=edit.get_text(' ', strip=True) if edit else None,
                        html=content_html, text=content_text,
                        hash=hashlib.sha256(content_html.encode()).hexdigest(),
                        links=links, assets=assets))
    if not all_pids:
        raise ParseError('no_post_containers')
    if len(set(all_pids)) != len(all_pids):
        raise ParseError('duplicate_pid_on_page')
    if page == 1 and owner != UID:
        raise ParseError('thread_not_owned_by_configured_owner')
    next_pages, declared = page_links(s, tid, page)
    return dict(tid=tid, page=page, title=title, owner_uid=owner,
                fid=int(query(forum['href'])['fid']), forum=forum.get_text(' ', strip=True),
                views=counts[0] if counts else None, replies=counts[1] if counts else None,
                posts=own, all_pids=all_pids, next_pages=next_pages,
                declared_pages=declared, gaps=gaps, parser_version=VERSION)


def parse_discovery(html, url):
    s = soup_of(html)
    guard(s)
    q = query(url)
    if q.get('uid') != str(UID) or q.get('do') != 'thread' or q.get('type') == 'reply':
        raise ParseError('not_owner_thread_directory')
    records = {}
    for a in s.select('table th a[href]'):
        aq = query(a['href'])
        if aq.get('mod') != 'viewthread' or not aq.get('tid', '').isdigit():
            continue
        tid = int(aq['tid'])
        row = a.find_parent('tr')
        fid = forum = None
        for f in row.select('a[href]'):
            fq = query(f['href'])
            if fq.get('mod') == 'forumdisplay' and fq.get('fid', '').isdigit():
                fid = int(fq['fid'])
                forum = f.get_text(' ', strip=True) or None
                break
        records.setdefault(tid, dict(tid=tid, title=a.get_text(' ', strip=True), fid=fid, forum=forum))
    if not records:
        raise ParseError('empty_directory_unconfirmed_end')
    next_url = None
    for a in s.select('a[href]'):
        if a.get_text(strip=True) != '下一页':
            continue
        nq = query(a['href'])
        if (nq.get('uid') == str(UID) and nq.get('do') == 'thread' and
                nq.get('type') != 'reply' and nq.get('page', '').isdigit() and
                int(nq['page']) == int(q.get('page', 1)) + 1):
            next_url = directory_url(int(nq['page']))
    return dict(threads=list(records.values()), next_url=next_url)
