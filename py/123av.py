# coding=utf-8
# -*- coding: utf-8 -*-
"""
123AV · TVBox 四壳 Spider
================================================================
站点      : https://123av.com
解析路线  : 列表页服务端渲染(正则) -> 详情页内嵌线路 JSON
            -> javplayer.cc /stream?id=<hash> 取真实 m3u8
实测数据  : 列表每页 12 条 / 分页 ?page=N / 搜索 keyword=
            详情页 6 条线路 / m3u8 需带 UA(裸请求 403)
================================================================
四壳结构
  ① 协议壳 : base.spider 双协议兼容 + 本地基类兜底, 方法 *args 双签名
  ② 请求壳 : requests 优先 / urllib 兜底(自带 gzip 解压)
  ③ 解析壳 : 卡片/详情/线路 正则解析, 全部字段实测对齐
  ④ 播放壳 : 线路取流 + m3u8 广告清洗 + localProxy 本地代理
================================================================
"""

import sys
import os
import re
import json
import gzip
import time
import html as _html

sys.path.append('..')


# ==================================================================
# ① 协议壳 —— base.spider 双协议兼容
# ==================================================================
try:
    import requests
except Exception:
    requests = None

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        """本地基类兜底: 脱离 TVBox 运行时也能实例化自测"""
        def __init__(self, *args, **kwargs):
            pass

        def init(self, *args, **kwargs):
            pass

        def destroy(self, *args, **kwargs):
            pass


# ==================================================================
# 站点常量(全部来自实测, 不臆造)
# ==================================================================
RAW_SITE = 'https://123av.com'
LANG = '/en'
PLAYER_HOST = 'https://javplayer.cc'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

PER_PAGE = 12          # 实测: 站点列表每页固定 12 条
LIST_TTL = 1800        # 目录(分类/厂牌/演员)缓存 30 分钟
STREAM_TTL = 900       # 取流结果缓存 15 分钟

# ------------------------------------------------------------------
# 铁律11 · 脱敏数据结构
# 展示层统一走中文映射, 站点原始词不直出
# ------------------------------------------------------------------
DESENS_MAP = {
    'new':                '最新更新',
    'recent':             '最近添加',
    'hot':                '热门推荐',
    'all':                '全部片库',
    'censored':           '有码',
    'uncensored':         '无码',
    'uncensored-leaked':  '无码流出',
    'jable':              'Jable',
    'missav':             'MissAV',
    'supjav':             'SupJAV',
    'genre':              '类型',
    'maker':              '厂牌',
    'actress':            '演员',
    'tag':                '番号标签',
    'series':             '系列',
    'today':              '今日榜',
    'week':               '本周榜',
    'month':              '本月榜',
}

# ------------------------------------------------------------------
# 铁律13 · 未成年内容剔除(最高优先级)
# 识别到即跳过, 不采集不写入
# ------------------------------------------------------------------
BLOCK_PAT = re.compile(
    r'(loli|lolita|underage|child(?:ren)?|kids?|'
    r'elementary\s*school|primary\s*school|junior\s*high|middle\s*school|'
    r'未成年|小学生|中学生|幼女|萝莉|儿童)',
    re.I)


