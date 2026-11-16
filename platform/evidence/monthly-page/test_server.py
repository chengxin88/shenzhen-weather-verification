import http.server,sys,json,os,mimetypes
from urllib.parse import urlparse
root=os.path.abspath(sys.argv[1]);fx=os.path.abspath(os.path.join(os.path.dirname(__file__),'../../fixtures'));port=int(sys.argv[2]) if len(sys.argv)>2 else 8544
PROBE=b"""<script>setTimeout(()=>{const f=[...document.querySelectorAll('.monthly-maps figure')].map(x=>{const r=x.getBoundingClientRect();return r.width.toFixed(1)+'x'+r.height.toFixed(1)});const c=document.querySelector('canvas[data-map=obs]');const g=c.getContext('2d');const d=g.getImageData(0,0,c.width,c.height).data;const set=new Set();for(let i=0;i<d.length;i+=4)if(d[i+3])set.add(d[i]+','+d[i+1]+','+d[i+2]);const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({sizes:f,obsColors:set.size});document.body.appendChild(p)},4000)</script>"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def send(s,code,body,ct):
        s.send_response(code);s.send_header('Content-Type',ct);s.send_header('Content-Length',str(len(body)));s.end_headers();s.wfile.write(body)
    def do_GET(s):
        p=urlparse(s.path).path
        if p.endswith('api/monthly/catalog'):return s.send(200,open(fx+'/monthly-catalog.json','rb').read(),'application/json')
        if p.endswith('api/monthly/data'):return s.send(200,open(fx+'/monthly-data-2026-09.json','rb').read(),'application/json')
        if '/api/' in p:return s.send(404,b'{}','application/json')
        f=os.path.join(root,p.lstrip('/') or 'index.html')
        if p.endswith('/'):f=os.path.join(root,'index.html')
        if os.path.isfile(f) and f.endswith('index.html') and os.environ.get('PROBE'):
            b=open(f,'rb').read().replace(b'</body>',PROBE+b'</body>');return s.send(200,b,'text/html')
        if os.path.isfile(f):return s.send(200,open(f,'rb').read(),mimetypes.guess_type(f)[0] or 'application/octet-stream')
        s.send(404,b'nf','text/plain')
http.server.ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
