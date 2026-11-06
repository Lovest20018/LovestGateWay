# Web 管理界面优化实施方案

本文档供实施者（人或模型）直接执行。内容来自 2026-11-06 对测试部署的实际走查（桌面 1120px、750px、375px，亮/暗主题）与源码 review。每项都给出证据、根因、改动位置、实施方案、验收标准与测试要求。行号基于提交 `0185693`，实施前请先 `Grep` 复核。

**历史走查基线**：2026-11-06、提交 `0185693`。以下原方案保留为问题与验收依据；实际实施范围与调整见第 11 节。文中的 `192.0.2.41` 为文档示例地址，真实测试地址不进入公开源码。

## 0. 实施约束（必须遵守）

1. **兼容性**：不得修改 `ppgw-admin`、`paopaogw` 服务名、配置路径、API `kind=paopaogateway`、PPsub 格式标记、浏览器存储键 `PaoPaoGateWay` / `ppgw-*` / `conns_cols`。
2. **不改业务语义**：只改展示、交互、性能与安全加固；不得改变订阅、策略、规则的计算与保存结果，不得自动修改用户配置。
3. **敏感信息**：不得把真实订阅地址、Token、管理密码写入代码、测试、fixture、文档或提交信息。测试环境凭据向项目负责人索取。
4. **小步提交**：每个条目独立提交，提交信息使用现有风格（如 `fix(ui): ...`、`feat(web): ...`）。提交前通过 `python3 scripts/check-public-source.py`。
5. **验证**：每批完成后运行第 7 节的命令。涉及 UI 的条目必须同时检查 1440px 桌面、320/375px 手机、亮色和暗色主题。
6. **前端源码位置**：运行页面在 `frontend/src/`；网关设置与订阅编辑器在 `integration/gateway.js`、`integration/gateway.css`、`integration/ppsub_panel.js`、`frontend/src/editor.js`。构建入口为 `frontend/build.mjs`。`FILES/etc/config/clash/clash-dashboard/ui/` 是上游归档，**不要直接修改**。
7. **测试环境**：`http://192.0.2.41:18781/ui`，仅用于复验，不得在其上执行切换来源、清空记录、恢复备份等写操作，除非负责人确认。

## 1. 总览

| ID | 标题 | 类型 | 优先级 | 主要文件 | 规模 |
|---|---|---|---|---|---|
| B1 | 连接页在空闲时持续报错 | 缺陷 | P0 | `frontend/src/connection-model.js` | S |
| B2 | 诊断页把历史失败显示为当前故障 | 缺陷 | P0 | `integration/manage_diagnostics.py`、`frontend/src/diagnostics.js` | S |
| B3 | 国旗在 Windows 显示为字母（如“нк”） | 缺陷 | P0 | `frontend/src/shell.css` | XS |
| B4 | 代理组搜索框被压缩到约 31px | 缺陷 | P0 | `frontend/src/proxies.css` | XS |
| S1 | 订阅地址（含 Token）明文显示 | 安全 | P0 | `integration/gateway.js` | S |
| B5 | 区域检测初始显示“0 / 0”及状态重复 | 缺陷 | P1 | `frontend/src/region.js` | XS |
| B6 | 概览模式徽标显示内部值 `suburl` | 缺陷 | P1 | `frontend/src/overview.js` | XS |
| B7 | 概览残留 Cloudflared 序列 | 清理 | P1 | `frontend/src/overview.js` | S |
| B8 | 规则为 0 时空状态文案误导 | 缺陷 | P1 | `frontend/src/rules.js` | XS |
| S2 | 管理访问范围为空时缺少风险提示；ACL 文案不一致 | 安全/文案 | P1 | `integration/gateway.js`、`frontend/src/overview.js` | S |
| S3 | 缺少安全响应头 | 安全 | P1 | `sniffbox/crates/sb-web/src/respond.rs` | M |
| P1 | theme.css 中约 125KB 死样式 | 性能 | P1 | `frontend/build.mjs` | M |
| P2 | 所有页面模块首屏静态加载 | 性能 | P1 | `frontend/src/shell.js` | M |
| P3 | 静态资源未压缩传输 | 性能 | P2 | `sniffbox/crates/sb-web/src/static_files.rs`、打包脚本 | M |
| P4 | 外壳每 5 秒重复拉取 `/sniffbox/info` | 性能 | P2 | `frontend/src/shell.js` | XS |
| U1 | 手机导航抽屉无遮罩、焦点与 Esc 行为不当 | 交互 | P1 | `frontend/src/shell.js`、`shell.css` | S |
| U2 | 订阅信息伪节点混入节点列表 | 交互 | P1 | `frontend/src/proxy-model.js`、`proxies.js` | M |
| U3 | 右键直接锁定节点，无确认 | 交互 | P1 | `frontend/src/proxies.js` | S |
| U4 | 全部测速锁死页面且不可取消 | 交互 | P1 | `frontend/src/proxies.js` | M |
| U5 | 模式切换为二选一按钮 | 交互 | P2 | `frontend/src/proxies.js`、`rules.js` | S |
| U6 | 手机端节点卡片单列过长 | UI | P2 | `frontend/src/proxies.css` | S |
| U7 | 图表、日志无数据时为空白 | UI | P2 | `frontend/src/monitor-view.js`、`logs.js` | S |
| U8 | 导出用下拉框充当操作菜单；危险操作未区分 | 交互 | P2 | `frontend/src/traffic.js` | S |
| U9 | 订阅编辑器加载时布局跳动；无修改也可保存 | 交互 | P1 | `frontend/src/editor.js`、`integration/ppsub_panel.js` | M |
| U10 | 配置来源切换表单常驻展开 | 交互 | P2 | `integration/gateway.js` | S |
| U11 | 网关设置页的文案与控件细节 | UI | P2 | `integration/gateway.js`、`gateway.css` | S |
| U12 | 诊断状态颜色语义错误 | UI | P2 | `frontend/src/diagnostics.js`、`diagnostics.css` | XS |
| U13 | 概览：虚拟网卡噪声、MAC 断行、流量图重置 | UI | P2 | `frontend/src/overview.js`、`overview.css` | S |
| U14 | 日志页工具栏换行与重复选择器 | UI | P3 | `frontend/src/logs.js`、`monitor.css` | XS |
| U15 | 代理页批量操作的失败处理：切换成功却报未完成；更新所有订阅首个失败即中止 | 交互/缺陷 | P1 | `frontend/src/proxies.js` | S |
| P5 | 发行包附带未引用的上游 bundle（约 551KB） | 清理 | 需决策 | `frontend/build.mjs`、`tests/test_dashboard.py` | XS |
| P6 | `shell.css` 重复样式 | 清理 | P3 | `frontend/src/shell.css` | XS |
| P7 | 构建产物导入顺序（已验证，无需处理） | 记录 | — | `frontend/build.mjs`、`tests/test_dashboard.py` | — |
| R1 | 启用托管功能后策略修改永远无法热重载 | 后端优化 | P3 | `integration/manage_policy.py`、`unified_editor.py`、`mihomo_reload.py` | L |
| S4 | 登录凭据永久有效 | 安全 | 需决策 | `sniffbox` 认证、`frontend/src/auth.js` | L |

