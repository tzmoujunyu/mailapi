"""Derive actionable notices from existing, non-secret resource snapshots."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
import time


def health_observations(accounts, plans=(), now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
    result = []
    for account in accounts:
        key = 'gpt:'+account['key']
        label = account.get('email') or account.get('login') or key
        base = {'resource_key': key, 'user_id': '', 'label': label, 'listed': bool(account.get('listed'))}
        automatic = next((item.get('subscription', {}).get('expires_at') for item in account.get('codex', [])
                          if item.get('subscription', {}).get('available') and
                          type(item.get('subscription', {}).get('expires_at')) in (int, float) and
                          math.isfinite(item['subscription']['expires_at'])), None)
        manual = account.get('subscription_expires_on') or account.get('subscription_ends_on')
        expired = automatic <= now if automatic is not None else bool(manual and manual <= today)
        result.append(base | {'key':key+':expired', 'kind':'expired', 'active':expired})
        for codex in account.get('codex', []):
            identity = key+':'+str(codex['id'])
            name = label+' · '+str(codex.get('label') or codex['id'])
            result.append(base | {'key':identity+':auth', 'label':name, 'kind':'authorization',
                                  'active':bool(codex.get('reauth_required'))})
            # Transport errors are unknown, not recovery from an exhausted quota.
            exhausted = codex.get('status_reason') == 'usage_limit_exhausted'
            failed = codex.get('error') or codex.get('usage_error') or codex.get('status_reason') not in (None, '', 'usage_ok', 'usage_limit_exhausted')
            known = not failed and (exhausted or codex.get('usage_status') == 'available')
            result.append(base | {'key':identity+':quota', 'label':name, 'kind':'quota',
                                  'active':exhausted if known else None})
    for plan in plans:
        quota = plan.get('quota') or {}
        windows = quota.get('windows') or []
        remaining = [w['remaining_percent'] for w in windows if type(w.get('remaining_percent')) in (int,float) and math.isfinite(w['remaining_percent'])]
        active = None
        if remaining and not quota.get('error'):
            if min(remaining) <= 0:
                active = True
            elif len(remaining) == len(windows):
                active = False
        key = 'coding-plan:'+plan['id']
        result.append({'key':key+':quota','resource_key':key,'user_id':'','listed':True,
                       'label':plan['name'],'kind':'quota','active':active})
    return result
