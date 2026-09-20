# Mideo Viz 共享帧：实现、构建与验收

## 状态与边界

版本 2 的代码链路为：

```
Mideo 教学时间轴 → Browser 强制重绘 + 新 Surface
→ Viz SoftwareRenderer → 持久 BGRA 槽
→ FFmpeg mideoshm 输入 → BGRA 转 YUV420P → 原有 libx264 与封装
```

代码已包含这条路径及验证程序；Chromium/FFmpeg 的编译、真实像素和性能结果以 CI 为准。
没有 10ms/帧达标结论，也没有对既有正式视频的兼容性验收结论。
本地只编辑源码和运行宿主协议测试，原生构建仅在 GitHub Actions 执行。

只支持 Linux x64、软件合成、固定画幅、sRGB、无缩放完整 viewport。
GPU、缩放、越界、像素尺寸不匹配会失败，不暗中回退 PNG 或复用上一帧。
普通截图路径及 Mideo 默认发布设置保持原实现，候选路径需要显式启用。

## 浏览器与共享内存协议

宿主预创建 tmpfs 文件，并实际分配 `width * height * 4 * 3` 字节，启动时传入：

```
--disable-gpu --force-color-profile=srgb
--mideo-frame-buffer=/dev/shm/独立目录/frames.bgra
--mideo-frame-buffer-slots=3
```

Browser 在阻塞线程打开文件并取得排他文件锁；CDP 仅允许可信、有本地文件权限的会话。
Viz 只收到授权文件句柄，不能按网页给定路径打开文件。每个显示器缓存最近一个映射，
换缓冲身份时释放旧映射，Browser/Viz 退出时释放最终映射，不累积会话缓存。
宿主必须先终止浏览器再删除文件；错误后的新导出使用新的 Browser 和文件。

`Page.captureScreenshot(format:"mideo-shm", fromSurface:true, captureBeyondViewport:false)`
保留 ForceRedraw → RequestRepaintOnNewSurface → 等待新 Surface 的顺序。
SoftwareRenderer 直接 readPixels 到目标槽，无中间 SkBitmap，无 PNG 编码，无全帧 Mojo 回传。
回传 `data` 仅为下面 JSON 的 Base64：

```json
{"version":2,"slot":0,"sequence":"0","width":2560,"height":1440,"stride":10240,"pixelFormat":"bgra8-unpremul","producer":"viz-software"}
```

捕获串行提交；槽未释放不能覆盖。消费完成后，宿主调用
`Page.releaseMideoFrame({slot:0,sequence:"0"})`；旧序号、未知槽和重复释放失败。
错误回执释放本次预留槽；成功帧保持占用直到宿主显式归还。
尺寸变化需要新的缓冲和浏览器。这里的持久映射是 Browser 生命周期资源，不承诺断开
CDP 后在同一个 Browser 内重新取得同一个文件的租约。

## FFmpeg 消费协议

`tools/mideo/ffmpeg/mideoshm.c` 是原生 FFmpeg demuxer，而不是 Node 像素管道桥。
输入参数示例：

```
-f mideoshm -buffer_path /dev/shm/独立目录/frames.bgra
-video_size 2560x1440 -framerate 30 -slots 3 -ack_fd 1 -i pipe:0
```

stdin 每行 `slot ticket\n`，ticket 从 0 连续递增；stdout 仅回传 `ticket\n`。
FFmpeg 只映射一次 BGRA，swscale 转换直接写入独立 YUV 编码输入后才发送确认。
因此编码器的 lookahead 不再引用源槽，不要求更改 preset、CRF 或关闭 B 帧来释放共享内存。
这不是完全“零复制”：有一次合成像素读出和必要的 BGRA→YUV 转换；消除的是中间
BGRA 位图、图片编码/解码、像素 Base64 和跨进程全帧管道搬运。

`build-media.py` 使用 `media-lock.json` 中 SHA256 校验的 Mideo 同源 FFmpeg、x264、LAME
和原构建配置，只添加 mideoshm 与 swscale。分发包包含许可证、来源锁和补丁源码。

## Mideo 接入

MagicTutor 接入提交为 `6b7b76404c`。runtime 新增 `SharedFrameRenderer`，显式设置
`MIDEO_SHARED_CAPTURE_DIR=/候选制品目录` 后，从中读取 `headless_shell`、`ffmpeg`。
只允许 PNG 发布档、显式 width/height；不混用 native-svg 或其他截图模式。
普通运行时锁、默认导出与 Modal 部署不会自动切换。

正文像素不进入 Node。宿主逐帧等待 FFmpeg 消费确认，然后报告进度；静态画面和片尾
继续使用保留槽，变化帧到来才归还旧槽。封面在正文之前一次转为 BGRA 写入槽 0，
正文开始后拒绝封面写入。取消、失败和完成均依次关闭编码器、帧 Adapter、浏览器及 tmpfs。
每个 Browser 有独立文件和进程配置，支持原有四 Browser 并行编排。

## 构建时限与已知历史

使用个人账户可用的 `ubuntu-24.04` 托管 Runner。上次实测 4 核、约 15GiB 内存，
清理后 110GiB 可用磁盘。210 分钟时编译达到 26231/40269 个构建步骤，因自设时限退出。
sccache 的 21702 次调用均不可缓存，因此本版本删除无效缓存配置，不声称可以断点续编。

所有任务 `timeout-minutes: 360`，删除编译命令的 210 分钟 timeout。
Chromium、同源媒体构建、真实验证分别运行在独立 job；验证不占用 Chromium 的编译窗口。
平台 6 小时上限包含该 job 的源码准备与上传，不能保证获得整整 360 分钟纯编译时间。
也不能保证超时后的 always 步骤仍有机会保存产物。未验证构建只标为 candidate。

## 截图专用构建裁剪

关闭 PDF（`enable_pdf=false`）、打印与 CUPS（`enable_printing=false use_cups=false`）、
插件支持（`enable_plugins=false`）及 Shell 命令行截图/打印入口
（`headless_enable_commands=false`）。这是四类功能、五个构建参数。
CDP 截图和 Viz 共享帧接口保留，绘制、字体及图像解码能力不在此次裁剪范围内。
此组合的编译兼容性与截图效果由同一 CI 门禁验证；编译加速幅度尚未实测。

## 验收门禁

`shared_frame_buffer_smoke.py` 使用真实二进制，检查：

- 三槽背压、失败不覆盖、释放与重复释放拒绝。
- HTML 中英文与数学字符、Canvas 渐变与动图形、半透明 SVG、透明背景。
- 连续 36 帧与同构建 PNG 解码逐像素一致、变化帧不重复。
- 使用相同 FFmpeg/x264/CRF15/slow/aq-mode3，PNG 与共享输入的成片解码像素完全一致。
- 1440p 单 Browser 与四 Browser，预热后分别记录 PNG 和共享捕获的 mean/P50/P95。

测试日志、代表帧和对照 MP4 作为验证证据上传，像素或成片门禁失败不生成 verified 制品。
性能报告是捕获命令端到端耗时，不能冒充整课导出速度或分阶段 CPU profiler。
完整 Mideo Lesson（封面、旁白、片尾、背景音乐）的实际视频回归仍需要用构建产物执行；
正式替换前还必须对比既有基线浏览器，不以“同构建 PNG 一致”替代跨版本画质验证。
