#!/usr/bin/python
# coding: utf-8
"""
Minimal CLI to pull Douyin live danmu (chat) and save to a file.

Usage:
    python danmu_cli.py --live-id 510200350291 --out danmu.jsonl

Notes:
    - Requires network access to douyin domains.
    - Depends on Node.js runtime for execjs (a_bogus.js).
"""

import argparse
import configparser
import gzip
import hashlib
import json
import os
import random
import re
import string
import threading
import time
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.parse

import execjs
import requests
import websocket
from py_mini_racer import MiniRacer

from ac_signature import get__ac_signature
from protobuf.douyin import ChatMessage, PushFrame, Response

VERSION = "v0.3.0-auto"


# ------------------ helpers ------------------ #

def generate_ms_token(length: int = 182) -> str:
    chars = string.ascii_letters + string.digits + "-_"
    return "".join(random.choice(chars) for _ in range(length))


def execute_js(js_file: str):
    with open(js_file, "r", encoding="utf-8") as file:
        js_code = file.read()
    return js_code


def generate_signature(wss: str, script_file: str = "sign.js") -> str:
    params = (
        "live_id,aid,version_code,webcast_sdk_version,"
        "room_id,sub_room_id,sub_channel_id,did_rule,"
        "user_unique_id,device_platform,device_type,ac,"
        "identity"
    ).split(",")
    wss_params = urllib.parse.urlparse(wss).query.split("&")
    wss_maps = {i.split("=")[0]: i.split("=")[-1] for i in wss_params}
    tpl_params = [f"{i}={wss_maps.get(i, '')}" for i in params]
    param = ",".join(tpl_params)
    md5_param = hashlib.md5(param.encode()).hexdigest()

    script = execute_js(script_file)
    ctx = MiniRacer()
    ctx.eval(script)
    return ctx.call("get_sign", md5_param)


# ------------------ core fetcher ------------------ #

