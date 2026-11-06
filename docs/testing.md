# 测试与验证

公开仓库仅保留使用合成数据的测试和可复现的验证方法。真实部署的地址、实例标识、订阅、服务状态、操作日志、截图和验收记录应保存在仓库之外。

## 本地检查

```sh
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
python3 scripts/check-public-source.py
python3 scripts/export-api-contract.py
python3 scripts/check-api-contract.py
git diff --exit-code -- docs/openapi-v1.json integration/fixtures
node --check integration/gateway.js
node --check integration/ppsub_panel.js
```

API 契约校验需要 `jsonschema`，YAML 相关功能需要 `PyYAML`。面板构建和调用打包脚本的测试需要 Node.js 18+（Debian Bookworm 可安装 `nodejs`）；不需要安装 npm 依赖，Node 不属于生产运行依赖。`PPGW_NODE` 可指定构建解释器。确定性样例来自合成核心与实际 HTTP handler，不代表外部网络或真实部署已经通过验收。

受限 Mihomo 热加载的真实连接与完整 Debian 服务验收分别使用 `scripts/verify-mihomo-reload.py` 和 `scripts/verify-mihomo-policy-linux.py`，运行条件与覆盖边界见[热加载说明](mihomo-reload-assessment.md)。只在一次性隔离环境运行失败注入，不对现用配置执行此类实验。

```sh
node frontend/build.mjs
node --test frontend/tests/model.test.mjs frontend/tests/diagnostics.test.mjs frontend/tests/overview.test.mjs frontend/tests/proxies.test.mjs frontend/tests/rules.test.mjs
```

```sh
cd sniffbox
cargo test --workspace --locked
cargo build --release --locked -p sniffbox
```

## 浏览器检查

安装 Playwright 和 Chrome 后先生成面板资源，再运行使用合成 API 的浏览器脚本：

```sh
python3 scripts/prepare_dashboard.py --output dist/test-ui
node scripts/verify-settings-fixture.cjs dist/test-ui
node scripts/verify-dashboard-fixture.cjs dist/test-ui
node scripts/verify-runtime-navigation.cjs dist/test-ui
node scripts/verify-unified-runtime.cjs dist/test-ui
node scripts/verify-overview-fixture.cjs dist/test-ui
node scripts/verify-proxies-fixture.cjs dist/test-ui
node scripts/verify-rules-fixture.cjs dist/test-ui
node scripts/verify-monitors-fixture.cjs dist/test-ui
node scripts/verify-subscription-flow.cjs dist/test-ui
node scripts/verify-subscription-drafts.cjs dist/test-ui
node scripts/verify-source-editor.cjs dist/test-ui
node scripts/verify-complex-editor.cjs dist/test-ui
node scripts/verify-diagnostics-fixture.cjs dist/test-ui
node scripts/verify-observer-api-fixture.cjs dist/test-ui
```

`PPGW_PLAYWRIGHT_MODULE` 和 `PPGW_PYTHON` 可指定本地工具位置。生成资源、截图和日志保存在被忽略的 `dist/` 中。

订阅草稿验证同时检查结构化变化预览、来源覆盖说明、敏感内容隐藏、名称按文本显示、后台生效计划及手机预览尺寸；API 使用合成响应，不触碰真实订阅或服务。

源码编辑器验证在非安全 HTTP 合成地址上检查登录、重新认证保留草稿、偏好保留和消息来源校验，并将实际表单提交交给后端 PPsub 校验或 Clash 合并函数，验证未编辑字段和隐藏凭据保留。`tests/test_dashboard.py` 检查构建清单、原兼容 bundle 字节不变和重复构建结果一致。

概览验证直接加载可维护源码，断言不请求兼容 bundle，所有状态请求只读；覆盖动态采样、计数与启动身份变化、静态嵌套字段保留、API 故障重试、重新登录、主题、图表悬停、页面离开后的取消与迟到响应，以及 390px/320px 手机布局。冷启动节点选择的单元与真实 Mihomo 隔离复验见 [节点选择保留](node-selection-persistence.md)。

代理页验证直接加载可维护源码，断言初次读取不测速、不更新、不切换。覆盖 Selector 与自动组的行为差异、普通及智能锁定/解锁、长按、组测速前解锁、URL 编码与验证、提供商更新、限定旧连接关闭范围、仅模式字段的 PATCH、状态失败及 ETag、重新登录后不重放操作、搜索焦点和偏好保留、节点详情字段白名单、源码页面前进后退、离页停止与迟到响应。大列表使用 60 个节点组和 1000 个候选，检查每组分批展开、末尾节点搜索与 390px/320px 手机布局。这些是合成 UI 验证，不替代真实 Mihomo 或客户端业务验收。路径段名称 `.` 和 `..` 无法可靠通过浏览器发送，界面会拒绝相关操作并要求调整配置名称。

规则页通过 `scripts/verify-rules-fixture.cjs` 验证初次只读、后端规则索引、临时启停与失败保留、重复操作抑制、仅模式字段 PATCH、分层节点组与手动选择、自动/智能组只读、提供商更新、GEO 二次确认和逐文件失败、主动 URL 测试及日志定位、429 提示、状态失败与畸形响应恢复、重新登录不重放、搜索焦点与偏好、源码页面前进后退、隐藏/离页及队列终止。规模检查包含 10000 条规则、1000 个组候选，核对默认分页、末尾规则搜索、分批展开及 390px/320px 表格内部滚动。`--baseline` 从单独的基线构建记录原页面。以上为合成 UI 验证，真实网关和客户端业务需另行验收。

