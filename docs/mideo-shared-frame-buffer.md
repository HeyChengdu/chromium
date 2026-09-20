# Mideo 共享内存抓帧：实施状态与协议

## 状态

当前代码是未经 Chromium 编译验证的 Browser 进程原型，尚未实现 Viz 直接写入。
不能作为正式导出版本，也没有 10ms/帧性能结论。本地只修改源码，构建在 GitHub Actions 执行。

目标链路：Viz SoftwareRenderer → 持久 BGRA 共享内存槽 → 消费适配器 → FFmpeg。
当前原型仍经过 CopyOutput 临时位图和 Browser SkBitmap，仅移除 PNG 编码及大帧 Base64。

## 原型协议

宿主预创建文件，长度为 `width * height * 4 * slots`，启动时提供：

```text
--mideo-frame-buffer=/absolute/path/to/frames.bgra
--mideo-frame-buffer-slots=3
```

Linux 正式候选应使用 tmpfs 或 memfd；普通磁盘文件映射不能冒充相同性能。
每个浏览器与 CDP 会话独占一个缓冲区；当前原型不支持多个会话共同写入同一文件。

`Page.captureScreenshot(format: "mideo-shm")` 写入紧密排列的 BGRA8 非预乘像素，
返回 `data` 中仅包含 Base64 编码的元数据 JSON，而非像素：

```json
{"version":1,"slot":0,"sequence":"0","width":2560,"height":1440,"stride":10240,"pixelFormat":"bgra8-unpremul"}
```

消费完成后调用 `Page.releaseMideoFrame({slot:0, sequence:"0"})`。
槽位未释放时生产者返回错误，不覆盖旧帧、不静默丢帧。重复释放或旧序号释放失败。
序号使用十进制字符串，避免 JavaScript 数字精度损失。尺寸变化必须重建会话和缓冲区。

普通 FFmpeg rawvideo 不会自动映射这个文件或理解槽位协议。消费端还需原生适配器：
映射相同内存、按序处理元数据、将槽位交给 libavcodec，编码器不再引用该槽后再释放。
若采用 stdin 管道桥接，必须明确记录额外复制成本，不能称为零复制。

## Viz 直写的实施要求

1. 复用 Chromium SharedImage/BlitRequest 可映射目标能力，先验证能否复用既有 IPC；
   如果不能满足 BGRA 与宿主映射，再增加窄的外部缓冲目标，不建立平行截图系统。
2. 由 Browser 授权并传递句柄，Viz 不按网页传入路径打开文件。
3. 软件渲染器在无缩放路径直接向目标槽读取合成像素，不分配中间 SkBitmap。
4. 回执仅传状态、槽位和序号；原有 screenshot 的最新帧同步保证必须保留。
5. GPU、缩放、颜色空间不支持时明确失败，不返回上一帧作为成功结果。
6. 导航、尺寸变化、会话断开、取消与消费者崩溃都必须有清理路径。

## 构建资源

当前个人账户实际可用的 Linux x64 托管规格为 `ubuntu-24.04`：4 vCPU、16GB 内存。
没有已注册的自托管 Runner；GitHub 大规格托管 Runner 需要组织/企业账户：
https://docs.github.com/en/actions/how-tos/manage-runners/larger-runners/manage-larger-runners

工作流采用上述实际可用规格，清理一次性 Runner 中无关的 SDK 后记录真实剩余磁盘，
以 4 个编译任务并发尝试构建。官方保证的可用 SSD 仅为 14GB，而 Chromium 官方建议
至少 100GB 磁盘，因此即使清理后通过 45GiB 尝试门槛，也不保证足够完成构建。
GitHub 托管任务上限 6 小时；编译阶段限时 210 分钟，为依赖同步、检查和缓存上传留时间。
使用 sccache 复用编译结果，超时或磁盘不足均按失败报告，保留诊断，不发布残缺二进制。

工作流支持手动触发及 `mideo-raw-frame-capture` 分支推送触发；同步依赖固定到本次
提交 SHA，避免构建过程中跟随远端 main。
不使用用户本机作为 Runner，不在本地下载编译依赖或执行 Chromium 编译。

## 验收门槛

单红色像素冒烟只证明通道连通，不能作为画质或性能验收。
还须完整比较同一 Chromium 构建下 PNG 解码像素与 BGRA：文字、公式、Canvas、SVG、
渐变、透明度、连续变化帧、槽位环绕和背压；并验证帧号与实际画面对应。

性能统计需覆盖预热后的连续变化帧，报告均值、P50、P95及样本量，单浏览器与四浏览器
分别测试；初始化、合成、像素写入、消费等待和编码分开记录。
10ms/帧是目标，原生复制微基准不能替代这条端到端测量。