规模估算：XS ≤ 30 分钟，S ≤ 半天，M ≤ 1 天，L > 1 天。

## 2. 缺陷（P0/P1）

### B1 连接页在空闲时持续报错

- **现象**：网关没有活动连接时，连接页始终显示“连接状态响应无效，请稍后重试。”，“关闭全部”不可用。
- **证据**：测试环境 `GET /connections` 返回 `{"downloadTotal":18100,"uploadTotal":45466,"connections":null,"memory":0}`。
- **根因**：`frontend/src/connection-model.js:19` 只接受数组，`connections:null`（Mihomo 空列表的真实形态）会抛异常。`frontend/tests/monitors.test.mjs:15` 只覆盖了 `[]`。
- **方案**：
  - 在 `update()` 中：若 `value` 是非数组对象，且 `value.connections === null`，则按空数组处理；字段缺失仍为无效响应；其他非数组值仍然抛出原错误。
  - 已追踪的连接应正常移入“已关闭”。
  - 检查其他读取 `/connections` 的位置是否已兼容（`frontend/src/proxies.js:89` 已用 `Array.isArray` 兜底，无需改）。
- **验收**：
  - 空闲网关上，连接页不显示错误，活动计数为 0。
  - 有连接后再全部断开，这些连接出现在“已关闭”。
- **测试**：
  - 在 `monitors.test.mjs` 增加 `tracker.update({connections:null,downloadTotal:0,uploadTotal:0})` 的用例。
  - 在 `scripts/verify-monitors-fixture.cjs` 的合成服务中增加一个返回 `connections:null` 的阶段。

### B2 诊断页把历史失败显示为当前故障

- **现象**：诊断页“配置来源与故障”持续显示“已有管理任务正在执行，完成后再操作。”，但实际没有任务在执行。
- **证据**：`GET /sniffbox/settings/diagnostics` 的 `causes` 为 `["MANAGEMENT_BUSY"]`，来源是 `recent[4]`：一条前一天失败的 `settings/config` 记录。没有任何 running/queued 记录。
- **根因**：`integration/manage_diagnostics.py:237` 把 `recent` 里所有记录的 `error` 都并入了 `causes`。提交 `90db928` 在网关设置页修过同类问题，诊断页漏掉了。
- **方案**：
  - **后端**：`causes` 只保留当前状态原因，即采样失败、`RECOVERY_REQUIRED`、`INVALID_TRANSACTION_JOURNAL`，以及 `recent` 中满足以下任一条件的记录的 `error`：`recovery_required == True`，或 `status` 属于 `{'queued','running','applying'}`。历史失败仍保留在 `recent[].error` 中，不新增字段，保持接口兼容。
  - **前端**：`frontend/src/diagnostics.js:120-122` 不改数据结构。在“最近操作”中显示历史错误时，对 `MANAGEMENT_BUSY` 使用历史语义文案“该次操作因其他管理任务占用未执行”，与 `integration/gateway.js:600` 保持一致。
  - 若 `docs/openapi-v1.json` 描述了该字段语义，同步更新说明，并运行契约脚本。
- **验收**：
  - 只有历史失败时，显示“本次诊断未发现已记录的故障。”。
  - 存在 running 任务或待恢复事务时，仍然显示对应原因。
- **测试**：
  - `tests/test_manage_diagnostics.py` 增加三个用例：只有历史失败、存在 running 记录、存在 recovery_required 记录。
  - 更新 `scripts/verify-diagnostics-fixture.cjs` 中对应断言。

### B3 国旗在 Windows 显示为字母

- **现象**：代理页组标题的“当前：🇭🇰 …”、分布页节点表等位置显示为“нк Hong Kong 09”。节点卡片显示正常，因为它单独设置了字体。
- **根因**：上游主题 `--font-normal` 把 `"Twemoji Country Flags"` 放在字体栈最后。Windows 上 `Segoe UI` 等字体会先把区域指示符渲染为字母。
- **方案**：
  - 在 `frontend/src/shell.css` 顶部添加 `:root` 覆盖，把 `"Twemoji Country Flags"` 放到 `--font-normal` 最前面，其余顺序保持不变。
  - 不要修改上游归档文件。`@font-face` 已经通过 `unicode-range: U+1F1E6-1F1FF` 限定只作用于国旗字符，放到最前面不会影响其他文字。
  - `shell.css` 在 `theme.css` 之后加载，同为 `:root` 选择器，后者覆盖前者。
  - 可选：删除 `proxies.css`、`rules.css` 中重复声明的 `font-family:'Twemoji Country Flags',...`。
- **验收**：Windows Chrome/Edge 下，代理组“当前：”、分布节点表、连接代理链、规则代理链都显示国旗图形；中文和数字字体不变。
- **测试**：在 `scripts/verify-proxies-fixture.cjs` 中断言 `.proxy-current` 的 computed `font-family` 以 `"Twemoji Country Flags"` 开头。

### B4 代理组搜索框被压缩

- **现象**：在约 750px 宽度下，组内搜索框只有约 31px 宽，看起来像一个空白方块。
- **根因**：`frontend/src/proxies.css:2` 中 `.proxies-page input:not([type=checkbox]):not([type=range])` 设置了 `min-width:0`。它的选择器优先级（0,3,1）高于 `.proxy-group-tools>input[type=search]`（0,2,1），把 `min-width:140px` 覆盖掉了。
- **方案**：
  - 把规则改为 `.proxies-page .proxy-group-tools>input[type=search]{flex:1 1 180px;min-width:140px;max-width:260px}`。
  - 在 ≤640px 时搜索框独占一行：`flex-basis:100%; max-width:none`。
