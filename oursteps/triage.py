"""Local historical triage. Evidence and archive records are never modified."""
def evidence(db, tid=None):
    reasons = {}
    params = () if tid is None else (tid,)
    scope = '' if tid is None else ' AND i.tid=?'
    def add(tid, reason):
        reasons.setdefault(tid, set()).add(reason)
    for tid, declared in db.execute('SELECT p.tid,p.declared_pages FROM pages p JOIN inventory i ON i.tid=p.tid WHERE p.declared_pages>10'+scope, params):
        add(tid, 'declared_pages_gt_10')
    for tid, reason in db.execute('SELECT DISTINCT j.tid,g.reason FROM gaps g JOIN jobs j ON j.url=g.url JOIN inventory i ON i.tid=j.tid WHERE 1=1'+scope, params):
        # Transport and access failures remain governed by existing retry/auth policy.
        if reason == 'deleted_author_identity_unknown':
            add(tid, reason)
        elif reason in ('missing_or_hidden_author_body', 'unresolved_relative_time', 'empty_author_body') or any(x in reason for x in ('repeated_page_pid_set', 'page_identity_mismatch', 'canonical_page_mismatch', 'missing_author_identity')):
            add(tid, 'page_integrity:' + reason)
    for tid, error in db.execute("SELECT j.tid,j.error FROM jobs j JOIN inventory i ON i.tid=j.tid WHERE j.error IS NOT NULL"+scope, params):
        if any(x in error for x in ('repeated_page_pid_set', 'page_identity_mismatch', 'canonical_page_mismatch', 'missing_author_identity')):
            add(tid, 'page_integrity:' + error)
    for tid, error in db.execute("SELECT p.tid,p.error FROM pages p JOIN inventory i ON i.tid=p.tid WHERE p.error IS NOT NULL"+scope, params):
        if any(x in error for x in ('repeated_page_pid_set', 'page_identity_mismatch', 'canonical_page_mismatch', 'missing_author_identity')):
            add(tid, 'page_integrity:' + error)
    return {tid: sorted(values) for tid, values in sorted(reasons.items())}


def quarantined(db, tid=None):
    result = set(evidence(db, tid))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_triage'").fetchone():
        result.update(row[0] for row in db.execute("SELECT tid FROM historical_triage WHERE state='quarantine'" + ('' if tid is None else ' AND tid=?'), () if tid is None else (tid,)))
    return result


def migrate(db, apply=False, clear=False):
    if apply or clear:
        raise ValueError('triage writes disabled until locking and backup are supported')
    found = evidence(db)
    return {'dry_run': True, 'operation': 'classify', 'items': [{'tid': tid, 'reasons': reasons} for tid,reasons in found.items()]}