复杂编辑器验证使用 12 个提供商、60 个节点组、200 条规则和 1000 个节点候选，分别检查原生 PPsub、完整 Clash 配置的 1440px 桌面和 320px 手机布局。长名称包含按文本展示的 HTML 样例；测试末尾分组的编辑、复制、移动、删除、节点标签提交、模式切换及预览保存，并检查提供商后台刷新与页签切换保留草稿。提交交给实际后端校验或合并函数，逐项比较未编辑分组、规则、扩展字段和隐藏凭据；完整配置重命名分组时还检查规则引用按原顺序同步更新。证据默认写入 `dist/complex-editor-evidence/`，第三个参数可指定其他证据目录。该验证不访问真实机场，也不代表终端网络已通过验收。

诊断恢复验证检查分页备份校验、损坏备份禁用、脱敏报告固定字段、签名预览与确认、过期预览拒绝、网络断开与刷新后按原任务号续查、待核实任务的导航保护，以及历史密码恢复后的重新登录。明确被拒绝的提交与查询时认证失效分别处理；成功结果区分真实验收、目标未启用和暂存环境，并验证管理任务服务保留状态的例外提示。所有操作均使用合成响应，同时检查 320px 手机布局、主题和文本安全。

未知任务的 404 不会自动解锁或重放：页面保留任务号并展示只读诊断；存在待恢复事务时不能解除。只有人工明确确认已通过管理脚本或状态核对，且点击时再次读取诊断确认无待恢复事务，才停止本浏览器追踪；测试同时断言该步骤不会创建、取消或重新提交服务端任务。

诊断 fixture 还会延迟旧任务完成后的运行信息请求，在此期间提交第二个备份任务，验证新任务无需人工操作即可继续查询至终态；桌面导航按 free 模式完整可见项集合、文字与位置边界验收，避免空集合检查误通过。

## 隔离运行验证

安装包可用暂存根目录检查文件布局、摘要及升级保留行为。真实 systemd、服务权限和透明转发验证需要独立 Debian 环境及可丢弃客户端；不能以本机 API 或显式代理成功代替完整转发路径验证。

`scripts/verify-observer-api-linux.py` 只接受带标记的一次性 systemd 容器，覆盖 API 默认关闭、管理员启用、Token 轮换、停用后释放端口、关闭采集和启用失败回退。其他使用 API 的隔离网络脚本需要先在测试配置中明确开启 API。

`scripts/verify-native-network.py --run --fixture-only --report /evidence/network.json` 可在安装了正式发行包的一次性网关中验证 FakeIP DNS、IPv4/IPv6 透明 HTTP、普通 UDP、UDP/443 回显和透明入口连接记录。需要 root、`/run/management-validation-only` 标记以及启用 UDP、IPv6 和连接采集的合成配置。该模式允许本地 YAML，跳过公网 HTTPS，并在报告中标记 `synthetic_only`；不会把隔离结果当作现用代理出口或真实客户端业务验收。缺少 `--run` 或标记时拒绝执行。UDP/443 回显不能证明 QUIC 协议兼容；公网 IPv6 需另行具备出口并测试。

脚本在网关所在网络命名空间创建临时客户端、目标和转发规则，结束后检查配置、运行模式、节点选择、外部防火墙和路由恢复。应使用独立网络的可丢弃网关，保留同版本二进制摘要、结果、恢复核对和容器清理证据；不要挂载真实运行配置或代理凭据。报告中的 `complete` 只表示检查执行结束，业务验收应另列通过和失败项。

FakeIP DNS 同时监听 UDP 和 TCP，两种协议共用解析与地址池。Rust 测试覆盖 IPv4/IPv6、分段帧、同连接连续查询和流水查询、大报文、畸形查询后续查、读写超时、连接上限、关闭释放及 TCP 占用时释放候选 UDP 监听；管理预检分别验证 TCP、UDP 占用拒绝。隔离网络脚本分别查询两种协议并比较 FakeIP 答案。

验收时仍需核对实际监听进程和透明转发规则，排除其他 DNS 或透明代理代答。公网出口对照也应排除其他透明代理的 OUTPUT 接管，记录同一节点、相同信任设置和相同网关配置下的前后结果。停止其他代理时先检查其清理脚本是否会清空网关共用的策略路由表，不要让测试破坏已有转发路径。

测试输入应使用虚构名称、文档示例地址和合成凭据。公开构建仅从源码生成通用安装包，不包含运行配置、订阅缓存或部署验收记录。

## 发布前检查

首次使用克隆后运行 `git config core.hooksPath .githooks`，启用推送前检查。检查覆盖本次推送的所有引用及其可达历史，拒绝部署记录目录、私有归档和运行凭据文件；CI 也执行同一检查。检查是对文件位置的保护，不能替代对源码内容的脱敏审查。

历史清理后，旧克隆应重新克隆，或仅将审查后的修改移植到新的主线。不要将旧分支合并回来或恢复公开推送旧备份。真实部署的截图、日志和记录继续保存在仓库之外。

连接、流量、分布、日志、区域通过 `scripts/verify-monitors-fixture.cjs` 使用本机 HTTP/SSE 合成服务验证，浏览器阻断所有外部请求。覆盖活动/已关闭连接、筛选和列偏好、显式关闭、流量分组及 JSON 导出、暂停恢复、六组分布排行和确认重置、安全 ANSI、独立日志搜索和暂停缓冲、2000 行保留上限、59 项区域定义、429 重试、最多五个检测请求、排队取消、隐藏/离页停止、401 后共享重新登录不重放操作、未启用采集和不可用终端，以及桌面亮暗主题与 320px 手机。`--baseline` 从迁移前构建采集同一数据的原页面截图。Node 测试补充流式 UTF-8/CRLF、多行 SSE、计数重置、最长持续时间、CSV 单元格转义、检测依赖和队列取消。上述为合成 UI 验证，不代表真实出口或商业服务解锁验收。
