# LovestGateWay 与上游差异及 Web 设置范围

本文说明代码层面的功能差异及配置应用方式。具体操作见 [Web 设置与订阅策略](debian-web-settings.md)，验证方法见[测试说明](testing.md)。

## 对比基准

- 已合入官方基线：`4a5757e93a46669a54c9e5864d7b4a8d5484da76`。
- 当日读取官方 `main`：`f45e3d9f523c3a09c2093cc95e31b5f42c8458dc`。本轮仅复用规则格式自动识别所需后端改动，没有整批替换上游面板、离线编辑器或 Cargo.lock。
- 官方项目为 [kkkgo/PaoPaoGateWay](https://github.com/kkkgo/PaoPaoGateWay)。此处比较仓库代码，不代表已核验上游镜像每个 tag 的二进制。

## 当前主要差异

| 范围 | 上游基线 | LovestGateWay |
| --- | --- | --- |
| 部署与发布 | ISO 及其 Docker 构建流程 | Debian 原生包、systemd、多服务；旧 ISO 脚本仅作来源追踪 |
| 宿主网络 | 配套系统的网络、时钟和调优流程 | 不修改宿主 IP/默认路由，不全局清空防火墙，不接管 NTP；仅管理项目自己的 nft 表和策略路由 |
| 运行隔离 | 配套系统内运行 | 私有挂载/临时目录，按服务 cgroup 识别进程，Web/DNS 支持指定监听地址 |
| 可选观测 API | 无本项目 observer 契约 | 默认关闭，通过 Web 开关启用；提供实例身份、能力/健康/指标、FakeIP、TCP 观测、Mihomo 快照、历史和受限探测；观测凭据不能改配置 |
| 日常 Web | 原面板和 PPsub 编辑器 | 11 个分栏均由本项目源码维护；服务端会话、配置预览、持久任务、实际加载核对和回退，支持手机与亮暗主题 |
| PPsub | 日常订阅与策略编辑模型 | 保留高级和旧配置兼容入口；完整 Clash/Mihomo 配置日常直接编辑，保留未展示字段与节点凭据 |
| 脚本管理 | 原控制脚本 | 中文 ppgw-admin、类型校验、revision、备份、事务锁、升级和中断恢复；与 Web 复用后端 |
| 安装默认值 | 配套环境的初始化流程 | 依赖/网卡/随机初始登录/启动；代理、UDP、统计、IPv6、历史及自动更新按需在 Web 开启，既有安装保留原值 |
| Mihomo | 上游集成方式 | 官方稳定版独立安装、CPU 型号自动选择、摘要校验、更新和回退，维护开关控制服务/定时器 |
| 功能取舍 | 包含 Cloudflared | Debian 运行、Web 和能力声明禁用 Cloudflared；free/socks5/ovpn/yaml/suburl 五模式保留 |
| 品牌 | PaoPaoGateWay | LovestGateWay 名称/Logo，保留既有服务名、配置路径、API kind、PPsub 格式和登录存储兼容标识 |

FakeIP、透明 TCP/UDP、嗅探、显式 HTTP/SOCKS5 代理、Mihomo 面板、订阅调度等基础能力仍沿用上游，不是本项目从零新增。实际可用能力由目标环境、配置及独立验证决定。

## 规则自动识别复用

上游 [f45e3d9：fix ruleset auto](https://github.com/kkkgo/PaoPaoGateWay/commit/f45e3d9f523c3a09c2093cc95e31b5f42c8458dc) 补充 PPsub 自动格式识别：未知后缀先按 YAML 推测，`.list`/`.conf` 按文本处理；首次未识别的自动规则集在 Mihomo 启动后再检查，更新重试状态及输出摘要。

复用范围包括 `ruleset.rs`、`ppsub/rules.rs`、`ppsub/state.rs`、`ppsub/retry.rs` 和对应命令入口，不包含无关的依赖更新或面板替换。

## 实际应用方式

管理器先预览，再在事务锁内检查版本、备份、写入、应用和验收。核心未运行时只保存，不标为已加载；无法读取旧运行状态时，热更新降级为重启。

| 设置 | 本轮管理器应用方式 |
| --- | --- |
| 管理/代理 CIDR、管理密码、显式代理认证、splice_threshold | 仅热重载核心，检查 boot_id 不变和运行 revision 改变 |
| max_rec、net_cleanday | net_rec 已开启时热重载，否则重启 |
| Mihomo 节点选择、rule/global/direct | 继续走原面板运行时 API；不等同于持久网关模式修改 |
| 观测 API 开关 | 启停 API 及自启；关闭时同步停止 API 历史采集，不重启核心 |
| 历史采集/保留 | API 开启时启停或重启采集服务，已有历史保留 |
| 自动更新 | 启停维护 timer |
| API 名称、探测地址、监听、专用 Token | API 开启且活动时仅重启 API；关闭时只保存配置；失败恢复原配置及 Token |
| 五种出站模式、订阅源、文件、分流策略 | 校验后重启网关，来源冲突需明确清除旧本地覆盖 |
| DNS/FakeIP、UDP、统计开关、监听、网卡、IPv6、时区 | 重启网关；时区还同步相关辅助服务 |
| box 阻断选项 | 重启；QUIC 同时涉及 nft 规则，不能只更新核心 |

原有 `/sniffbox/reload` 会重载网关/Mihomo，不能据其 boot_id 不变推断所有连接不中断。`/sniffbox/reload/core` 只刷新私有 INI 并发 SIGHUP。管理器写持久文件前发布等待标记，主循环等核心已接受的快照与目标摘要一致后推进配置摘要，避免把同一次热更新再次当成完整重载。已有连接和辅助进程的实际保留情况需在目标模式中独立验证。

## Web、安装与 PPsub 的分工

1. 安装只处理依赖、网卡、初始认证、服务和可恢复升级。安装时必须保留随机密码与访问范围这类基础保护；业务偏好移至 Web，旧测试配置保留显式低层选项。
2. 网关设置按访问与统计、维护与 API、网络与出站、高级选项分组，预览显示核心及辅助服务影响，监听切换给出新地址。
3. 订阅与策略直接管理完整 Clash/Mihomo URL/本地 YAML 和原生组/规则。后端在完整对象上合并，保留未展示字段和凭据，避免日常往返简化 PPsub。
4. PPsub 的高级编辑、导入/导出和旧配置保存继续保留。新入口复用既有持久策略、订阅刷新和校验流程。
5. 独立 settings 服务执行任务，核心重启或浏览器重连后按任务号查询；失败回退不重启任务服务自身。任务元数据不落盘明文输入凭据。

## 验证与限制

自动检查及隔离环境的验证方法见[测试说明](testing.md)。模拟测试不能替代目标环境中的服务与网络验证。

主要实现依据：[配置事务](../integration/manage_config.py)、[运行操作](../integration/manage_runtime.py)、[字段模型](../integration/manage_model.py)、[Web worker](../integration/manage_web.py)、[原生策略合并](../integration/manage_policy.py)、[核心重载脚本](../deploy/debian/runtime.sh)、[原控制器](../FILES/usr/bin/ppg.sh)。
