"""Unified gateway on Spark port 8513 for all non-/Verification/test/ weather-platform routes.

Serves static assets (/Verification/compare/*, /globe/cesium/*, /download/*) and proxies loopback
services:
  - 127.0.0.1:8502  vp-streamlit-main.service   (/ and forecast pages, HTTP + WebSocket)
  - 127.0.0.1:8503  vp-compare-api.service      (/globe/* frontend & /globe/api/*)
  - 127.0.0.1:8505  vp-case-replay.service      (/compare-data/*)
  - 127.0.0.1:8506  vp-streamlit-verify.service (/Verification/*, HTTP + WebSocket)
  - /wn3/*  302 to https://wn3.szverify.xyz/ (portal moved to vp-tokyo; Spark vp-wn3-portal stopped 2026-11-16)
  - 127.0.0.1:8516  vp-balikun.service          (/Verification/balikun/*)
"""
import http.client
import json
import mimetypes
import select
import shutil
import socket
from pathlib import Path
from urllib.parse import unquote, urlsplit

R = Path('/home/yjzx/verification-platform')
WEB = R / 'compare-web'
CESIUM = R / 'cesium'
APK_PUBLIC = R / 'apk-public'

REDIRECTS_308 = {
    '/compare': '/Verification/compare/',
    '/Verification/compare': '/Verification/compare/',
    '/compare/case': '/Verification/compare/case/',
    '/Verification/compare/case': '/Verification/compare/case/',
    '/compare/analysis.html': '/Verification/compare/case/',
    '/Verification/compare/analysis.html': '/Verification/compare/case/',
    '/globe/forecast.html': '/globe/compare/',
    '/globe/compare': '/globe/compare/',
    '/Verification/balikun': '/Verification/balikun/',
    '/Verification': '/Verification/',
}
WN3_PUBLIC = 'https://wn3.szverify.xyz/'

REDIRECTS_302 = {
    '/download': '/download/',
    '/wn3': WN3_PUBLIC,
    '/globe': '/globe/',
}

PASSED_REQ = (
    'Accept', 'Accept-Encoding', 'Accept-Language', 'Cache-Control',
    'If-None-Match', 'If-Modified-Since', 'Range',
    'X-WN3-Prefetch', 'X-WN3-Cached-Only', 'X-Forwarded-Proto', 'X-Forwarded-For',
)
PASSED_RESP = (
    'Content-Type', 'Content-Encoding', 'Content-Length', 'Content-Disposition',
    'Cache-Control', 'ETag', 'Last-Modified', 'Location', 'Vary', 'Retry-After',
    'X-Content-Type-Options', 'X-WN3-Cache', 'Server-Timing', 'Accept-Ranges', 'Content-Range',
)
HOP_BY_HOP = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade',
}

CSP = (
    "default-src 'self'; connect-src 'self' https://services.arcgisonline.com data: blob:; "
    "img-src 'self' https://services.arcgisonline.com data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' blob: 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; "
    "frame-ancestors 'self'; form-action 'none'"
)
mimetypes.add_type('application/json', '.geojson')
mimetypes.add_type('application/vnd.android.package-archive', '.apk')


def fail(h, status, message):
    body = json.dumps({'error': message}, ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header('Content-Type', 'application/json; charset=utf-8')
    h.send_header('Content-Length', str(len(body)))
    h.send_header('Cache-Control', 'no-store')
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def redirect(h, status, target, query):
    loc = target + ('?' + query if query else '')
    h.send_response(status)
    h.send_header('Location', loc)
    h.send_header('Content-Length', '0')
    h.end_headers()


def proxy_readonly(h, port, target, force_cache=None):
    if h.command not in ('GET', 'HEAD'):
        return fail(h, 405, 'Method Not Allowed')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=130)
    try:
        headers = {k: h.headers[k] for k in PASSED_REQ if h.headers.get(k)}
        if h.headers.get('Host'):
            headers['Host'] = h.headers['Host']
        connection.request(h.command, target, headers=headers)
        reply = connection.getresponse()
    except OSError:
        connection.close()
        return fail(h, 502, '数据服务暂不可用，请稍后重试。')
    try:
        h.send_response(reply.status)
        has_len = False
        for name in PASSED_RESP:
            if force_cache and name.lower() == 'cache-control':
                continue
            val = reply.getheader(name)
            if val is not None:
                if name.lower() == 'content-length':
                    has_len = True
                h.send_header(name, val)
        if force_cache:
            h.send_header('Cache-Control', force_cache)
        h.end_headers()
        if h.command != 'HEAD' and reply.status not in (204, 304):
            if not has_len:
                h.close_connection = True
            shutil.copyfileobj(reply, h.wfile, 65536)
    finally:
        connection.close()


def proxy_websocket(h, port):
    try:
        upstream = socket.create_connection(('127.0.0.1', port), timeout=10)
    except OSError:
        return fail(h, 502, '应用服务暂不可用，请稍后重试。')
    try:
        upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        h.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    upstream.settimeout(None)
    h.connection.settimeout(None)
    h.close_connection = True

    lines = [f'{h.command} {h.path} HTTP/1.1']
    has_host = False
    for k, v in h.headers.items():
        kl = k.lower()
        if kl in ('authorization', 'cookie', 'connection', 'upgrade'):
            continue
        if kl == 'host':
            has_host = True
        lines.append(f'{k}: {v}')
    if not has_host:
        lines.append(f'Host: 127.0.0.1:{port}')
    lines.append('Upgrade: websocket')
    lines.append('Connection: Upgrade')
    lines.append('\r\n')

    try:
        h.wfile.flush()
        upstream.sendall('\r\n'.join(lines).encode('latin-1'))
        sockets = (h.connection, upstream)
        while True:
            readable, _, _ = select.select(sockets, [], [], 3600)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    return
                other = upstream if sock is h.connection else h.connection
                other.sendall(data)
    except OSError:
        pass
    finally:
        try:
            upstream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        upstream.close()


