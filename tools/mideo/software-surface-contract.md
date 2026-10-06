# Mideo 软件输出 Surface 契约

## 2026-10-06 全不透明像素优化的红灯契约

真实课程 `37482055331` 的 120 次 ReadPixels 全部精确配对；51 个 CPU
样本落在其中 50 次调用内，父栈均为 WriteMideoFrame → SkCanvas::readPixels
→ SkConvertPixels → SkRasterPipeline，叶函数以 store_8888 和 unpremul 为主。
离散采样比例不代表精确耗时，也不能将其他 Draw 的样本当作可删除帧。
该窗口候选端到端均值 11.259875ms，91/120 超过 10ms；无原生优化收益结论。

审计 DEPS 固定的 Skia `5456aa926156029fac5cdf5cf5a863cbe9cf3a75`：
SkPixmap::computeIsOpaque 对 BGRA8888 逐行扫描实际 Alpha；相同颜色空间且
opaque 源到 unpremul 目标不需要反预乘，SkConvertPixels 可走 rect_memcpy。
因此只评估可读、BGRA/Premul/sRGB、尺寸与紧密 rowBytes 严格匹配并且实际
每个 Alpha 均为 255 的像素。其他情况完整保留原 readPixels。

当前只加入 `CopyOpaqueMideoPixels` 的拒绝占位及四项真实 Skia 像素契约测试：
全帧非均匀色值逐字节及前后边界、任意位置单个 Alpha=254、透明像素，以及
空地址／未知颜色空间／格式／Alpha／尺寸／步长／输出容量不匹配的无写入拒绝。
尚未接入 WriteMideoFrame、实现快速路径或宣称提速；先由 Actions 运行确认
全不透明复制的预期行为红灯，已有 Surface、Alpha、Resize 门禁同时执行。
红灯后再实现最小候选、定向绿灯、同新源码完整构建和同 runner 真实课程，
最终完整旁白与逐帧质量验收通过前保持默认关闭。

首轮 `37488790510` 生产对象编译通过，软件测试目标在 16350/16351 编译
测试文件时失败：base::span 的 StrictNumeric 步长参数拒绝有符号 int 字面量。
将 subspan/first 的非负常量改为无符号字面量，保持边界和所有断言；本轮未
链接或执行测试，不能计为预期行为红灯。完整日志保存为
`/tmp/mideo-gate-37488790510.log`，修复后仍先执行同一红灯门禁。

## 2026-10-03 真实课程 10ms 主线的损伤边界验收

有效同机课程窗口显示目标帧提交被先前 Viz 绘制阻塞。现有 Display 在待交付
请求的每次聚合前强制根 Surface 全损伤，目标 token 则在聚合后才检查。
定向门禁 `37107414467` 的生产对象编译已完成，软件测试目标在编译真实
`viz/test:test_support` 时被 Chromium 风格检查拒绝：测试宿主的
`ArmMideoFrame` 含非空内联虚函数体。将原拒绝回调逐字移至 `.cc`，不修改
宿主行为、生产策略或风格检查；本轮尚未执行行为测试，不算预期红灯。

先新增 `MideoDisplayDamageTest`：通过真实 FrameSink 提交、Surface 聚合和
软件画布绘制观察旧 token 的局部损伤、目标 token 的全损伤及远角蓝色像素，
同时保留普通帧局部损伤与已有 Alpha／Resize 测试。仅宿主输出使用既有
FakeSoftwareOutputSurface 测试适配器，不替换 Surface 状态或渲染结果。

修复宿主后，`37125503023` 完成测试目标 16355/16355 编译与链接。四项实际
测试中仅 `UnarrivedTokenKeepsNormalDamage` 失败：预期 10,10 1×1，实际
0,0 100×100；目标 token 全损伤及远角蓝色、普通帧局部损伤和 Alpha／Resize
全部通过。这是实际行为红灯，完整日志为 `/tmp/mideo-gate-37125503023.log`。

候选在 Arm 时保存已经完整聚合的 Surface ID、输出矩形与嵌入／参考范围快照。
聚合前从当前根遍历全部 render pass 的 SurfaceDrawQuad，并保守加入 metadata
参考范围；按聚合器相同的 GetLatestInFlightSurface 规则解析。只有目标 Surface
仍是原 ID、非空旧 token，且全部节点与快照尺寸／范围稳定，才不额外强制根全损伤。
目标 token 到达、范围回退、新 ID、尺寸改变、缺失／未知节点、重复路径或超过
256 Surface／8192 Quad 预算均保持全损伤。正常 Draw 不删除，聚合后精确 token、
完整画布、copy 和 presentation 双门禁保持。新增实际嵌入旧／目标帧、缺失目标、
空 token、根／子新 ID、范围回退、未知嵌入、拓扑改变、多目标 Surface、输出 Resize
和 Quad 预算边界验收。尚待 Actions 编译与绿灯，不能称候选质量或性能已通过。
新候选仍须定向编译与单测、完整构建、同机课程
120 请求分布及完整真实课程质量联合验收，不能提前声称提速或低于 10ms。

