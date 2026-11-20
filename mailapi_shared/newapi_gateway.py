"""Server-side adapter for a separately running NewAPI engine.

The website remains the identity authority. Each linked engine account has only
the normal-user role and an independent random credential, encrypted by the
test keyring. Website passwords, TOTP seeds and sessions never leave MailAPI.
"""
import asyncio
import hashlib
import logging
import secrets
import time
from urllib.parse import urlsplit

import httpx


class NewAPIError(ValueError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class NewAPIGateway:
    def __init__(self, store, *, client=None):
        self.store = store
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self.sessions = {}
        self.locks = {}

    async def close(self):
        await self.client.aclose()
        self.sessions.clear()

    def config(self):
        config = self.store.api2api_config()
        if not config or config.get('engine') != 'newapi' or not config['enabled']:
            raise NewAPIError('NewAPI 网关尚未启用', 503)
        return config

    def management(self, config):
        return {'origin':config['base_url'][:-3], 'username':config['management_username'],
                'password':config['management_password']}

    async def session(self, credential, *, renew=False):
        cache_key = (credential['origin'],credential['username'],hashlib.sha256(credential['password'].encode()).digest())
        lock = self.locks.setdefault(('session',cache_key),asyncio.Lock())
        async with lock:
            previous = self.sessions.get(cache_key, {})
            if not renew and previous.get('expires',0) > time.time()+30:
                return previous
            response = None
            try:
                if previous.get('cookie'):
                    parsed = urlsplit(credential['origin'])
                    response = await self.client.post(credential['origin']+'/api/user/auth/refresh',
                        headers={'Cookie':previous['cookie'], 'Origin':f'{parsed.scheme}://{parsed.netloc}'})
                    if response.status_code != 200 or not response.json().get('success'):
                        response = None
                if response is None:
                    response = await self.client.post(credential['origin']+'/api/user/login',
                        headers={'Cookie':''}, json={'username':credential['username'],'password':credential['password']})
                data = response.json().get('data') or {}
                token = data.get('access_token')
                if response.status_code != 200 or not token:
                    raise NewAPIError('NewAPI 管理或关联账户认证失败，请检查独立管理凭据')
                cookie = response.cookies.get('new_api_refresh')
                result = {'token':token, 'expires':float(data.get('access_expires_at') or time.time()+600),
                          'cookie':('new_api_refresh='+cookie) if cookie else previous.get('cookie',''),
                          'user_id':int((data.get('user') or {}).get('id') or previous.get('user_id',0))}
                self.sessions[cache_key] = result
                return result
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                if isinstance(error, NewAPIError):
                    raise
                raise NewAPIError('无法连接或认证 NewAPI，请检查网关连接') from None

    async def request(self, credential, method, path, *, json=None, params=None):
        for attempt in range(2):
            session = await self.session(credential,renew=attempt>0)
            try:
                response = await self.client.request(method,credential['origin']+path,
                    headers={'Authorization':'Bearer '+session['token'],'Cookie':''},json=json,params=params)
                if response.status_code == 401 and attempt == 0:
                    continue
                data = response.json()
                if response.status_code >= 400 or data.get('success') is not True:
                    raise NewAPIError('NewAPI 操作失败，请检查网关配置、权限或额度')
                return data.get('data')
            except (httpx.HTTPError, ValueError, TypeError) as error:
                if isinstance(error, NewAPIError):
                    raise
                raise NewAPIError('NewAPI 暂时不可用，请稍后重试') from None

    async def verify(self):
        config = self.config()
        await self.request(self.management(config),'GET','/api/user/self')
        return {'ok':True,'engine':'newapi'}

    async def identity(self, user_id):
        config = self.config()
        origin = config['base_url'][:-3]
        lock = self.locks.setdefault(('identity',origin,user_id),asyncio.Lock())
        async with lock:
            user = self.store.gateway_user(origin,user_id)
            if not user['initialized']:
                management = self.management(config)
                try:
                    await self.request(management,'POST','/api/user/',json={
                        'username':user['username'],'password':user['password'],
                        'display_name':user['username'],'role':1})
                except NewAPIError:
                    # An earlier attempt may have committed before losing its
                    # response. Only our stored random credential can adopt it.
                    await self.session(user)
                session = await self.session(user)
                await self.request(management,'POST','/api/user/manage',json={
                    'id':session['user_id'],'action':'add_quota','mode':'override','value':config['initial_quota']})
                self.store.finish_gateway_user(origin,user_id,session['user_id'])
            return user

    async def create_key(self, user_id, name):
        name = self.store._text(name,'API Key 名称',80,required=True)
        user = await self.identity(user_id)
        config = self.config()
        item_id = secrets.token_hex(12)
        remote_name = 'mw_'+item_id
        await self.request(user,'POST','/api/token/',json={
            'name':remote_name,'expired_time':-1,'unlimited_quota':True,'remain_quota':0,
            'model_limits_enabled':bool(config['model_allowlist']),
            'model_limits':','.join(config['model_allowlist']),'group':'default'})
        found = await self.request(user,'GET','/api/token/search',params={'keyword':remote_name,'size':100})
        matches = [row for row in found['items'] if row['name']==remote_name]
        if len(matches) != 1:
            raise NewAPIError('无法确认 NewAPI 新密钥，请检查网关中的未关联令牌')
        remote_id = int(matches[0]['id'])
        try:
            result = await self.request(user,'POST',f'/api/token/{remote_id}/key')
            raw = result['key']
            if not raw.startswith('sk-'):
                raw = 'sk-'+raw
            if not raw or len(raw)>260:
                raise NewAPIError('NewAPI 返回了无效密钥')
            return self.store.register_gateway_key(user_id,name,item_id,remote_id,user['origin'],raw)
        except Exception:
            try:
                await self.request(user,'DELETE',f'/api/token/{remote_id}')
            except NewAPIError:
                logging.getLogger(__name__).warning('Unlinked NewAPI token requires cleanup: id=%s',remote_id)
            raise

    async def list_keys(self, user_id):
        config = self.config()
        origin = config['base_url'][:-3]
        rows = self.store.list_api_keys(user_id)
        own = [row for row in rows if row['provider']=='newapi' and row['engine_origin']==origin]
        remote = {}
        if own:
            user = await self.identity(user_id)
            for page in range(1,11):
                data = await self.request(user,'GET','/api/token/',params={'p':page,'size':100})
                remote.update({int(row['id']):row for row in data['items']})
                if len(data['items'])<100:
                    break
        result = []
        for row in rows:
            current = remote.get(row['remote_id'])
            item = {key:value for key,value in row.items() if key not in {'remote_id','engine_origin','user_id'}}
            item['enabled'] = bool(row['enabled'] and current and current.get('status')==1)
            item['available_here'] = row['provider']=='newapi' and row['engine_origin']==origin
            if current:
                item.update(used_quota=current.get('used_quota',0),remaining_quota=current.get('remain_quota',0),
                            unlimited=bool(current.get('unlimited_quota')),expired_at=current.get('expired_time',-1))
            result.append(item)
        return result

    async def revoke_key(self, key_id, user_id=None):
        row = self.store.gateway_key(key_id,user_id)
        if not row:
            raise NewAPIError('未找到 API Key',404)
        self.store.revoke_api_key(key_id,row['user_id'])
        if row['provider'] != 'newapi':
            return
        config = self.config()
        if row['engine_origin'] != config['base_url'][:-3]:
            raise NewAPIError('本站已停用密钥；请连接原网关完成上游撤销',409)
        try:
            user = await self.identity(row['user_id'])
            await self.request(user,'DELETE',f'/api/token/{row["remote_id"]}')
        except NewAPIError:
            raise NewAPIError('本站已停用密钥，NewAPI 撤销暂未完成，请重试') from None

    async def info(self, user_id):
        config = self.config()
        user = self.store.gateway_user(config['base_url'][:-3],user_id,create=False)
        if not user or not user['initialized']:
            return {'engine':'newapi','linked':False,'models':config['model_allowlist']}
        end = int(time.time())
        account, models, series = await asyncio.gather(self.request(user,'GET','/api/user/self'),
            self.request(user,'GET','/api/user/models'),
            self.request(user,'GET','/api/data/self',params={'start_timestamp':end-29*86400,'end_timestamp':end}))
        return {'engine':'newapi','linked':True,'quota':int(account['quota']),
                'used_quota':int(account['used_quota']),'requests':int(account.get('request_count',0)),
                'series':[{'created_at':row['created_at'],'model':row['model_name'],'requests':row['count'],
                           'tokens':row['token_used'],'quota':row['quota']} for row in (series or [])],
                'models':[name for name in models if not config['model_allowlist'] or name in config['model_allowlist']]}

    async def channels(self):
        config = self.config()
        data = await self.request(self.management(config),'GET','/api/channel/',params={'p':1,'size':100})
        return [{key:row.get(key) for key in ('id','name','base_url','models','status','response_time')}
                for row in data['items']]

    async def add_channel(self, payload):
        config = self.config()
        base = self.store._base_url(payload.get('base_url'))
        if base.endswith('/v1'):
            base = base[:-3]
        parsed = urlsplit(base)
        if parsed.scheme != 'https' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
            raise NewAPIError('上游地址须使用 HTTPS，本机回环测试除外',400)
        if base == config['base_url'][:-3]:
            raise NewAPIError('上游不能指向网关自身',400)
        name = self.store._text(payload.get('name'),'渠道名称',80,required=True)
        key = payload.get('api_key')
        if not isinstance(key,str) or not 1 <= len(key) <= 4096:
            raise NewAPIError('请填写上游 API Key',400)
        models = payload.get('models')
        if not isinstance(models,list) or not 1 <= len(models) <= 100:
            raise NewAPIError('请填写 1–100 个模型名称',400)
        models = [self.store._text(value,'模型名',120,required=True) for value in models]
        await self.request(self.management(config),'POST','/api/channel/',json={
            'mode':'single','channel':{'type':1,'name':name,'key':key,'base_url':base,
            'models':','.join(models),'group':'default','setting':'{"pass_through_body_enabled":true}'}})
        return {'ok':True}

    async def set_quota(self, user_id, value):
        if type(value) is not int or not 0 <= value <= 1_000_000_000:
            raise NewAPIError('额度须为 0–1000000000 的整数',400)
        user = await self.identity(user_id)
        session = await self.session(user)
        await self.request(self.management(self.config()),'POST','/api/user/manage',json={
            'id':session['user_id'],'action':'add_quota','mode':'override','value':value})
        return {'ok':True}