def proxy_streamlit(h, port):
    if h.headers.get('Upgrade', '').lower() == 'websocket':
        return proxy_websocket(h, port)

    length = int(h.headers.get('Content-Length', '0') or '0')
    if length > 320 * 1024 * 1024:
        return fail(h, 413, '上传文件过大。')
    body = h.rfile.read(length) if length > 0 else None

    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=120)
    try:
        headers = {}
        for k, v in h.headers.items():
            kl = k.lower()
            if kl in HOP_BY_HOP or kl in ('authorization', 'cookie'):
                continue
            headers[k] = v
        connection.request(h.command, h.path, body=body, headers=headers)
        reply = connection.getresponse()
    except OSError:
        connection.close()
        return fail(h, 502, '预报应用服务暂不可用，请稍后重试。')
    try:
        h.send_response(reply.status)
        has_len = False
        for k, v in reply.getheaders():
            kl = k.lower()
            if kl in HOP_BY_HOP:
                continue
            if kl == 'content-length':
                has_len = True
            h.send_header(k, v)
        h.end_headers()
        if h.command != 'HEAD' and reply.status not in (204, 304):
            if not has_len:
                h.close_connection = True
            shutil.copyfileobj(reply, h.wfile, 65536)
    finally:
        connection.close()


def static(h, root, relative, cache, csp=True, extra_headers=None):
    if h.command not in ('GET', 'HEAD'):
        return fail(h, 405, 'Method Not Allowed')
    rel_unquoted = unquote(relative)
    path = (root / rel_unquoted).resolve()
    if any(p.startswith('.') for p in Path(rel_unquoted).parts) or not path.is_relative_to(root) or not path.is_file():
        return h.send_error(404)
    h.send_response(200)
    h.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    h.send_header('Content-Length', str(path.stat().st_size))
    h.send_header('Cache-Control', cache)
    h.send_header('X-Content-Type-Options', 'nosniff')
    if csp:
        h.send_header('Content-Security-Policy', CSP)
    for k, v in (extra_headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    if h.command != 'HEAD':
        with path.open('rb') as handle:
            shutil.copyfileobj(handle, h.wfile, 65536)


def serve(h, path):
    query = urlsplit(h.path).query
    q_suffix = '?' + query if query else ''

    if path in REDIRECTS_302:
        return redirect(h, 302, REDIRECTS_302[path], query)
    if path in REDIRECTS_308:
        return redirect(h, 308, REDIRECTS_308[path], query)
    if path.startswith('/compare/'):
        if path.startswith(('/compare/.', '/compare/case/.')):
            return h.send_error(404)
        return redirect(h, 308, '/Verification' + path, query)

    # 1. APK /download/*
    if path.startswith('/download/'):
        if path in ('/download/', '/download/index.html'):
            return static(h, APK_PUBLIC, 'download/index.html', 'no-cache', csp=False)
        if path == '/download/qr.png':
            return static(h, APK_PUBLIC, 'download/qr.png', 'public, max-age=3600', csp=False)
        if path == '/download/szverify.apk':
            return static(
                h, APK_PUBLIC, 'download/szverify.apk', 'no-cache', csp=False,
                extra_headers={'Content-Disposition': 'attachment; filename="szverify.apk"'},
            )
        return h.send_error(404)

    # 2. Case replay & Compare static pages
    if path.startswith('/compare-data/'):
        return proxy_readonly(h, 8505, '/' + path[len('/compare-data/'):] + q_suffix)
    for prefix in ('/globe/cesium/', '/Verification/compare/case/cesium/'):
        if path.startswith(prefix):
            return static(h, CESIUM, path[len(prefix):], 'public, max-age=86400', csp=False)
    if path.startswith('/Verification/compare/case/'):
        return static(h, WEB, path[len('/Verification/compare/case/'):] or 'analysis.html', 'no-store')
    if path.startswith('/Verification/compare/'):
        return static(h, WEB, path[len('/Verification/compare/'):] or 'index.html', 'no-store')

    # 3. /globe/* frontend & APIs
    if path == '/globe/compare/':
        return proxy_readonly(h, 8503, '/forecast.html' + q_suffix, force_cache='no-store')
    if path.startswith('/globe/'):
        return proxy_readonly(h, 8503, '/' + path[len('/globe/'):] + q_suffix)

    # 4. /wn3/* portal: moved to vp-tokyo (wn3.szverify.xyz), redirect keeps sub-path and query
    if path.startswith('/wn3/'):
        return redirect(h, 302, WN3_PUBLIC + urlsplit(h.path).path[len('/wn3/'):], query)

    # 5. /Verification/balikun/*
    if path.startswith('/Verification/balikun/'):
        return proxy_readonly(h, 8516, h.path)

    # 6. /Verification/* Streamlit (:8506)
    if path.startswith('/Verification/'):
        return proxy_streamlit(h, 8506)

    # 7. Root & main Streamlit (:8502)
    return proxy_streamlit(h, 8502)


def handle(h, path):
    """True when the path does NOT belong to /Verification/test."""
    if path == '/Verification/test' or path.startswith('/Verification/test/'):
        return False
    try:
        serve(h, path)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    return True