候选首个定向门禁 `37134451537` 在生产 Display 编译中发现 token 字段为
`optional<UnguessableToken>`，不能直接调用 `is_empty()`。判定改为先拒绝字段缺失，
再拒绝空 token；两种情况均保持全损伤。尚未运行候选行为测试，不作绿灯或提速结论。

`37136437305` 完成编译、链接并实际执行 17 项测试，16 项通过；唯一失败是
`NewRootIdKeepsFullDamage`：新根 ID 仍只有 1×1 损伤。源码确认既有
`SetFullDamageForSurface` 只标记已存在的解析缓存，新根尚无缓存时无操作，
随后解析可能继承旧 Surface 的帧索引。因此增加仅供 Mideo 使用的强制损伤入口，
先按既有规则解析实际 active Surface，再令前帧索引失效；缺失 Surface 仍返回空帧。
普通绘制的既有入口保持原语义，根 ID 变化的全损伤断言保留。定向编译增加实际
修改的 SurfaceAggregator 对象，六 worker 与原预算不变；新门禁仍须实际全绿，
尚无候选性能或整课质量结论。

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

定向门禁 `36644490564` 在安装 depot_tools 时失败，尚未同步 Chromium 依赖或
编译生产对象：浅克隆报 HEAD 指向不存在的对象
`23d22cee4f996e8c9687ff8efe43eefe2536d645`。只读核对发现远端 Git ref
仍广告该值，但该提交的 Gitiles 查询返回 404；Gitiles main 与提交历史均指向
可读取的 `b2042c50e4d8a0ecc69ebc60983024a5b477c4ca`。
工作流改为固定后者并按完整 SHA 获取，检查 commit 对象、检出 HEAD 和对象连通性，
将工具版本写入构建诊断，再执行 bootstrap。没有因此修改生产渲染源码，也没有
在本地下载工具链或编译。该获取修复仍由新的 Actions 运行验证。

## 2026-09-30 DPR2 课程接入失败

完整构建 `36712286102` 的原始像素、编码、背压、同步 DOM 和 Alpha 门禁
全部通过，半透明首像素为 `[0,0,255,128]`，透明尾像素为 `[0,0,0,0]`。
该合成测试使用 DPR1，不能代替 DPR2 课程接入。

MagicTutor 运行 `36731621530` 在普通 PNG 基线完成后，首个共享请求报
`Cannot acquire exclusive Mideo buffer`，尚未到达课程逐帧对比。
调用链中 Node 按物理 2560×1440 创建独立 tmpfs 文件，而 PageHandler 把
`GetViewBounds().size()` 传给文件长度校验；打开、锁定、尺寸失败共用同一报错。
这提示逻辑尺寸与物理尺寸不一致，但不能仅凭错误文案认定锁竞争或已确认根因。

复用同一二进制增加 DPR1/DPR2 与物理/逻辑文件尺寸的对照诊断，记录 DOM 尺寸、
文件长度、首次请求前独占锁可用性、共享结果和随后 PNG 尺寸。小文件对照只用于
区分失败位置，不修改课程输出质量，也不充当截图预热或回退。

诊断 `36734489354` 已确认：DPR2 + 2560×1440 文件在独占锁可用时被拒绝；
DPR2 + 1280×720 文件能交付，但 metadata 为 1280×720，普通 PNG 为 2560×1440。
`ScreenMetricsEmulator` 保留真实设备比例用于 compositor，模拟 DPR 并不自动
放大最终呈现视口；普通截图另行设置 viewport scale 并临时放大视图。
仅替换 CDP 的尺寸查询不能产生缺失的物理像素。下一对照通过现有
`Emulation.setDeviceMetricsOverride.viewport` 固定物理合成视口，同时保留
1280×720 CSS 视口和 DPR2，与独立未开启 Mideo 的普通 PNG 逐字节比较。

