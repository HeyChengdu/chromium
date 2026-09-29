# Mideo 软件输出 Surface 契约

## 已确认的失败

诊断运行 `36522039310` 复用 `dc7407a62b55df661f23dc978f222ba581c30ce0` 候选。
冷启动、两次 rAF、PNG 预热均到达 `Mideo.DeliverPresentedFrame`，实际走
`SoftwareRenderer`。此前将失败归因于 SkiaRenderer 入口拒绝的判断不成立。

默认 `SoftwareOutputDevice::Resize` 创建无颜色空间标签的 opaque 画布，
而共享交付要求 sRGB。即使去掉颜色空间检查，也不能恢复 opaque 画布已经
丢失的透明信息，因此不允许通过放宽检查消除报错。

## 修复边界

- 只有接收 Mideo 请求的设备调用 `EnableMideoFrameOutput`，将最终栅格
  Surface 切换为 sRGB、premultiplied Alpha；其他设备保留原默认行为。
- 首次切换丢弃旧画布，因此 `Display::DrawAndSwap` 在待交付请求的每次
  聚合前标记根 Surface 完整损伤，避免中间帧消耗一次性标记或裁掉未损伤区域。
- 同一设备后续请求不重新分配，Resize 保持导出格式。平台设备若未使用基类
  栅格 Surface 则拒绝切换，不伪造支持。
- `WriteMideoFrame` 继续检查尺寸、sRGB，并检查 premultiplied Alpha；
  读回仍直接进入共享内存，交付格式为 BGRA8888、unpremultiplied Alpha、sRGB。
- 仍以同一交换的像素交付和 presentation feedback 双条件完成请求。
  不添加 `Page.captureScreenshot` 回退。
- `MideoFrameBuffer` 在允许阻塞的线程关闭文件并释放独占锁，避免 CDP
  会话在 UI 线程析构时触发已有的 blocking DCHECK。

## 验证顺序

原生构建和执行均在 Actions。定向门禁包含修改过的生产对象，并构建运行
`mideo_software_output_device_unittests`，验证启用前默认格式、启用后 Alpha、
重复请求保留内容、Resize 和其他普通设备的隔离。

运行 `36543112892` 已完成 7023/7023 个生产编译及生成任务，但测试步骤报
`unknown target`，尚未执行断言。原因是 `.gn` 的 `root_patterns` 只保留
headless_shell 依赖图，排除了独立单测。现将该测试精确加入根模式，并在
生产编译前执行 `gn desc` 校验目标；不把测试加成浏览器的运行时依赖。
根模式行为见 [GN 官方说明](https://gn.googlesource.com/gn/+/main/docs/reference.md#dotfile)。

完整候选继续执行 `shared_frame_buffer_smoke.py`。新增冷帧先共享交付再与 PNG
逐字节比较，并与独立的、未启用共享导出的普通 PNG 会话比较首帧，避免两条
路径一起改变画面后相互印证。还检查明确的半透明红色和透明背景；关闭后不能出现 blocking
DCHECK。保留连续帧、背压、同步 DOM、编码像素一致性和墙钟门禁。

这些检查通过前不能称修复完成，更不能据此推断整课无损或每帧低于 10ms。
Phase 1 保留，最终仍在 MagicTutor 隔离工作树以正式 2560×1440、PNG、
crf15/slow/aq-mode3 做课程逐帧与墙钟验收。

## 2026-09-29 完整候选的 Alpha 失败

运行 `36631448422` 使用源码 `3b03e707bbc9315209f68f579faae7e48dc1b11f`，
恢复时两个 checkpoint 文件 SHA256 通过，1,107,166 个输入内容核验通过，
Ninja 最终完成 3580/3580 并成功打包。验收已执行到半透明像素断言，
此前冷帧、普通会话对比、36 帧像素及编码、背压、同步 DOM 检查未报错；
该结果不能代替完整验收。Alpha 断言失败后性能步骤尚未执行。

现有失败产物没有保存该断言处的实际像素，因此先扩充候选复用诊断脚本，
记录透明场景首个共享帧、随后 PNG、PNG 后共享帧与两次 rAF 后共享帧的
首尾 BGRA、全帧 Alpha 分布及图像，并分别检查首次启用和不透明帧之后切换。
这些预热对照只用于区分问题来源，不改变正式门禁或允许截图回退。

诊断运行 `36644069640` 复用该二进制，证明冷启动和切换后共享帧的 Alpha
均为 255；PNG 则为红色 `[0,0,255,128]` 与透明背景 `[0,0,0,0]`。
PNG 预热和两次 rAF 后共享帧仍不透明，排除单纯等待不足。

沿实际合成链路审计：`HeadlessWindowTreeHost` 创建外层 `ui::Compositor`；
`cc::CommitState::background_color` 默认为白色；
`LayerTreeHostImpl::CalculateRenderPasses` 将不透明根背景标记到根 render pass
并填充背景；`SoftwareRenderer::ClearFramebuffer` 仅对透明 pass 清透明色。
因此只修正最终 SkSurface 的 Alpha 格式仍无法恢复已被外层合成压平的信息。

本次仅为显式带 `--mideo-frame-buffer` 的 headless 导出进程，在首个合成帧前
将外层 compositor 背景设为透明，页面自身的背景保持由页面渲染。未带该开关
的普通会话保留默认白底。正式门禁的普通 PNG 基线改为真正不传导出开关的
独立会话；Alpha 断言保持原值，同时在断言前保存共享帧、PNG 与实际首尾像素。
定向门禁加入 headless_window_tree_host 生产对象，总计 19 个；静态检查
不能证明该修复成功，仍须先通过定向编译与软件画布单测，再重做完整验收。