- **验收**：在 1440、1120、750、375、320px 下，搜索框宽度均 ≥140px，且不出现横向溢出。
- **测试**：`verify-proxies-fixture.cjs` 增加对 750px 视口下搜索框宽度的断言。

### S1 订阅地址（含 Token）明文显示

- **现象**：“订阅与策略 → 配置来源”中，订阅地址的完整查询参数（含 Token）在只读 `<code>` 和输入框中各明文显示一次，截图或投屏即会泄露。
- **位置**：`integration/gateway.js:507-513`（`locationText`）、`:519`（`draft.url`）、`:524-527`（输入框）。
- **方案**：
  1. 新增 `maskUrl(url)`：解析 URL，保留协议、主机、路径；查询参数只保留键名，值替换为 `•••`；去掉用户名和密码部分。无法解析的地址只保留前 24 个字符，后面加 `…`。
  2. 只读区默认显示脱敏后的值，并提供“显示 / 隐藏”切换（`aria-pressed`）和“复制完整地址”按钮。复制使用现有的复制工具函数，失败时提示用户手动复制。
  3. 切换表单（见 U10）默认收起。展开后，若目标来源仍是远程配置且用户未编辑，输入框显示脱敏占位；用户点击“修改地址”后才显示真实值供编辑。**`draft.url` 的真实值保持不变**，未编辑时提交逻辑不受影响。
  4. 检查 `frontend/src/editor.js` / `integration/ppsub_panel.js` 中提供商 URL 的展示，按同样规则脱敏只读展示；编辑字段保持原值。
- **验收**：
  - 默认页面和截图中不出现完整 Token。
  - 显示、复制、修改后保存的行为与原来一致。
  - 未修改时“预览来源切换”的请求体与原来一致。
- **测试**：
  - 在 `scripts/verify-source-editor.cjs` / `verify-subscription-flow.cjs` 中，用合成 Token 断言其默认不出现在 DOM 文本和 `value` 中，点“显示”后出现。
  - 断言未编辑时预览请求体不变。

### B5 区域检测计数与状态重复

- **位置**：`frontend/src/region.js:24-25`。
- **方案**：
  - 未检测时，进度显示 `共 {regionTests.length} 项 · 尚未检测`；检测中和检测完成后保持原格式。
  - 磁贴在 `waiting` 状态且没有结果时，只显示一个状态文本（保留 `stateNames.waiting`，`label` 留空）。
- **验收**：进入页面时显示“共 59 项 · 尚未检测”，每张卡片只出现一次“待检测”。
- **测试**：更新 `verify-monitors-fixture.cjs` 中对应断言。

### B6 概览模式徽标显示内部值

- **位置**：`frontend/src/overview.js:86`（`put('mode',text(data.mode))`）。
- **方案**：在 `overview.js` 增加与 `integration/gateway.js:160` 一致的映射 `{free:'直连',socks5:'SOCKS5',ovpn:'OpenVPN',yaml:'本地 YAML',suburl:'订阅'}`。若 `data.ppsub` 为真，显示“原生 PPsub”。未知值原样显示。
- **验收**：测试环境概览徽标显示“订阅”。
- **测试**：`verify-overview-fixture.cjs` 增加断言。

### B7 概览残留 Cloudflared

- **位置**：`frontend/src/overview.js:67-68`（图表序列）、`:97`（进程列表）、`:122`（历史数据字段）。
- **方案**：仅当本次会话收到过非 null 的 `cpu.cloudflared` 或 `memory.cloudflared_rss` 时，才加入 Cloudflared 序列和图例。Debian 版本默认不出现，图表的 `aria-label` 中也不再包含“Cloudflared —”。
- **验收**：测试环境的 CPU/内存图例和 `aria-label` 中不出现 Cloudflared。
- **测试**：`scripts/verify-overview-fixture.cjs` 与 `verify-dashboard-fixture.cjs` 中现有的 Cloudflared 断言需要同步调整：分别覆盖“有值时显示”和“全为 null 时隐藏”两种情况。

### B8 规则为 0 时空状态误导

- **位置**：`frontend/src/rules.js:87`。
- **方案**：
  - 区分三种情况：读取中（保持现有文案）；配置本身没有规则（显示“当前配置没有规则。”，附带“前往订阅与策略”链接 `#/subscriptions`）；有规则但被筛选后为空（显示“没有匹配的规则。”）。
  - 页头摘要“0 条规则”保持不变。
- **验收**：测试环境（规则为空）显示新文案和链接。
- **测试**：`verify-rules-fixture.cjs` 增加空配置用例。

## 3. 安全加固

### S2 管理访问范围为空的风险提示与 ACL 文案

- **现象**：
  - 设置页“管理访问范围”为空（含义为允许所有来源），但没有任何提示。README 写明新安装默认只允许私网访问。
  - 概览“允许代理客户端 IP 段：全部”读取的是 `/sniffbox/info` 中的 `acl.proxy_cidr`（为空）；设置页“代理访问范围”则填写了私网列表。两者口径不清。
- **方案**：
  1. 设置页“管理访问范围”为空时，在字段下方显示警告样式提示：“当前允许任何来源访问管理页面。建议填写本机所在网段，例如 192.168.1.0/24。”（不要自动填写）。
  2. 概览“配置信息”中，管理 IP 段为“全部”时使用 `--warn-text` 颜色，并加 `title` 说明。
  3. **先确认语义**：在 `integration/` 与 `sniffbox/` 中查清 `acl.proxy_cidr` 与设置页“代理访问范围”分别对应哪个监听（透明网关 / 显式 HTTP·SOCKS5 1080）。然后修正两处标签，使用户能区分，例如“透明网关客户端范围”和“显式代理客户端范围”。若确实是同一个设置但显示不一致，则按缺陷处理并报告根因。
- **验收**：空值时出现警告；两处标签语义明确，并在提交说明中写清对应关系。

### S3 安全响应头

