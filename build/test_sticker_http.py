"""Private image/auth/reveal routes use explicit origin and bounded bodies."""
import base64
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from backend import server,custom_stickers,stickers


class PrivateRoutes(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='aiwu-private-http-');self.addCleanup(temporary.cleanup)
        home=Path(temporary.name)
        current=patch.object(custom_stickers,'ASTRBOT',home/'astrbot');current.start();self.addCleanup(current.stop)
        custom_stickers._module.cache_clear()
        setting=patch.object(stickers,'load_settings',return_value=dict(stickers.DEFAULT_SETTINGS));setting.start();self.addCleanup(setting.stop)
        self.server=server.LocalServer(('127.0.0.1',0),server.Handler)
        self.server.preview=False;self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.origin='http://127.0.0.1:'+str(self.server.server_address[1])

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        try:
            connection.request(method,path,body=None if body is None else json.dumps(body),headers=headers or {})
            response=connection.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally:connection.close()

    def post(self,path,body):return self.request('POST',path,body,{'Origin':self.origin,'Content-Type':'application/json'})

    def test_reveal_is_explicit_same_origin_post_and_never_cached(self):
        with patch.object(server.dashboard_credentials,'reveal',return_value={'fields':[{'id':'password','value':'synthetic-only'}]}) as reveal:
            for headers in ({},{'Origin':'https://untrusted.example'},{'Origin':self.origin,'Host':'evil.example'}):
                status,_,_=self.request('POST','/api/dashboard/credentials',{'target':'astrbot'},headers)
                self.assertEqual(status,403)
            reveal.assert_not_called()
            status,_,_=self.request('GET','/api/dashboard/credentials');self.assertEqual(status,405)
            status,headers,body=self.post('/api/dashboard/credentials',{'target':'astrbot'})
            self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertEqual(headers['X-Frame-Options'],'DENY');reveal.assert_called_once_with('astrbot')
            self.assertIn(b'synthetic-only',body)

    def test_real_local_image_import_toggle_asset_and_remove(self):
        image=io.BytesIO();Image.new('RGB',(64,64),'blue').save(image,format='PNG')
        status,_,body=self.post('/api/stickers/custom/import',{'title':'合成测试小鱼','emotion':'happy','image_base64':base64.b64encode(image.getvalue()).decode()})
        self.assertEqual(status,200,body)
        item=json.loads(body)['item'];identifier=item['id']
        status,headers,content=self.request('GET',item['url'])
        self.assertEqual(status,200);self.assertTrue(content.startswith(b'\x89PNG'))
        self.assertEqual(headers['Cross-Origin-Resource-Policy'],'same-origin')
        status,_,_=self.post('/api/stickers/custom/update',{'id':identifier,'changes':{'enabled':False}});self.assertEqual(status,200)
        status,_,body=self.request('GET','/api/stickers')
        rows=json.loads(body)['stickers'];self.assertFalse(next(row for row in rows if row['id']==identifier)['enabled'])
        status,_,_=self.post('/api/stickers/custom/delete',{'id':identifier});self.assertEqual(status,200)
        status,_,_=self.request('GET',item['url']);self.assertNotEqual(status,200)

    def test_upload_body_limit_rejects_before_reading_or_importing(self):
        with patch.object(server.custom_stickers,'import_item') as importer:
            status,_,_=self.request('POST','/api/stickers/custom/import',{}, {'Origin':self.origin,'Content-Length':str(12*1024*1024+1)})
            self.assertEqual(status,413);importer.assert_not_called()

    def test_contribution_needs_origin_and_preview_bound_consent(self):
        for path in ('connect','disconnect','preview','submit','export'):
            status,_,_=self.request('POST','/api/stickers/contributions/'+path,{})
            self.assertEqual(status,403)
        status,_,_=self.post('/api/stickers/contributions/export',{'rights_confirmed':True})
        self.assertEqual(status,400)
        with patch.object(server.sticker_contributions,'_credentials') as auth:
            status,_,_=self.post('/api/stickers/contributions/submit',{'rights_confirmed':False})
            self.assertEqual(status,400);auth.assert_not_called()

    def test_preview_does_not_read_private_library_or_credentials(self):
        self.server.preview=True
        with patch.object(server.dashboard_credentials,'reveal',side_effect=AssertionError('private read')),patch.object(server.custom_stickers,'list_items',side_effect=AssertionError('private read')):
            for path in ('/api/stickers','/api/stickers/contributions/auth','/api/stickers/contributions/status'):
                status,_,_=self.request('GET',path);self.assertEqual(status,200)
            status,_,_=self.post('/api/dashboard/credentials',{'target':'astrbot'});self.assertEqual(status,403)


if __name__=='__main__':unittest.main()
