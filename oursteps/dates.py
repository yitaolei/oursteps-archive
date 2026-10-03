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


MAX_DST_CHAIN_GAP=240*24*60*60

def _sydney_transition_count(start,end):
    if end<=start:
        return 0
    cursor=start
    previous=dt.datetime.fromtimestamp(cursor,SYDNEY).utcoffset()
    count=0
    while cursor<end:
        cursor=min(cursor+6*60*60,end)
        current=dt.datetime.fromtimestamp(cursor,SYDNEY).utcoffset()
        if current!=previous:
            count+=1
            previous=current
    return count

def cached_display_offset(saved,now=None):
    """Reuse verified Sydney display-time evidence with a bounded DST chain.

    Automatic rollover requires an explicitly verified Australia/Sydney timezone,
    a recent effective offset state, and exactly one Sydney DST transition since
    that state. Original human/primary verification evidence is preserved.
    """
    from .auth_verify import VerificationIssue
    now=now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    stamp=saved.get('display_offset_verified_at',0)
    offset=saved.get('display_offset_hours')
    if not stamp or not isinstance(offset,(int,float)) or not -12<=offset<=14:
        raise VerificationIssue('timezone_required','missing_verified_display_offset')

    current=dt.datetime.fromtimestamp(now,SYDNEY)
    current_hours=current.utcoffset().total_seconds()/3600
    timezone_name=saved.get('display_timezone_name')

    if timezone_name=='Australia/Sydney':
        effective_at=saved.get('display_offset_effective_at',stamp)
        if not isinstance(effective_at,(int,float)) or effective_at<=0 or effective_at>now:
            raise VerificationIssue('timezone_required','invalid_display_offset_effective_time')
        age=now-effective_at
        if age>MAX_DST_CHAIN_GAP:
            raise VerificationIssue('timezone_required','stale_display_offset_effective_time')
        old=float(offset)
        effective_sydney=dt.datetime.fromtimestamp(effective_at,SYDNEY)
        effective_hours=effective_sydney.utcoffset().total_seconds()/3600

        if old==current_hours:
            if old!=effective_hours:
                raise VerificationIssue('timezone_required','display_offset_state_inconsistent')
            return offset

        if not (
            old==effective_hours and
            {old,current_hours}=={10.0,11.0} and
            abs(current_hours-old)==1.0 and
            _sydney_transition_count(effective_at,now)==1
        ):
            raise VerificationIssue('timezone_required','seasonal_offset_changed_reconfirm_display_timezone')
        saved['display_offset_hours']=current_hours
        saved['display_offset_effective_at']=now
        saved['display_offset_derived_at']=now
        saved['display_offset_derivation']=(
            'Australia/Sydney seasonal DST auto-rollover (%s)' % current.tzname())
        return current_hours

    then=dt.datetime.fromtimestamp(stamp,SYDNEY)
    if then.utcoffset()!=current.utcoffset():
        raise VerificationIssue('timezone_required','seasonal_offset_changed_reconfirm_display_timezone')
    return offset
