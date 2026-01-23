# DouyinDanmakuCatcher

轻量级 CLI，用于从抖音直播间拉取弹幕并写入 JSONL。本项目基于 https://github.com/saermart/DouyinLiveWebFetcher 改造，聚焦 Python 流程并提供简单命令行入口。

## 与上游的区别
- 精简为单文件入口 `danmu_cli.py`，专注 Webcast 聊天消息。
- 输出 JSONL，时间戳做毫秒归一化，便于后续分析。
- 随附最小的 JS 辅助脚本（`a_bogus.js`、`sign.js`），通过 `execjs`/`py_mini_racer` 调用。

## 环境要求
- Python 3.9+
- Node.js 16+（`execjs` 计算签名需要）
- 安装依赖：`pip install -r requirements.txt`

## 快速开始
```bash
python danmu_cli.py --live-id 510200350291 --out danmu.jsonl
```

参数说明：
- `--live-id`（必填）：直播间 ID，格式 `https://live.douyin.com/<id>`。
- `--out`（默认 `danmu.jsonl`）：输出 JSONL 文件。
- `--tz`（默认 `Asia/Shanghai`）：时间格式化时区。
- `--config`（默认 `config/config.ini`）：配置文件路径，缺失时会自动生成并填充默认值。
- `--poll-live` / `--poll-off`：分别为开播/未开播时的轮询间隔（秒），若在配置文件中填写可省略 CLI 传参。

## 自动开播监测与弹幕抓取

1. 首次运行会生成 `config/config.ini`，在 `[douyin]` 部分填入 `live_id=直播间数字ID`，可设置 `out`、`poll_live`、`poll_off` 等。
2. 直接执行 `python danmu_cli.py` 即可：程序会按未开播间隔轮询；检测到 `status==2` 自动连接 WebSocket 开始收集弹幕，关播后自动断开并回到轮询。
3. 也可继续使用命令行参数临时覆盖配置文件中的值（如 `--live-id` 或 `--out`）。

## 输出格式
每行一条 JSON：
```json
{
  "event_ts_ms": 1721106114633,
  "event_iso": "2024-07-16T12:21:54.633+08:00",
  "server_now_ms": 1721106114633,
  "server_now_iso": "2024-07-16T12:21:54.633+08:00",
  "recv_iso": "2024-07-16T12:21:55.102+08:00",
  "user_id": 12345678,
  "user_name": "nickname",
  "content": "弹幕内容"
}
```

## 注意事项
- 需要访问抖音域名，`ttwid` 会自动获取。
- 如果签名失败，请确认已安装 Node，重启 CLI 会重新生成 `__ac_signature` / `a_bogus`。
- 使用 `Ctrl+C` 停止，WebSocket 会在退出时关闭。
