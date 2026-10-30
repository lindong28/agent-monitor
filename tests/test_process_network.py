"""Process observations use synthetic inputs and two isolated native programs."""
import json
import http.client
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from agent_monitor.network import process_network as pn


class ProcessNetworkTests(unittest.TestCase):
    def test_popup_authorizes_origin_host_nonce_and_keeps_report_out_of_hub(self):
        from agent_monitor.network.client_connector import Connector
        connector = Connector.__new__(Connector)
        connector.server = 'http://hub.test'
        connector.stopped = threading.Event()
        connector.registration = {'connection': {'capability': 'must-not-appear'}}
        connector.process_report_lock = threading.Lock()
        server = ThreadingHTTPServer(('127.0.0.1', 0), connector.pair_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def get(origin='http://hub.test', nonce='a' * 32, host=None):
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                client.request('GET', f'/process-network?origin={origin}&nonce={nonce}', headers={'Host': host or f'127.0.0.1:{server.server_port}'})
                response = client.getresponse(); return response.status, response.read().decode()
            finally:
                client.close()
        with mock.patch.object(pn, 'read_report', return_value={'host': 'local-fixture'}) as reader:
            for kwargs in ({'origin': 'http://evil.test'}, {'nonce': 'invalid'}, {'host': 'evil.test'}):
                self.assertEqual(get(**kwargs)[0], 403)
            reader.assert_not_called()
            status, body = get()
            self.assertEqual(status, 200); self.assertIn('local-fixture', body)
            self.assertNotIn('must-not-appear', body)
            reader.assert_called_once()
            connector.process_report_lock.acquire()
            try:
                message, _ = json.JSONDecoder().raw_decode(get()[1].split('postMessage(', 1)[1])
                self.assertIn('已有采集', message['error'])
            finally: connector.process_report_lock.release()
            connector.stopped.set()
            self.assertEqual(get()[0], 403)

    def test_browser_process_report_flow(self):
        result = subprocess.run(['node', '--test', 'tests/test_process_network.cjs'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_environment_projects_only_network_fields_and_drops_url_secrets(self):
        env = {'HTTPS_PROXY': 'http://name:secret@127.0.0.1:9234/private?token=secret#secret',
               'ANTHROPIC_BASE_URL': 'https://example.test/private-key?key=secret',
               'OPENAI_API_KEY': 'secret', 'NO_PROXY': 'localhost,.example.test',
               'NODE_TLS_REJECT_UNAUTHORIZED': '0'}
        data = pn.network_environment(env)
        self.assertEqual(data['HTTPS_PROXY'], {'address': 'http://127.0.0.1:9234', 'credentials_present': True})
        self.assertEqual(data['ANTHROPIC_BASE_URL']['address'], 'https://example.test')
        self.assertEqual(data['NO_PROXY']['value'], 'localhost,.example.test')
        self.assertNotIn('secret', json.dumps(data))
        self.assertNotIn('OPENAI_API_KEY', data)
        self.assertTrue(pn.safe_address('http://host:bad')['invalid'])
        self.assertEqual(pn.network_environment({'NO_PROXY': '10.0.0.0/8,::1/128'})['NO_PROXY']['value'], '10.0.0.0/8,::1/128')
        self.assertTrue(pn.network_environment({'NO_PROXY': 'https://secret/path'})['NO_PROXY']['invalid'])

    def test_darwin_denied_environment_preserves_connections(self):
        backend = object.__new__(pn.Darwin)
        backend.candidates = lambda: iter([(3, '/bin/codex')])
        backend.identity = lambda pid: '123'
        backend.executable = lambda pid: '/bin/codex'
        backend.args_environment = mock.Mock(side_effect=PermissionError(1, 'private'))
        backend.connections = lambda pid: [{'local': '127.0.0.1:1', 'peer': '127.0.0.1:2', 'state': 'ESTABLISHED'}]
        item = pn.capture(backend)['processes'][0]
        self.assertEqual(item['environment_status'], 'unavailable')
        self.assertEqual(len(item['connections']), 1)
        self.assertNotIn('private', json.dumps(item))

    def test_node_entry_is_identified_without_matching_prompt_arguments(self):
        self.assertEqual(pn.agent_kind('/usr/bin/node', ['node', '/lib/node_modules/@anthropic-ai/claude-code/cli.js']), 'claude')
        self.assertEqual(pn.agent_kind('/usr/bin/node', ['node', '/lib/node_modules/@openai/codex/bin/codex.js']), 'codex')
        self.assertIsNone(pn.agent_kind('/usr/bin/node', ['node', '/other.js', '/@openai/codex/bin/codex.js']))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / '.local/bin').mkdir(parents=True)
            binary = root / '2.1.123'; binary.write_text('synthetic native binary')
            (root / '.local/bin/claude').symlink_to(binary)
            with mock.patch.object(pn.Path, 'home', return_value=root):
                self.assertEqual(pn.agent_kind(str(binary), ['claude']), 'claude')
                self.assertIsNone(pn.agent_kind(str(root / 'unrelated'), []))
                versions = root / '.local/share/claude/versions'
                versions.mkdir(parents=True)
                old, new = versions/'2.1.1', versions/'2.1.2'
                old.touch(); new.touch()
                (root/'.local/bin/claude').unlink(); (root/'.local/bin/claude').symlink_to(new)
                self.assertEqual(pn.agent_kind(str(old), ['claude']), 'claude')

    def test_darwin_argc_keeps_empty_arguments_out_of_environment(self):
        raw = struct.pack('i', 3) + b'/bin/codex\0\0codex\0\0prompt=x\0HTTPS_PROXY=http://localhost:9\0SECRET=value\0'
        argv, env = pn.parse_darwin_args(raw)
        self.assertEqual(argv, ['codex', '', 'prompt=x'])
        self.assertEqual(env['HTTPS_PROXY'], 'http://localhost:9')
        self.assertNotIn('prompt', env)

    def test_linux_ipv4_ipv6_and_stat_identity(self):
        self.assertEqual(pn.linux_address('0100007F:01BB'), '127.0.0.1:443')
        self.assertEqual(pn.linux_address('00000000000000000000000001000000:01BB'), '[::1]:443')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / '12').mkdir()
            (root / '12/stat').write_text('12 (name ) with spaces) S ' + ' '.join(['0'] * 18 + ['98765']))
            self.assertEqual(pn.Linux(root).identity(12), '98765')

    def test_permission_failure_preserves_other_process_and_pid_reuse_rejects_snapshot(self):
        backend = mock.Mock()
        backend.candidates.return_value = [(1, '/bin/codex'), (2, '/bin/claude')]
        backend.identity.side_effect = ['one', 'replacement', 'two', 'two']
        backend.executable.side_effect = lambda pid: '/bin/codex' if pid == 1 else '/bin/claude'
        backend.arguments.return_value = ['codex']
        backend.environment.side_effect = [{'HTTPS_PROXY': 'http://localhost:1'}, PermissionError(13, 'secret path')]
        backend.connections.return_value = [{'local': '127.0.0.1:10', 'peer': '127.0.0.1:20', 'state': 'ESTABLISHED'}]
        report = pn.capture(backend)
        self.assertEqual(report['processes'][0]['status'], 'unavailable')
        self.assertNotIn('environment', report['processes'][0])
        self.assertEqual(report['processes'][1]['environment_status'], 'unavailable')
        self.assertEqual(len(report['processes'][1]['connections']), 1)
        self.assertNotIn('secret path', json.dumps(report))

    def test_pid_replaced_before_first_identity_does_not_reuse_old_executable(self):
        backend = mock.Mock()
        backend.candidates.return_value = [(1, '/bin/codex')]
        backend.identity.return_value = 'new-process'
        backend.executable.return_value = '/bin/unrelated'
        backend.arguments.return_value = ['unrelated']
        report = pn.capture(backend)
        self.assertEqual(report['processes'], [])

    @unittest.skipUnless(shutil.which('cc') and sys.platform in ('darwin', 'linux'), 'native C fixture requires cc')
    def test_actual_two_process_environments_and_sockets_then_exit(self):
        # No full machine discovery: override only enumeration with our own PIDs.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'peer.c'
            source.write_text('''#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <stdlib.h>
int main(int argc,char **argv){int s=socket(AF_INET,SOCK_STREAM,0); struct sockaddr_in a={0};
a.sin_family=AF_INET;a.sin_port=htons(atoi(argv[1]));inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);
if(connect(s,(void*)&a,sizeof(a)))return 2;write(1,"ready\\n",6);sleep(20);return 0;}
''')
            subprocess.run(['cc', str(source), '-o', str(root / 'codex')], check=True, capture_output=True, timeout=20)
            shutil.copy2(root / 'codex', root / 'claude')
            processes, servers, peers = [], [], []
            try:
                for i, name in enumerate(('codex', 'claude')):
                    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(5); servers.append(server)
                    proc = subprocess.Popen([str(root / name), str(server.getsockname()[1])], stdout=subprocess.PIPE,
                                            env={'HTTPS_PROXY': f'http://proxy{i}.invalid:8080', 'SECRET': 'do-not-export'})
                    processes.append(proc); peer, _ = server.accept(); peers.append(peer)
                base = pn.Darwin if sys.platform == 'darwin' else pn.Linux
                class Selected(base):
                    def candidates(self):
                        return iter((p.pid, str(root / name)) for p, name in zip(processes, ('codex', 'claude')))
                report = pn.capture(Selected())
                self.assertEqual(len(report['processes']), 2)
                for i, item in enumerate(report['processes']):
                    self.assertFalse(item['errors'], item)
                    self.assertEqual(item['environment']['HTTPS_PROXY']['address'], f'http://proxy{i}.invalid:8080')
                    self.assertIn('127.0.0.1:' + str(servers[i].getsockname()[1]), [c['peer'] for c in item['connections']])
                self.assertNotIn('do-not-export', json.dumps(report))
                for proc in processes: proc.terminate(); proc.wait(timeout=5)
                after = pn.capture(Selected())
                self.assertTrue(all(p['status'] == 'unavailable' for p in after['processes']))
            finally:
                for proc in processes:
                    if proc.poll() is None: proc.terminate(); proc.wait(timeout=5)
                    proc.stdout.close()
                for sock in peers + servers: sock.close()


if __name__ == '__main__':
    unittest.main()
