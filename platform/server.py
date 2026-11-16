"""Loopback-only preview/production server behind the existing login gateway."""
import argparse
import gzip
import json
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from catalog import build_catalog
from catalog_http import CatalogSnapshot
from case_api import QueueBusy
import model_interfaces

ROOT=Path(__file__).resolve().parent
PREFIX='/Verification/test/'
lock=threading.Lock()
snapshot=None
http_snapshot=None
recent_snapshots={}
loaded=0
refreshing=False

def rebuild():
    global snapshot,http_snapshot,loaded,refreshing
    try:
        if os.environ.get('VERIFICATION_FROZEN_CATALOG'):
            from spark_overlay import overlay
            value=overlay(build_catalog(),os.environ['VERIFICATION_FROZEN_CATALOG'])
        elif os.environ.get('VERIFICATION_READ_ONLY')=='1':
            source=Path(os.environ.get('VERIFICATION_PREVIEW_DATA_ROOT',str(ROOT)))/'native-catalog.json'
            value=json.loads(source.read_bytes())
        else:
            value=build_catalog()
        prepared=CatalogSnapshot(value,ROOT/'cache'/'catalog-http')
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf8')
        with lock:
            snapshot,http_snapshot,loaded=data,prepared,time.monotonic()
            recent_snapshots[prepared.snapshot_id]=prepared
            while len(recent_snapshots)>3:
                recent_snapshots.pop(next(iter(recent_snapshots)))
    except Exception as exc:
        print('Catalog rebuild deferred:',type(exc).__name__,flush=True)
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

def current_http_snapshot(ident=None,refresh=False):
    catalog_bytes(refresh)
    with lock:
        return recent_snapshots.get(ident) if ident else http_snapshot

