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

### 单独修复媒体构建

工作流手动触发参数 `media_only=true` 只构建 FFmpeg，使用独立并发组，不中断正在运行的 Chromium 构建。此模式仅生成候选媒体产物，不执行完整像素与编码验收，也不生成已验证运行时。FFmpeg 自定义输入声明必须位于生成的 demuxer 列表之前。

### FFmpeg 独立仓库

FFmpeg 源码及构建已迁移到 https://github.com/HeyChengdu/FFmpeg 的 mideo-shared-input 分支，基于 n9.0.1。Chromium 不再下载 FFmpeg 源码或注入注册补丁，也不编译 FFmpeg。tools/mideo/ffmpeg-artifact-lock.json 固定完整提交，fetch-media.py 仅接受该提交的成功构建，并校验提交文件与二进制 SHA256SUMS，保留来源 runId 和校验和。media job 现在只获取独立产物；media_only 参数只执行获取。

独立产物是候选版本，仍须 Chromium verify 完成像素、编码与性能联合验收才能发布为组合运行时。Actions 产物保留90天，过期需明确重建固定提交。之前的“单独修复媒体构建”流程由本节替代。

独立联合验收入口为 verify-mideo-runtimes.yml：传入 Chromium 完整提交及运行 ID，校验运行来源和包内提交，再与固定 FFmpeg 候选组合；不重新编译任何一端。允许使用整体运行失败但 linux-x64 已成功上传的候选产物，最终仍须联合验收通过。

### Headless 构建图范围

GN 使用 `--root-target=//headless:headless_shell --root-pattern=//headless:headless_shell`，仅生成导出运行时及其传递依赖。完整 Chrome 菜单和测试目标不属于本产物，不应因默认全仓构建图而拉入已关闭的 PDF/打印模块。此调整保留原裁剪开关，不跳过 headless 实际依赖的编译和联合验收。GN 官方 setup.cc 的 FillOtherConfig 实现支持这两个参数，实际生成结果由 CI 验证。

构建入口已固化在 `.gn` 的 `root` 与 `root_patterns`，不再仅向 `gn gen` 传参。V8 metagen 在编译中独立调用 `gn desc`，也必须读取同一入口；CI 在编译前先执行对应 V8 目标查询，失败时直接保留错误。

### 有界编译与续跑试验

下一轮使用6个编译 worker，保持所有 Release、画质和功能参数。旧4 worker全量构建未在6小时内结束，不能只用不同文件组合的任务计数认定加速；vmstat与构建日志用于观察CPU、内存、交换和进度。

单轮编译最多270分钟，且最晚在Job启动300分钟时停止，预留约60分钟用于压缩上传；准备时间过长直接报错。超时后等待Ninja退出，保存完整out/Mideo与输入SHA256/权限/纳秒时间戳清单，上传mideo-checkpoint候选，然后明确标记需要续跑，不冒充构建成功。

手动workflow_dispatch的resume_run填上轮ID，并使用完全相同提交。恢复核对上轮提交、压缩包校验和及每个输入内容/权限；绝对路径也必须相同。任何工具链或生成输入变化均拒绝恢复，不能静默混用旧对象。仅核验通过才恢复输入时间戳与原输出。此版本只支持同提交续编，尚不提供跨提交增量缓存。产物保留7天，无额外付费资源；上传是否能在预算内完成、实际恢复复用率均待CI验证。

修改单元已经在上一轮通过，本轮直接构建headless_shell（其中仍包含这些单元），避免把单元预检时间放在有界编译计时之外。全部联合验收仍保留。

恢复控制脚本与编译源码可分离：build_commit固定编译源码完整SHA，controller读取当前工作流提交的恢复脚本；checkpoint仍必须属于build_commit，不允许跨源码提交复用。唯一时间标记例外为third_party/depot_tools/.disable_auto_update：上游脚本写入当前时间，但语义仅为存在即禁用自动更新；要求文件存在且格式匹配，不恢复其mtime。其余源码和工具链仍严格核验。

恢复核验会汇总全部缺失/变化文件的路径、大小、权限与哈希，不输出文件内容；差异报告随诊断产物上传。全部核验通过后才恢复时间戳，避免失败留下部分恢复状态。CIPD元数据不自动豁免，需根据完整差异判定是否发生工具包漂移。

CIPD私有pkgs数字槽发生重排时，恢复按相同实例后缀查找候选，并要求大小、权限及完整SHA256相同且唯一，才接受槽迁移；description.json同样要求完整内容匹配。不忽略二进制或包描述差异，不接受版本漂移。该规则用于验证编号变化假设，实际复用结果仍由CI确认。

匹配优先使用原路径且内容/权限完全相同的文件，仅原路径校验不通过时尝试CIPD槽迁移。这避免多个相同.lock文件产生虚假的迁移歧义，不降低内容校验。

多轮续编传入resume_controller以核对checkpoint来源运行的完整工作流SHA；build_commit独立固定实际源码，恢复清单继续严格核验源码提交与内容。两者不再错误地要求相等，来源运行校验不取消。
