"""Public model metadata from the user's native gateway, without raw responses."""
import math
import re


def number(value, *, positive=False):
    return value if type(value) in {int,float} and math.isfinite(value) and (value>0 if positive else value>=0) else None


def text(value, limit=120):
    return value[:limit] if isinstance(value,str) else ''


def strings(value):
    return list(dict.fromkeys(item for item in value if isinstance(item,str) and 0<len(item)<=120)) if isinstance(value,list) else []


def metrics(value):
    if not isinstance(value,dict):
        return None
    result = {name:number(value.get(name)) for name in ('avg_latency_ms','avg_ttft_ms','avg_tps','success_rate')}
    if result['success_rate'] is not None and result['success_rate']>100:
        result['success_rate'] = None
    return result if any(item is not None for item in result.values()) else None


def series(value):
    if not isinstance(value,list):
        return []
    result = []
    for row in value[:256]:
        if isinstance(row,dict) and type(row.get('ts')) is int and row['ts']>0:
            result.append({'ts':row['ts'],**(metrics(row) or {})})
    return sorted(result,key=lambda row:row['ts'])


def billing_metadata(raw):
    """Public pricing contract only; never forward arbitrary engine fields."""
    result = {key:raw[key] for key in ('billing_mode','billing_expr') if isinstance(raw.get(key),str)}
    schema = raw.get('billing_usage_schema')
    if isinstance(schema,dict):
        result['billing_usage_schema'] = {name:{key:value for key,value in definition.items()
                                               if key in {'type','unit','unitLabel','enum','enumLabels','description'}}
                                          for name,definition in schema.items() if isinstance(definition,dict)}
        examples = raw.get('billing_usage_examples')
        if isinstance(examples,list):
            result['billing_usage_examples'] = [
                {'label':text(row.get('label'),500),
                 'facts':{key:value for key,value in row['facts'].items() if key in schema and
                          (isinstance(value,str) or type(value) is bool or number(value) is not None)}}
                for row in examples if isinstance(row,dict) and isinstance(row.get('facts'),dict)]
    variants = raw.get('billing_plugin_variants')
    if isinstance(variants,list):
        result['billing_plugin_variants'] = [
            {**billing_metadata({key:value for key,value in row.items() if key!='billing_plugin_variants'}),
             'plugin_key':text(row.get('plugin_key')),'plugin_name':text(row.get('plugin_name'))}
            for row in variants if isinstance(row,dict)]
    if raw.get('ordinary_pricing') is True:
        result['ordinary_pricing'] = True
    return result


