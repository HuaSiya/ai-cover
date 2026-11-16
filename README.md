# AI 翻唱点歌

给 MaiBot 增加 `/翻唱 歌名` 和 `/点歌 歌名`：机器人会去 B 站搜索 AI 翻唱或歌曲音频，自动挑选最合适的版本，下载音频，然后直接以 QQ 语音消息发出来。默认只发语音，不在对话里发“正在找”或标题/UP 主/链接。

## 功能

- `/翻唱 晴天` / `/翻唱 晴天 周杰伦` / `/ai翻唱 晴天`
- `/点歌 晴天` / `@机器人 点歌 晴天`：点歌模式直接用歌名搜 B 站，原唱、MV、翻唱、普通翻唱都可以
- 支持自然语言：`@机器人 翻唱晴天`、`@机器人 唱寒枝摇碎晚霞`、`@机器人 帮我唱一下《晴天》`、`@机器人 帮我点歌一下《晴天》`
- 自然语言只在 @ 机器人时识别，避免抢走群里的普通聊天
- 依次搜索 `歌名 AI翻唱`、`歌名 AI cover`、`歌名 翻唱`，按标题、标签、简介、分区、播放量、时长综合打分
- 自动过滤伴奏、纯音乐、教学、翻弹、直播、reaction、合集等明显不是翻唱的结果
- 找不到明确 AI 版本时，可配置退而发送普通翻唱；默认不追加文字提示
- 音频下载到插件私有缓存，重复点同一首歌不再重复下载；带过期时间和容量上限
- 语音通过 `send.custom("voice", base64)` 发送，不要求 MaiBot 与 NapCat 共享目录
- 支持冷却、每日上限、群号/QQ 号白名单、并发限制
- 音频失败时默认静默；需要时可以打开链接回退

## 安装

- 插件市场搜索 **AI 翻唱点歌**，或插件 ID `github.husiya.ai-cover`
- 手动安装：把仓库目录放到 `MaiBot/plugins/ai-cover/`，重启 MaiBot
- 依赖：`httpx>=0.27.0`（MaiBot 主程序通常已自带）

B 站搜索不需要登录；如果遇到风控，可以在配置里填一个可用的 `SESSDATA`。

## 使用

```text
/翻唱 晴天
/翻唱 晴天 周杰伦
/ai翻唱 起风了
#翻唱 富士山下
/点歌 晴天
@机器人 点歌 晴天
@机器人 /点歌 晴天
@机器人 /翻唱 晴天
@机器人 翻唱晴天
@机器人 帮我翻唱一下《晴天》可以吗
@机器人 能翻唱《起风了》吗
@机器人 唱寒枝摇碎晚霞
@机器人 唱一下《晴天》
@机器人 给我唱一首晴天
@机器人 唱首歌
```

第一个词最好是歌名，后面的词可以带歌手，例如 `晴天 周杰伦`。插件会要求第一个词出现在标题/标签/简介里，避免搜到同歌手的其他歌。

### 翻唱/唱歌模式

`/翻唱`、`@机器人 翻唱XX`、`@机器人 唱XX` 会搜索 `歌名 AI翻唱`、`歌名 AI cover`、`歌名 翻唱`，优先选 AI 翻唱；找不到 AI 版时可以退而发送普通翻唱。

### 点歌模式

`/点歌`、`@机器人 点歌XX` 会**直接用歌名搜索 B 站**，允许原唱、MV、翻唱和普通翻唱，不要求 AI。会过滤伴奏、纯音乐、教学、演奏、直播、合集、循环等不适合直接听的视频。

自然语言触发只处理 **@ 机器人** 的消息：

- `@机器人 翻唱晴天` → 歌名“晴天”
- `@机器人 唱寒枝摇碎晚霞` → 歌名“寒枝摇碎晚霞”
- `@机器人 点歌 晴天` → 直接搜“晴天”
- `@机器人 唱首歌` / `@机器人 唱歌` / `@机器人 点歌` → 没有具体歌名，回复“想听哪首？”提示
- `@机器人 你会翻唱吗` / `@机器人 唱歌功能怎么用` / `@机器人 点歌功能怎么用` / `@机器人 唱得不错` → 不触发，交给正常聊天
- 直接发 `翻唱晴天` / `点歌晴天`（没有 / 且没有 @）不会触发，避免抢群聊