- **证据**：`/ui/` 与静态资源只返回 `Content-Type`、`Cache-Control`，没有防嵌入、防类型嗅探等响应头。
- **位置**：`sniffbox/crates/sb-web/src/respond.rs`（`file()`、`send()`），可能还有 HTML 入口的处理位置。
- **方案**：对 `/ui` 下的所有响应（HTML 和静态资源）增加以下响应头：
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `X-Frame-Options: DENY`
  - `Content-Security-Policy: frame-ancestors 'none'; object-src 'none'; base-uri 'self'`

  **暂不加 `script-src` 限制**：`legacy.html`、`gateway.html` 使用了内联脚本，区域检测会访问外部地址。若后续要收紧 CSP，需要先把内联脚本外置，并单独评估 `connect-src`。

  API 响应可以同样加 `nosniff`。
- **验收**：用 `curl -I` 确认 `/ui/`、哈希 JS/CSS、`/ui/gateway.html` 都带上述头；所有页面功能正常，包括区域检测、导出、复制。
- **测试**：在 `respond.rs` / `static_files.rs` 的单元测试中断言这些响应头；运行 `cargo test --workspace --locked` 和全部 `verify-*.cjs`。

### S4 登录会话（需决策，不在本轮实施）

- **现状**：浏览器端计算 `sha256(管理密码)`，作为 Bearer 凭据永久保存在 `localStorage`，没有过期时间；退出登录只清除本地存储。
- **建议方向**：服务端签发短期会话 Token（例如 12 小时，滑动续期），对登录失败做限速，并支持服务端注销。需要同步修改 `sniffbox` 认证、`frontend/src/auth.js`、所有 `fetch` 调用点与 API 契约。
- **要求**：实施前需由负责人确认，并单独设计文档。本轮只记录，不实施。

## 4. 性能与工程

### P1 移除 theme.css 中的死样式

- **证据**：`theme.css` 约 124KB，共 1147 条规则。其中 1028 条带 `.svelte-` 作用域类，约 125KB，当前页面上匹配数为 0。来源是 `frontend/build.mjs:22` 把上游 `index.html` 的全部 `<style>` 提取了出来。
- **方案**：在 `build.mjs` 中实现一个基于花括号配对的轻量 CSS 过滤器：
  - 遍历顶层规则；对 `@media` / `@supports` 递归过滤其中的规则。
  - 删除选择器中包含 `.svelte-` 的规则；若一条规则有多个选择器，只删除其中带 `.svelte-` 的选择器，剩下的保留。
  - 删除过滤后变为空的 `@media` 块。
  - 保留 `@font-face`、`@keyframes`、`:root` 以及所有非 svelte 规则。
  - **不要用简单的正则整段删除**，否则会破坏嵌套结构。
- **验收**：`theme.css` 小于 25KB；所有 `verify-*.cjs` 通过；人工对照桌面、手机、亮色、暗色下各页面，无样式回退。
- **测试**：在 `tests/test_dashboard.py` 中断言输出的主题 CSS 不含 `svelte-`，且仍含 `@font-face` 和 `--font-normal`。

### P2 路由模块按需加载

- **证据**：首屏加载约 590KB 的 JS/CSS（未压缩）。`frontend/src/shell.js:1-12` 静态导入了全部页面模块，包括 `region-catalog.js`（99KB）和 `editor.js`。
- **方案**：
  - `shell.js` 改为在 `show()` 中按路由 `await import('./overview.js')` 等动态加载。加载期间显示“正在加载页面…”。导入失败时显示可重试的错误提示。
  - 注意竞态：连续切换路由时，只挂载最后一次请求的路由（用自增序号判断）。
  - `pendingRecoveryJob` 需要在启动时同步可用，可把它拆到一个很小的独立模块，或保留对 `diagnostics.js` 的静态导入。
  - `build.mjs` 已经对 `'./x.js'` 字符串做哈希替换，动态导入同样适用；`tests/test_dashboard.py:36` 的导入检查也覆盖 `import(`。
  - `integration/gateway.js`、`ppsub_panel.js`（经典脚本，约 110KB）可在第二步改为进入设置或订阅路由时再注入 `<script>`，要保证 `window.ppgwGateway` 就绪后再挂载。
- **验收**：直接打开 `#/overview` 时，不请求 `region-catalog`、`rules`、`proxies`、`editor` 等模块；前进、后退、草稿保护、重新登录等行为不变。
- **测试**：`verify-runtime-navigation.cjs`、`verify-unified-runtime.cjs`、`verify-overview-fixture.cjs` 通过；新增断言首屏请求列表不含上述模块。

### P3 静态资源压缩

- **证据**：服务端忽略 `Accept-Encoding`，`Content-Length` 等于原始大小。
- **方案（推荐预压缩）**：
  - 打包阶段（`scripts/prepare_dashboard.py` 或 `package-debian.py`）为 `.js` / `.css` / `.html` 生成确定性的 `.gz`（`mtime=0`）。
  - `static_files.rs` 在请求头包含 `gzip`，且同名 `.gz` 存在时返回压缩文件，并带上 `Content-Encoding: gzip` 和 `Vary: Accept-Encoding`。
  - 安装清单和校验和需要包含新增的 `.gz` 文件（检查 `scripts/source-manifest.py`、`verify-release-features.py`）。
- **验收**：首屏传输量下降 ≥ 70%；不支持 gzip 的客户端仍拿到原文件；校验通过。
- **测试**：Rust 单元测试覆盖协商逻辑；`python3 -m unittest` 覆盖打包产物。

### P4 外壳轮询频率

- **位置**：`frontend/src/shell.js:56`（`setInterval(runtime,5000)`）。
- **方案**：间隔改为 30 秒；`document.hidden` 时跳过；概览页通过 `onInfo` 回调已经拿到最新 info，这时外壳不再重复请求。登录失效检测仍由各页面的 401 处理负责。
- **验收**：在概览页停留 60 秒，外壳发出的 `/sniffbox/info` 请求不超过 2 次；切换到代理/规则页时，导航中代理/规则的显隐仍能及时更新。

### P5 未引用的上游 bundle（需决策）

- `frontend/build.mjs:21` 把上游 `bundle.*.js`（约 551KB）复制到输出目录，页面并不引用它。`tests/test_dashboard.py:25-26` 明确断言了这次复制，可能是为了兼容缓存了旧 `index.html` 的浏览器。
- **实施前需负责人确认**是否仍需要这种兼容。若不需要，删除复制步骤并更新测试；若需要，在 `build.mjs` 中注明保留原因。

