#!/usr/bin/env python3
"""Integration tests for ngx_stream_upstream_check_module.

    python3 tests/run_tests.py /path/to/nginx        # an nginx built with this module

Starts small TCP/UDP/HTTP backends and nginx on 127.0.0.1 only, in a temporary
directory, and stops everything at the end. Standard library only.
"""
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

NGINX = None


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TCPBackend(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, tag, reply=None, banner=None):
        self.tag, self.reply, self.banner = tag, reply, banner
        super().__init__(('127.0.0.1', 0), TCPHandler)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        if srv.banner:
            self.request.sendall(srv.banner)
        self.request.settimeout(2)
        try:
            data = self.request.recv(4096)
        except OSError:
            return
        if data.startswith(b'PING'):
            self.request.sendall(srv.reply or b'')
        elif data.startswith(b'WHO'):
            self.request.sendall(srv.tag.encode())


class UDPBackend(socketserver.ThreadingUDPServer):
    def __init__(self, reply):
        self.reply = reply
        super().__init__(('127.0.0.1', 0), UDPHandler)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        _, sock = self.request
        sock.sendto(self.server.reply, self.client_address)


def http_backend(code):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code)
            self.send_header('Content-Length', '0')
            self.end_headers()
        do_HEAD = do_GET

        def log_message(self, *a):
            pass
    s = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    s.daemon_threads = True
    return s


CHECK = 'interval=200 rise=1 fall=1 timeout=1000'
BALANCERS = ['', 'least_conn;', 'hash $remote_port;', 'hash $remote_port consistent;',
             'random;', 'random two;', 'least_time connect;']


def nginx_version():
    out = subprocess.run([NGINX, '-v'], capture_output=True, text=True).stderr
    return tuple(int(x) for x in out.split('/')[1].split()[0].split('.'))


class ModuleTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.servers = {
            'pong': TCPBackend('pong', reply=b'+PONG\r\n'),
            'err': TCPBackend('err', reply=b'-ERR\r\n'),
            'banner_ok': TCPBackend('banner_ok', banner=b'220 ready\r\n'),
            'banner_bad': TCPBackend('banner_bad', banner=b'554 go away\r\n'),
            'a': TCPBackend('A'),
            'b': TCPBackend('B'),
            'http200': http_backend(200),
            'http500': http_backend(500),
            'udp_ok': UDPBackend(b'OK'),
            'udp_no': UDPBackend(b'NO'),
        }
        for s in cls.servers.values():
            threading.Thread(target=s.serve_forever, daemon=True).start()
        port = {k: s.server_address[1] for k, s in cls.servers.items()}
        cls.closed_tcp = free_port()
        cls.closed_udp = free_port(socket.SOCK_DGRAM)
        cls.status_port = free_port()
        # least_time exists since 1.31; the legacy 1.12-1.22 patch does not hook random
        need = {'least_time': (1, 31), 'random': (1, 24)}
        balancers = [b for b in BALANCERS
                     if all(k not in b or nginx_version() >= v for k, v in need.items())]
        cls.proxy_ports = {b or 'round_robin': free_port() for b in balancers}
        cls.dir = tempfile.mkdtemp(prefix='ngx-check-')
        os.mkdir(os.path.join(cls.dir, 'logs'))
        conf = f'''
daemon off;
master_process on;
worker_processes 1;
error_log logs/error.log info;
pid logs/nginx.pid;
events {{ worker_connections 256; }}

stream {{
    upstream connect_only {{
        server 127.0.0.1:{port['a']};
        server 127.0.0.1:{cls.closed_tcp};
        check {CHECK} type=tcp;
    }}
    upstream send_expect {{
        server 127.0.0.1:{port['pong']};
        server 127.0.0.1:{port['err']};
        check {CHECK} type=tcp;
        check_send "PING\\r\\n";
        check_backend_expect_alive "+pong";
    }}
    upstream banner {{
        server 127.0.0.1:{port['banner_ok']};
        server 127.0.0.1:{port['banner_bad']};
        check {CHECK} type=tcp;
        check_backend_expect_alive "220 ";
    }}
    upstream web {{
        server 127.0.0.1:{port['http200']};
        server 127.0.0.1:{port['http500']};
        check {CHECK} type=http;
        check_http_send "HEAD / HTTP/1.0\\r\\n\\r\\n";
        check_http_expect_alive http_2xx http_3xx;
    }}
    upstream dgram {{
        server 127.0.0.1:{port['udp_ok']};
        server 127.0.0.1:{port['udp_no']};
        check {CHECK} type=udp;
        check_send "ping";
        check_backend_expect_alive "OK";
    }}
{''.join(f"""
    upstream routed{i} {{
        {b}
        server 127.0.0.1:{port['a']} max_fails=0;
        server 127.0.0.1:{cls.closed_tcp} max_fails=0;
        server 127.0.0.1:{port['b']} down;
        check {CHECK} type=tcp;
    }}
    server {{ listen 127.0.0.1:{cls.proxy_ports[b or 'round_robin']}; proxy_pass routed{i}; proxy_connect_timeout 1s;
                proxy_next_upstream off; }}
""" for i, b in enumerate(balancers))}
    upstream dgram_plain {{
        server 127.0.0.1:{port['udp_ok']};
        server 127.0.0.1:{cls.closed_udp};
        check {CHECK} type=udp;
    }}
    upstream unchecked {{
        server 127.0.0.1:{port['b']};
    }}
}}

http {{
    access_log off;
    server {{
        listen 127.0.0.1:{cls.status_port};
        location /status {{ l4check_status; }}
    }}
}}
'''
        cls.conf = os.path.join(cls.dir, 'nginx.conf')
        open(cls.conf, 'w').write(conf)
        cls.port = port
        cls.nginx = subprocess.Popen([NGINX, '-p', cls.dir, '-c', cls.conf],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait until every peer has two consecutive results of the same kind;
        # the first start of a fresh binary can be slow (e.g. macOS scanning it)
        deadline = time.time() + 30
        while True:
            try:
                peers = json.loads(cls.status())['servers']['server']
                if peers and all(p['rise'] + p['fall'] >= 2 for p in peers):
                    break
            except OSError:
                pass
            if time.time() > deadline:
                cls.tearDownClass()
                raise RuntimeError('health checks did not settle, see logs/error.log (KEEP=1)')
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.nginx.terminate()
        cls.nginx.wait(10)
        for s in cls.servers.values():
            s.shutdown()
            s.server_close()
        if os.environ.get('KEEP') is None:
            shutil.rmtree(cls.dir)
        else:
            print('kept', cls.dir)

    @classmethod
    def status(cls, query='format=json'):
        url = f'http://127.0.0.1:{cls.status_port}/status?{query}'
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.read().decode()

    def peers(self, query='format=json'):
        doc = json.loads(self.status(query))
        return {(p['upstream'], p['name']): p for p in doc['servers']['server']}, doc

    def state(self, upstream, port):
        return self.peers()[0][(upstream, f'127.0.0.1:{port}')]['status']

    def test_connect_only(self):
        self.assertEqual(self.state('connect_only', self.port['a']), 'up')
        self.assertEqual(self.state('connect_only', self.closed_tcp), 'down')

    def test_send_expect(self):
        self.assertEqual(self.state('send_expect', self.port['pong']), 'up')
        self.assertEqual(self.state('send_expect', self.port['err']), 'down')

    def test_banner_expect_without_send(self):
        self.assertEqual(self.state('banner', self.port['banner_ok']), 'up')
        self.assertEqual(self.state('banner', self.port['banner_bad']), 'down')

    def test_http(self):
        self.assertEqual(self.state('web', self.port['http200']), 'up')
        self.assertEqual(self.state('web', self.port['http500']), 'down')

    def test_udp_expect(self):
        self.assertEqual(self.state('dgram', self.port['udp_ok']), 'up')
        self.assertEqual(self.state('dgram', self.port['udp_no']), 'down')

    def test_udp_plain_icmp(self):
        # no expect: up unless the peer answers with ICMP port unreachable
        self.assertEqual(self.state('dgram_plain', self.port['udp_ok']), 'up')
        self.assertEqual(self.state('dgram_plain', self.closed_udp), 'down')

    def test_down_and_unchecked_servers_not_registered(self):
        peers, doc = self.peers()
        self.assertNotIn(('routed0', f'127.0.0.1:{self.port["b"]}'), peers)
        self.assertNotIn('unchecked', {u for u, _ in peers})
        self.assertEqual(doc['servers']['total'], len(peers))

    def test_traffic_skips_down_peer(self):
        for balancer, port in self.proxy_ports.items():
            with self.subTest(balancer=balancer):
                seen = []
                for _ in range(12):
                    # nginx accepts first, so a dead upstream shows up as an empty reply
                    with socket.create_connection(('127.0.0.1', port), timeout=3) as c:
                        c.sendall(b'WHO')
                        seen.append(c.recv(16))
                self.assertEqual(set(seen), {b'A'})

    def test_status_filters_are_valid_json(self):
        for q in ('format=json&status=up', 'format=json&status=down'):
            peers, doc = self.peers(q)
            want = q.rsplit('=', 1)[1]
            self.assertTrue(peers)
            self.assertTrue(all(p['status'] == want for p in peers.values()), q)
            self.assertEqual(doc['servers']['total'], len(peers))

    def test_status_formats(self):
        self.assertIn('<html', self.status('format=html').lower())
        self.assertIn('send_expect', self.status('format=csv'))


class ConfigErrorTest(unittest.TestCase):

    def nginx_t(self, body):
        d = tempfile.mkdtemp(prefix='ngx-check-t-')
        try:
            os.mkdir(os.path.join(d, 'logs'))
            conf = os.path.join(d, 'nginx.conf')
            open(conf, 'w').write('error_log logs/error.log; events {}\nstream { upstream u { server 127.0.0.1:1; '
                                  + body + ' } }\n')
            r = subprocess.run([NGINX, '-t', '-p', d, '-c', conf], capture_output=True, text=True)
            return r.returncode, r.stderr
        finally:
            shutil.rmtree(d)

    def test_tcp_send_needs_expect(self):
        rc, err = self.nginx_t('check interval=1000 type=tcp; check_send "PING";')
        self.assertNotEqual(rc, 0)
        self.assertIn('needs "check_backend_expect_alive"', err)

    def test_expect_not_for_http(self):
        rc, err = self.nginx_t('check interval=1000 type=http; check_backend_expect_alive "x";')
        self.assertNotEqual(rc, 0)
        self.assertIn('only supported for type=tcp and type=udp', err)

    def test_valid_config(self):
        rc, err = self.nginx_t('check interval=1000 type=tcp; check_backend_expect_alive "+OK";')
        self.assertEqual(rc, 0, err)


if __name__ == '__main__':
    NGINX = os.path.abspath(sys.argv.pop(1))
    unittest.main(verbosity=2)