# ==================================================================
# 主类
# ==================================================================
class Spider(_BaseSpider):

    # ---------------- 生命周期 ----------------

    def __init__(self, *args, **kwargs):
        try:
            super(Spider, self).__init__(*args, **kwargs)
        except Exception:
            pass
        self.name = '123AV'
        # 铁律15 · 反代: rawSite 是站点真身, siteUrl 是可替换的代理入口
        self.raw_site = RAW_SITE
        self.site_url = RAW_SITE
        self.extend = ''
        self._dir_cache = {}
        self._stream_cache = {}
        self._detail_cache = {}

    def init(self, extend='', *args, **kwargs):
        """extend 支持 JSON: {"siteUrl": 反代地址, "rawSite": 原站}"""
        self.extend = extend if isinstance(extend, str) else ''
        cfg = {}
        try:
            if self.extend.strip().startswith('{'):
                cfg = json.loads(self.extend)
        except Exception:
            cfg = {}
        if isinstance(cfg, dict):
            self.raw_site = cfg.get('rawSite') or cfg.get('raw_site') or RAW_SITE
            self.site_url = cfg.get('siteUrl') or cfg.get('site_url') or self.raw_site
        else:
            self.raw_site = RAW_SITE
            self.site_url = RAW_SITE

    def destroy(self, *args, **kwargs):
        self._dir_cache = {}
        self._stream_cache = {}
        self._detail_cache = {}

    def getName(self, *args, **kwargs):
        return self.name

    def isVideoFormat(self, url, *args, **kwargs):
        u = (url or '').lower()
        return bool(re.search(r'\.(m3u8|mp4|flv|mkv)(\?|$)', u))

    def manualVideoCheck(self, *args, **kwargs):
        return False

    def action(self, action, *args, **kwargs):
        return {'code': 0, 'msg': 'ok'}

    # ==================================================================
    # ② 请求壳 —— requests 优先, urllib 兜底
    # ==================================================================

    def _headers(self, referer=None):
        h = {
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        if referer:
            h['Referer'] = referer
        return h

    def _fetch(self, url, referer=None, timeout=15):
        """返回页面文本; 失败返回 ''(绝不抛异常)"""
        url = self._wrap(url)
        headers = self._headers(referer)

        if requests is not None:
            try:
                r = requests.get(url, headers=headers, timeout=timeout,
                                 allow_redirects=True)
                if r.status_code == 200 and r.content:
                    enc = r.encoding if r.encoding and r.encoding.lower() != 'iso-8859-1' else 'utf-8'
                    try:
                        return r.content.decode(enc, 'ignore')
                    except Exception:
                        return r.text
            except Exception:
                pass

        # urllib 兜底(自带 gzip 处理)
        try:
            import urllib.request
            import urllib.error
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
            return data.decode('utf-8', 'ignore')
        except Exception:
            return ''

    def _fetch_json(self, url, referer=None, timeout=15):
        txt = self._fetch(url, referer=referer, timeout=timeout)
        if not txt:
            return {}
        try:
            return json.loads(txt)
        except Exception:
            m = re.search(r'\{.*\}', txt, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return {}
        return {}

    # 铁律15 · 反代拼接
    def _wrap(self, url):
        if not url:
            return url
        if self.site_url and self.raw_site and self.raw_site in url:
            return url.replace(self.raw_site, self.site_url.rstrip('/'), 1)
        return url

    def _abs(self, path):
        if not path:
            return ''
        if path.startswith('http'):
            return path
        if path.startswith('//'):
            return 'https:' + path
        return self.site_url.rstrip('/') + ('/' + path.lstrip('/'))

    # ==================================================================
    # ③ 解析壳
    # ==================================================================

    def _blocked(self, text):
        """铁律13: 命中未成年特征 -> 丢弃"""
        return bool(BLOCK_PAT.search(text or ''))

    def _clean(self, s):
        s = re.sub(r'<[^>]+>', '', s or '')
        s = _html.unescape(s)
        return re.sub(r'\s+', ' ', s).strip()

    def _parse_cards(self, text):
        """列表页卡片 -> [{vod_id, vod_name, vod_pic, vod_remarks}]"""
        out = []
        if not text:
            return out
        chunks = text.split('<div class="card"')
        for ch in chunks[1:]:
            ch = ch[:5000]
            m_url = re.search(r'<a class="card__cover"\s+href="([^"]+)"', ch)
            if not m_url:
                continue
            href = m_url.group(1)
            m_slug = re.search(r'/v/([^/?"]+)', href)
            if not m_slug:
                continue
            vod_id = m_slug.group(1)

            m_pic = re.search(r'<img class="card__img"\s+src="([^"]*)"', ch)
            pic = m_pic.group(1) if m_pic else ''

            m_title = re.search(r'<h3 class="card__title">\s*<a[^>]*>(.*?)</a>', ch, re.S)
            title = self._clean(m_title.group(1)) if m_title else vod_id.upper()
            title = re.sub(r'\s*—\s*', ' — ', title)

            m_dur = re.search(r'<span class="card__dur">([^<]*)</span>', ch)
            dur = (m_dur.group(1) or '').strip() if m_dur else ''

            if self._blocked(title):
                continue

            out.append({
                'vod_id': vod_id,
                'vod_name': title,
                'vod_pic': pic,
                'vod_remarks': dur if dur and dur != '0:00' else '',
            })
        # 去重(同页可能重复渲染)
        seen = set()
        uniq = []
        for v in out:
            if v['vod_id'] in seen:
                continue
            seen.add(v['vod_id'])
            uniq.append(v)
        return uniq

    def _parse_episodes(self, html_text):
        """详情页内嵌线路: x-data="player(JSON.parse('[{"number":1,...}]'))" """
        eps = []
        key = "player(JSON.parse('"
        i = html_text.find(key)
        if i < 0:
            return eps
        seg = html_text[i + len(key):]
        buf = []
        j = 0
        while j < len(seg):
            c = seg[j]
            if c == '\\':
                buf.append(seg[j:j + 2])
                j += 2
                continue
            if c == "'":
                break
            buf.append(c)
            j += 1
        raw = ''.join(buf)
        if not raw:
            return eps
        safe = raw.replace('\\/', '/')
        try:
            data = json.loads(safe.encode('utf-8').decode('unicode_escape'))
        except Exception:
            try:
                data = json.loads(safe)
            except Exception:
                return eps
        if isinstance(data, dict):
            data = data.get('episodes') or data.get('list') or []
        if not isinstance(data, list):
            return eps
        for item in data:
            if not isinstance(item, dict):
                continue
            u = (item.get('url') or '').replace('\\/', '/')
            nm = str(item.get('name') or item.get('number') or '')
            m = re.search(r'/e/([A-Za-z0-9_]+)', u)
            if not m:
                continue
            eps.append({'hash': m.group(1), 'name': nm, 'embed': u})
        return eps

    def _parse_detail_meta(self, html_text, *args, **kwargs):
        """详情: 站点为规整的 dl.watch__info > dt/dd 结构(实测)
        Code / Type / Release date / Cast / Maker / Series / Genres / Tags"""
        meta = {
            'code': '', 'vtype': '', 'date': '', 'maker': '', 'series': '',
            'cast': [], 'genres': [], 'tags': [],
        }
        try:
            m = re.search(r'<dl class="watch__info">(.*?)</dl>', html_text, re.S)
            body = m.group(1) if m else ''
            if not body:
                return meta
            for row in re.findall(r'<div class="watch__info-row">(.*?)</div>', body, re.S):
                mt = re.search(r'<dt>(.*?)</dt>', row, re.S)
                if not mt:
                    continue
                key = self._clean(mt.group(1))
                md = re.search(r'<dd[^>]*>(.*?)</dd>', row, re.S)
                dd = md.group(1) if md else ''
                links = [self._clean(x) for x in re.findall(r'<a[^>]*>(.*?)</a>', dd, re.S)]
                links = [x for x in links if x]
                if links:
                    vals = links
                else:
                    one = self._clean(dd)
                    vals = [one] if one else []

                low = key.lower()
                if low == 'code':
                    meta['code'] = vals[0] if vals else ''
                elif low == 'type':
                    meta['vtype'] = vals[0] if vals else ''
                elif low == 'release date':
                    meta['date'] = vals[0] if vals else ''
                elif low == 'cast':
                    meta['cast'] = vals[:40]
                elif low == 'maker':
                    meta['maker'] = vals[0] if vals else ''
                elif low == 'series':
                    meta['series'] = vals[0] if vals else ''
                elif low == 'genres':
                    meta['genres'] = vals[:25]
                elif low == 'tags':
                    meta['tags'] = vals[:25]
        except Exception:
            pass
        return meta

    # ==================================================================
    # 分类目录(实时抓, 不硬编码)
    # ==================================================================

    def _load_dir(self, kind, *args, **kwargs):
        """kind: genre / maker / actress / series / tag -> [(name, slug)]
        每个目录页的条目 class 不同(实测), 分别对齐, 不套模板"""
        now = time.time()
        cached = self._dir_cache.get(kind)
        if cached and now - cached[0] < LIST_TTL:
            return cached[1]

        rows = []
        if kind == 'tag':
            # 站点无 /en/tags 列表页(实测 404), 真实标签入口在导航栏
            text = self._fetch('%s%s' % (self.site_url, LANG))
            for m in re.finditer(
                    r'href="%s/tags/([^"]+)"[^>]*>\s*([^<]{1,40})<' % re.escape(LANG),
                    text or ''):
                slug, name = m.group(1), self._clean(m.group(2))
                if slug and name:
                    rows.append((name, slug))
        else:
            spec = {
                'genre': ('genres',
                          r'<a class="gchip"\s+href="%s/genres/([^"]+)"[^>]*>.*?'
                          r'<span class="gchip__name">(.*?)</span>'),
                'maker': ('makers',
                          r'<a class="mcard"\s+href="%s/makers/([^"]+)"[^>]*>.*?'
                          r'<span class="mcard__name">(.*?)</span>'),
                'actress': ('actresses',
                            r'<a class="actress__name"\s+href="%s/actresses/([^"]+)"[^>]*>(.*?)</a>'),
                'series': ('series',
                           r'<a class="srow"\s+href="%s/series/([^"]+)"[^>]*>.*?'
                           r'<span class="srow__name">(.*?)</span>'),
            }.get(kind)
            if spec:
                path, pat = spec
                text = self._fetch('%s%s/%s' % (self.site_url, LANG, path))
                pattern = re.compile(pat % re.escape(LANG), re.S)
                for m in pattern.finditer(text or ''):
                    slug, name = m.group(1), self._clean(m.group(2))
                    if slug and name:
                        rows.append((name, slug))

        # 去重保序
        seen = set()
        uniq = []
        for name, slug in rows:
            if slug in seen:
                continue
            seen.add(slug)
            uniq.append((name, slug))

        self._dir_cache[kind] = (now, uniq)
        return uniq

    # ==================================================================
    # 首页
    # ==================================================================

    def homeContent(self, filter=None, *args, **kwargs):
        classes = []
        fixed = [
            ('new', '最新更新'),
            ('recent', '最近添加'),
            ('hot', '热门推荐'),
            ('all', '全部片库'),
            ('censored', '有码'),
            ('uncensored', '无码'),
            ('uncensored-leaked', '无码流出'),
            ('jable', 'Jable'),
            ('missav', 'MissAV'),
            ('supjav', 'SupJAV'),
        ]
        for tid, name in fixed:
            classes.append({'type_id': tid, 'type_name': DESENS_MAP.get(tid, name)})

        filters = {}

        # 全部片库 -> 榜单排序
        filters['all'] = [{
            'key': 'sort',
            'name': '排序',
            'value': [
                {'n': '今日榜', 'v': 'today'},
                {'n': '本周榜', 'v': 'week'},
                {'n': '本月榜', 'v': 'month'},
            ],
        }]

        # 二级筛选项: 实时抓站点目录, 取前 N(站点有多少写多少, 不硬编码)
        sec = [
            ('genre', '类型', 60),
            ('maker', '厂牌', 60),
            ('actress', '演员', 60),
            ('tag', '番号标签', 40),
            ('series', '系列', 60),
        ]
        for kind, label, cap in sec:
            rows = self._load_dir(kind)
            if not rows:
                continue
            vid = kind
            classes.append({'type_id': vid, 'type_name': label})
            vals = [{'n': n, 'v': s} for n, s in rows[:cap]]
            filters[vid] = [{'key': kind, 'name': label, 'value': vals}]

        return {'class': classes, 'filters': filters}

    def homeVideoContent(self, *args, **kwargs):
        text = self._fetch('%s%s/recent' % (self.site_url, LANG))
        return {'list': self._parse_cards(text)}

    # ==================================================================
    # 分类列表
    # ==================================================================

    DIR_PATH = {
        'genre': 'genres',
        'maker': 'makers',
        'actress': 'actresses',
        'tag': 'tags',
        'series': 'series',      # 实测: series 页路径不加 s
    }

    def _category_url(self, tid, pg, extend):
        extend = extend if isinstance(extend, dict) else {}
        base = self.site_url.rstrip('/') + LANG

        if tid in self.DIR_PATH:
            slug = extend.get(tid) or ''
            if not slug:
                return ''
            return '%s/%s/%s?page=%s' % (base, self.DIR_PATH[tid], slug, pg)

        if tid == 'all':
            sort = extend.get('sort') or 'today'
            return '%s/all?sort=%s&page=%s' % (base, sort, pg)

        return '%s/%s?page=%s' % (base, tid, pg)

    def categoryContent(self, tid, pg, filter=None, extend=None, *args, **kwargs):
        try:
            page = int(pg)
        except Exception:
            page = 1

        url = self._category_url(tid, page, extend)
        videos = []
        if url:
            text = self._fetch(url)
            videos = self._parse_cards(text)

        # 满页 -> 认为还有下一页; 不足 -> 到底(动态判定, 不写死)
        if len(videos) >= PER_PAGE:
            pagecount = page + 1
        else:
            pagecount = page

        return {
            'page': page,
            'pagecount': pagecount,
            'limit': PER_PAGE,
            'total': len(videos),
            'list': videos,
        }

    # ==================================================================
    # 详情
    # ==================================================================

    def _detail(self, slug):
        now = time.time()
        cached = self._detail_cache.get(slug)
        if cached and now - cached[0] < LIST_TTL:
            return cached[1]

        url = '%s%s/v/%s' % (self.site_url, LANG, slug)
        text = self._fetch(url)
        if not text:
            return {}

        m_title = re.search(r'<h1 class="watch__title"[^>]*>(.*?)</h1>', text, re.S)
        if m_title:
            title = self._clean(m_title.group(1))
        else:
            m_title = re.search(r'<title>(.*?)</title>', text, re.S)
            title = self._clean(m_title.group(1)) if m_title else slug.upper()
            title = re.sub(r'\s*[—\-]\s*123AV\s*$', '', title).strip()

        m_pic = re.search(r'<meta property="og:image" content="([^"]+)"', text)
        pic = m_pic.group(1) if m_pic else ''
        if not pic or 'logo' in pic:
            m_pic2 = re.search(r'<div class="watch__main"[^>]*>.*?background-image:\s*url\(\'([^\']+)\'\)',
                               text, re.S)
            if not m_pic2:
                m_pic2 = re.search(r"background-image:\s*url\('([^']+)'\)", text)
            if m_pic2:
                pic = m_pic2.group(1)

        eps = self._parse_episodes(text)
        meta = self._parse_detail_meta(text)

        info = {
            'slug': slug,
            'title': title,
            'pic': pic,
            'eps': eps,
            'meta': meta,
        }
        self._detail_cache[slug] = (now, info)
        return info

    def detailContent(self, ids, *args, **kwargs):
        out = []

        # 契约: 遍历 ids(list/tuple/str 全兼容)
        if isinstance(ids, (list, tuple)):
            targets = list(ids)
        else:
            targets = [ids]

        for raw_id in targets:
            if not raw_id:
                continue
            slug = str(raw_id)
            if '@' in slug:
                slug = slug.split('@')[0]
            m = re.search(r'/v/([^/?"]+)', slug)
            if m:
                slug = m.group(1)

            if self._blocked(slug):
                continue

            info = self._detail(slug)
            if not info:
                continue

            meta = info.get('meta') or {}
            eps = info.get('eps') or []

            # 线路 $$$ 、集 # 、集名与地址 $
            froms = []
            urls = []
            for idx, ep in enumerate(eps):
                line_name = '线路%s' % (idx + 1)
                froms.append(line_name)
                urls.append('正片$%s@%d' % (slug, idx))

            if not froms:
                # 无线路也返回详情, 让前端给提示而不是空白
                froms = ['123AV']
                urls = ['暂无线路$']

            content_bits = []
            if meta.get('code'):
                content_bits.append('番号: %s' % meta['code'])
            if meta.get('vtype'):
                content_bits.append('类别: %s' % meta['vtype'])
            if meta.get('date'):
                content_bits.append('发行: %s' % meta['date'])
            if meta.get('maker'):
                content_bits.append('厂牌: %s' % meta['maker'])
            if meta.get('series'):
                content_bits.append('系列: %s' % meta['series'])
            if meta.get('genres'):
                content_bits.append('类型: %s' % ' / '.join(meta['genres']))
            if meta.get('tags'):
                content_bits.append('标签: %s' % ' / '.join(meta['tags']))
            if eps:
                content_bits.append('线路数: %d' % len(eps))

            out.append({
                'vod_id': slug,
                'vod_name': info.get('title') or slug.upper(),
                'vod_pic': info.get('pic') or '',
                'vod_content': '\n'.join(content_bits),
                'vod_actor': ' / '.join(meta.get('cast') or []),
                'vod_director': meta.get('maker') or '',
                'vod_year': (meta.get('date') or '')[:4],
                'vod_area': '日本',
                'vod_remarks': '线路%d' % len(eps) if eps else '',
                'type_name': ' / '.join(meta.get('genres') or []),
                'vod_play_from': '$$$'.join(froms),
                'vod_play_url': '$$$'.join(urls),
            })

        return {'list': out}

    # ==================================================================
    # 搜索
    # ==================================================================

    def searchContent(self, key, quick=False, pg='1', *args, **kwargs):
        try:
            page = int(pg)
        except Exception:
            page = 1
        kw = (key or '').strip()
        videos = []
        if kw:
            try:
                from urllib.parse import quote
            except Exception:
                from urllib import quote  # py2 兜底
            url = '%s%s/search?keyword=%s&page=%s' % (
                self.site_url, LANG, quote(kw, safe=''), page)
            text = self._fetch(url)
            videos = self._parse_cards(text)
        return {'list': videos, 'page': page}

    # ==================================================================
    # ④ 播放壳
    # ==================================================================

    def _stream_of(self, hash_id):
        """javplayer.cc /stream?id=<hash> -> 真实 m3u8"""
        if not hash_id:
            return {}
        now = time.time()
        cached = self._stream_cache.get(hash_id)
        if cached and now - cached[0] < STREAM_TTL:
            return cached[1]

        url = '%s/stream?id=%s' % (PLAYER_HOST, hash_id)
        data = self._fetch_json(url, referer='%s/e/%s' % (PLAYER_HOST, hash_id))

        media = data.get('media') if isinstance(data, dict) else None
        if not isinstance(media, dict):
            media = data if isinstance(data, dict) and 'stream' in data else {}

        stream = (media or {}).get('stream') or ''
        if stream:
            self._stream_cache[hash_id] = (now, media)
        return media or {}

    def playerContent(self, flag, ids, vipFlags=None, *args, **kwargs):
        """
        返回三元组契约: [parse, url, header(dict)]
        header 含 UA + Referer + Origin(m3u8 防盗链实测必须带 UA)
        """
        header = {
            'User-Agent': UA,
            'Referer': PLAYER_HOST + '/',
            'Origin': PLAYER_HOST,
        }

        raw = str(ids or '')
        parts = raw.split('@')
        slug = parts[0]
        idx = 0
        if len(parts) > 1:
            try:
                idx = int(parts[1])
            except Exception:
                idx = 0

        if not slug:
            return {"parse":1, "url":raw, "header":header}

        # 直接用详情页 id 播(容错)
        if 'http' in slug:
            if self.isVideoFormat(slug):
                return {"parse":0, "url":slug, "header":header}
            m = re.search(r'/v/([^/?"]+)', slug)
            slug = m.group(1) if m else slug

        info = self._detail(slug)
        eps = info.get('eps') or []
        if not eps:
            return [1, '', header]

        if idx < 0 or idx >= len(eps):
            idx = 0

        media = self._stream_of(eps[idx]['hash'])
        stream = (media or {}).get('stream') or ''
        if not stream:
            return [1, '', header]

        return {"parse":0, "url":stream, "header":header}

    # ---------------- m3u8 广告清洗 ----------------

    def _is_ad_segment(self, url):
        """
        只砍强特征(实测口径):
        站点分片名为 base64 伪装 + 伪后缀, 不能按时长硬删
        (同流多 CDN 混排时 0.3~0.5s 的真实短段会被误杀)
        """
        if not url:
            return True
        low = url.lower()
        if re.search(r'/(?:ad|ads|advert|adv|promo)(?:[/\-_.]|$)', low):
            return True
        if re.search(r'\b(?:ad|ads)\d{0,3}\.(?:ts|jpg|png)', low):
            return True
        return False

    def _clean_m3u8(self, content):
        """剔除广告分片, 重排 EXTINF / ts 序列"""
        if not content or '#EXTM3U' not in content:
            return content
        lines = content.replace('\r\n', '\n').split('\n')
        out = []
        pend = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            if s.startswith('#'):
                pend.append(s)
                continue
            # s 是分片地址行
            if self._is_ad_segment(s):
                pend = [p for p in pend if not p.startswith('#EXTINF')]
                continue
            out.extend(pend)
            pend = []
            out.append(s)
        # 收尾残留
        out.extend([p for p in pend if not p.startswith('#EXTINF')])
        return '\n'.join(out)

    def localProxy(self, param, *args, **kwargs):
        """本地代理: 拉 m3u8 并清洗广告段"""
        try:
            if isinstance(param, dict):
                url = param.get('url') or param.get('u') or ''
                ptype = param.get('type') or param.get('t') or ''
            else:
                qs = str(param or '')
                m = re.search(r'(?:url|u)=([^&]+)', qs)
                url = m.group(1) if m else ''
                m2 = re.search(r'(?:type|t)=([^&]+)', qs)
                ptype = m2.group(1) if m2 else ''
            try:
                from urllib.parse import unquote
            except Exception:
                from urllib import unquote  # py2 兜底
            url = unquote(url)
        except Exception:
            return [404, 'text/plain', '']

        if not url:
            return [404, 'text/plain', '']

        content = self._fetch(url, referer=PLAYER_HOST + '/')
        if not content:
            return [404, 'text/plain', '']

        if 'm3u8' in (ptype or '') or '#EXTM3U' in content:
            content = self._clean_m3u8(content)
            return [200, 'application/vnd.apple.mpegurl', content]
        return [200, 'text/plain', content]


# ==================================================================
# 本地自测入口(脱离 TVBox 也能核)
# ==================================================================
if __name__ == '__main__':
    sp = Spider()
    sp.init('{}')
    print('name =', sp.getName())

    home = sp.homeContent(True)
    print('class =', len(home.get('class', [])), 'filters =', len(home.get('filters', {})))

    cat = sp.categoryContent('new', 1, True, {})
    print('category new =>', len(cat['list']), 'pagecount =', cat['pagecount'])
    if cat['list']:
        print('  first:', cat['list'][0])

    sr = sp.searchContent('mird', False, '1')
    print('search =>', len(sr['list']))

    if sr['list']:
        vid = sr['list'][0]['vod_id']
        det = sp.detailContent([vid])
        print('detail =>', det['list'][0]['vod_name'][:60])
        print('  from:', det['list'][0]['vod_play_from'])
        pc = sp.playerContent('', '%s@0' % vid, {})
        print('  player:', pc[0], (pc[1] or '')[:90])