### P6 shell.css 重复样式

- `frontend/src/shell.css:11` 与 `:27`、`:22` 与 `:28`、`:17-21` 与 `:24-25`/`:30-31` 存在相互覆盖。按最终生效结果合并为单一定义，视觉不变。用 `verify-runtime-navigation.cjs` 的截图核对。

### P7 构建产物导入顺序（已验证，无需处理）

- **背景**：`frontend/build.mjs:25` 按手写顺序逐个生成哈希文件名，并在源码中把 `./x.js` 替换为对应的哈希文件名。如果某个模块引用了排在它后面才生成的模块，这条引用就不会被替换，运行时会加载失败。
- **核对结果**：当前顺序正确（例如 `proxies.js` 引用的 `overview.js` 排在它前面）。`tests/test_dashboard.py:36-37` 已经检查每个输出 JS 中的 `from './…'` 和 `import('./…')` 都指向实际存在的文件，调整顺序导致的错误会被测试发现。
- **实施要求**：不要为此单独修改 `build.mjs`。实施 P2（按需加载）时，`import()` 也会被这条测试覆盖，只需确认测试仍然通过。

## 5. 交互与 UI

### U1 手机导航抽屉

- **位置**：`frontend/src/shell.js:56`（菜单按钮）、`:73`（Esc 处理）；`frontend/src/shell.css:17-25`。
- **问题**：抽屉打开时没有遮罩，背景文字透出；点外部不会关闭；Esc 在抽屉关闭时也会把焦点抢到菜单按钮上。
- **方案**：
  - 新增 `.shell-backdrop`，仅在 ≤760px 且抽屉打开时显示（半透明并带模糊），点击时关闭抽屉。
  - 抽屉打开时，焦点限制在导航内（Tab 循环）；关闭后焦点回到菜单按钮。
  - Esc 只在抽屉打开时处理。
  - 抽屉背景使用不透明的 `--panel-solid`。
- **验收**：在 375px 下打开抽屉，背景变暗且不可点击；点击遮罩或按 Esc 关闭；在桌面端按 Esc 不会移动焦点。

### U2 订阅信息伪节点

- **现象**：GLOBAL 组中出现“Traffic Reset: 19 Days Left”“Expire Date: …”“6.50 GB | 200 GB”这类订阅信息节点，可被选择和测速。
- **方案**：
  - 在 `frontend/src/proxy-model.js` 新增 `infoNode(name)`，按名称识别。规则至少覆盖：剩余流量 / 套餐到期 / 过期时间 / 距离下次重置 / 官网 / Expire / Traffic / Reset / Remaining，以及形如 `数字 + GB/TB | 数字 + GB/TB` 的用量格式。正则集中定义，并配单元测试。
  - 新增偏好项 `hideInfoNodes`，默认开启。开启时，这些节点不显示为卡片，而是汇总为组顶部的一行灰色“订阅信息”文字。
  - 这些节点**不从配置中删除**，也不改变当前选择；如果当前选中的正好是这类节点，仍要正常显示为“当前”。
  - `testPlan` 与组测速都排除这类节点。
- **验收**：测试环境中，GLOBAL 组顶部显示订阅信息摘要，节点网格中不出现这三项；关闭偏好后恢复原样。
- **测试**：`frontend/tests/proxies.test.mjs` 覆盖识别规则（正例、反例，反例如“Hong Kong 01”“GB-London”）；`verify-proxies-fixture.cjs` 增加相应断言。

### U3 右键锁定需确认

- **位置**：`frontend/src/proxies.js:154-157`。
- **方案**：
  - 右键时不再直接锁定，改为弹出小菜单（锁定/解锁、详情、测速），菜单项是可聚焦的按钮。
  - 长按在触屏设备保留，按住时显示进度反馈（例如卡片描边动画），触发后在状态栏显示“已锁定 X，可在组顶部解除”。
  - 组说明文字（`:164`）保持现状。
- **验收**：在桌面右键节点不会改变任何状态；在菜单中选择“锁定”后才发出请求。

### U4 测速可取消且不锁死页面

- **位置**：`frontend/src/proxies.js:80-86`（`perform`）、`:105-118`（`runTests`）。
- **方案**：
  - 测速改为独立的 `testing` 状态，不复用 `busy`。测速期间，节点选择、刷新、模式切换仍可用。
  - 工具栏的“全部测速”在测速期间变为“停止测速”，通过 `AbortController` 中止尚未开始和正在进行的请求，并显示“已完成 n / m，已停止”。
  - 已完成的结果保留，被中止的节点不计为失败。
- **验收**：在 92 个节点的组中执行全部测速，途中可以切换节点、可以停止；停止后不再有新的 `/delay` 请求。
- **测试**：`verify-proxies-fixture.cjs` 增加“停止测速”和“测速中切换节点”两个场景。

### U5 模式切换三态

- **位置**：`frontend/src/proxies.js:21-22`、`:193`；`frontend/src/rules.js` 中的同类按钮。
- **方案**：替换为“规则 / 全局 / 直连”分段控件（`role=radiogroup`）。提交仍使用 `PATCH /configs {mode}`。保留原说明：这是临时切换，重新加载配置后恢复。
- **验收**：三种模式都能切换，当前模式高亮；在直连模式下不再显示“切换全局模式”。

### U6 手机端节点卡片

- **位置**：`frontend/src/proxies.css` 中的 `.proxy-node-grid`、`.proxy-node-footer`。
- **方案**：≤480px 时，网格改为 `repeat(2,minmax(0,1fr))`；卡片内隐藏 `.proxy-node-tags`；“详情”“测速”改为 28px 图标按钮，并保留 `aria-label`。筛选控件在 ≤480px 时两列排列，下拉框文字不截断。
- **验收**：375px 下一屏至少显示 8 个节点；没有横向溢出；所有按钮触控区域 ≥ 28px。

### U7 无数据时的空状态

- **位置**：`frontend/src/monitor-view.js`（`sparkline`）、`traffic.js`、`distribution.js`、`logs.js`。
- **方案**：
  - 速率图样本少于 2 个时，在图表区居中显示“等待速率数据…”；收到 SSE 前显示“正在连接…”，断线时显示“已断开，正在重连”。
  - 日志区为空时显示“暂无日志（当前级别：Info）”。
