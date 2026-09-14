"""Pure response classification: layout/clock failures are not lost authentication."""
from .config import UID, USERNAME
from .parser import soup_of,query,parse_discovery,ParseError,Busy,Blocked

class VerificationIssue(Exception):
    def __init__(self,category,reason,retry=False,retry_after=0):
        self.category,self.reason,self.retry,self.retry_after=category,reason,retry,retry_after
        super().__init__(reason)

def identity(soup):
    def target(a):
        return query(a.get('href','')).get('uid')==str(UID) and a.get_text(strip=True).lower()==USERNAME.lower()
    headers=soup.select('#um a[href], #toptb a[href]')
    if any(target(a) for a in headers):return True
    # A profile/author link alone is not authentication. Require logout navigation
    # and no conflicting account header for the alternate-layout fallback.
    if any(query(a.get('href','')).get('uid') not in (None,str(UID)) for a in headers):return False
    logout=any(query(a.get('href','')).get('action')=='logout' for a in soup.select('a[href]'))
    return logout and any(target(a) for a in soup.select('a[href]'))

def inspect_response(content,status=200,directory=None):
    if status==429:raise VerificationIssue('retry_later','http_429',True)
    if status in (502,503,504) or status>=500:raise VerificationIssue('retry_later','http_'+str(status),True)
    if status==403:raise VerificationIssue('permission_denied','http_403')
    if status==401:raise VerificationIssue('auth_required','http_401')
    soup=soup_of(content)
    notices=' '.join(n.get_text(' ',strip=True) for n in soup.select('#messagetext,#main_message,.alert_error,.alert_info'))
    text=notices or (soup.get_text(' ',strip=True)[:2500] if not identity(soup) else '')
    low=text.lower()
    if any(x in low for x in ('captcha','security verification','验证码','安全验证','两步验证','just a moment')):
        raise VerificationIssue('manual_required','security_challenge')
    if any(x in text for x in ('喝茶时间','系统暂时繁忙','服务器繁忙')):
        raise VerificationIssue('retry_later','site_busy',True)
    if not identity(soup) and any(x in low for x in ('请先登录','请登录','尚未登录','please log in')):
        raise VerificationIssue('auth_required','explicit_login_required')
    if any(x in low for x in ('无权访问','没有权限','阅读权限','access denied')):
        raise VerificationIssue('permission_denied','explicit_permission_denial')
    if not identity(soup):
        if any(x in low for x in ('请先登录','请登录','尚未登录','please log in')) or (soup.select_one('input[name="username"]') and soup.select_one('input[name="password"]')):
            raise VerificationIssue('auth_required','explicit_login_required')
        if not content.strip() or b'</html>' not in content.lower():
            raise VerificationIssue('retry_later','incomplete_html',True)
        raise VerificationIssue('unexpected_layout','identity_not_confirmed_no_logout_evidence')
    if status!=200:raise VerificationIssue('unexpected_layout','http_'+str(status))
    if directory and b'</html>' not in content.lower():
        raise VerificationIssue('retry_later','incomplete_html',True)
    if directory:
        try:return parse_discovery(content,directory)
        except Busy:raise VerificationIssue('retry_later','site_busy',True) from None
        except ParseError as e:
            if b'</html>' not in content.lower():raise VerificationIssue('retry_later','incomplete_html',True) from None
            raise VerificationIssue('unexpected_layout',str(e)) from None
    return True
