"""Loopback-only preview/production server behind the existing login gateway."""
import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from catalog import build_catalog
from case_api import QueueBusy
import model_interfaces

ROOT=Path(__file__).resolve().parent
PREFIX='/Verification/test/'
lock=threading.Lock()
snapshot=None
loaded=0
refreshing=False

def rebuild():
    global snapshot,loaded,refreshing
    try:
        data=json.dumps(build_catalog(),ensure_ascii=False,allow_nan=False).encode('utf8')
        with lock: snapshot,loaded=data,time.monotonic()
    finally:
        with lock: refreshing=False

def catalog_bytes(refresh=False):
    global refreshing
    with lock:
        current,stale=snapshot,snapshot is None or time.monotonic()-loaded>300
        previous_load=loaded
        if (stale or refresh) and not refreshing:
            refreshing=True
            threading.Thread(target=rebuild,daemon=True).start()
    if current is not None and not refresh:
        return current
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        with lock:
            if snapshot is not None and (not refresh or loaded>previous_load): return snapshot
            if not refreshing: break
        time.sleep(0.2)
    raise RuntimeError('catalog not ready')

class Handler(BaseHTTPRequestHandler):
    def send_json(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store, private')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in [PREFIX+'api/case/score',PREFIX+'api/typhoon/score',PREFIX+'api/quality-control/refresh']: self.send_error(404); return
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
            self.send_json({'error':'请求来源不匹配。'},403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=10000: raise ValueError('检验条件过长或为空。')
            if not self.headers.get('Content-Type','').startswith('application/json'): raise ValueError('请提交JSON检验条件。')
            if path==PREFIX+'api/quality-control/refresh':
                if json.loads(self.rfile.read(length))!={}:raise ValueError('质控扫描不接受外部路径或自定义请求参数。')
                import quality_control
                value=quality_control.refresh()
                self.send_json(value,202 if value['state']=='running' else 200);return
            if path==PREFIX+'api/typhoon/score':
                from typhoon_api import submit
            else:
                from case_api import submit
            result=submit(json.loads(self.rfile.read(length)))
            self.send_json(result,200 if result['status']=='done' else 202)
        except QueueBusy as exc: self.send_json({'error':str(exc),'retry_after':2},429)
        except ValueError as exc: self.send_json({'error':str(exc)},400)
        except Exception: self.send_json({'error':'检验数据源暂不可用，请稍后重试。'},503)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path==PREFIX+'api/quality-control/catalog':
            if urlsplit(self.path).query:
                self.send_json({'error':'质控目录不接受外部查询参数。'},400);return
            try:
                import quality_control
                self.send_json(quality_control.status())
            except Exception:self.send_json({'error':'质控结果暂不可用，请点击重新检查。'},503)
            return
        demo_root=PREFIX+'api/wn3-module-demo'
        if path in [demo_root+'/catalog',demo_root+'/monthly/catalog',demo_root+'/monthly/data']:
            try:
                import module_demo
                if path==demo_root+'/catalog':value=module_demo.catalog()
                elif path==demo_root+'/monthly/catalog':value=module_demo.monthly_catalog()
                else:
                    query=parse_qs(urlsplit(self.path).query,max_num_fields=1,keep_blank_values=True)
                    if set(query)!={'month'} or len(query['month'])!=1:raise ValueError('请选择有效演示月份。')
                    value=module_demo.read_month(query['month'][0])
                self.send_json(value)
            except ValueError:self.send_json({'error':'WN3演示资料或月份不合法。'},400)
            except (OSError,KeyError):self.send_json({'error':'WN3演示资料暂不可用。'},503)
            return
        interface_root=PREFIX+'api/model-interfaces'
        if path==interface_root:
            self.send_json(model_interfaces.catalog());return
        if path==interface_root+'/demo/detail':
            try:
                from wn3_demo import detail
                self.send_json(detail())
            except (ValueError,OSError,KeyError):self.send_json({'error':'WN3演示资料暂不可用。'},503)
            return
        interface_match=re.fullmatch(re.escape(interface_root)+r'/([a-z0-9-]+)/(contract|field|track|demo-field)',path)
        if interface_match:
            product_id,operation=interface_match.groups()
            try:model_interfaces.product(product_id)
            except ValueError:
                self.send_json({'error':'未知的模式产品。'},404);return
            try:
                if operation=='contract':
                    self.send_json(model_interfaces.contract(product_id));return
                query=parse_qs(urlsplit(self.path).query,max_num_fields=8,keep_blank_values=True)
                allowed={'run_utc','storm_id'} if operation=='track' else {'run_utc','lead_minutes','variable','accumulation_minutes','level_hpa'}
                if set(query)-allowed or any(len(values)!=1 for values in query.values()):
                    raise ValueError('接口参数不合法或重复。')
                params={key:values[0] for key,values in query.items()}
                if operation in ('field','demo-field'):
                    for key in ('lead_minutes','accumulation_minutes','level_hpa'):
                        if key in params:
                            try:params[key]=int(params[key])
                            except ValueError:raise ValueError('时效、累计长度和气压层必须为整数。') from None
                    request=model_interfaces.GridRequest(product_id=product_id,**params)
                else:request=model_interfaces.TrackRequest(product_id=product_id,**params)
                if operation=='demo-field':
                    from wn3_demo import field as demo_field
                    self.send_json(demo_field(request))
                else:self.send_json(model_interfaces.fetch(request))
            except model_interfaces.InterfacePending as exc:
                self.send_json({'status':'reserved','product_id':product_id,'error':str(exc),
                                'contract_url':f'{interface_root}/{product_id}/contract'},503)
            except (ValueError,TypeError):
                self.send_json({'error':'请求参数或模式输出不符合接入协议，请核对接口说明。'},400)
            except Exception:
                self.send_json({'error':'模式资料暂不可用。'},503)
            return
        if path in [PREFIX+'api/typhoon/catalog',PREFIX+'api/typhoon/storm'] or path.startswith(PREFIX+'api/typhoon/jobs/'):
            try:
                from typhoon_api import catalog,detail,job_status
                query=parse_qs(urlsplit(self.path).query,max_num_fields=8)
                if path==PREFIX+'api/typhoon/catalog':value=catalog(query.get('year',[None])[0])
                elif path==PREFIX+'api/typhoon/storm':value=detail(query.get('year',[None])[0],query.get('storm_id',[None])[0])
                else:value=job_status(path.rsplit('/',1)[-1])
                self.send_json(value)
            except ValueError as exc:self.send_json({'error':str(exc)},400)
            except Exception:self.send_json({'error':'台风资料暂不可用，请刷新重试。'},503)
            return
        if path in [PREFIX+'api/monthly/catalog',PREFIX+'api/monthly/data']:
            try:
                import monthly_precip
                if path==PREFIX+'api/monthly/catalog':self.send_json(monthly_precip.catalog());return
                month=parse_qs(urlsplit(self.path).query,max_num_fields=4).get('month',[None])[0]
                data=monthly_precip.read(month)
                self.send_response(200)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store, private')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers();self.wfile.write(data)
            except ValueError as exc:self.send_json({'error':str(exc)},404)
            except Exception:self.send_json({'error':'月降水检验资料暂不可用，请刷新重试。'},503)
            return
        if path==PREFIX+'api/case/catalog' or path.startswith(PREFIX+'api/case/jobs/'):
            try:
                from case_api import metadata,job_status
                value=metadata() if path==PREFIX+'api/case/catalog' else job_status(path.rsplit('/',1)[-1])
                self.send_json(value)
            except ValueError as exc: self.send_json({'error':str(exc)},400)
            except Exception: self.send_json({'error':'个例数据源暂不可用，请稍后重试。'},503)
            return
        if path=='/Verification/test':
            self.send_response(308); self.send_header('Location',PREFIX); self.end_headers(); return
        names={PREFIX:('index.html','text/html'),PREFIX+'app.js':('app.js','text/javascript'),
            PREFIX+'core.js':('core.js','text/javascript'),PREFIX+'style.css':('style.css','text/css'),PREFIX+'modules.js':('modules.js','text/javascript'),PREFIX+'case.js':('case.js','text/javascript'),PREFIX+'case_cache.js':('case_cache.js','text/javascript'),PREFIX+'case.css':('case.css','text/css'),PREFIX+'typhoon.js':('typhoon.js','text/javascript'),PREFIX+'typhoon.css':('typhoon.css','text/css'),PREFIX+'monthly.js':('monthly.js','text/javascript'),PREFIX+'monthly.css':('monthly.css','text/css')}
        if path==PREFIX+'api/catalog':
            try:
                query=parse_qs(urlsplit(self.path).query,max_num_fields=4)
                data=catalog_bytes(refresh=query.get('refresh')==['1']); mime='application/json'
            except Exception:
                self.send_error(503,'Verification data temporarily unavailable'); return
        elif path==PREFIX+'health':
            data=b'{"status":"ok","service":"verification-test"}'; mime='application/json'
        elif path==PREFIX+'model-interfaces.js':
            data=(ROOT/'model-interfaces.js').read_bytes();mime='text/javascript'
        elif path==PREFIX+'model-interfaces.css':
            data=(ROOT/'model-interfaces.css').read_bytes();mime='text/css'
        elif path==PREFIX+'demo-models.js':
            data=(ROOT/'demo-models.js').read_bytes();mime='text/javascript'
        elif path==PREFIX+'demo-models.css':
            data=(ROOT/'demo-models.css').read_bytes();mime='text/css'
        elif path==PREFIX+'quality.js':
            data=(ROOT/'quality.js').read_bytes();mime='text/javascript'
        elif path==PREFIX+'quality.css':
            data=(ROOT/'quality.css').read_bytes();mime='text/css'
        elif path in names:
            name,mime=names[path]; data=(ROOT/name).read_bytes()
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store, private')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(data)
    def log_message(self,fmt,*args):
        pass

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8512)
    args=parser.parse_args()
    with lock: refreshing=True
    threading.Thread(target=rebuild,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
