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
