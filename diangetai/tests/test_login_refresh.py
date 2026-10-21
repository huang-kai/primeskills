"""过期登录自动续期、设备复用及配置/取流之间的凭据轮换。"""
import asyncio
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Item
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]


class CredentialExpiredError(Exception):
    pass


class CredentialRefreshError(Exception):
    def __init__(self, code):
        super().__init__('private-token-and-url')
        self.code = code


class NetworkError(Exception):
    pass


@dataclass
class Credential:
    musicid: int = 123
    musickey: str = 'old'
    refresh_key: str = 'old-refresh-key'
    refresh_token: str = 'old-refresh-token'

    def model_dump_json(self):
        return json.dumps(asdict(self))

    @classmethod
    def model_validate(cls, value):
        return cls(**value)


@dataclass
class Device:
    version: dict = field(default_factory=dict)
    open_udid: str = field(default_factory=lambda: uuid4().hex)


class LoginRefreshTests(unittest.TestCase):
    def setUp(self):
        self.music = load_entry(ROOT, 'src/music/qqmusic_client.py')
        self.setup = load_entry(ROOT, 'src/setup.py')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.old = Credential()
        self.new = Credential(musickey='new', refresh_key='new-key', refresh_token='new-token')
        self.refresh = AsyncMock(return_value=self.new)
        self.song = Item(name='测试歌', title='测试歌', singer=[Item(name='测试歌手')],
                         mid='test-song', file=Item(media_mid='test-media'))
        self.clients = []
        self.api = Item(Credential=Credential, Client=Mock(side_effect=self.client))
        patches = [
            patch.dict(os.environ, DIANGETAI_DATA=str(self.data)),
            patch.dict('sys.modules', {
                'qqmusic_api': self.api,
                'qqmusic_api.utils.device': Item(Device=Device),
                'qqmusic_api.core.exceptions': Item(
                    CredentialExpiredError=CredentialExpiredError,
                    CredentialRefreshError=CredentialRefreshError, NetworkError=NetworkError),
                'qqmusic_api.modules.song': Item(SongFileInfo=Item, SongFileType=Item(
                    ACC_48=Item(name='ACC_48'), MP3_128=Item(name='MP3_128'))),
                'niquests.exceptions': Item(ConnectionError=ConnectionError, Timeout=TimeoutError),
            }),
        ]
        for context in patches:
            context.start()
            self.addCleanup(context.stop)
        self.music.save_credential(self.old, self.data)
        self.device = Path(self.music.device_path(self.data)).read_bytes()

    def client(self, **kwargs):
        client = MagicMock()
        client.credential = kwargs.get('credential')
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login.check_expired = AsyncMock(
            side_effect=lambda: client.credential.musickey == 'old')
        client.login.refresh_credential = self.refresh
        client.search.search_by_type = AsyncMock(return_value=Item(song=[self.song]))
        async def urls(*args):
            if client.credential.musickey == 'old':
                raise CredentialExpiredError('private-token-and-url')
            return Item(data=[Item(mid='test-song', result=0, purl='song.aac')])
        client.song.get_song_urls = AsyncMock(side_effect=urls)
        client.song.get_cdn_dispatch = AsyncMock(
            return_value=Item(retcode=0, sip=['https://test.qq.com/']))
        self.clients.append(client)
        return client

    def assert_saved(self, expected):
        self.assertEqual(self.music.load_credential(self.data), expected)
        self.assertEqual((self.data / 'credential.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.data / 'device.json').read_bytes(), self.device)
        for call in self.api.Client.call_args_list:
            self.assertEqual(call.kwargs['device_path'], str(self.data / 'device.json'))

    def test_check_expired_refreshes_and_persists_rotated_credentials(self):
        self.assertTrue(asyncio.run(self.music.check_login(self.data)))
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)
        self.assertEqual(self.clients[0].credential, self.new)
        self.assertTrue(asyncio.run(self.music.check_login(self.data)))
        self.refresh.assert_awaited_once()

    def test_valid_login_does_not_refresh(self):
        self.music.save_credential(self.new, self.data)
        self.assertTrue(asyncio.run(self.music.check_login(self.data)))
        self.refresh.assert_not_awaited()
        self.assert_saved(self.new)

    def test_missing_login_requests_initial_scan_without_refresh(self):
        (self.data / 'credential.json').unlink()
        self.assertFalse(asyncio.run(self.music.check_login(self.data)))
        self.refresh.assert_not_awaited()

    def test_only_expired_refresh_authorization_returns_false(self):
        for code in (1000, 104401, 104400):
            with self.subTest(code=code):
                self.refresh.side_effect = CredentialRefreshError(code)
                self.assertFalse(asyncio.run(self.music.check_login(self.data)))
                self.assert_saved(self.old)
        self.assertEqual(self.refresh.await_count, 3)

    def test_absent_refresh_fields_require_scan(self):
        self.old = Credential(refresh_key='', refresh_token='')
        self.music.save_credential(self.old, self.data)
        self.assertFalse(asyncio.run(self.music.check_login(self.data)))
        self.refresh.assert_not_awaited()
        self.assert_saved(self.old)

    def test_refresh_business_errors_do_not_request_scan_or_leak_secrets(self):
        for code in (20279, 104604, 20277, 20278, 20450, 20261, 99999):
            with self.subTest(code=code):
                self.refresh.side_effect = CredentialRefreshError(code)
                with self.assertRaises(self.music.SkillError) as caught:
                    asyncio.run(self.music.check_login(self.data))
                self.assertNotIn('扫码', str(caught.exception))
                self.assertNotIn('private', str(caught.exception))
                self.assertIn(str(code), str(caught.exception))
                self.assert_saved(self.old)

    def test_refresh_network_failure_keeps_old_login_and_propagates(self):
        for error in (NetworkError('private'), ConnectionError('private'), TimeoutError('private')):
            with self.subTest(error=type(error).__name__):
                self.refresh.side_effect = error
                with self.assertRaises(type(error)):
                    asyncio.run(self.music.check_login(self.data))
                self.assert_saved(self.old)

    def test_post_refresh_network_failure_preserves_rotated_credentials(self):
        def factory(**kwargs):
            client = self.client(**kwargs)
            client.login.check_expired.side_effect = [True, NetworkError('private')]
            return client
        self.api.Client.side_effect = factory
        with self.assertRaises(NetworkError):
            asyncio.run(self.music.check_login(self.data))
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)

    def test_invalid_refresh_response_does_not_overwrite_existing_login(self):
        for updated in (Credential(musicid=0), Credential(musickey=''),
                        Credential(musicid=456)):
            with self.subTest(updated=updated.musicid):
                self.refresh.return_value = updated
                with self.assertRaises(self.music.SkillError) as caught:
                    asyncio.run(self.music.check_login(self.data))
                self.assertNotIn('扫码', str(caught.exception))
                self.assert_saved(self.old)

    def test_failed_save_does_not_retry_request_with_unpersisted_credentials(self):
        with patch.object(self.music, 'save_credential', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.assertEqual(self.clients[0].song.get_song_urls.await_count, 1)
        self.assert_saved(self.old)

    def test_select_song_retries_expired_resource_once_with_new_credential(self):
        song, url = asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.assertIs(song, self.song)
        self.assertEqual(url, 'https://test.qq.com/song.aac')
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(self.clients[0].song.get_song_urls.await_count, 2)

    def test_expired_search_is_also_refreshed(self):
        def factory(**kwargs):
            client = self.client(**kwargs)
            async def search(**args):
                if client.credential.musickey == 'old':
                    raise CredentialExpiredError('private')
                return Item(song=[self.song])
            client.search.search_by_type.side_effect = search
            return client
        self.api.Client.side_effect = factory
        asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)

    def test_expired_refresh_during_selection_prompts_configuration(self):
        self.refresh.side_effect = CredentialRefreshError(104401)
        with self.assertRaisesRegex(self.music.SkillError, 'configure.*扫码') as caught:
            asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.assertNotIn('private', str(caught.exception))
        self.refresh.assert_awaited_once()
        self.assert_saved(self.old)

    def test_second_auth_rejection_stops_without_refresh_loop_or_scan(self):
        def factory(**kwargs):
            client = self.client(**kwargs)
            client.song.get_song_urls.side_effect = CredentialExpiredError('private')
            return client
        self.api.Client.side_effect = factory
        with self.assertRaises(self.music.SkillError) as caught:
            asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.assertNotIn('扫码', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))
        self.refresh.assert_awaited_once()
        self.assertEqual(self.clients[0].song.get_song_urls.await_count, 2)
        self.assert_saved(self.new)

    def test_standalone_resource_fetch_refreshes(self):
        self.assertEqual(asyncio.run(self.music.song_url(self.song)), 'https://test.qq.com/song.aac')
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)

    def test_selection_network_failure_during_refresh_has_network_message(self):
        self.refresh.side_effect = NetworkError('private')
        with self.assertRaises(self.music.SkillNetworkError) as caught:
            asyncio.run(self.music.select_song({'artist': '', 'song': '测试歌'}))
        self.assertNotIn('扫码', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(self.clients[0].song.get_song_urls.await_count, 1)
        self.assert_saved(self.old)

    def test_setup_refreshes_without_showing_qr(self):
        ui = Mock()
        ui.ask.return_value = '30'
        ui.choose.return_value = 1
        with patch.object(self.setup, 'login', new=AsyncMock()) as login:
            self.setup.setup(ui, self.data, {'play_seconds': 30})
        login.assert_not_awaited()
        self.assertFalse(any('扫码' in call.args[0] for call in ui.confirm.call_args_list))
        self.assert_saved(self.new)

    def test_setup_offers_scan_only_after_refresh_auth_expires(self):
        self.refresh.side_effect = CredentialRefreshError(1000)
        ui = Mock()
        ui.confirm.return_value = False
        with patch.object(self.setup, 'login', new=AsyncMock()) as login:
            self.assertIsNone(self.setup.setup(ui, self.data, {}))
        self.assertIn('扫码', ui.confirm.call_args.args[0])
        login.assert_not_awaited()
        self.refresh.assert_awaited_once()

    def test_setup_falls_back_to_one_qr_without_retrying_expired_refresh(self):
        self.refresh.side_effect = CredentialRefreshError(1000)
        ui = Mock()
        ui.ask.return_value = '30'
        ui.confirm.return_value = True
        session = Item(
            get_qrcode=AsyncMock(return_value=Item(save=lambda data: data / 'qr.png')),
            wait_qrcode_login=AsyncMock(return_value=self.new))
        qr = Mock(return_value=session)
        with patch.dict('sys.modules', {
            'qqmusic_api.models.login': Item(QRLoginType=Item(QQ='qq')),
            'qqmusic_api.modules.login_utils': Item(QRCodeLoginSession=qr),
        }), patch.object(self.setup, 'show_qr', return_value=nullcontext()):
            self.assertIsNotNone(self.setup.setup(ui, self.data, {}))
        self.refresh.assert_awaited_once()
        qr.assert_called_once()
        self.assert_saved(self.new)

    def test_setup_device_limit_stops_without_qr_or_retry_prompt(self):
        self.refresh.side_effect = CredentialRefreshError(20279)
        ui = Mock()
        ui.ask.return_value = '30'
        ui.choose.return_value = 1
        with patch.object(self.setup, 'login', new=AsyncMock()) as login:
            self.assertIsNone(self.setup.setup(ui, self.data, {}))
        ui.confirm.assert_not_called()
        ui.choose.assert_not_called()
        login.assert_not_awaited()
        self.assertIn('设备', str(ui.info.call_args_list))

    def test_setup_network_failure_does_not_offer_scan(self):
        self.refresh.side_effect = NetworkError('private')
        ui = Mock()
        ui.choose.return_value = 1
        with patch.object(self.setup, 'login', new=AsyncMock()) as login:
            self.assertIsNone(self.setup.setup(ui, self.data, {}))
        ui.confirm.assert_not_called()
        login.assert_not_awaited()
        self.assertNotIn('private', str(ui.info.call_args_list))
        self.assert_saved(self.old)

    def test_qr_login_rechecks_new_credentials_saved_by_another_process(self):
        self.music.save_credential(self.new, self.data)
        qr = Mock(side_effect=AssertionError('不能再次创建二维码'))
        with patch.dict('sys.modules', {
            'qqmusic_api.models.login': Item(QRLoginType=Item(QQ='qq')),
            'qqmusic_api.modules.login_utils': Item(QRCodeLoginSession=qr),
        }):
            asyncio.run(self.setup.login(Mock(), self.data))
        qr.assert_not_called()
        self.refresh.assert_not_awaited()
        self.assert_saved(self.new)

    def test_qr_device_limit_stops_setup_without_retry(self):
        ui = Mock()
        with patch.object(self.setup, 'check_login', new=AsyncMock(return_value=False)), \
                patch.object(self.setup, 'login', new=AsyncMock(side_effect=CredentialRefreshError(20279))):
            self.assertIsNone(self.setup.setup(ui, self.data, {}))
        ui.choose.assert_not_called()
        self.assertIn('设备', str(ui.info.call_args_list))
        self.assertNotIn('private', str(ui.info.call_args_list))

    def test_concurrent_checks_refresh_once_and_read_latest_saved_login(self):
        async def run():
            entered = asyncio.Event()
            release = asyncio.Event()
            async def refresh():
                entered.set()
                await release.wait()
                return self.new
            self.refresh.side_effect = refresh
            first = asyncio.create_task(self.music.check_login(self.data))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 1)
                second = asyncio.create_task(self.music.check_login(self.data))
                await asyncio.sleep(.05)
                self.assertEqual(self.refresh.await_count, 1)
                release.set()
                self.assertEqual(await asyncio.wait_for(asyncio.gather(first, second), 2), [True, True])
            finally:
                release.set()
                for task in (first, second):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
        asyncio.run(run())
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)

    def test_selection_waits_for_configuration_refresh_and_uses_new_login(self):
        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            async def refresh():
                entered.set()
                await release.wait()
                return self.new
            self.refresh.side_effect = refresh
            configuring = asyncio.create_task(self.music.check_login(self.data))
            selection = None
            try:
                await asyncio.wait_for(entered.wait(), 1)
                selection = asyncio.create_task(self.music.select_song({'artist': '', 'song': '测试歌'}))
                await asyncio.sleep(.05)
                self.assertEqual(len(self.clients), 1)
                release.set()
                checked, (song, url) = await asyncio.wait_for(asyncio.gather(configuring, selection), 2)
                self.assertTrue(checked)
                self.assertIs(song, self.song)
                self.assertEqual(url, 'https://test.qq.com/song.aac')
            finally:
                release.set()
                tasks = [t for t in (configuring, selection) if t is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run(run())
        self.refresh.assert_awaited_once()
        self.assert_saved(self.new)
        self.assertEqual(self.clients[1].song.get_song_urls.await_count, 1)

    def test_cancelled_refresh_releases_lock_and_keeps_original_login(self):
        async def run():
            entered = asyncio.Event()
            async def refresh():
                entered.set()
                await asyncio.Event().wait()
            self.refresh.side_effect = refresh
            task = asyncio.create_task(self.music.check_login(self.data))
            try:
                await asyncio.wait_for(entered.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assert_saved(self.old)
            self.refresh.side_effect = None
            self.assertTrue(await asyncio.wait_for(self.music.check_login(self.data), 1))
        asyncio.run(run())
        self.assert_saved(self.new)

    def test_other_process_account_lock_is_respected_and_wait_is_cancellable(self):
        command = """import fcntl, sys
with open(sys.argv[1], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('locked', flush=True)
    sys.stdin.read(1)
"""
        async def run():
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-c', command, str(self.data / 'account.lock'),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            task = None
            try:
                self.assertEqual(await asyncio.wait_for(process.stdout.readline(), 2), b'locked\n')
                task = asyncio.create_task(self.music.check_login(self.data))
                await asyncio.sleep(.05)
                self.api.Client.assert_not_called()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 2)
            self.assertTrue(await asyncio.wait_for(self.music.check_login(self.data), 1))
        asyncio.run(run())
        self.assert_saved(self.new)
        self.assertEqual((self.data / 'account.lock').stat().st_mode & 0o777, 0o600)
