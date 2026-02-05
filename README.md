# DouyinDanmakuCatcher

轻量级 CLI，用于从抖音直播间拉取弹幕并写入 JSONL。本项目基于 [DouyinLiveWebFetcher](https://github.com/saermart/DouyinLiveWebFetcher) 改造，轮询/配置/开播检测 参考 [DouyinLiveRecorder](https://github.com/ihmily/DouyinLiveRecorder) 的逻辑，可直接复用 `config/config.ini` 与 `config/URL_config.ini` 的写法。

## 功能
- 捕获抖音弹幕与礼物信息，可分文件储存，提供礼物价值过滤功能

  
## 与上游的区别
- 精简为单文件入口 `danmu_cli.py`，专注 Webcast 聊天消息。
- 输出 JSONL，时间戳做毫秒归一化，便于后续分析。
- 随附最小的 JS 辅助脚本（`a_bogus.js`、`sign.js`），通过 `execjs`/`py_mini_racer` 调用。

## 环境要求
- Python 3.9+
- Node.js 16+（`execjs` 计算签名需要）
- 安装依赖：`pip install -r requirements.txt`

## 快速开始（与 DouyinLiveRecorder 相同的配置体验）
1) 在 `config/URL_config.ini` 填入直播间，一行一个；可选写法与 DouyinLiveRecorder 保持一致：  
   - 直接写链接：`https://live.douyin.com/745964462470`  
   - 带画质：`原画, https://live.douyin.com/745964462470`  
   - 带主播标注：`原画, https://live.douyin.com/745964462470, 主播: 某某`  
   - 注释行用 `#` 开头。
2) 可在 `config/config.ini` 的「录制设置」里调整：`循环时间(秒)`、`保存文件夹是否以作者/时间/标题区分`、`保存文件名是否包含标题`、`直播保存路径(不填则默认)`、`时区` 等（沿用 DouyinLiveRecorder 字段名与默认值）。
3) 运行：
```bash
python danmu_cli.py
```
程序会循环轮询：未开播则等待，检测到开播自动开启弹幕 WebSocket；关播后30min内未重新开播则自动断开ws连接并继续轮询。

## Docker运行
项目根目录运行命令`docker compose up`，自动拉取镜像并运行服务

## 输出与存储路径
保存位置遵循 DouyinLiveRecorder 的目录习惯（默认 `downloads/`）：  
- 层级：`downloads/<平台=抖音直播>/<主播名(optional)>/<日期optional>/<标题optional>/<文件名>.danmu.jsonl`  
- 文件名包含时间戳，若在 config 中开启「文件名包含标题」则会附加标题。

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


项目由ChatGPT CodeX协助生成和完善

Made with ❤️ by Buterr
