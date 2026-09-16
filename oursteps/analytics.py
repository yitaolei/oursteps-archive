"""Privacy-minimal article read analytics from the dedicated nginx article log."""
import json
from pathlib import Path
import re

ARTICLE = re.compile(r'^/([0-9]+)\.html$')


def counts(root, allowed=None):
    root=Path(root)
    allowed=None if allowed is None else {str(x) for x in allowed}
    result={}
    path=root/'data/public-analytics/article-views.log'
    try:
        handle=path.open('r',encoding='utf-8',errors='replace')
    except OSError:
        return result
    with handle:
        for line in handle:
            parts=line.rstrip('\n').split('\t')
            if len(parts)!=3 or parts[2] != '200':
                continue
            match=ARTICLE.fullmatch(parts[1])
            if not match:
                continue
            tid=match.group(1)
            if allowed is not None and tid not in allowed:
                continue
            result[tid]=result.get(tid,0)+1
    return result


def payload(root, allowed=None):
    data=counts(root,allowed)
    return (json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode()