- **验收**：在测试环境空闲状态下，流量、分布、日志页都没有空白大块。

### U8 导出菜单与危险操作

- **位置**：`frontend/src/traffic.js:14`。
- **方案**：
  - `select('导出…')` 改为“导出”按钮加弹出菜单（四个选项，键盘可操作）。
  - “清空记录”使用危险样式（红色描边的幽灵按钮），放到工具栏最右侧，与普通操作之间留出间距。原有确认对话框保留。
  - 分布页的“清空节点统计”按相同方式处理。
- **验收**：导出的四种格式都正确；清空仍需二次确认。

### U9 订阅编辑器加载与保存状态

- **现象**：
  - 加载时先显示空的“配置内节点提供商”卡片，加载完成后该卡片消失，导致布局跳动。
  - 没有任何修改时，“保存并应用”也可以点击。
- **方案**：
  - 加载完成前，用骨架占位（固定高度的灰色块），不渲染真实的卡片结构。
  - 维护“已修改”状态：与加载时的快照做深比较，或复用现有的草稿机制。没有修改时禁用“保存并应用”，并在按钮旁显示“没有未保存的修改”；有修改时显示“n 项修改”。
  - 现有草稿保护、冲突检测逻辑不改。
- **验收**：加载前后卡片数量不跳变；未修改时按钮禁用，修改后启用，撤回修改后重新禁用。
- **测试**：`verify-subscription-drafts.cjs`、`verify-complex-editor.cjs` 通过，并增加禁用状态断言。

### U10 配置来源切换表单默认收起

- **位置**：`integration/gateway.js:517-551`。
- **方案**：
  - 默认只显示“当前配置来源”摘要卡片（使用 S1 的脱敏显示），以及一个“切换配置来源”按钮。
  - 点击后才展开表单；存在 `sourceDraft.changed` 时自动展开。
  - “取消切换”关闭表单并清空草稿。
- **验收**：首次进入时看不到表单；草稿在切换页签后仍然保留并保持展开。

### U11 网关设置页细节

- **位置**：`integration/gateway.js:353` 及管理访问卡片、`integration/gateway.css`。
- **方案**：
  - `:353` 文案改为“填写新密码并再次确认后，页面底部会出现保存栏，点击‘预览变更’并在预览中‘确认应用’……”。
  - “管理密码”与“确认新管理密码”两列顶部对齐：统一标签高度，或使用 `align-items:start`。
  - “清空已保存的值”改为明确的次级按钮样式（`btn btn-ghost` 加图标），不要再像输入框。
  - S2 的警告也放在这张卡片里。
- **验收**：1120px 下两个密码框顶部对齐；按钮外观明显区别于输入框。

### U12 诊断状态颜色

- **位置**：`frontend/src/diagnostics.js:116-122`、`frontend/src/diagnostics.css`。
- **方案**：`.diagnostic-runtime` 按状态加上修饰类（`ok`、`warn`、`unknown`），分别使用 `--up-text`、`--warn-text`、`--text-2`。服务列表每行前加 8px 圆点：运行中为绿色，未运行但设为开机启用为红色，未运行且未启用为灰色。
- **验收**：“核心响应正常”显示为绿色；“核心维护：未运行·未设为开机启用”显示灰色，不应被误读为故障。

### U13 概览细节

- **位置**：`frontend/src/overview.js:53`（页头）、`:92`（接口表）、`:122`（历史数据）；`frontend/src/overview.css`。
- **方案**：
  - 名称匹配 `^(docker\d*|br-|veth|virbr|lo$)` 的接口默认折叠到“虚拟接口（n）”中，可展开；有网关地址的接口始终显示。
  - MAC 列加 `white-space:nowrap`。
  - 把流量和资源的最近 40 个样本保存在模块级变量中，带时间戳；60 秒内重新进入概览时复用这些样本，避免图表从空白开始。
  - “刷新”按钮与时钟放在同一行右侧，避免它单独占一行。
- **验收**：测试环境概览默认只显示 `ens33` 和折叠项；离开概览再返回时，图表保留之前的数据。

### U14 日志页工具栏

- **位置**：`frontend/src/logs.js`、`frontend/src/monitor.css`。
- **方案**：页头的“核心日志”来源选择与卡片标题重复，保留一处即可。工具栏中，搜索框弹性伸缩，其余控件不换行；空间不足时整体换行，而不是只把最后一个下拉框挤到第二行。
- **验收**：在 1120px 宽度下工具栏为单行。

### U15 代理页批量操作的失败处理

- **问题 1：节点已切换，却提示“切换节点未完成”**
  - **位置**：`frontend/src/proxies.js:87-94`。
  - **现象**：开启“切换后关闭旧连接”时，`select()` 先切换节点并更新 `target.now`，再在同一个 `perform('切换节点')` 中调用 `closeOld()`。`closeOld()` 逐条串行发送 `DELETE /connections/{id}`，任意一条失败（超时、网络错误、HTTP 错误）都会抛出异常，整个操作被报告为“切换节点未完成”，而节点实际已经切换成功。连接很多时，串行关闭也很慢。
  - **方案**：把切换和关闭旧连接拆成两个结果。切换请求成功后，立即显示“已切换到 X”；关闭旧连接改为有并发上限（例如 8 个）的批量请求，单条失败只计数，不抛出异常。最后追加提示“已关闭 n 条旧连接，m 条未关闭”。
  - **验收**：模拟部分 `DELETE` 失败时，状态栏显示切换成功，并给出未关闭的数量；节点选择保持新值。
- **问题 2：“更新所有订阅”遇到第一个失败就中止**
  - **位置**：`frontend/src/proxies.js:33`。
  - **现象**：`updateAll` 按顺序逐个执行 `PUT /providers/proxies/{name}`，第一个失败就中止，剩下的提供商不再更新。提示只有“请求失败（HTTP n）”，看不出是哪个提供商失败。
  - **方案**：逐个执行（或使用较小并发），单个失败不影响其余提供商。结束后汇总显示“成功 n 个，失败 m 个：名称 1、名称 2”。
  - **验收**：模拟其中一个提供商返回 500 时，其余提供商仍然完成更新，提示中列出失败的名称。
- **测试**：在 `scripts/verify-proxies-fixture.cjs` 增加两个场景：“切换后部分连接关闭失败”和“多个提供商中有一个失败”。

## 6. 后端优化（Review 跟进）

