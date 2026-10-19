"""QQ 登录、校验和取流共用私有的持久设备标识。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as Item
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Device:
    version: dict = field(default_factory=dict)
    open_udid: str = field(default_factory=lambda: uuid4().hex)


class DeviceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/music/qqmusic_client.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'account'
        self.sdk = patch.dict('sys.modules', {'qqmusic_api.utils.device': Item(Device=Device)})
        self.sdk.start()
        self.addCleanup(self.sdk.stop)

    def test_first_device_has_private_file_and_directory(self):
        path = Path(self.module.device_path(self.directory))
        saved = json.loads(path.read_text())
        self.assertTrue(saved['open_udid'])
        self.assertEqual(saved['version'], {})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_existing_identity_is_preserved_and_permissions_repaired(self):
        path = Path(self.module.device_path(self.directory))
        saved = path.read_bytes()
        path.chmod(0o644)
        self.directory.chmod(0o755)
        with patch.dict('sys.modules', {'qqmusic_api.utils.device': Item(Device=Mock(side_effect=AssertionError))}):
            self.assertEqual(self.module.device_path(self.directory), str(path))
        self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_parallel_initialization_keeps_one_identity(self):
        create = Mock(side_effect=Device)
        with patch.dict('sys.modules', {'qqmusic_api.utils.device': Item(Device=create)}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(pool.map(lambda _: self.module.device_path(self.directory), range(16)))
        self.assertEqual(len(set(paths)), 1)
        create.assert_called_once()

    def test_sdk_in_place_session_updates_keep_private_permissions(self):
        path = Path(self.module.device_path(self.directory))
        identity = json.loads(path.read_text())
        updated = dict(identity, session_sid='private-test-session', session_vkey='private-test-key')
        path.write_bytes(json.dumps(updated).encode())
        self.module.device_path(self.directory)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), updated)

    def client(self):
        self.song = Item(name='测试歌', title='测试歌', mid='test-song',
                         singer=[Item(name='测试歌手')], file=Item(media_mid='test-media'))
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login.check_expired = AsyncMock(return_value=False)
        client.search.search_by_type = AsyncMock(return_value=Item(song=[self.song]))
        client.song.get_song_urls = AsyncMock(return_value=Item(data=[
            Item(mid='test-song', result=0, purl='test-song.aac')]))
        client.song.get_cdn_dispatch = AsyncMock(return_value=Item(retcode=0, sip=['https://test.qq.com/']))
        self.factory = Mock(return_value=client)
        modules = {
            'qqmusic_api': Item(Client=self.factory),
            'qqmusic_api.core.exceptions': Item(CredentialExpiredError=type('CredentialExpiredError', (Exception,), {}),
                                                NetworkError=type('NetworkError', (Exception,), {})),
            'niquests.exceptions': Item(ConnectionError=ConnectionError, Timeout=TimeoutError),
            'qqmusic_api.modules.song': Item(SongFileInfo=Item,
                SongFileType=Item(ACC_48=Item(name='ACC_48'), MP3_128=Item(name='MP3_128'))),
        }
        sdk = patch.dict('sys.modules', modules)
        sdk.start()
        self.addCleanup(sdk.stop)
        return client

    def test_check_login_uses_explicit_data_and_same_persistent_device(self):
        self.client()
        credential = object()
        with patch.dict(os.environ, DIANGETAI_DATA=str(self.directory.parent / 'other-account')), \
                patch.object(self.module, 'load_credential', return_value=credential) as load:
            self.assertTrue(asyncio.run(self.module.check_login(self.directory)))
            self.assertTrue(asyncio.run(self.module.check_login(self.directory)))
        load.assert_called_with(self.directory)
        self.assertEqual(self.factory.call_count, 2)
        for call in self.factory.call_args_list:
            self.assertEqual(call.kwargs, {'credential': credential,
                                          'device_path': str(self.directory / 'device.json')})
        self.assertFalse((self.directory.parent / 'other-account').exists())

    def test_select_song_reuses_client_and_persistent_default_device(self):
        client = self.client()
        with patch.dict(os.environ, DIANGETAI_DATA=str(self.directory)), \
                patch.object(self.module, 'load_credential', return_value=object()):
            for _ in range(2):
                song, url = asyncio.run(self.module.select_song({'artist': '测试歌手', 'song': '测试歌'}))
                self.assertIs(song, self.song)
                self.assertEqual(url, 'https://test.qq.com/test-song.aac')
        self.assertEqual(self.factory.call_count, 2)
        for call in self.factory.call_args_list:
            self.assertEqual(call.kwargs['device_path'], str(self.directory / 'device.json'))
        self.assertEqual(client.search.search_by_type.await_count, 2)
        self.assertEqual(client.song.get_song_urls.await_count, 2)

    def test_standalone_search_and_url_use_same_default_device(self):
        self.client()
        with patch.dict(os.environ, DIANGETAI_DATA=str(self.directory)), \
                patch.object(self.module, 'load_credential', return_value=object()):
            asyncio.run(self.module.search(self.module.SearchQuery('测试歌', 'song')))
            saved = (self.directory / 'device.json').read_bytes()
            asyncio.run(self.module.song_url(self.song))
        self.assertEqual(self.factory.call_count, 2)
        for call in self.factory.call_args_list:
            self.assertEqual(call.kwargs['device_path'], str(self.directory / 'device.json'))
        self.assertEqual((self.directory / 'device.json').read_bytes(), saved)