成功时默认只收到一条 QQ 语音消息，不会先发等待提示，也不会发标题、UP 主或链接。需要这些文案时，在配置里打开 `delivery.send_ack` 或 `delivery.send_caption`。

## 配置

配置文件：`config.toml`。主要选项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `plugin.enabled` | `true` | 总开关 |
| `search.query_templates` | 3 个模板 | 翻唱/唱歌模式搜索关键词，`{song}` 会替换为歌名 |
| `search.request_query_templates` | `["{song}"]` | 点歌模式搜索关键词，默认直接搜歌名 |
| `search.require_ai` | `true` | 翻唱模式只接受带 AI 翻唱特征的候选 |
| `search.allow_non_ai_fallback` | `true` | 翻唱模式找不到 AI 版时是否发普通翻唱 |
| `search.min_score` | `10` | 翻唱候选最低分 |
| `search.request_min_score` | `8` | 点歌候选最低分 |
| `search.min_duration_seconds` / `max_duration_seconds` | `20` / `600` | 视频时长过滤 |
| `search.sessdata` | 空 | 可选 B 站登录凭证，提高风控下的成功率 |
| `audio.quality` | `low` | `low` 是 64k 音频，做成语音足够，流量最小 |
| `audio.max_file_size_mb` | `9` | 单个音频上限，过大不会塞进 RPC 帧 |
| `audio.cache_enabled` | `true` | 是否缓存音频 |
| `delivery.send_ack` | `false` | 是否先发“正在找”等待提示 |
| `delivery.send_caption` | `false` | 是否发标题/UP 主/链接文案 |
| `delivery.caption_template` | 见配置 | 文案模板，支持 `{title} {author} {duration} {url} {song}` |
| `delivery.fallback_to_link` | `false` | 音频失败时是否发原视频链接兜底 |
| `delivery.non_ai_note` | 空 | 退而发送普通翻唱时的提示，留空表示不提示 |
| `delivery.request_no_result_text` | 见配置 | 点歌模式没有候选时的文案 |
| `delivery.voice_storage` | `false` | 是否把语音二进制写入聊天历史；关闭可省数据库空间 |
| `limits.cooldown_seconds` | `20` | 同一聊天流冷却 |
| `limits.daily_limit` | `10` | 每个聊天流每日上限，`0` 不限制 |
| `limits.allowed_group_ids` | `[]` | 群白名单，空表示所有群 |
| `limits.allowed_user_ids` | `[]` | QQ 白名单，空表示所有用户 |

## 工作原理

```text
/翻唱 晴天
  ├─ 1. B 站搜索（自动取 buvid Cookie、限速、WBI 签名回退）
  ├─ 2. 候选打分（歌名、翻唱、AI、分区、时长、播放量、负关键词）
  ├─ 3. 读取视频详情拿 cid
  ├─ 4. 获取 DASH 音频地址，必要时回退 durl
  ├─ 5. 下载音频（限制大小，缓存到 data/plugins/github.husiya.ai-cover/audio_cache）
  ├─ 6. 默认不发任何文案（可选等待提示/标题文案）
  └─ 7. send.custom("voice", base64) → SnowLuma/NapCat 转 silk → QQ 语音
```

语音不使用 `voiceurl` 本地路径，因为很多部署里 MaiBot 容器和 SnowLuma 容器没有共享音乐目录；base64 直接走宿主能力最稳。SnowLuma 自带 ffmpeg，会把 m4a / mp4 / mp3 转成 QQ 需要的 silk。

## 常见问题

### 1. 一直提示“B 站搜索暂时失败了”

B 站对匿名请求有风控，常见原因是出口 IP 被限、请求太快或缺少 Cookie。插件已经自动获取 `buvid3/buvid4` 并做了限速；仍然失败时，可以：

- 在 WebUI 配置里填 `search.sessdata`；
- 把 `search.rate_limit_seconds` 调大，例如 `2.0`；
- 隔几分钟再试。