### R1 启用托管功能时无法热重载

- **现状**：`integration/manage_policy.py:207`、`:224` 和 `integration/unified_editor.py:129` 调用 `plan(snapshot['document'], effective)`。其中 `before` 是 `logical_document` 生成的逻辑文档（已移除 `x-ppgw-features`），`after` 是 `apply_features` 生成的物化文档。只要启用了任何托管功能，两者就会多出 `x-ppgw-features` 这个差异键，结果始终判为不可热重载，走重启。当前行为安全，但带来不必要的重启。
- **方案（需评估后实施）**：
  - 比较时使用同一口径：`before` 取运行中的物化文档（即 `/tmp/clash.yaml` 的原始内容），且要求托管功能选项不变。
  - `mihomo_reload.apply_policy` 中的 `logical_document(raw) != update['before']` 检查需要相应调整。
  - `plan()` 对物化后节点组的白名单校验保持保守：带 `use` 的智能组仍判为不可热重载。
- **验收**：功能选项不变、只修改静态规则时，可以走 `mihomo_reload`；任何不确定的情况仍然重启。
- **测试**：`tests/test_mihomo_reload.py` 增加启用功能时的用例；在 Linux 隔离环境运行 `scripts/verify-mihomo-reload.py`。

### R2 热重载中的短暂选择窗口（记录）

`reload_verified` 在 `PUT /configs` 之后才恢复 Selector 选择，中间会有一段很短的时间回到默认节点。建议在隔离验证中测量这个窗口；若默认节点是 REJECT 等会造成业务中断的节点，再评估是否借助 `profile.store-selected` 或调整下发的顺序。本轮只记录，不改动。

## 7. 验证命令

每批提交前在仓库根目录执行：

```sh
python3 -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
node --check integration/gateway.js
node --check integration/ppsub_panel.js
node frontend/build.mjs
python3 scripts/check-public-source.py
python3 scripts/export-api-contract.py && python3 scripts/check-api-contract.py   # 涉及 API 时
(cd sniffbox && cargo test --workspace --locked)                                  # 涉及 Rust 时
python3 scripts/prepare_dashboard.py --output dist/test-ui
for s in settings dashboard overview proxies rules monitors diagnostics observer-api; do node scripts/verify-$s-fixture.cjs dist/test-ui; done
node scripts/verify-runtime-navigation.cjs dist/test-ui
node scripts/verify-unified-runtime.cjs dist/test-ui
node scripts/verify-subscription-flow.cjs dist/test-ui
node scripts/verify-subscription-drafts.cjs dist/test-ui
node scripts/verify-source-editor.cjs dist/test-ui
node scripts/verify-complex-editor.cjs dist/test-ui
```

以上为合成验证。部署到测试环境后，还需按第 9 节做只读复验。

## 8. 建议批次

| 批次 | 内容 | 完成标准 |
|---|---|---|
| 1 | B1、B2、B3、B4、S1、B5、B6、B8 | 第 7 节全部通过；测试环境中连接页无报错，诊断页无误报，国旗正常显示，订阅 Token 默认打码 |
| 2 | B7、S2、S3、P1、P4、P6、U12 | 主题 CSS 小于 25KB，安全响应头齐全，视觉无回退 |
| 3 | P2、P3、U1、U2、U3、U4、U9 | 首屏只加载当前路由；交互项逐条验收 |
| 4 | U5、U6、U7、U8、U10、U11、U13、U14、U15 | 375px/320px 下逐页截图核对 |
| 后续 | P5、S4、R1、R2 | 已按确认方案执行，结果与边界见第 12 节 |

## 9. 测试环境只读复验清单

部署后用浏览器逐条核对（不执行写操作）：

1. `#/connections`：空闲时无错误提示。
2. `#/diagnostics`：没有进行中的任务时，不显示“已有管理任务正在执行”。
3. `#/proxies`：组标题“当前：”显示国旗图形；搜索框宽度正常；订阅信息伪节点已汇总到顶部。
4. `#/distribution`：节点名的国旗正常显示。
5. `#/subscriptions` → 配置来源：Token 默认打码，切换表单默认收起。
6. `#/overview`：模式显示“订阅”；不出现 Cloudflared；虚拟网卡默认折叠。
7. `#/rules`：显示“当前配置没有规则”。
8. `#/region`：显示“共 59 项 · 尚未检测”。
9. `curl -I http://192.0.2.41:18781/ui/` 带安全响应头；`curl -H 'Accept-Encoding: gzip' -I` 拿到压缩后的哈希资源。
10. 在 375px 下打开导航抽屉，有遮罩，点击遮罩可关闭。

## 10. 附录：走查与 Review 结论

- **测试部署**：版本 `0.2.0-management.20261106.2`，mihomo `v1.19.33`，模式为订阅（远程完整配置），GLOBAL 全局模式，92 个节点，规则为 0。浏览器控制台无错误。哈希资源的缓存头为 `immutable`，配置正确。
- **前端单元测试**：`npm test`（frontend）23 项全部通过（基于提交 `0185693`）。
- **提交 7b465d7（启动时等待节点选择恢复）**：恢复结果分为 restored / removed / uncertain 三种，只有 removed 才触发测速选点，处理合理，未发现阻塞问题。
- **提交 88b7638、96b860a（静态策略热重载）**：白名单范围保守，覆盖进程身份核对、规则/节点组/模式/选择回读与失败回退，未发现阻塞问题。后续优化见 R1、R2。

### 复核记录

| 时间 | 范围 | 结果 |
|---|---|---|
| 2026-11-06 首次走查 | 测试部署全部 11 个页面（亮/暗主题，1120/750/375px）；最近 3 个功能提交；前端单元测试 | 形成本文档的全部条目 |
| 2026-11-06 复核 | 仓库 HEAD、测试部署版本与资源哈希、`/connections`、诊断接口 | 仓库与部署均无变化；B1、B2 仍可复现；补充 U15 与 P7 |

### 已排除或无需处理的项

- **构建导入顺序**：已有测试覆盖，见 P7。
- **网关设置页看不到“预览变更”按钮**：保存栏只在有修改时出现（`integration/gateway_view.html:13-16`），属于预期行为，只需修改说明文案（见 U11）。
- **规则页显示 0 条**：测试环境的配置确实没有规则，且处于 GLOBAL 模式（`GET /rules` 返回空数组），不是读取故障；只需修改空状态文案（见 B8）。
- **热重载在启用托管功能时退回重启**：行为安全，没有缺陷，只是可以优化，见 R1。


