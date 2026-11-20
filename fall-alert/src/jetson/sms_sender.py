"""Jetson-local incident ledger and Aliyun SMS delivery."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import re
import socket
import ssl
import sqlite3
from threading import Event
from uuid import uuid4
from zoneinfo import ZoneInfo


from pathlib import Path
from http.client import HTTPSConnection
from functools import partial
from urllib.parse import urlsplit

from settings import read_private, validate_sms

CONFIG_PATH = Path('/robot/persist/t1-fall-alert/private/sms.json')


def load_config(path):
    """Also accept the original flat file for standalone verification callers."""
    config = json.loads(read_private(path))
    return validate_sms(config.get('sms', config))


def aliyun_send(config):
    from alibabacloud_dysmsapi20170525.client import Client
    from alibabacloud_dysmsapi20170525.models import SendSmsRequest
    from alibabacloud_tea_openapi.models import Config
    from alibabacloud_tea_util.models import RuntimeOptions
    client = Client(Config(access_key_id=config['access_key_id'],
                           access_key_secret=config['access_key_secret'],
                           endpoint='dysmsapi.aliyuncs.com'))
    runtime = RuntimeOptions(autoretry=False, max_attempts=1, connect_timeout=5000, read_timeout=10000)

    def send(event_id, detected):
        local_time = detected.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')
        request = SendSmsRequest(phone_numbers=config['phone'], sign_name=config['sign_name'],
                                 template_code=config['template_code'], out_id=event_id,
                                 template_param=json.dumps({config['time_variable']: local_time},
                                                           ensure_ascii=False))
        result = client.send_sms_with_options(request, runtime).body
        return result.code, result.biz_id or ''
    return send


def sender_for(settings):
    phone = settings['contact']['phone']
    sms = settings['sms']
    if sms['provider'] == 'aliyun':
        return aliyun_send(dict(sms['aliyun'], phone=phone)), sms_endpoint_reachable
    gateway = sms['https_gateway']
    endpoint = urlsplit(gateway['url'])
    headers = {'Content-Type': 'application/json'}
    if gateway.get('bearer_token'):
        headers['Authorization'] = 'Bearer ' + gateway['bearer_token']
    path = endpoint.path or '/'
    if endpoint.query:
        path += '?' + endpoint.query

    def send(event_id, detected):
        local = detected.astimezone(ZoneInfo('Asia/Shanghai'))
        body = dict(event_id=event_id, phone=phone, detected_at=local.isoformat(),
                    message=f'倒地提醒：机器人在 {local:%Y-%m-%d %H:%M} 检测到有人倒地，请及时联系确认。')
        conn = HTTPSConnection(endpoint.hostname, endpoint.port or 443, timeout=10)
        try:
            conn.request('POST', path, json.dumps(body, ensure_ascii=False).encode(), headers)
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                raise ValueError('Gateway submission unconfirmed')
            raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError('Gateway response too large')
            result = json.loads(raw)
            if result.get('accepted') is True:
                return 'OK', str(result.get('id') or '')
            if result.get('accepted') is False:
                return 'GATEWAY_REJECTED', ''
            raise ValueError('Gateway returned no decision')
        finally:
            conn.close()

    return send, partial(sms_endpoint_reachable, endpoint.hostname, endpoint.port or 443)


def consume(database, event, send):
    event_id = event['event_id']
    if not re.fullmatch(r'[0-9a-f]{32}', event_id):
        raise ValueError('Invalid fall event ID')
    detected = datetime.fromisoformat(event['detected_at'])
    if detected.tzinfo is None:
        raise ValueError('Missing event timezone')
    with database:
        claim = database.execute("INSERT OR IGNORE INTO submissions VALUES (?, ?, 'pending', '')",
                                 (event_id, detected.isoformat()))
    if claim.rowcount != 1:
        return 'already_recorded'
    try:
        code, biz_id = send(event_id, detected)
        state = {'OK': 'accepted', 'SIMULATED': 'simulated'}.get(code, 'rejected')
    except Exception as error:
        # Transport errors may occur after delivery to the provider. Keep the reservation.
        code, biz_id, state = type(error).__name__, '', 'unknown'
    with database:
        database.execute("UPDATE submissions SET status=?, biz_id=? WHERE event_id=?",
                         (state, biz_id, event_id))
    print('SMS_PROVIDER', event_id, state, code, flush=True)
    return state




def sms_endpoint_reachable(host='dysmsapi.aliyuncs.com', port=443):
    with socket.create_connection((host, port), timeout=4) as tcp:
        with ssl.create_default_context().wrap_socket(tcp, server_hostname=host):
            return True


class RobotNotifier:
    def __init__(self, database_path, send, check_network=None):
        self.path = database_path
        self.send = send
        self.check_network = check_network
        self.accepted = Event()
        self.db = sqlite3.connect(database_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS submissions (
                event_id TEXT PRIMARY KEY, detected_at TEXT, status TEXT, biz_id TEXT
            );
            CREATE TABLE IF NOT EXISTS falls (
                event_id TEXT PRIMARY KEY, detected_at TEXT NOT NULL, box TEXT NOT NULL,
                active INTEGER NOT NULL, decision TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_fall ON falls(active) WHERE active=1;
        """)
        self.questions = deque(dict(row) for row in self.db.execute(
            "SELECT event_id, detected_at FROM falls WHERE decision='asking' ORDER BY rowid"))
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='t1-sms')
        self.tasks = {}
        if self.db.execute("""SELECT 1 FROM falls JOIN submissions USING(event_id)
                              WHERE active=1 AND status='accepted'""").fetchone():
            self.accepted.set()
        self.retry_unsubmitted()

    def retry_unsubmitted(self):
        self.tasks = {key: task for key, task in self.tasks.items() if not task.done()}
        # No provider request has been attempted for rows without a submission.
        for row in self.db.execute("""SELECT f.event_id, f.detected_at FROM falls f
                                     LEFT JOIN submissions s USING(event_id)
                                     WHERE f.decision IN ('yes','no_reply') AND s.event_id IS NULL"""):
            if row['event_id'] not in self.tasks:
                self.tasks[row['event_id']] = self.worker.submit(self._deliver, dict(row))

    @classmethod
    def from_config(cls, case_once=False, settings=None):
        os.umask(0o077)
        if settings is None:
            send, probe = aliyun_send(load_config(CONFIG_PATH)), sms_endpoint_reachable
        else:
            send, probe = sender_for(settings)
        path = CONFIG_PATH.with_name('sms-submissions.sqlite')
        if case_once:
            directory = CONFIG_PATH.parent / 'cases'
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = directory / (uuid4().hex + '.sqlite')
            path.touch(mode=0o600, exist_ok=False)
            print('CASE_LEDGER', path.name, flush=True)
        return cls(path, send, probe)

    def active(self):
        return self.db.execute('SELECT * FROM falls WHERE active=1').fetchone()

    def begin(self, box):
        if self.active() is not None:
            return
        event = {'event_id': uuid4().hex, 'detected_at': datetime.now(timezone.utc).isoformat()}
        with self.db:
            self.db.execute("INSERT INTO falls VALUES (?, ?, ?, 1, 'asking')",
                            (event['event_id'], event['detected_at'], json.dumps(box)))
        self.questions.append(event)

    def recovered_upright(self):
        with self.db:
            self.db.execute('UPDATE falls SET active=0 WHERE active=1')

    def result(self, event, decision):
        with self.db:
            updated = self.db.execute("UPDATE falls SET decision=? WHERE event_id=? AND decision='asking'",
                                      (decision, event['event_id']))
        if updated.rowcount and decision in ('yes', 'no_reply'):
            self.retry_unsubmitted()

    def _deliver(self, event):
        try:
            if self.check_network:
                try:
                    self.check_network()
                except OSError as error:
                    print('ROBOT_SMS_WAITING_NETWORK', event['event_id'], type(error).__name__, flush=True)
                    return
            with sqlite3.connect(self.path) as db:
                state = consume(db, event, self.send)
                if state == 'already_recorded':
                    state = db.execute('SELECT status FROM submissions WHERE event_id=?',
                                       (event['event_id'],)).fetchone()[0]
            if state == 'accepted':
                self.accepted.set()
            print('ROBOT_SMS', event['event_id'], state, flush=True)
        except Exception as error:
            print('ROBOT_SMS_ERROR', event['event_id'], type(error).__name__, flush=True)

    def close(self):
        self.worker.shutdown(wait=True)
        self.db.close()


if __name__ == '__main__':
    # Replay archived diagnostic streams only against a simulated sender.
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Offline replay. Live SMS runs inside t1-fall-watch on Jetson.')
    parser.add_argument('--dry-run', required=True, action='store_true')
    parser.parse_args()
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE submissions(event_id TEXT PRIMARY KEY, detected_at TEXT, status TEXT, biz_id TEXT)')
        for line in sys.stdin:
            if line.startswith('FAMILY_SMS '):
                event = json.loads(line.removeprefix('FAMILY_SMS '))
                consume(db, event, lambda *_: ('SIMULATED', ''))