def catalog(names, groups, pricing, status, performance, allowlist):
    groups = list(groups) if isinstance(groups,dict) else []
    pricing_ok = isinstance(pricing,dict) and isinstance(pricing.get('data'),list)
    pricing = pricing if pricing_ok else {}
    status = status if isinstance(status,dict) else {}
    perf_ok = isinstance(performance,dict) and isinstance(performance.get('models'),list)
    perf = {row.get('model_name'):row for row in performance['models'] if isinstance(row,dict)} if perf_ok else {}
    raw_models = {row.get('model_name'):row for row in pricing.get('data',[]) if isinstance(row,dict)}
    vendors = {row.get('id'):text(row.get('name')) for row in (pricing.get('vendors') or []) if isinstance(row,dict)}
    ratios = pricing.get('group_ratio') if isinstance(pricing.get('group_ratio'),dict) else {}
    endpoints = pricing.get('supported_endpoint') if isinstance(pricing.get('supported_endpoint'),dict) else {}
    items = []
    for name in sorted(set(strings(names))):
        if allowlist and name not in allowlist:
            continue
        raw = raw_models.get(name,{})
        enabled = strings(raw.get('enable_groups'))
        model_groups = [group for group in groups if group in enabled or 'all' in enabled]
        supported = []
        for key in strings(raw.get('supported_endpoint_types')):
            endpoint = endpoints.get(key)
            if not isinstance(endpoint,dict):
                continue
            path, method = endpoint.get('path'), endpoint.get('method')
            # The website exposes /api2api/v1, not the engine's other roots.
            if isinstance(path,str) and path.startswith('/v1/') and len(path)<=200 and re.fullmatch(r'[A-Za-z0-9_./:{}-]+',path) and '..' not in path.split('/') and method in {'GET','POST','PUT','PATCH','DELETE'}:
                supported.append({'id':key,'path':path,'method':method})
        kind = 'token' if raw.get('quota_type')==0 else 'request' if raw.get('quota_type')==1 else 'unknown'
        if raw.get('billing_expr') or raw.get('billing_plugin_variants') or raw.get('billing_mode') not in {None,'','ratio'}:
            kind = 'dynamic'
        prices = {}
        ratio_pricing = raw.get('ordinary_pricing') is True or any(
            isinstance(row,dict) and row.get('billing_mode')=='ratio' for row in raw.get('billing_plugin_variants') or [])
        if raw.get('quota_type')==0 and (kind=='token' or ratio_pricing):
            ratio = number(raw.get('model_ratio'))
            if ratio is not None and number(ratio*2) is not None:
                prices['input'] = ratio*2  # Native model ratios are USD / 500K input tokens.
                for key,field in (('output','completion_ratio'),('cache_read','cache_ratio'),('cache_write','create_cache_ratio'),('image','image_ratio'),('audio_input','audio_ratio')):
                    value = number(raw.get(field))
                    if value is not None:
                        product = number(ratio*2*value)
                        if product is not None:
                            prices[key] = product
        elif raw.get('quota_type')==1 and (kind=='request' or ratio_pricing):
            price = number(raw.get('model_price'))
            if price is not None:
                prices['request'] = price
        tags = raw.get('tags')
        tags = [tag.strip() for tag in tags.split(',')] if isinstance(tags,str) else tags
        summary = perf.get(name,{})
        recent = series(summary.get('recent_success_series'))
        if perf_ok and type(performance.get('window_start')) is int and type(performance.get('window_end')) is int:
            recent = [point for point in recent if performance['window_start']<=point['ts']<=performance['window_end']]
        else:
            recent = []
        items.append({'id':name,'vendor':vendors.get(raw.get('vendor_id')) or text(raw.get('owner_by')),
                      'description':text(raw.get('description'),2000),'tags':strings(tags),
                      'groups':[{'id':group,'ratio':number(ratios.get(group))} for group in model_groups],
                      'endpoints':supported,'billing':kind,'base_billing':'request' if raw.get('quota_type')==1 else 'token',
                      'prices':prices, 'dynamic_pricing':billing_metadata(raw),
                      'auto_groups':[group for group in strings(pricing.get('auto_groups')) if group in model_groups],
                      'performance':metrics(summary),'recent':recent})
    currency = {'symbol':'$','standard_rate':1,'recharge_rate':None}
    mode = status.get('quota_display_type')
    rate = number(status.get('usd_exchange_rate' if mode=='CNY' else 'custom_currency_exchange_rate'),positive=True)
    if mode in {'CNY','CUSTOM'} and rate is not None:
        currency.update(symbol='¥' if mode=='CNY' else text(status.get('custom_currency_symbol'),8) or '¤',standard_rate=rate)
    price, usd = number(status.get('price'),positive=True), number(status.get('usd_exchange_rate'),positive=True)
    if price is not None and usd is not None:
        currency['recharge_rate'] = number(currency['standard_rate']*price/usd,positive=True)
    return {'items':items,'groups':[{'id':group,'ratio':number(ratios.get(group))} for group in groups],
            'currency':currency,'pricing_available':pricing_ok,'performance_available':perf_ok}


def performance_details(data, name, groups):
    if not isinstance(data,dict) or data.get('model_name')!=name:
        return None
    return {'id':name,'summary':metrics(data.get('summary')),'series':series(data.get('series')),
            'groups':[{'id':row['group'],'summary':metrics(row),'series':series(row.get('series'))}
                      for row in (data.get('groups') or []) if isinstance(row,dict) and row.get('group') in groups]}
