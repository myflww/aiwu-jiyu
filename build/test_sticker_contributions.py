"""Public image submissions with fake GitHub transport; no remote writes."""
from __future__ import annotations
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from backend import sticker_contributions as sharing


class Contributions(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='aiwu-contributions-')
        self.addCleanup(self.temporary.cleanup)
        self.home=Path(self.temporary.name)
        self.patch_home=patch.object(sharing,'APP_HOME',self.home)
        self.patch_home.start();self.addCleanup(self.patch_home.stop)
        sharing._session.update(token='',login='')
        sharing._connecting=False
        sharing._job.update(running=False,state='idle',pr_url='')
        buffer=io.BytesIO();Image.new('RGB',(64,64),'blue').save(buffer,format='PNG')
        self.image=buffer.getvalue()
        self.export={'schema_version':1,'metadata':{'title':'开心小鱼','emotion':'happy','tags':['开心'],
            'description':'轻松聊天','use_when':'被夸奖','avoid_when':'严肃求助','attribution':'小鱼作者',
            'private_path':'C:/private/user.png','owner_qq':'123456789'},
            'image':{'filename':'private-name.png','mime':'image/png','sha256':hashlib.sha256(self.image).hexdigest(),
                     'base64':base64.b64encode(self.image).decode()}}
        self.patch_export=patch.object(sharing.custom_stickers,'export_item',return_value=self.export)
        self.patch_export.start();self.addCleanup(self.patch_export.stop)

    def plan(self):return sharing._plan('custom-abc','小鱼作者')

    def transport(self,*,login='alice',wrong_fork=False,extra_file=False,existing=False,missing_fork=False):
        outer=self
        class Fake:
            def __init__(self):self.calls=[];self.entries=[];self.fork_created=False
            def request(self,method,path,body=None):
                self.calls.append((method,path,body))
                if path=='/user':return {'login':login}
                if path=='/repos/'+sharing.REPOSITORY:return {'full_name':sharing.REPOSITORY,'private':False,'default_branch':'main'}
                if method=='GET' and '/pulls?' in path:
                    return [{'html_url':sharing.REPO_URL+'/pull/17'}] if existing else []
                if path=='/repos/alice/aiwu-jiyu':
                    if missing_fork and not self.fork_created:raise sharing.GitHubError(404)
                    return {'private':False,'fork':True,'parent':{'full_name':'other/repo' if wrong_fork else sharing.REPOSITORY}}
                if method=='POST' and path.endswith('/forks'):
                    self.fork_created=True;return {}
                if method=='GET' and path.endswith('/git/ref/heads/main'):return {'object':{'sha':'a'*40}}
                if method=='GET' and path.endswith('/git/commits/'+'a'*40):return {'tree':{'sha':'b'*40}}
                if method=='GET' and '/git/ref/heads/contrib/' in path:raise sharing.GitHubError(404)
                if method=='POST' and path.endswith('/git/blobs'):
                    return {'sha':sharing._blob_sha(base64.b64decode(body['content']))}
                if method=='POST' and path.endswith('/git/trees'):
                    outer.assertEqual(body['base_tree'],'b'*40)
                    self.entries=body['tree'];return {'sha':'d'*40}
                if method=='POST' and path.endswith('/git/commits'):
                    outer.assertEqual(body['parents'],['a'*40]);return {'sha':'c'*40}
                if method=='POST' and path.endswith('/git/refs'):
                    outer.assertTrue(body['ref'].startswith('refs/heads/contrib/stickers/'));return {}
                if method=='GET' and '/compare/' in path:
                    files=[{'filename':entry['path'],'sha':entry['sha'],'status':'added'} for entry in self.entries]
                    if extra_file:files.append({'filename':'app/main.py','sha':'e'*40,'status':'modified'})
                    return {'files':files}
                if method=='POST' and path.endswith('/pulls'):
                    outer.assertEqual(body['base'],'main')
                    outer.assertFalse(body['maintainer_can_modify'])
                    return {'html_url':sharing.REPO_URL+'/pull/17'}
                raise AssertionError((method,path))
        return Fake()

    def test_preview_contains_only_selected_public_fields(self):
        preview=sharing.preview('custom-abc','小鱼作者')
        encoded=json.dumps(preview,ensure_ascii=False)
        self.assertNotIn('private-name',encoded);self.assertNotIn('C:/private',encoded);self.assertNotIn('123456789',encoded)
        self.assertEqual(len(preview['files']),3)
        self.assertEqual(preview['metadata']['image']['width'],64)
        self.assertEqual(preview['license'],'CC-BY-NC-4.0')

    def test_export_requires_rights_and_is_a_portable_reviewable_zip(self):
        with self.assertRaises(ValueError):sharing.export_zip('custom-abc','作者',rights_confirmed=False)
        content,name=sharing.export_zip('custom-abc','作者',rights_confirmed=True)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(len(archive.namelist()),3)
            self.assertTrue(all(path.startswith(sharing.PREFIX+'/') and '..' not in path for path in archive.namelist()))
            meta=json.loads(archive.read(next(p for p in archive.namelist() if p.endswith('metadata.json'))))
            self.assertEqual(meta['attribution'],'作者')
        self.assertTrue(name.endswith('.zip'))

    def test_no_network_before_explicit_rights_or_if_preview_changed(self):
        with patch.object(sharing,'_credentials') as read_auth:
            with self.assertRaises(ValueError):sharing.start_submission({'item_id':'custom-abc','rights_confirmed':False})
            with self.assertRaises(ValueError):sharing.start_submission({'item_id':'custom-abc','attribution':'作者','rights_confirmed':True,'license':sharing.LICENSE_ID,'fingerprint':'old'})
            read_auth.assert_not_called()

    def test_submission_writes_only_three_reviewed_files_to_new_branch(self):
        client=self.transport()
        url=sharing._submit(self.plan(),'not-a-real-token','alice',client_factory=lambda token:client)
        self.assertEqual(url,sharing.REPO_URL+'/pull/17')
        paths=[(m,p,b) for m,p,b in client.calls if m in {'POST','PATCH','PUT','DELETE'}]
        self.assertFalse(any(m in {'PATCH','PUT','DELETE'} for m,p,b in paths))
        self.assertEqual(len(client.entries),3)
        self.assertTrue(all(e['mode']=='100644' and e['path'].startswith(sharing.PREFIX+'/') for e in client.entries))
        self.assertEqual(sum(p.endswith('/pulls') for m,p,b in paths),1)

    def test_fork_creation_and_owner_account_both_keep_main_untouched(self):
        for login,missing in [('alice',True),('myflww',False)]:
            client=self.transport(login=login,missing_fork=missing)
            sharing._submit(self.plan(),'not-a-real-token',login,client_factory=lambda token:client)
            self.assertEqual(client.fork_created,missing)
            self.assertFalse(any('/git/refs/heads/main' in p for m,p,b in client.calls if m!='GET'))

    def test_existing_pr_is_reused_without_uploading_again(self):
        client=self.transport(existing=True)
        sharing._submit(self.plan(),'not-a-real-token','alice',client_factory=lambda token:client)
        self.assertTrue(all(method=='GET' for method,path,body in client.calls))

    def test_unrelated_fork_or_extra_changes_never_create_a_pr(self):
        for options in ({'wrong_fork':True},{'extra_file':True}):
            client=self.transport(**options)
            with self.assertRaises(ValueError):sharing._submit(self.plan(),'not-a-real-token','alice',client_factory=lambda token:client)
            self.assertFalse(any(m=='POST' and p.endswith('/pulls') for m,p,b in client.calls))

    def test_transient_authorization_never_writes_plaintext(self):
        client=self.transport()
        token='test_only_token_not_a_secret'
        result=sharing.connect({'token':token,'remember':False},client_factory=lambda value:client)
        self.assertEqual(result['login'],'alice');self.assertNotIn(token,json.dumps(result))
        self.assertFalse((self.home/'run/sticker-github-auth.json').exists())
        sharing.disconnect();self.assertFalse(sharing.auth_status()['configured'])

    def test_remember_requires_real_dpapi_and_status_never_returns_token(self):
        client=self.transport()
        with patch.object(sharing.dpapi,'protect',return_value='plain:unsafe'):
            with self.assertRaises(ValueError):sharing.connect({'token':'test_only_token_not_a_secret','remember':True},client_factory=lambda value:client)
        with patch.object(sharing.dpapi,'protect',return_value='dpapi:encrypted-test'):
            sharing.connect({'token':'test_only_token_not_a_secret','remember':True},client_factory=lambda value:client)
        self.assertNotIn('test_only_token_not_a_secret',(self.home/'run/sticker-github-auth.json').read_text())
        self.assertNotIn('token',json.dumps(sharing.auth_status()))

    def test_arbitrary_public_destinations_and_redirects_are_rejected(self):
        with self.assertRaises(ValueError):sharing._pr_url('https://other.example/pull/1')
        with self.assertRaises(ValueError):sharing.Client('not-a-secret').request('POST','//evil.example')
        with self.assertRaises(ValueError):sharing._NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example')

    def test_disconnection_cancels_an_inflight_connection(self):
        entered,release=threading.Event(),threading.Event()
        errors=[]
        class Pending:
            def request(self,*args):
                entered.set();release.wait(2);return {'login':'alice'}
        def connect():
            try:sharing.connect({'token':'test_only_token_not_a_secret','remember':True},client_factory=lambda token:Pending())
            except ValueError as error:errors.append(str(error))
        with patch.object(sharing.dpapi,'protect',return_value='dpapi:encrypted-test'):
            thread=threading.Thread(target=connect);thread.start()
            self.assertTrue(entered.wait(1))
            sharing.disconnect();release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertTrue(errors)
        self.assertFalse(sharing.auth_status()['configured'])
        self.assertFalse((self.home/'run/sticker-github-auth.json').exists())

    def test_submission_claim_and_authorization_read_are_atomic(self):
        sharing._session.update(token='test_only_token_not_a_secret',login='alice')
        entered,release=threading.Event(),threading.Event()
        sending,finish=threading.Event(),threading.Event()
        original=sharing._credentials
        disconnected=threading.Event();errors=[]
        def read_credentials():
            result=original();entered.set();release.wait(2);return result
        def submit(*args):
            sending.set();finish.wait(2);return sharing.REPO_URL+'/pull/17'
        def disconnect():
            try:sharing.disconnect()
            except ValueError as error:errors.append(str(error))
            finally:disconnected.set()
        body={'item_id':'custom-abc','attribution':'作者','fingerprint':sharing._plan('custom-abc','作者')['fingerprint'],
              'rights_confirmed':True,'license':sharing.LICENSE_ID}
        with patch.object(sharing,'_credentials',side_effect=read_credentials),patch.object(sharing,'_submit',side_effect=submit):
            thread=threading.Thread(target=lambda:sharing.start_submission(body));thread.start()
            self.assertTrue(entered.wait(1))
            revoke=threading.Thread(target=disconnect);revoke.start()
            self.assertFalse(disconnected.wait(.05))
            release.set();thread.join(2);self.assertTrue(sending.wait(1));revoke.join(1)
            self.assertTrue(errors);self.assertTrue(sharing.auth_status()['configured'])
            finish.set()
            deadline=time.monotonic()+2
            while sharing._job['running'] and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(sharing._job['running'])


if __name__=='__main__':unittest.main()
