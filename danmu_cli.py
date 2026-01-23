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
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.parse

import execjs
import requests
import websocket
from py_mini_racer import MiniRacer

from ac_signature import get__ac_signature
from protobuf.douyin import ChatMessage, PushFrame, Response


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
        data = resp.json().get("data", {})
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
    parser = argparse.ArgumentParser(description="Fetch Douyin live danmu and save to file.")
    parser.add_argument("--live-id", help="Douyin live room ID (from live.douyin.com/<id>)")
    parser.add_argument("--out", help="Output file path (JSON Lines).")
    parser.add_argument("--tz", help="Display timezone (e.g., Asia/Shanghai, UTC).")
    parser.add_argument("--config", default="config/config.ini", help="Config file path (auto-created if missing).")
    parser.add_argument("--poll-live", type=int, help="Polling interval (seconds) while live.")
    parser.add_argument("--poll-off", type=int, help="Polling interval (seconds) while offline.")
    args = parser.parse_args()

    defaults = {
        "live_id": "",
        "out": "danmu.jsonl",
        "tz": "Asia/Shanghai",
        "poll_live": 15,
        "poll_off": 45,
    }

    cfg = configparser.ConfigParser()
    cfg_path = Path(args.config)
    cfg_dir = cfg_path.parent
    os.makedirs(cfg_dir, exist_ok=True)

    if cfg_path.exists():
        cfg.read(cfg_path, encoding="utf-8")
    if "douyin" not in cfg:
        cfg["douyin"] = {}

    # fill defaults for any missing keys
    updated = False
    for key, val in defaults.items():
        if key not in cfg["douyin"]:
            cfg["douyin"][key] = str(val)
            updated = True
    if updated:
        with open(cfg_path, "w", encoding="utf-8") as f:
            cfg.write(f)

    live_id = args.live_id or cfg["douyin"].get("live_id", "").strip()
    if not live_id:
        raise SystemExit("请在 --live-id 或配置文件 [douyin].live_id 中提供直播间 ID")

    out_path = args.out or cfg["douyin"].get("out", defaults["out"])
    tz_value = args.tz or cfg["douyin"].get("tz", defaults["tz"])
    poll_live = args.poll_live or int(cfg["douyin"].get("poll_live", defaults["poll_live"]))
    poll_off = args.poll_off or int(cfg["douyin"].get("poll_off", defaults["poll_off"]))

    tz_offset = timedelta(hours=8) if tz_value.lower() == "asia/shanghai" else timedelta(0)
    display_tz = timezone(tz_offset)

    fetcher = DanmuFetcher(live_id)
    out_file = open(out_path, "a", encoding="utf-8")

    def normalize_ts_ms(ts: int) -> int:
        """
        Normalize timestamp to milliseconds.
        - 10 digits (seconds) -> *1000
        - 13 digits (milliseconds) -> keep
        - 16 digits (microseconds) -> //1000
        - 19 digits (nanoseconds) -> //1_000_000
        """
        if ts < 1_000_000_000_0:          # <1e10  => seconds
            return ts * 1000
        if ts < 1_000_000_000_0000:       # <1e13  => milliseconds
            return ts
        if ts < 1_000_000_000_0000000:    # <1e16  => microseconds
            return ts // 1000
        return ts // 1_000_000            # nanoseconds

    def on_chat(chat: ChatMessage, server_now_ms: int):
        recv_ts = time.time()
        event_ms = normalize_ts_ms(chat.event_time)
        server_now_ms = normalize_ts_ms(server_now_ms)
        record = {
            "event_ts_ms": event_ms,
            "event_iso": datetime.fromtimestamp(event_ms / 1000, tz=display_tz).isoformat(),
            "server_now_ms": server_now_ms,
            "server_now_iso": datetime.fromtimestamp(server_now_ms / 1000, tz=display_tz).isoformat(),
            "recv_iso": datetime.fromtimestamp(recv_ts, tz=display_tz).isoformat(),
            "user_id": chat.user.id,
            "user_name": chat.user.nick_name,
            "content": chat.content,
        }
        out_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        out_file.flush()
        print(f"[{record['event_iso']}] {record['user_name']}: {record['content']}")

    try:
        ws_thread = None
        is_capturing = False
        while True:
            try:
                status = fetcher.fetch_live_status()
                anchor = status.get("anchor") or ""
                title = status.get("title") or ""
            except Exception as e:  # network errors should not crash
                print(f"[watcher] 获取直播状态失败: {e}")
                status = {"is_live": False, "status": None, "anchor": "", "title": ""}

            ts = datetime.now(tz=display_tz).isoformat()
            if status["is_live"] and not is_capturing:
                print(f"[{ts}] {anchor or live_id} 已开播，开始抓取弹幕。标题: {title}")
                ws_thread = threading.Thread(target=fetcher.start, args=(on_chat,), daemon=True)
                ws_thread.start()
                is_capturing = True
            elif (not status["is_live"]) and is_capturing:
                print(f"[{ts}] 直播已结束，停止抓取弹幕。")
                fetcher.stop()
                if ws_thread:
                    ws_thread.join(timeout=5)
                is_capturing = False

            sleep_sec = poll_live if status["is_live"] else poll_off
            time.sleep(max(5, sleep_sec))

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        fetcher.stop()
        out_file.close()


if __name__ == "__main__":
    main()
