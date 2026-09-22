#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
溏心AV Spider - 四壳通用Python Spider
站点: https://sjw.txav4.motorcycles/txav/
"""

import re
import urllib.request
import urllib.parse

try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def init(self, extend=""): pass
        def homeContent(self, filter): pass
        def categoryContent(self, tid, pg, filter, extend): pass
        def detailContent(self, ids): pass
        def searchContent(self, key, quick, pg="1"): pass
        def playerContent(self, flag, id, vipFlags): pass
        def localProxy(self, params): return [404, "text/plain", ""]
        def isVideoFormat(self, url): return False
        def manualVideoCheck(self): return False
        def getName(self): return "溏心AV"
        def getDependence(self): return []
        def destroy(self): pass


class Spider(Spider):
    def init(self, extend=""):
        self.siteUrl = "https://sjw.txav4.motorcycles/txav"
        self.rawUrl = "https://sjw.txav4.motorcycles/txav"
        return "溏心AV"

    def homeContent(self, filter):
        result = {}
        class_name = []
        for i in range(20, 30):
            class_name.append({"type_name": f"分类{i}", "type_id": str(i)})
        result["class"] = class_name
        result["filters"] = {}
        result["list"] = self.categoryContent("20", "1", False, {})["list"]
        return result

    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        url = f"{self.siteUrl}/cn/home/web/index.php/vod/type/id/{tid}.html"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        
        list = []
        items = re.findall(r'<a href="(/cn/home/web/index\.php/vod/play/id/(\d+)/sid/\d+/nid/\d+\.html)"[^>]*title="([^"]*)"', html)
        seen = set()
        for href, id, title in items:
            if id in seen:
                continue
            seen.add(id)
            list.append({"vod_id": id, "vod_name": title, "vod_pic": "", "vod_remarks": ""})
        
        result["list"] = list
        result["page"] = int(pg)
        result["pagecount"] = 100
        result["limit"] = 20
        result["total"] = 2000
        return result

    def detailContent(self, ids):
        vod_id = ids[0]
        url = f"{self.siteUrl}/cn/home/web/index.php/vod/play/id/{vod_id}/sid/1/nid/1.html"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        
        m3u8 = re.search(r'player_data\s*=\s*{[^}]*url\s*:\s*"([^"]*)"', html)
        if not m3u8:
            m3u8 = re.search(r"rawUrl\s*=\s*'([^']*)'", html)
        if not m3u8:
            m3u8 = re.search(r'"(https?://[^"]*\.m3u8[^"]*)"', html)
        
        play_url = m3u8.group(1) if m3u8 else ""
        title = re.search(r'<title>(.*?)</title>', html)
        
        vod_list = [{
            "vod_id": vod_id, "vod_name": title.group(1) if title else "", "vod_pic": "",
            "vod_remarks": "", "vod_year": "", "vod_area": "", "vod_actor": "",
            "vod_director": "", "vod_content": "",
            "vod_play_from": "溏心AV", "vod_play_url": play_url
        }]
        return {"list": vod_list}

    def searchContent(self, key, quick, pg="1"):
        url = f"{self.siteUrl}/cn/home/web/index.php/vod/search.html?wd={urllib.parse.quote(key)}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        
        list = []
        items = re.findall(r'<a href="(/cn/home/web/index\.php/vod/play/id/(\d+)/sid/\d+/nid/\d+\.html)"[^>]*title="([^"]*)"', html)
        seen = set()
        for href, id, title in items:
            if id in seen:
                continue
            seen.add(id)
            list.append({"vod_id": id, "vod_name": title, "vod_pic": "", "vod_remarks": ""})
        
        return {"list": list, "page": int(pg), "pagecount": 10, "limit": 20, "total": 200}

    def playerContent(self, flag, id, vipFlags):
        url = f"{self.siteUrl}/cn/home/web/index.php/vod/play/id/{id}/sid/1/nid/1.html"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        
        m3u8 = re.search(r'player_data\s*=\s*{[^}]*url\s*:\s*"([^"]*)"', html)
        if not m3u8:
            m3u8 = re.search(r"rawUrl\s*=\s*'([^']*)'", html)
        if not m3u8:
            m3u8 = re.search(r'"(https?://[^"]*\.m3u8[^"]*)"', html)
        
        return {
            "parse": 0, "jx": 0,
            "url": m3u8.group(1) if m3u8 else "",
            "header": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": self.rawUrl + "/", "Origin": self.rawUrl
            }
        }

    def localProxy(self, params): return [404, "text/plain", ""]
    def getName(self): return "溏心AV"
    def getDependence(self): return []
    def destroy(self): pass