class DanmuFetcher:
    def __init__(self, live_id: str, abogus_file: str = "a_bogus.js"):
        self.live_id = live_id
        self.abogus_file = abogus_file
        self.host = "https://www.douyin.com/"
        self.live_url = "https://live.douyin.com/"
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        )
        self.headers = {"User-Agent": self.user_agent}
        self.session = requests.Session()
        self._ttwid = None
        self._room_id = None
        self._running = False
        self.ws = None

    # -------- properties -------- #
    @property
    def ttwid(self):
        if self._ttwid:
            return self._ttwid
        resp = self.session.get(self.live_url, headers=self.headers)
        resp.raise_for_status()
        self._ttwid = resp.cookies.get("ttwid")
        return self._ttwid

    @property
    def room_id(self):
        if self._room_id:
            return self._room_id
        url = self.live_url + self.live_id
        headers = {
            "User-Agent": self.user_agent,
            "cookie": f"ttwid={self.ttwid}&msToken={generate_ms_token()}; __ac_nonce=0123407cc00a9e438deb4",
        }
        resp = self.session.get(url, headers=headers)
        resp.raise_for_status()
        match = re.search(r'roomId\\":\\"(\d+)\\"', resp.text)
        if not match:
            raise RuntimeError("room_id not found, please retry or check live_id")
        self._room_id = match.group(1)
        return self._room_id

    # -------- signing -------- #
    def get_ac_nonce(self):
        resp_cookies = self.session.get(self.host, headers=self.headers).cookies
        return resp_cookies.get("__ac_nonce")

    def get_ac_signature(self, __ac_nonce: str = None) -> str:
        __ac_signature = get__ac_signature(self.host[8:], __ac_nonce, self.user_agent)
        self.session.cookies.set("__ac_signature", __ac_signature)
        return __ac_signature

    def get_a_bogus(self, url_params: dict):
        url = urllib.parse.urlencode(url_params)
        ctx = execjs.compile(execute_js(self.abogus_file))
        return ctx.call("get_ab", url, self.user_agent)

    # -------- live status -------- #
    def fetch_live_status(self) -> dict:
        """
        Poll Douyin web enter API to determine whether the room is live.
        Returns dict with keys: is_live(bool), status(int|None), title(str), anchor(str).
        """
        if not self.live_id:
            return {"is_live": False, "status": None, "anchor": "", "title": ""}

        ms_token = generate_ms_token()
        params = {
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "language": "zh-CN",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "116.0.0.0",
            "web_rid": self.live_id,
            "msToken": ms_token,
        }

        query = urllib.parse.urlencode(params)
        a_bogus = self.get_a_bogus(params)
        api = f"https://live.douyin.com/webcast/room/web/enter/?{query}&a_bogus={a_bogus}"
        headers = {
            "User-Agent": self.user_agent,
            "cookie": f"ttwid={self.ttwid}; msToken={ms_token}",
            "referer": f"https://live.douyin.com/{self.live_id}",
        }

        resp = self.session.get(api, headers=headers, timeout=10)
        resp.raise_for_status()
        try:
            data = resp.json().get("data", {})
        except Exception:
            # 非 JSON（可能风控/验证码），视为未开播
            return {"is_live": False, "status": None, "anchor": "", "title": ""}
        room_list = data.get("data") or []
        room_info = room_list[0] if room_list else {}
        status = room_info.get("status")
        anchor = room_info.get("anchor_name") or (data.get("user") or {}).get("nickname") or ""
        title = room_info.get("title") or ""
        return {
            "is_live": status == 2,
            "status": status,
            "anchor": anchor,
            "title": title,
        }

    # -------- runtime -------- #
    def start(self, on_chat):
        self._running = True
        wss = (
            "wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
            "&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
            "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
            "&browser_name=Mozilla"
            "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
            "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
            "&browser_online=true&tz_name=Asia/Shanghai"
            "&cursor=d-1_u-1_fh-7392091211001140287_t-1721106114633_r-1"
            f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:7319483754668557238"
            f"|first_req_ms:1721106114541|fetch_time:1721106114633|seq:1|wss_info:0-1721106114633-0-0|wrds_v:7392094459690748497"
            f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
            f"&user_unique_id=7319483754668557238&im_path=/webcast/im/fetch/&identity=audience"
            f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0"
        )

        signature = generate_signature(wss)
        wss += f"&signature={signature}"

        headers = {"cookie": f"ttwid={self.ttwid}", "user-agent": self.user_agent}
        self.ws = websocket.WebSocketApp(
            wss,
            header=headers,
            on_open=self._on_open,
            on_message=lambda ws, msg: self._on_message(msg, on_chat),
            on_error=self._on_error,
            on_close=self._on_close,
        )
        try:
            self.ws.run_forever()
        finally:
            self._running = False

    def stop(self):
        self._running = False
        if self.ws:
            self.ws.close()

    # -------- callbacks -------- #
    def _on_open(self, _ws):
        threading.Thread(target=self._send_heartbeat, daemon=True).start()
        print("WebSocket connected, listening for danmu... (Ctrl+C to stop)")

    def _on_message(self, message, on_chat):
        package = PushFrame().parse(message)
        response = Response().parse(gzip.decompress(package.payload))

        if response.need_ack:
            ack = PushFrame(
                log_id=package.log_id,
                payload_type="ack",
                payload=response.internal_ext.encode("utf-8"),
            ).SerializeToString()
            self.ws.send(ack, websocket.ABNF.OPCODE_BINARY)

        for msg in response.messages_list:
            if msg.method != "WebcastChatMessage":
                continue
            try:
                chat = ChatMessage().parse(msg.payload)
                on_chat(chat, response.now)
            except Exception:
                continue

    def _on_error(self, _ws, error):
        print(f"WebSocket error: {error}")

    def _on_close(self, _ws, *args):
        print("WebSocket closed.")

    def _send_heartbeat(self):
        while self._running:
            try:
                heartbeat = PushFrame(payload_type="hb").SerializeToString()
                self.ws.send(heartbeat, websocket.ABNF.OPCODE_PING)
            except Exception:
                break
            time.sleep(5)


# ------------------ CLI entry ------------------ #