class Handler(BaseHTTPRequestHandler):
    def send_station_rows(self, path, immutable=False):
        # Stream the offline product; never retain decoded station rows in the process.
        compressed = any(part.split(';')[0].strip().lower() in ('gzip', '*') and
                         not re.search(r';\s*q=0(?:\.0*)?(?:\s*;|\s*$)', part)
                         for part in self.headers.get('Accept-Encoding', '').split(','))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'private, max-age=300' if immutable else 'no-store, private')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(path.stat().st_size))
        self.end_headers()
        with (path.open('rb') if compressed else gzip.open(path, 'rb')) as handle:
            shutil.copyfileobj(handle, self.wfile, length=65536)

    def send_json(self,value,status=200,cache_seconds=0):
        data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control',f'private, max-age={cache_seconds}' if cache_seconds else 'no-store, private')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers(); self.wfile.write(data)
    def do_HEAD(self):
        path=urlsplit(self.path).path
        from compare_gateway import handle as compare_page
        if compare_page(self,path):return
        if path==PREFIX+'health':
            self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length','46'); self.end_headers(); return
        self.send_error(405)
    def do_PUT(self):
        from compare_gateway import handle as compare_page
        if compare_page(self,urlsplit(self.path).path):return
        self.send_error(405)
    def do_DELETE(self):
        from compare_gateway import handle as compare_page
        if compare_page(self,urlsplit(self.path).path):return
        self.send_error(405)
    def do_POST(self):
        path=urlsplit(self.path).path
        from compare_gateway import handle as compare_page
        if compare_page(self,path):return
        if os.environ.get('VERIFICATION_READ_ONLY')=='1':
            self.send_json({'error':'预览服务只读；资料由独立脚本准备。'},405);return
        if path not in [PREFIX+'api/case/score',PREFIX+'api/case/fields',PREFIX+'api/typhoon/score',PREFIX+'api/typhoon/tracks',PREFIX+'api/quality-control/refresh']: self.send_error(404); return
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
            self.send_json({'error':'请求来源不匹配。'},403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=10000: raise ValueError('检验条件过长或为空。')
            if not self.headers.get('Content-Type','').startswith('application/json'): raise ValueError('请提交JSON检验条件。')
            if path==PREFIX+'api/case/fields':
                from case_api import fields
                self.send_json(fields(json.loads(self.rfile.read(length))));return
            if path==PREFIX+'api/quality-control/refresh':
                if json.loads(self.rfile.read(length))!={}:raise ValueError('质控扫描不接受外部路径或自定义请求参数。')
                import quality_control
                value=quality_control.refresh()
                self.send_json(value,202 if value['state']=='running' else 200);return
            if path==PREFIX+'api/typhoon/score':
                from typhoon_api import submit
            elif path==PREFIX+'api/typhoon/tracks':
                from typhoon_api import submit_tracks as submit
            else:
                from case_api import submit
            result=submit(json.loads(self.rfile.read(length)))
            self.send_json(result,200 if result['status']=='done' else 202)
        except QueueBusy as exc: self.send_json({'error':str(exc),'retry_after':2},429)
        except ValueError as exc: self.send_json({'error':str(exc)},400)
        except Exception: self.send_json({'error':'检验数据源暂不可用，请稍后重试。'},503)
    def do_GET(self):
        path=urlsplit(self.path).path
        from compare_gateway import handle as compare_page
        if compare_page(self,path):return
        if path==PREFIX+'api/rows':
            try:
                query=parse_qs(urlsplit(self.path).query,max_num_fields=2,keep_blank_values=True,strict_parsing=True)
                if set(query)!={'dataset','snapshot'} or any(len(v)!=1 or not v[0] for v in query.values()):
                    raise ValueError('请选择有效的检验资料集。')
                if len(query['dataset'][0])>128 or not re.fullmatch(r'[a-f0-9]{32}',query['snapshot'][0]):
                    raise ValueError('检验资料参数不合法。')
                current=current_http_snapshot(query['snapshot'][0])
                if current is None:
                    self.send_json({'error':'资料版本已更新，请刷新资料。'},409);return
                file=current.rows_file(query['dataset'][0])
            except ValueError as exc:self.send_json({'error':str(exc)},400);return
            except KeyError:self.send_json({'error':'未知的检验资料集。'},404);return
            except Exception:self.send_json({'error':'检验资料暂不可用。'},503);return
            self.send_station_rows(file,immutable=True);return
        station_root=PREFIX+'api/stations/'
        if path.startswith(station_root):
            try:
                import station_api
                value = station_api.dispatch(path[len(station_root):],urlsplit(self.path).query)
                versioned=path==station_root+'grid-rows' and bool(parse_qs(urlsplit(self.path).query).get('generation'))
                if isinstance(value, Path): self.send_station_rows(value,immutable=versioned)
                else: self.send_json(value,cache_seconds=300 if versioned else 0)
            except KeyError:self.send_json({'error':'未知站点接口。'},404)
            except ValueError as exc:self.send_json({'error':str(exc)},400)
            except Exception:self.send_json({'error':'站点结果暂不可用，请稍后读取。'},503)
            return
        nowcast_root=PREFIX+'api/nowcast/'
        if path in (nowcast_root+'qpe',nowcast_root+'qpf'):
            try:
                import nowcast_api
                if path==nowcast_root+'qpe':
                    value=nowcast_api.qpe()
                else:
                    query=parse_qs(urlsplit(self.path).query,max_num_fields=2,keep_blank_values=True)
                    if set(query)!={'days'} or len(query['days'])!=1:
                        raise ValueError('请选择日期范围。')
                    value=nowcast_api.qpf(query['days'][0])
                self.send_json(value)
            except ValueError as exc:self.send_json({'error':str(exc)},400)
            except Exception:self.send_json({'error':'临近降水评分暂不可用。'},503)
            return
        if os.environ.get('VERIFICATION_READ_ONLY')=='1' and path.startswith(PREFIX+'api/') and path not in (PREFIX+'api/catalog',PREFIX+'api/quality-control/catalog'):
            self.send_json({'error':'此预览只读取已发布目录和站点结果。'},503);return
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
        if path in [PREFIX+'api/typhoon/catalog',PREFIX+'api/typhoon/storm',PREFIX+'api/typhoon/issues'] or path.startswith(PREFIX+'api/typhoon/jobs/'):
            try:
                from typhoon_api import catalog,detail,issues,job_status
                query=parse_qs(urlsplit(self.path).query,max_num_fields=8)
                if path==PREFIX+'api/typhoon/catalog':value=catalog(query.get('year',[None])[0])
                elif path==PREFIX+'api/typhoon/storm':value=detail(query.get('year',[None])[0],query.get('storm_id',[None])[0])
                elif path==PREFIX+'api/typhoon/issues':value=issues(query.get('year',[None])[0],query.get('storm_id',[None])[0])
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
        if path in ('/','/index.html','/Verification','/Verification/','/Verification/test'):
            self.send_response(308); self.send_header('Location',PREFIX); self.end_headers(); return
        names={PREFIX:('index.html','text/html'),PREFIX+'app.js':('app.js','text/javascript'),PREFIX+'overview.js':('overview.js','text/javascript'),PREFIX+'nowcast.js':('nowcast.js','text/javascript'),
            PREFIX+'core.js':('core.js','text/javascript'),PREFIX+'style.css':('style.css','text/css'),PREFIX+'modules.js':('modules.js','text/javascript'),PREFIX+'case.js':('case.js','text/javascript'),PREFIX+'case_cache.js':('case_cache.js','text/javascript'),PREFIX+'case.css':('case.css','text/css'),PREFIX+'typhoon.js':('typhoon.js','text/javascript'),PREFIX+'typhoon-track.js':('typhoon-track.js','text/javascript'),PREFIX+'typhoon-basemap.json':('typhoon-basemap.json','application/json'),PREFIX+'typhoon.css':('typhoon.css','text/css'),PREFIX+'monthly.js':('monthly.js','text/javascript'),PREFIX+'monthly.css':('monthly.css','text/css')}
        if path==PREFIX+'api/catalog':
            try:
                query=parse_qs(urlsplit(self.path).query,max_num_fields=4)
                refresh=query.get('refresh')==['1']
                data=(current_http_snapshot(refresh=refresh).catalog_bytes if query.get('format')==['metadata']
                      else catalog_bytes(refresh=refresh)); mime='application/json'
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
        elif path in (PREFIX+'stations.js',PREFIX+'stations.css',PREFIX+'grid-stations.js'):
            name=path.rsplit('/',1)[-1];data=(ROOT/name).read_bytes();mime='text/javascript' if name.endswith('.js') else 'text/css'
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
    if os.environ.get('VERIFICATION_READ_ONLY')!='1':
        with lock: refreshing=True
        threading.Thread(target=rebuild,daemon=True).start()
    ThreadingHTTPServer((os.environ.get('VERIFICATION_BIND','127.0.0.1'),args.port),Handler).serve_forever()
