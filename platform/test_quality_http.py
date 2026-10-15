"""Quality API integration, route isolation and async refresh without live scans."""
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
import json,threading,unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import quality_control as qc
import server


class QualityHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'
        cls.prefix='/Verification/test/api/quality-control'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join()
    def request(self,path,body=None,headers=None):
        request=Request(self.base+path,data=body,headers=headers or {},method='POST' if body is not None else 'GET')
        try:
            with urlopen(request,timeout=5) as response:return response.status,response.read(),dict(response.headers)
        except HTTPError as error:return error.code,error.read(),dict(error.headers)
    def fixture(self,state='ready'):
        return dict(schema=1,state=state,stale=False,ttl_seconds=300,job={},snapshot=dict(schema=1,generated_utc=datetime.now(timezone.utc).isoformat(),sources=[],summary={}))
    def test_catalog_retains_cached_snapshot_during_async_refresh(self):
        value=self.fixture('running')
        with patch.object(qc,'status',return_value=value):
            status,raw,headers=self.request(self.prefix+'/catalog')
            self.assertEqual(status,200);self.assertEqual(json.loads(raw),value)
            self.assertIn('no-store',headers['Cache-Control'])
        with patch.object(qc,'refresh',return_value=value) as refresh:
            status,raw,_=self.request(self.prefix+'/refresh',b'{}',{'Content-Type':'application/json'})
            self.assertEqual(status,202);self.assertEqual(json.loads(raw),value);refresh.assert_called_once_with()
    def test_scan_rejects_external_parameters_and_cross_origin(self):
        with patch.object(qc,'refresh') as refresh:
            for body,headers in [(b'{"path":"/private"}',{'Content-Type':'application/json'}),
                                  (b'{"source_url":"https://example.com"}',{'Content-Type':'application/json'}),
                                  (b'{}',{'Content-Type':'text/plain'}),
                                  (b'{}',{'Content-Type':'application/json','Origin':'https://unrelated.example'})]:
                with self.subTest(body=body,headers=headers):
                    expected=403 if 'Origin' in headers else 400
                    self.assertEqual(self.request(self.prefix+'/refresh',body,headers)[0],expected)
            refresh.assert_not_called()
        self.assertEqual(self.request(self.prefix+'/catalog?source_url=x')[0],400)
        self.assertEqual(self.request(self.prefix+'/refresh')[0],404)
    def test_qc_failure_is_sanitized(self):
        with patch.object(qc,'status',side_effect=RuntimeError('internal path /private/secret-token')):
            status,body,_=self.request(self.prefix+'/catalog')
            self.assertEqual(status,503);self.assertNotIn(b'private',body);self.assertNotIn(b'secret',body)
    def test_sidebar_assets_and_module_script_order(self):
        for suffix in ('quality.js','quality.css','health'):
            self.assertEqual(self.request('/Verification/test/'+suffix)[0],200)
        page=self.request('/Verification/test/')[1].decode()
        self.assertIn('data-module="quality"',page);self.assertIn('id="quality-workspace"',page)
        self.assertLess(page.index('quality.js'),page.index('./modules.js'))
        self.assertEqual(self.request('/Verification/test/cache/quality-control/snapshot.json')[0],404)


if __name__=='__main__':unittest.main()
