"""点歌台自己的 QQ 账号、检索与取流逻辑；语音和音频互斥由框架管理。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import fcntl
import time
import json
import logging
import urllib.parse

from ..support import read_json, write_json, skill_data
from ..support import measure

logger = logging.getLogger('primebuddy.skills.diangetai.qqmusic_client')


def device_path(data=None):
    directory = data or skill_data('diangetai')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / 'device.json'
    with (directory / 'device.lock').open('a') as lock:
        (directory / 'device.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            path.chmod(0o600)
        else:
            # 0.7.3 默认只在内存生成指纹；先私有地保存完整结构，
            # 后续 SDK 更新会话时原地写入，保留文件的 600 权限。
            from qqmusic_api.utils.device import Device
            write_json(path, asdict(Device()))
    return str(path)


def load_credential(data=None):
    from qqmusic_api import Credential
    saved = read_json((data or skill_data('diangetai')) / 'credential.json')
    return Credential.model_validate(saved) if saved else None


def save_credential(credential, data=None):
    write_json((data or skill_data('diangetai')) / 'credential.json', json.loads(credential.model_dump_json()))


async def check_login(data=None):
    async with account_lock(data) as data:
        return await _check_login(data)


async def _check_login(data, *, refresh=True):
    """调用者持有账号锁；扫码前也重新读盘，避免覆盖另一进程的新登录。"""
    from qqmusic_api import Client
    from qqmusic_api.core.exceptions import CredentialExpiredError
    credential = load_credential(data)
    if credential is None:
        return False
    async with Client(credential=credential, device_path=device_path(data)) as client:
        try:
            expired = await asyncio.wait_for(client.login.check_expired(), 30)
        except CredentialExpiredError:
            expired = True
        if not expired:
            return True
        if not refresh or not await refresh_login(client, data):
            return False
        # 刷新已返回新凭据，后续检查失败不能再判为需要扫码。
        try:
            expired = await asyncio.wait_for(client.login.check_expired(), 30)
        except CredentialExpiredError:
            expired = True
        if expired:
            raise SkillError('QQ 音乐续期后仍未通过鉴权，请稍后重试。')
        return True


class SkillError(Exception):
    pass


class SkillNetworkError(SkillError):
    pass


class SkillLoginBlockedError(SkillError):
    pass


@asynccontextmanager
async def account_lock(data=None):
    directory = data or skill_data('diangetai')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / 'account.lock'
    with path.open('a') as lock:
        path.chmod(0o600)
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SkillError('QQ 音乐账号正在被其他任务使用，请稍后重试。') from None
                await asyncio.sleep(.05)
        try:
            yield directory
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def login_error_message(code):
    if code == 20279:
        return 'QQ 音乐登录设备数量已达上限（20279），请在 QQ 音乐 App 处理设备记录后重试。'
    if code == 104604:
        return 'QQ 音乐登录操作过于频繁（104604），请稍后重试。'
    if code in (20277, 20278, 20450):
        return f'QQ 音乐账号受限（{code}），请在 QQ 音乐 App 核实账号状态。'
    return f'QQ 音乐暂时无法续期（code={code}），请稍后重试。'


async def refresh_login(client, data=None):
    """在账号锁内刷新一次；只把明确的授权失效交给扫码流程。"""
    from qqmusic_api.core.exceptions import CredentialRefreshError
    credential = client.credential
    if not (credential.refresh_key or credential.refresh_token):
        return False
    try:
        updated = await asyncio.wait_for(client.login.refresh_credential(), 30)
    except CredentialRefreshError as error:
        if error.code in (1000, 104401, 104400):
            return False
        raise SkillLoginBlockedError(login_error_message(error.code)) from None
    if not updated.musicid or not updated.musickey or updated.musicid != credential.musicid:
        raise SkillError('QQ 音乐续期返回的账号信息无效，原登录态已保留。')
    # SDK 返回新对象，不会更新 Client。先原子落盘，避免后续网络失败丢失轮换后的令牌。
    save_credential(updated, data)
    client.credential = updated
    return True


async def retry_expired(operation, client, data=None):
    from qqmusic_api.core.exceptions import CredentialExpiredError
    try:
        return await operation()
    except CredentialExpiredError:
        if not await refresh_login(client, data):
            raise SkillError('QQ 音乐登录及刷新凭据已失效，请运行 python3 main.py configure 重新扫码登录。') from None
    try:
        return await operation()
    except CredentialExpiredError:
        raise SkillError('QQ 音乐续期后仍未通过鉴权，请稍后重试。') from None


@dataclass(frozen=True)
class SearchQuery:
    keyword: str
    kind: str  # artist：歌手；song：歌名


def matching_songs(songs, intent: SearchQuery):
    def equal(a, b):
        return ''.join(a.split()).casefold() == ''.join(b.split()).casefold()
    artist = [s for s in songs if any(equal(a.name, intent.keyword) for a in s.singer)]
    title = [s for s in songs if equal(s.name, intent.keyword) or equal(s.title, intent.keyword)]
    if intent.kind == 'artist':
        return artist
    return title


async def search(intent, client=None):
    from qqmusic_api import Client
    if client is None:
        async with account_lock() as data:
            async with Client(credential=load_credential(data), device_path=device_path(data)) as client:
                return await retry_expired(lambda: search(intent, client=client), client, data)
    with measure('QQ搜索', '正在搜索 QQ 音乐…'):
        result = await asyncio.wait_for(client.search.search_by_type(keyword=intent.keyword, num=10, highlight=False), 30)
    songs = matching_songs(result.song, intent)
    if not songs:
        if intent.kind == 'artist':
            if matching_songs(result.song, SearchQuery(intent.keyword, 'song')):
                raise SkillError('没有找到匹配的歌手，但找到了同名歌曲。若要按歌名播放，请说“播放歌曲”加歌名。')
            raise SkillError('没有找到匹配的歌手，请确认歌手名后再试。')
        raise SkillError('没有找到匹配的歌曲，请确认歌名后再试。')
    return songs[:5]


def validate_url(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ''
    if parts.scheme not in {'http', 'https'} or parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise SkillError('音乐资源地址格式不符合预期。')
    if not any(host == d or host.endswith('.' + d) for d in ('qq.com', 'qqmusic.com')):
        raise SkillError('音乐资源未指向预期的 QQ 音乐域名。')
    return url



async def select_song(intent):
    from qqmusic_api import Client
    from qqmusic_api.core.exceptions import NetworkError
    from niquests.exceptions import ConnectionError as TransportConnectionError, Timeout
    # 保持正常关闭语义，但单独记录它，避免把收尾误记成搜索耗时。
    close_started = None
    try:
        async with account_lock() as data:
            credential = load_credential(data)
            if credential is None:
                raise SkillError('获取歌曲资源前请先运行 python3 main.py configure 完成登录。')
            # 搜索、取流和续期复用同一客户端；锁内读盘，避免并发使用旧凭据。
            with measure('QQ客户端创建'):
                context = Client(credential=credential, device_path=device_path(data))
            async with context as client:
                try:
                    async def select():
                        artist, title = intent['artist'], intent['song']
                        songs = await search(SearchQuery(title or artist, 'song' if title else 'artist'), client=client)
                        if artist and title:
                            expected = ''.join(artist.split()).casefold()
                            songs = [s for s in songs if any(''.join(a.name.split()).casefold() == expected for a in s.singer)]
                            if not songs:
                                raise SkillError('未找到指定歌手的这首歌，未自动改播其他版本。')
                        song = songs[0]
                        return song, await song_url(song, client=client)
                    return await retry_expired(select, client, data)
                finally:
                    close_started = time.monotonic()
    except (NetworkError, TransportConnectionError, Timeout, TimeoutError) as error:
        # 只转换 SDK/传输层网络异常；HTTP 状态、鉴权和业务拒绝保留原有处理。
        if (isinstance(error, NetworkError) and error.__cause__ is not None
                and not isinstance(error.__cause__, (TransportConnectionError, Timeout, TimeoutError))):
            raise
        raise SkillNetworkError('网络连接出现了问题，请重新连接网络。') from None
    finally:
        if close_started is not None:
            logger.info('STAGE_TIME %s', json.dumps({'stage': 'QQ连接关闭',
                  'seconds': round(time.monotonic() - close_started, 3)}, ensure_ascii=False))


async def song_url(song, client=None):
    from qqmusic_api import Client
    from qqmusic_api.modules.song import SongFileInfo, SongFileType
    if client is None:
        async with account_lock() as data:
            credential = load_credential(data)
            if credential is None:
                raise SkillError('获取歌曲资源前请先运行 python3 main.py configure 完成登录。')
            async with Client(credential=credential, device_path=device_path(data)) as client:
                return await retry_expired(lambda: song_url(song, client=client), client, data)
    # 优先低码率完整资源；只有该格式缺失才回退，权限拒绝不能靠换格式绕过。
    for file_type in (SongFileType.ACC_48, SongFileType.MP3_128):
        with measure('QQ资源地址-' + file_type.name):
            result = await asyncio.wait_for(client.song.get_song_urls([
                SongFileInfo(mid=song.mid, media_mid=song.file.media_mid, file_type=file_type)
            ]), 30)
        info = next((x for x in result.data if x.mid == song.mid), None)
        if info is None or info.result != 0:
            code = info.result if info else 'missing'
            raise SkillError(f'该歌曲未取得可播放资源（result={code}）；未尝试绕过权限。')
        if info.purl:
            logger.info('QQ_AUDIO_QUALITY %s', file_type.name)
            break
    if not info.purl:
        raise SkillError('该歌曲未取得可播放资源（result=0）；资源格式不可用。')
    with measure('QQ-CDN分配'):
        cdn = await asyncio.wait_for(client.song.get_cdn_dispatch(), 30)
    if cdn.retcode != 0 or not cdn.sip:
        raise SkillError('未取得可用的音乐 CDN。')
    return validate_url(urllib.parse.urljoin(cdn.sip[0], info.purl))