def main():
    parser = argparse.ArgumentParser(description="循环监测直播并按开播自动抓取抖音弹幕")
    parser.add_argument("--config", default="config/config.ini", help="配置文件路径")
    parser.add_argument("--url-config", default="config/URL_config.ini", help="直播间列表文件，一行一个")
    args = parser.parse_args()

    # ---------- 加载配置 ---------- #
    script_path = Path(__file__).parent.resolve()
    default_path = script_path / "downloads"
    default_path.mkdir(exist_ok=True)

    cfg = configparser.RawConfigParser()
    cfg.read(args.config, encoding="utf-8-sig")

    def cfg_get(section, key, default):
        if not cfg.has_section(section):
            cfg.add_section(section)
        if cfg.has_option(section, key):
            return cfg.get(section, key)
        cfg.set(section, key, str(default))
        return default

    delay_default = int(cfg_get("录制设置", "循环时间(秒)", 120))
    loop_time = cfg_get("录制设置", "是否显示循环秒数", "否") == "是"
    folder_by_author = cfg_get("录制设置", "保存文件夹是否以作者区分", "是") == "是"
    folder_by_time = cfg_get("录制设置", "保存文件夹是否以时间区分", "否") == "是"
    folder_by_title = cfg_get("录制设置", "保存文件夹是否以标题区分", "否") == "是"
    filename_by_title = cfg_get("录制设置", "保存文件名是否包含标题", "否") == "是"
    video_save_path = cfg_get("录制设置", "直播保存路径(不填则默认)", "")
    tz_value = cfg_get("录制设置", "时区", "Asia/Shanghai")

    # 同步写回默认值
    with open(args.config, "w", encoding="utf-8") as f:
        cfg.write(f)

    tz_offset = timedelta(hours=8) if tz_value.lower() == "asia/shanghai" else timedelta(0)
    display_tz = timezone(tz_offset)

    # ---------- 解析 URL 列表 ---------- #
    def get_quality_code(qn):
        mapping = {"原画": "OD", "蓝光": "BD", "超清": "UHD", "高清": "HD", "标清": "SD", "流畅": "LD"}
        return mapping.get(qn.strip(), "OD")

    def parse_url_line(line: str):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        parts = [p.strip() for p in line.split(",") if p.strip()]
        quality = "原画"
        anchor = ""
        url = ""
        if len(parts) == 1:
            url = parts[0]
        else:
            if parts[0].startswith("http"):
                url = parts[0]
            else:
                quality = parts[0]
                url = parts[1]
                if len(parts) > 2 and "主播" in parts[2]:
                    anchor = parts[2].split(":", 1)[-1].strip()
        return {"quality": get_quality_code(quality), "url": url, "anchor": anchor}

    def clean_name(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "空白昵称"
        text = re.sub(r'[\\\\/:*?"<>|#]+', "_", text)
        return text.strip("_") or "空白昵称"

    url_lines = []
    url_cfg_path = Path(args.url_config)
    url_cfg_path.parent.mkdir(exist_ok=True, parents=True)
    if url_cfg_path.exists():
        with open(url_cfg_path, "r", encoding="utf-8-sig") as f:
            url_lines = [parse_url_line(i) for i in f.readlines()]
    else:
        url_cfg_path.write_text("", encoding="utf-8-sig")
    url_tasks = [i for i in url_lines if i]
    if not url_tasks:
        raise SystemExit("URL_config.ini 为空，请填入直播间链接")

    # ---------- 辅助函数 ---------- #
    def normalize_ts_ms(ts: int) -> int:
        if ts < 1_000_000_000_0:
            return ts * 1000
        if ts < 1_000_000_000_0000:
            return ts
        if ts < 1_000_000_000_0000000:
            return ts // 1000
        return ts // 1_000_000

    def resolve_live_id(url: str) -> str:
        """Resolve to live.douyin.com/<web_rid> by following redirects."""
        try:
            if "live.douyin.com/" in url:
                return url.split("live.douyin.com/")[1].split("?")[0].split("/")[0]
            # short链接或其他跳转
            resp = requests.get(url, headers={"User-Agent": DanmuFetcher("", "").user_agent}, allow_redirects=True, timeout=10)
            final = resp.url
            if "live.douyin.com/" in final:
                return final.split("live.douyin.com/")[1].split("?")[0].split("/")[0]
        except Exception:
            return ""
        return ""

    def build_outfile(platform: str, anchor: str, title: str):
        base = Path(video_save_path) if video_save_path else default_path
        platform_path = base / platform
        anchor_clean = clean_name(anchor)
        title_clean = clean_name(title) if title else ""
        anchor_path = platform_path / anchor_clean if folder_by_author else platform_path
        date_path = anchor_path / datetime.now(tz=display_tz).strftime("%Y-%m-%d") if folder_by_time else anchor_path
        if folder_by_title and title_clean:
            date_path = date_path / title_clean
        date_path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=display_tz).strftime("%Y-%m-%d_%H-%M-%S")
        name = anchor_clean
        if filename_by_title and title_clean:
            name = f"{anchor_clean}_{title_clean}"
        filename = f"{name}_{ts}.danmu.jsonl"
        return date_path / filename

    # ---------- 单直播任务线程 ---------- #
    class LiveTask(threading.Thread):
        def __init__(self, task_conf):
            super().__init__(daemon=True)
            self.url = task_conf["url"]
            self.quality = task_conf["quality"]
            self.anchor_hint = task_conf["anchor"]
            self.running = False
            self.fetcher = None
            self.stop_flag = False
            self.live_id = resolve_live_id(self.url)
            self.pending_stop_deadline = None
            self.grace_stop_seconds = 30 * 60

        def run(self):
            while not self.stop_flag:
                try:
                    status = DanmuFetcher(self.live_id or "").fetch_live_status()
                    is_live = status.get("is_live", False)
                    anchor_name = status.get("anchor", "") or self.anchor_hint or self.live_id
                    title = status.get("title", "") or ""
                    if not self.live_id:
                        self.live_id = resolve_live_id(self.url)

                    ts = datetime.now(tz=display_tz).isoformat()
                    if is_live:
                        if self.pending_stop_deadline:
                            self.pending_stop_deadline = None
                        if not self.running and self.live_id:
                        outfile = build_outfile("抖音直播", anchor_name or self.live_id, title)
                        print(f"[{ts}] {anchor_name or self.live_id} 开播，弹幕保存到 {outfile}")
                        self.start_fetch(anchor_name or self.live_id, outfile)
                    elif (not is_live) and self.running:
                        if not self.pending_stop_deadline:
                            self.pending_stop_deadline = time.time() + self.grace_stop_seconds
                            mins = int(self.grace_stop_seconds / 60)
                            print(f"[{ts}] {anchor_name or self.live_id} 已关播，进入{mins}分钟延迟断开")
                        elif time.time() >= self.pending_stop_deadline:
                            print(f"[{ts}] {anchor_name or self.live_id} 延迟到期，停止弹幕抓取")
                            self.pending_stop_deadline = None
                            self.stop_fetch()

                except Exception as e:
                    print(f"[task] {self.url} 状态检测失败: {e}")

                sleep_time = delay_default
                while sleep_time > 0 and not self.stop_flag:
                    if loop_time:
                        print(f"\r{self.url} 循环等待 {sleep_time} 秒 ", end="")
                    time.sleep(1)
                    sleep_time -= 1
                if loop_time:
                    print("\r检测直播间中...", end="")

        def start_fetch(self, anchor_name: str, outfile: Path):
            self.running = True
            self.fetcher = DanmuFetcher(self.live_id)
            out_file = open(outfile, "a", encoding="utf-8")

            def on_chat(chat: ChatMessage, server_now_ms: int):
                recv_ts = time.time()
                event_ms = normalize_ts_ms(chat.event_time)
                server_now_ms_norm = normalize_ts_ms(server_now_ms)
                record = {
                    "event_ts_ms": event_ms,
                    "event_iso": datetime.fromtimestamp(event_ms / 1000, tz=display_tz).isoformat(),
                    "server_now_ms": server_now_ms_norm,
                    "server_now_iso": datetime.fromtimestamp(server_now_ms_norm / 1000, tz=display_tz).isoformat(),
                    "recv_iso": datetime.fromtimestamp(recv_ts, tz=display_tz).isoformat(),
                    "user_id": chat.user.id,
                    "user_name": chat.user.nick_name,
                    "content": chat.content,
                }
                out_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_file.flush()
                print(f"[{record['event_iso']}] {record['user_name']}: {record['content']}")

            self.ws_thread = threading.Thread(target=self.fetcher.start, args=(on_chat,), daemon=True)
            self.ws_thread.start()
            self.out_file = out_file

        def stop_fetch(self):
            self.running = False
            if self.fetcher:
                self.fetcher.stop()
            if hasattr(self, "ws_thread"):
                self.ws_thread.join(timeout=5)
            if hasattr(self, "out_file"):
                self.out_file.close()

    # ---------- 主循环 ---------- #
    tasks = [LiveTask(t) for t in url_tasks]
    for t in tasks:
        t.start()

    stop_event = threading.Event()

    def display_info():
        while not stop_event.is_set():
            running = sum(1 for t in tasks if t.running)
            total = len(tasks)
            now = datetime.now(tz=display_tz).strftime("%H:%M:%S")
            print(f"\r共监测{total}个直播 | 正在录制{running}个 | 循环间隔{delay_default}s | 当前时间: {now}", end="")
            time.sleep(5)

    info_thread = threading.Thread(target=display_info, daemon=True)
    info_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n收到退出指令，正在停止...")
    finally:
        stop_event.set()
        for t in tasks:
            t.stop_flag = True
            t.stop_fetch()
        for t in tasks:
            t.join(timeout=3)
        info_thread.join(timeout=3)



if __name__ == "__main__":
    main()
