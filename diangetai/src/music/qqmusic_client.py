"""点歌台自己的 QQ 账号、检索与取流逻辑；语音和音频互斥由框架管理。"""
import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
import time
import json
import logging
import urllib.parse

from primebuddy.config.storage import read_json, write_json, skill_data
from primebuddy.core.timing import measure

logger = logging.getLogger('primebuddy.skills.diangetai.qqmusic_client')


def load_credential(data=None):
    from qqmusic_api import Credential
    saved = read_json((data or skill_data('diangetai')) / 'credential.json')
    return Credential.model_validate(saved) if saved else None


def save_credential(credential, data=None):
    write_json((data or skill_data('diangetai')) / 'credential.json', json.loads(credential.model_dump_json()))


async def check_login(data=None):
    from qqmusic_api import Client
    credential = load_credential(data)
    if credential is None:
        return False
    async with Client(credential=credential) as client:
        return not await asyncio.wait_for(client.login.check_expired(), 30)


class SkillError(Exception):
    pass


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
    async with (nullcontext(client) if client is not None else Client(credential=load_credential())) as client:
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
    from qqmusic_api.core.exceptions import CredentialExpiredError
    credential = load_credential()
    if credential is None:
        raise SkillError('获取歌曲资源前请先运行 primebuddy skill configure diangetai 完成登录。')
    # 搜索和资源地址属于同一次点歌，复用连接池，减少重复DNS/TLS建连。
    # 客户端仍限定在当前事件循环内，不跨 asyncio.run 复用已关闭的会话。
    with measure('QQ客户端创建'):
        context = Client(credential=credential)
    # 保持正常关闭语义，但单独记录它，避免把收尾误记成搜索耗时。
    close_started = None
    try:
        async with context as client:
            try:
                artist, title = intent['artist'], intent['song']
                songs = await search(SearchQuery(title or artist, 'song' if title else 'artist'), client=client)
                if artist and title:
                    expected = ''.join(artist.split()).casefold()
                    songs = [s for s in songs if any(''.join(a.name.split()).casefold() == expected for a in s.singer)]
                    if not songs:
                        raise SkillError('未找到指定歌手的这首歌，未自动改播其他版本。')
                song = songs[0]
                url = await song_url(song, client=client)
                return song, url
            finally:
                close_started = time.monotonic()
    except CredentialExpiredError:
        # 搜索成功不代表登录仍有效，取流也可能被服务端拒绝。
        raise SkillError('QQ 音乐登录已过期，请运行 primebuddy skill configure diangetai 重新扫码登录。') from None
    finally:
        if close_started is not None:
            logger.info('STAGE_TIME %s', json.dumps({'stage': 'QQ连接关闭',
                  'seconds': round(time.monotonic() - close_started, 3)}, ensure_ascii=False))


async def song_url(song, client=None):
    from qqmusic_api import Client
    from qqmusic_api.modules.song import SongFileInfo, SongFileType
    if client is None:
        credential = load_credential()
        if credential is None:
            raise SkillError('获取歌曲资源前请先运行 primebuddy skill configure diangetai 完成登录。')
        context = Client(credential=credential)
    else:
        context = nullcontext(client)
    async with context as client:
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