## 11. 实施记录（2026-11-06）

### 范围与状态

以下为首次交付状态；当时暂缓的四项现已完成后续处理，见第 12 节。

| 条目 | 状态 | 结果 |
|---|---|---|
| B1–B8 | 已实施 | 空闲连接、历史诊断、国旗字体、搜索宽度、区域初始状态、模式徽标、条件 Cloudflared 序列、规则空状态 |
| S1 | 已实施 | 默认隐藏订阅路径、查询和片段；明确显示 / 修改 / 复制动作；草稿保存仍使用真实内存值 |
| S2 | 已实施 | 空管理 ACL 风险提示；代理范围说明透明 TCP 与显式代理共用；修复混合 IPv4 / IPv6 ACL 解析 |
| S3 | 已实施 | 防嗅探、无 referrer、防嵌入与最小 CSP；HTML、JS、CSS 和 HEAD 均有测试 |
| P1–P4、P6 | 已实施 | 主题 13,513 字节；按路由加载；确定性 gzip 与协商；外壳 30 秒轮询、隐藏暂停；删除失效重复样式 |
| U1–U15 | 已实施 | 导航遮罩与键盘、显式说明节点过滤、锁定确认、取消测速、三态模式、手机双列、空状态、导出菜单、编辑器加载与脏状态、来源折叠、设置布局、诊断颜色、概览保留、日志工具栏、批量部分失败汇总 |
| P7 | 已有覆盖，无需改动 | 构建导入顺序和产物引用校验继续通过 |
| P5 | 暂缓 | 保留上游兼容 bundle 与现有发行契约，另行决定是否去除 |
| S4 | 暂缓 | 服务端会话设计不在本轮范围，现有认证方式保持 |
| R1、R2 | 暂缓 / 记录 | 保留已验证的保守热加载与重启回退；物化文档优化和短暂选择窗口另行评估 |

### 评审后的执行调整

- B1 只接受真实空闲形态 `connections:null`，不把缺失字段当成功，避免掩盖接口错误。
- S1 同时隐藏路径与 fragment，无法解析的地址全部隐藏，不截取可能含凭据的前缀。
- S2 的两处代理范围是同一设置。根因是旧核心使用 IPv4 网络列表，遇到已保存的 IPv6 条目后整项解析失败并回退为空 ACL，导致“全部允许”。现支持双栈；非空无效配置限制到本机回环，且日志只记录字段名。未自动更改用户配置。
- U2 使用默认关闭的可选显示过滤，不按名称自动删除或禁用节点，不改变全部 / 组测速范围。
- U4 测速独立于写操作状态，最多 8 个并发；停止、离页或权限失效后不再启动排队请求，取消项不计为失败。
- U9 复用完整配置指纹并纳入全局更新计划；没有改动时禁用保存，撤回改动后恢复禁用。
- U13 仅在同一认证、启动标识、核心版本和出站模式下复用 60 秒内最近 40 个样本；不写入浏览器持久存储。

### 验证与部署边界

本轮使用合成 API、本机 HTTP/SSE fixture 和测试服务器独立 Docker 目录进行验证。最终回归通过：前端 31 项；Python 共 306 项（302 通过、4 跳过）；API 契约 23 个样本及摘要；15 组浏览器 fixture；Rust 全工作区 1,048 项通过、5 项按原设置忽略。截图证据及产物摘要见工作区 `dist/ui-optimization-20261106/acceptance.json`。公开源码检查在每项本地提交前执行。

仅提交到本地主线，不推送 GitHub。本轮尚未更新测试服务器生产服务；隔离验证通过不等同于生产部署验收。原第 9 节只读复验清单在实际部署后执行。

## 12. 暂缓项后续实施（2026-11-06）

| 条目 | 状态 | 执行结果 |
| --- | --- | --- |
| P5 | 已实施 | 不再复制或压缩已退役的上游 JS bundle；旧 HTML 入口继续跳转。重复构建时清理旧 manifest 登记的 bundle，保留调用方其他文件。共享字体、图标和源码主题继续使用。 |
| S4 | 已实施 | 服务端随机 HttpOnly 会话、12 小时绝对期限、30 分钟无请求期限、注销与改密撤销、登录限速、同源写入校验；前端不再持久保存密码校验值，清理各旧配置的认证字段并保留偏好。远端永久管理 Bearer 拒绝，仅保留本机内部服务例外；独立观察 API 不变。 |
| R1 | 已实施 | 托管功能选项不变时按物化文档比较，只放开静态规则变化。功能、节点凭据、监听、provider、动态成员或不确定的物化快照继续重启。 |
| R2 | 已完成测量与执行判断 | 覆盖 rule/global、三种默认项及额外 50ms 压力组；未复现成功连接误走默认出口，既有长连接保留，但新连接仍会 EOF/重置。保留选择恢复与回读，预览明确提示新连接可能短暂失败，不宣称零中断。 |

S4 的协议、升级兼容、远端脚本迁移及 HTTP/TLS 边界见[浏览器管理会话](browser-sessions.md)。R1 见[受限热加载说明](mihomo-reload-assessment.md)，R2 的样本数、耗时口径和复现方法见[新连接测量](mihomo-selection-window.md)。

后续最终回归：Python 307 项（303 通过、4 跳过），前端 32 项通过，16 组浏览器 fixture 通过，独立观察 API 23 个实际 HTTP 样例及摘要一致，Rust 全工作区 1,054 项通过、5 项按原设置忽略。真实 Rust HTTP/SSE 测试核对会话撤销和期限；真实 Mihomo 的隔离 TCP 测试核对托管功能不变时的规则生效、进程/长连接/选择保留及失败回退。源码包先统一为 Git 的 LF 格式，避免旧源码文本断言受 Windows 换行影响；最终全量回归没有跳过这些用例。

验收索引及原始日志保留在工作区 `dist/deferred-followup-20261106/acceptance.json`。以上是代码与隔离验证完成；本轮仅本地提交，未 push、未部署生产。测试服务器仍按现有原生 Linux/systemd 运行，Docker 仅用于隔离构建与实验。实际部署后的第 9 节只读复验尚需执行。
