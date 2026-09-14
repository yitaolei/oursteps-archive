"""Resolve displayed forum wall times to Sydney dates using verified clock evidence."""
import datetime as dt
import re
import pytz
from .parser import ParseError, soup_of
SYDNEY=pytz.timezone('Australia/Sydney')
DATE_RE=r'(20\d\d-\d{1,2}-\d{1,2} \d{1,2}:\d{2})'

def sydney_today(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(SYDNEY).date().isoformat()

def infer_display_offset(content,before,after):
    # Legacy forensic reproducer only: last-visit is NOT a live clock.
    # Production authentication must use cached_display_offset, not this inference.
    # Accept only one quarter-hour offset consistent with a recent visit; stale or
    # ambiguous evidence stops the run, never silently assumes the account timezone.
    text=soup_of(content).get_text(' ',strip=True)
    match=re.search(r'最后访问\s*'+DATE_RE,text)
    if not match: raise ParseError('forum_clock_evidence_missing')
    wall=dt.datetime.strptime(match.group(1),'%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc).timestamp()
    offsets=[n/4 for n in range(-48,57) if before-300 <= wall-n*900 <= after+60]
    if len(offsets)!=1: raise ParseError('forum_clock_stale_or_ambiguous')
    return offsets[0]

def publication_time(value,content,display_offset=None):
    match=re.search(DATE_RE,value)
    if not match: raise ParseError('publication_date_missing')
    footer=soup_of(content).select_one('#ft')
    offset=re.search(r'GMT\s*([+-]\d+(?:\.\d+)?)',footer.get_text(' ',strip=True) if footer else '')
    hours=float(offset.group(1)) if offset else display_offset
    if hours is None: raise ParseError('publication_timezone_evidence_missing')
    wall=dt.datetime.strptime(match.group(1),'%Y-%m-%d %H:%M')
    instant=wall.replace(tzinfo=dt.timezone(dt.timedelta(hours=hours)))
    return instant.astimezone(SYDNEY).strftime('%Y-%m-%d %H:%M')


def cached_display_offset(saved,now=None):
    """Keep previous corroborated evidence; authentication is not a timezone probe.

    Do not infer an offset from a historical last-visit clock on repeated runs.
    Reconfirmation is required when Sydney's seasonal offset changes, or the
    account timezone is changed (invalidate this evidence explicitly).
    """
    from .auth_verify import VerificationIssue
    now=now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    stamp=saved.get('display_offset_verified_at',0)
    offset=saved.get('display_offset_hours')
    if not stamp or not isinstance(offset,(int,float)) or not -12<=offset<=14:
        raise VerificationIssue('timezone_required','missing_verified_display_offset')
    then=dt.datetime.fromtimestamp(stamp,SYDNEY);current=dt.datetime.fromtimestamp(now,SYDNEY)
    if then.utcoffset()!=current.utcoffset():
        raise VerificationIssue('timezone_required','seasonal_offset_changed_reconfirm_display_timezone')
    return offset