### 2. 群里显示“发送者版本过低，无法展示内容”

这条提示通常不是翻唱语音本身，而是**音乐卡片 / JSON 卡片签名失败**后 QQ 客户端显示的占位提示。SnowLuma 日志里会同时出现：

```text
[MsgParser] music sign failed: message element "json" field "text" must not be empty, falling back to local card
[OneBot] 群聊 ... | 发送：[JSON消息]
```

1.0.2 起 `/翻唱` 命令支持 `@机器人 /翻唱 歌名`，1.2.0 起 `@机器人 唱XX` 也会直接走翻唱插件，不再让这些消息落到 LLM/音乐卡片链路。如果仍然出现，检查是否还有其他插件在发送音乐卡片，或给 NapCat/SnowLuma 配置可用的 `musicSignUrl`。

### 3. 语音发不出去

- 确认 QQ 适配器支持 `record/voice` 段。SnowLuma / NapCat 支持；QQ 官方机器人适配器不支持语音，会回退文字链接。
- 查看 MaiBot 日志里是否有 `语音发送失败` 或 `send.custom(voice)` 相关错误。
- 如果音频超过 RPC 帧限制（16 MB），插件会自动跳过并尝试下一个候选；可以把 `audio.quality` 设为 `low`。

### 4. 发的是普通翻唱，不是 AI 版

说明当前搜索结果里没有通过 AI 特征过滤的版本。可以：

- 确认歌名写对；
- 把 `search.require_ai` 保持 `true`，关掉 `allow_non_ai_fallback` 只看 AI 版；
- 在 B 站手动确认是否有该歌的 AI 翻唱；没有的话插件也变不出来。

### 5. 长歌会不会被 QQ 截断

QQ 语音本身的时长限制因客户端/协议而异。插件默认允许最长 10 分钟的视频，音频会完整上传；如果你们的 QQ 客户端只播放前 60 秒，可以把 `search.max_duration_seconds` 调小，优先挑短版本。

### 6. 缓存位置

`MaiBot/data/plugins/github.husiya.ai-cover/audio_cache/`（容器内路径）。对应宿主机通常是
`maibot/data/MaiMBot-plugin-data/github.husiya.ai-cover/audio_cache/`。默认 72 小时过期、总容量 512 MB，会自动清理。

## 测试

不启动机器人也可以测 B 站链路：

```bash
cd MaiBot/plugins/ai-cover
python tools/check_bilibili.py "晴天"
python tools/check_bilibili.py "晴天" --download   # 顺便下载第一名，检查音频
```

单元测试：

```bash
cd MaiBot/plugins/ai-cover
python -m unittest discover -s tests -v
```

## 合规提示

- 插件只做搜索、下载和转发，不生成翻唱，也不绕过 B 站登录/付费限制。
- AI 翻唱涉及原曲著作权、演唱者声音权益和 UP 主作品权益，请只在获得授权的场景使用，不要商用或批量搬运。
- 请遵守 B 站用户协议和 QQ 平台规则；不要高频刷指令。

## 许可证

MIT

## 版本

- 1.3.0：新增点歌功能 `/点歌 歌名`、`@机器人 点歌 歌名`；点歌直接用歌名搜 B 站，支持原唱/MV/翻唱。
- 1.2.0：自然语言触发扩展到 `@机器人 唱XX`、`@机器人 唱一下《晴天》`、`@机器人 唱首歌`；无具体歌名时回复选歌提示。
- 1.1.0：新增 `@机器人 翻唱XX` 自然语言触发，自动清理“一下 / 一首 / 可以吗 / 给我听 / 这首歌”等口语尾巴。
- 1.0.2：支持 `@机器人 /翻唱 歌名`，修复 @ 时命令不触发、消息落到音乐卡片并显示“发送者版本过低”的问题。
- 1.0.1：默认改为只发语音，关闭等待提示、标题/UP 主/链接和失败链接回退。
- 1.0.0：首个版本，支持 `/翻唱`、候选打分、音频缓存、语音发送、失败回退、冷却和每日上限。