课程接入 `36735664645` 在缓冲区打开之后报 `Viz presented-frame delivery failed`，
尚无课程像素结果。成功的 Python 诊断以物理尺寸启动窗口，而 Playwright
`_updateViewport` 会把外层窗口设置为逻辑尺寸；Emulation 只更新子视图，
`HeadlessPlatformDelegate::SetWebContentsBounds` 才同时更新 RootWindow。
`Display::ArmMideoFrame` 仍要求请求尺寸等于外层 `current_surface_size_`。
新增同二进制对照：从逻辑窗口启动，分别保持原窗口、通过 Browser.setWindowBounds
改为物理窗口，再设置相同 Emulation viewport；记录窗口边界和独立 PNG 像素结果。

诊断 `36739302036` 复用 `dcfff3ce0774fe7d11e243174fed0bc0eefb4c14`：
逻辑窗口 + 显式 viewport 复现相同 Viz 交付失败；逻辑窗口先改为物理窗口后，
4 帧（含透明帧）均与独立普通 PNG 逐字节一致，DOM 保持 1280×720 / DPR2，
共享回执为 2560×1440。MagicTutor 候选 `a429956395884f0979deb11770d8ed7d54dcfe77`
因此在共享 Adapter 初始化时先设置外层窗口，再设置逻辑视口和 DPR；14 项
宿主协议/物理目标测试及定向类型检查通过，课程门禁为 `36740179652`，结果待定。

该诊断还有未解决的质量信号：直接以物理窗口启动的对照，前三帧出现文字像素
差异（2117 / 1974 / 2056 像素，范围仅在顶部文字，Alpha 无差异），第四透明帧
一致；同场景在 `36734926461` 曾全部一致。尚不能确定差异原因，也不能将
本次诊断整体宣称为像素通过。保留原始图片、JSON 和严格课程哈希门禁；
当前结果只能支持外层尺寸是交付失败的原因，不能证明课程或所有场景已保真。


## 2026-09-30 整课质量与构建性能归因

MagicTutor `36785592923` 的五组完整受控旁白时间线均为 2239 帧：受管 145 PNG、
候选 156 PNG、候选共享、受管禁 GPU PNG、候选默认参数 PNG。全部逐帧哈希精确
相同，测试通过；工作流只在制品配额步骤失败。六个 gzip 已从原始日志恢复并验证
长度与 SHA256，独立逐帧复核位于 `/tmp/mideo-full-validation-36785592923`。
这是 videoOnly 受控旁白课程，不能混称生产真实音频验收。

五组墙钟依次为 413.897、694.087、579.539、405.728、698.197 秒。共享比同候选
PNG 缩短 16.503%，但仍比受管 PNG 慢约 40%。禁 GPU 参数不能解释本轮主要差距，
标签不证明实际 GPU 后端。页面推进累计受管约 204–209 秒、候选约 447–456 秒；
普通 PNG 抓帧约 151–158 秒接近，阶段有嵌套，不能相加推导总墙钟。

从已成功构建 `36712286102` 的 `v8-compile-flags.json` 核实：虽然使用 `-O3` 和
`NDEBUG`，仍包含 `DCHECK_ALWAYS_ON=1`、`DEBUG`、`CPPGC_VERIFY_HEAP`、
`V8_VERIFY_WRITE_BARRIERS`、`V8_ENABLE_DEBUG_CODE` 等调试宏。
`build/config/dcheck_always_on.gni` 对非 official 构建默认启用 DCHECK 与昂贵检查，
`build/config/BUILDCONFIG.gn` 明确指出仅 `is_debug=false` 不等于发布性能配置。
这提供下一项单变量构建对照，尚未证明全部回退均由此导致。

工作流显式设置 `dcheck_always_on=false`、`enable_expensive_dchecks=false`；其余
编译优化、PGO、并发和资源参数不变，避免同时引入 LTO 或其他优化混杂。保存实际
`args.gn` 和 V8 编译宏，并在编译前要求 `NDEBUG` 存在、`DEBUG`／
`DCHECK_ALWAYS_ON` 不存在。`MideoFrameBuffer::Open/Reserve/Release`、
`Display::ArmMideoFrame` 与 `SoftwareRenderer::WriteMideoFrame` 的尺寸、独占锁、
槽序列、映射范围、sRGB／premul 检查均为普通条件判断，未修改或放宽；普通 CHECK
仍保留。已通过的带 DCHECK 二进制与证据保留。

新提交必须先在 Actions 通过 19 个生产对象定向编译及软件 Surface 单测，再从新
源码 SHA 完整构建，不跨 SHA 复用旧 checkpoint。随后重跑原始像素、Alpha、背压、
同步 DOM、编码及完整课程逐帧和墙钟验收；当前只提出构建对照，不能宣布性能已修复。
