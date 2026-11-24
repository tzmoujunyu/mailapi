"""Production-mode rehearsal uses synthetic, separately encrypted SQLite stores."""
from base64 import b64encode
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mailapi_shared.credential_crypto import CredentialKeyring
from mailapi_shared.resource_database import create_resource_database
from mailapi_shared.site_accounts import SiteAccountStore
from mailapi_shared.site_database import write_site_database
from mailapi_shared.workspace_config import workspace_enabled


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceProductionTests(unittest.TestCase):
    def test_workspace_flag_keeps_the_existing_defaults(self):
        for mode, flag, expected in [('test','',True),('test','0',True),('production','',False),
                                     ('production','0',False),('production','1',True),('production','invalid',False)]:
            with self.subTest(mode=mode, flag=flag), patch.dict(os.environ, {'MAILAPI_MODE':mode,'MAILAPI_WORKSPACE_ENABLED':flag}):
                self.assertEqual(workspace_enabled(),expected)

    def rehearse(self, enabled):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        project = root/'project';project.mkdir()
        shutil.copytree(ROOT/'mailapi_shared',project/'mailapi_shared',ignore=shutil.ignore_patterns('__pycache__'))
        data=project/'runtime';data.mkdir(mode=0o700)
        key=root/'production-key.json'
        key.write_text(json.dumps({'environment':'production','namespace':'workspace-rehearsal','active':'one',
                                  'keys':{'one':b64encode(AESGCM.generate_key(bit_length=256)).decode()}}));key.chmod(0o600)
        ring=CredentialKeyring(key,data_dirs=(data,),production=True)
        seed=SiteAccountStore(root/'accounts.json',require_admin_mfa=True)
        with patch('mailapi_shared.site_accounts.password_is_pwned',return_value=False):
            seed.provision_admin('admin','fixture-entry-8642','GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ')
            admin=next(user for user in seed._data['users'].values() if user['role']=='admin')
            user=seed.create_user(admin['id'],'alice','fixture-entry-8642')
        snapshot=json.loads(seed.path.read_text());snapshot['users'][user['id']]['must_change_password']=False
        write_site_database(data/'site_accounts.sqlite3',snapshot,keyring=ring)
        create_resource_database(data,keyring=ring,oauth_encrypted=True)
        environment={name:os.environ[name] for name in ('PATH','HOME','LANG','TMPDIR') if name in os.environ}
        environment.update(MAILAPI_MODE='production',MAILAPI_DATA_DIR=str(data),MAILAPI_SITE_ACCOUNTS_BACKEND='sqlite',
            MAILAPI_RESOURCE_BACKEND='sqlite',MAILAPI_CREDENTIAL_KEY_FILE=str(key),MAILAPI_OAUTH_ENCRYPTION='1',
            MAILAPI_EXTERNAL_WATCHERS='0',APP_BASE_URL='https://workspace.example',EXPECT_WORKSPACE=str(int(enabled)),
            TEST_ADMIN_PASSWORD='this-must-not-replace-production')
        if enabled:environment['MAILAPI_WORKSPACE_ENABLED']='1'
        script=textwrap.dedent('''
            import os
            from pathlib import Path
            import pyotp
            from fastapi.testclient import TestClient
            from mailapi_shared import application as main
            enabled=os.environ['EXPECT_WORKSPACE']=='1'
            assert not main.IS_TEST and main.APP_MODE=='production'
            assert main.WORKSPACE_ENABLED is enabled
            assert Path(main.DATA_DIR)==Path(os.environ['MAILAPI_DATA_DIR']).resolve()
            assert main.app.state.site_accounts.require_admin_mfa is True
            assert main.CREDENTIAL_KEYRING.environment=='production'
            assert main.TEST_DATA_EPHEMERAL is False
            assert main.EXTERNAL_WATCHERS is False
            with TestClient(main.app,base_url='https://workspace.example',follow_redirects=False) as client:
                login=client.get('/login')
                assert login.status_code==200
                assert ('/static/test-header.js' in login.text) is enabled
                assert '/static/performance-monitor.js' not in login.text
                bad=client.post('/api/auth/login',json={'username':'admin','password':'fixture-entry-8642'})
                assert bad.status_code==401
                response=client.post('/api/auth/login',json={'username':'admin','password':'fixture-entry-8642',
                    'totp_code':pyotp.TOTP('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ').now()})
                assert response.status_code==200,response.text
                assert len(main.app.state.site_accounts._data['users'])==2
                page=client.get('/test-features')
                if enabled:
                    assert page.status_code==200,page.text
                    assert '<title>MILVLG</title>' in page.text
                    assert '/static/test-router.js' in page.text
                    assert '/static/performance-monitor.js' not in page.text
                    assert 'data-performance-monitor="disabled"' in page.text
                    assert '模拟上游数据仅供功能验证' not in page.text
                    for path in ('/', '/admin', '/admin/access', '/preview'):
                        workspace_page=client.get(path)
                        assert workspace_page.status_code==200,path
                        assert '/static/test-router.js' in workspace_page.text,path
                        assert '/static/performance-monitor.js' not in workspace_page.text,path
                    assert client.get('/api/finance/announcements').json()=={'items':[]}
                    response=client.post('/api/admin/finance/announcements',json={'title':'Staged','body':'Synthetic content'})
                    assert response.status_code==201,response.text
                    assert client.get('/api/test-ui/preferences').status_code==200
                    assert client.get('/api/admin/api2api/config').json()['configured'] is False
                else:
                    assert page.status_code==404
                    assert main.TEST_FEATURE_STORE is None
                    assert not (Path(main.DATA_DIR)/'test_features.sqlite3').exists()
                response=client.post('/login',data={'username':'alice','password':'fixture-entry-8642'})
                assert response.status_code==303
                assert response.headers['location']==('/test-features#view-overview' if enabled else '/')
                if enabled:
                    assert client.get('/api/finance/announcements').json()['items'][0]['title']=='Staged'
                    assert client.get('/api/admin/api2api/config').status_code==403
                    client.cookies.clear()
                    response=client.post('/api2api/v1/chat/completions',json={'model':'fixture','messages':[]})
                    assert response.status_code==401 and response.json()['error']['type']=='authentication_error'
                    assert client.get('/api/finance/announcements').status_code==401
                    assert client.get('/static/test-header.js').status_code==200
            assert Path(main.DATA_DIR).is_dir()
        ''')
        result=subprocess.run([sys.executable,'-c',script],cwd=project,env=environment,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

    def test_production_keeps_the_original_entry_and_routes_without_the_flag(self):
        self.rehearse(False)

    def test_opted_in_workspace_preserves_production_mfa_encryption_and_permissions(self):
        self.rehearse(True)
