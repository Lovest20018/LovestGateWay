# Debian 运行机制（维护参考）

日常安装、升级请使用[安装指南](../debian-deployment.md)。本页保留原生服务、低层安装器及兼容行为的详细说明，供排障和维护时查阅。

本 fork 主交付是 Debian 12/13 的原生 Linux 二进制、systemd 服务和 Python 标准库 API。ISO 构建脚本保留作上游历史参考，不再参与 fork 的发布流程。首次迁移应使用独立 Debian 实例，完成转发验收后再更改主路由。

## 安装

推荐使用[脚本管理入口](../debian-management.md)：核对发布包外部 SHA256 并解压后运行 `sudo sh install.sh --interface eth0 --install-deps --yes`，使用默认开机自启并立即启动的计划完成依赖、备份、安装和启动验收。直接运行 `sudo sh install.sh` 可按中文向导选择网卡、监听及启动方式；`--startup manual` 仅本次运行，`--startup disabled` 只安装。升级默认保留原有状态。升级使用 `sudo ppgw-admin deploy --archive 包.tar.gz --sha256 可信摘要 --yes`。中文菜单运行 `sudo ppgw-admin`。以下原生 `install.py` 是保留的低层入口，需自行停止服务和控制启动。

安装基础依赖：

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-yaml nftables iproute2 procps ca-certificates util-linux openvpn openssl
sha256sum -c lovestgateway-版本-debian-amd64.tar.gz.sha256
tar -xzf lovestgateway-版本-debian-amd64.tar.gz
cd lovestgateway-版本-debian-amd64
sudo python3 install.py
```

安装会校验包内 manifest，不自动启用服务。配置和实例身份首次生成，重复安装保留；更新前须停止核心、API、订阅写入、设置、历史采集与维护服务。源构建请用当前 Rust stable、clang/libclang-dev、cmake、perl、g++，执行 `cargo test --workspace --locked` 和 `cargo build --release --locked -p sniffbox`；不执行 `cargo update`。构建元数据记录 Git 基线提交及源文件摘要，未提交构建不可只用提交号识别。

修改 `/etc/paopaogw/service.env` 的 `PPGW_INTERFACE`，填写实际网卡名，如 `ens18`。IP、默认路由、DNS、NTP 由 Debian 管理。修改 `/etc/paopaogw/files/ppgw.ini`；格式和五种模式沿用上游，密码首次安装随机生成，8 位随机字符。配置不作为 shell 执行。

FakeIP DNS 默认在 `0.0.0.0:53` 同时监听 UDP 和 TCP，两个协议共用同一 FakeIP 地址池。安装预检同时检查两个协议的端口占用；任一监听失败时核心启动失败。与其他服务共用宿主时，可设置 `PPGW_DNS_LISTEN="192.0.2.10:53"`，将文档示例地址替换为实际网关 LAN 地址，避免接管宿主的 `127.0.0.1:53`。该字段可通过脚本配置管理修改，生效需要重启核心；原有宿主 resolver 配置保持不变。

`free`、`socks5` 不需要 mihomo。`yaml`、`suburl` 模式先运行 `sudo python3 /usr/local/lib/paopaogw/mihomo_update.py install`，直接从 MetaCubeX/mihomo 官方 GitHub 稳定版按 CPU 与系统能力选择 amd64-v1/v2/v3 资产，v1 可使用旧 compatible 命名，验证官方 SHA256、ELF、版本和现有配置后安装。Geo 数据按规则需要放入 `/var/lib/paopaogw/clash/`，可用原生面板 Geo 更新入口维护。配置文件放 `/etc/paopaogw/files/custom.yaml` 或填订阅地址；依赖远端配置的模式需要目标可达。`ovpn` 的 openvpn/openssl 已列为安装依赖，配置和证书放同一 files 目录；优先使用内嵌证书的单个 .ovpn 文件。Cloudflared 已从 Debian 运行路径、API 和导航入口移除，旧 token 不再启用进程或 nft UID 规则。

```sh
sudo systemctl enable --now paopaogw.service paopaogw-subscription.service paopaogw-settings.service
# 对外观测 API、历史采集和自动更新按需在 Web 开启
sudo systemctl status paopaogw.service paopaogw-settings.service
sudo journalctl -u paopaogw.service --since '10 minutes ago'
```

对外观测 API 默认关闭，不监听端口。确需接入通用观测客户端时，在“网关设置 → 维护与 API”开启，或通过脚本设置 `api.enabled=true`。默认监听配置为 `127.0.0.1:8788`；其他设备连接时将 `listen` 改成专用管理网地址，并限制允许访问的来源；跨不可信网络使用 TLS 反向代理。客户端使用 `/etc/paopaogw/read-token` 中的专用观测凭据，不能使用它修改网关配置。Web 管理和订阅功能不依赖这个 API。

`probe_urls` 是管理员预配置的精确公开 HTTP(S) URL 列表，新安装为空；单次 HEAD、禁用重定向、最多 10 秒、并发 2。配置修改后重启 API 服务。无法强制地址族/固定本次 DNS 地址/指定节点，调用这些选项返回 unsupported。

## 订阅与策略

“订阅与策略”仅编辑 LovestSub。配置来源分栏显示已安装实例正在使用的 LovestSub、本地 YAML 或远程完整 Clash 配置。切换时可导入新的 LovestSub JSON（兼容导入旧 `ppsub.json`），或复用已保存的本机配置。有旧覆盖策略冲突时，可在预览前勾选备份并清除。旧 `/ui/#/ppsub` 自动跳转至该页面。日常操作见[订阅与策略及网关设置](../debian-web-settings.md)。

安装 Mihomo 后，启用内部订阅写入服务。

```sh
sudo systemctl enable --now paopaogw-subscription.service
```

该服务仅监听 `127.0.0.1:8789`。面板经已认证的 `/sniffbox/subscription/config`、`status`、`apply` 固定路由读取和写入，浏览器不需要访问 8789 或再次输入密码。观测 API Token 不能使用这些管理路由。

编辑器优先读本机 `/etc/paopaogw/files/ppsub.json`（保留兼容文件名与 `ppsub@` 内部前缀），也可读取远程 LovestSub 的运行副本并在首次保存时转为本机来源。全局选项、提供商、节点组、规则和更新计划统一保存。保存前校验配置版本及字段，应用后核对核心加载结果。加载失败且配置未被外部修改时恢复原文件。运行完整 Clash 配置的实例会得到只读状态及切换指引。

LovestSub 保留原有 JSON 字段。节点组在手动选择下可设置 `failover: true`，并通过 `monitor_url`、`expected_status` 和 `retries` 配置健康检查。自动测速和智能测速分别使用测速参数与 `smart`。代理快照只对当前生效的 LovestSub 手动 Selector 组附加 `failover: true`，代理页和规则页据此显示「手动 · 故障转移」。点击节点沿用 Mihomo Selector 的选择接口。

LovestSub 按订阅将节点域名与专属 DoH/DoT 写入 Mihomo 的 `proxy-server-nameserver-policy`。同一域名出现在多个订阅时，按订阅顺序合并并去重。解析器地址中的 `#` 策略组后缀会被移除。缺少订阅解析策略的节点使用网关的 `proxy-server-nameserver`，`default-nameserver` 仅接收 IP 地址。订阅中的 `hosts` 域名别名会保留并在节点 `server` 上直接展开为目标域名（同时保留原有 TLS `sni`/`servername`），使目标域名直接命中对应订阅的专属 DoH/DoT 策略。`dns_burn` 可另外生成 `@subdns` 等固定 IP 副本。

合并 `base.yaml` 时，网关模板决定 DNS 启用状态、监听地址、IPv6、`use-system-hosts`、`enhanced-mode` 和 `nameserver`，并优先采用模板的同名 `hosts` 记录，包含 `paopaogateway.dns: 127.0.0.1`。合并会保留有效的节点和解析器 `hosts`，有额外记录时允许 LovestSub 启用 `use-hosts`。节点 DNS 策略缺少 `proxy-server-nameserver` 或 `default-nameserver` 时，从模板的 `nameserver` 补齐。

发布时 `scripts/prepare_dashboard.py` 从可维护源码构建统一路由、全部运行页面、设置、来源与 LovestSub 编辑器；LovestSub 导入和导出 JSON（默认 `lovestsub.json`，兼容导入旧 `ppsub.json`）继续可用。保留的上游 bundle 不参与运行页面加载，生成资源使用内容摘要文件名，避免旧缓存。

代理页和规则页的顶部切换只提交 `rule` 或 `global`。外部设置 `direct` 时会显示提示，点击「规则」即可切回。此切换临时作用于 Mihomo，重新加载配置后以配置值为准。

兼容策略管理接口仍可在支持范围内热加载静态规则和节点组。LovestSub 编辑保存和来源切换按配置事务应用。有关热加载的白名单、连接验收和失败恢复，见[热加载说明](../mihomo-reload-assessment.md)。

更新时同时停止正在运行的 `paopaogw-subscription`，安装后再启动。该服务独立于可选观测 API。

## 与宿主隔离

核心通过 systemd mount namespace 使用私有 `/tmp`、`/etc/hosts`、`/www` 和旧式 `/etc/config` 路径，系统真实 hosts 保持不变。不改 Debian 的网络管理器、NTP、IPv6 总开关或整套防火墙。不执行 ISO 脚本的全局清空操作。核心只创建 `ip/ip6 ppgw` 表及 `fwmark 1 → table 100`、priority 11099 的本地策略路由；这些资源已有占用则拒绝启动。规则仅拦截 `PPGW_INTERFACE` 入站。正常停止清理本服务登记的资源，不清空其他表/路由。

Debian 模式按本服务的 cgroup 识别 sniffbox、mihomo 和 OpenVPN 进程，忽略其他容器或服务中的同名进程；sniffbox 多调用 CLI 辅助进程也不会被当作主服务。内部 mihomo 启动请求使用 `PPGW_WEB_LISTEN` 的实际地址和端口，并绑定回环源地址，保留控制端点的回环访问限制。私有 Clash YAML 在降权前读取，域名解析仍在降权后执行，无需放宽配置权限。

现有宿主防火墙仍决定数据是否能到达网关；需要为 FakeIP DNS、管理面板和透明流量按实际网络设置准入。新安装采用最小配置：free 模式，显式代理、UDP、核心统计、IPv6、历史采集和自动更新关闭；保留随机管理密码及管理/代理私网范围。其余通过 Web 按需开启，显式代理启用时必须设置认证。主机 IPv6 地址、路由仍由 Debian 管理。既有安装即使缺少旧初始化标记，也不会重新套用默认配置；旧测试配置仅可用 `install.py --enable-test-features` 显式应用。

主路由的 FakeIP/真实地址段静态路由、DNS 转发以及终端实际访问需独立验收。本机代理探测通过不代表透明入口或终端路径通过。

观测 API 独立运行于 `paopaogw-api` 用户。启停 API 不重启核心；无 `Requires`、`BindsTo` 或自动维护接口。核心停止时 API 返回 `CORE_UNAVAILABLE`，不会尝试修复或重启。管理器通过核心专用 reload 路径更新访问 CIDR、认证、转发阈值和已开启的统计上限，检查 boot-id 保持及 revision 变化；该路径不重新加载 Mihomo。真实长连接保持需单独验收；版本与观测采样受同一配置锁保护。监听、出站模式、记录开关等需 `systemctl restart paopaogw`。持久历史独立存储，内存计数仍会随核心重启清零。不要直接编辑私有 /tmp 文件；持久输入始终位于 files/ppgw.ini。

## 更新、克隆与卸载

更新前备份 `/etc/paopaogw`、`/var/lib/paopaogw` 和原安装包，先停止维护 timer 和所有本项目服务，安装新包后启动。`install.py --root /临时目录` 可在 staging 目录验证安装，不触碰运行服务。新包不替换已有配置、Token、revision key、UUID 和运行数据；mihomo 单独维护。旧 API 配置没有 `enabled` 字段时按关闭处理；正常升级会停止 API 及其采集服务、取消自启，之后可在 Web 明确开启。

普通重启保留 instance-id，核心每次启动生成 boot-id。克隆后先停止 API，在克隆机重新生成 `/etc/paopaogw/instance-id` 和 `read-token`，再按需启用 API 并更新观测客户端配置；不要把克隆作为同一实例。

`sudo sh uninstall.sh` 停止/禁用本项目核心、API、订阅、设置、历史采集和维护服务、移除服务定义与项目程序。配置、身份、凭据和数据保留，便于恢复；固定 UID 用户也保留。其他服务及宿主网络配置不动。

验证方法见[测试说明](../testing.md)。部署前需在自己的隔离环境中检查实际模式、服务及转发路径，不能根据包中存在相关代码就视为通过。


## 官方核心维护与冲突恢复

`paopaogw-maintenance.timer` 启动后每天检查官方稳定版；新安装 maintenance.json 的 auto_update 默认 false。可在“网关设置 → 维护与 API”检查、在线更新和回退；订阅与策略编辑器负责配置编辑与保存应用。下载不经过第三方镜像；官方 digest 缺失、下载校验不符或配置校验失败时不替换。新核心加载失败自动恢复旧二进制，上一成功核心保留为 bin/clash.previous。首次安装核心也可执行上面的 install 命令。该维护权限独立于观测 API。

刷新完整配置时，`ppg.sh` 调用 `integration/clash_policy.py` 对生成的 `/tmp/clash.yaml` 应用与来源匹配的旧覆盖并校验最终配置，保存 `/var/lib/paopaogw/admin/clash.last-good.yaml`，同时更新 `policy-status.json`。相同来源的无效候选可使用最后成功版本。“恢复订阅原策略”会备份本地覆盖，再撤销并重新加载，失败时恢复覆盖。runtime API 分别提供源订阅、本地策略和最终配置摘要。

所有服务调度使用 Asia/Shanghai。控制器日志写入 /var/lib/paopaogw/logs/controller.log，保留一个 1 MiB 轮转文件；启动屏页面读取本实例日志。URL 在展示和控制器日志中脱敏。核心输出仍通过 journalctl 查询。

API 历史采集由 paopaogw-observe 独立运行，需同时开启 API 和历史采集开关，默认保留 30 天、最多 100000 条；客户端通过 /api/v1/history 查询。关闭 API 会停止采集并保留已有数据。Mihomo 原生决策快照通过 /api/v1/mihomo/connections 查询。两者都有采样缺口，不能证明完整流量历史或远端 DNS。

升级服务顺序示例（先备份配置、状态和旧包）：

```sh
sudo systemctl stop paopaogw-maintenance.timer
sudo systemctl stop paopaogw-maintenance paopaogw-core-action@check paopaogw-core-action@update paopaogw-core-action@rollback paopaogw-observe paopaogw-settings paopaogw-subscription paopaogw-api paopaogw
sudo python3 install.py
sudo systemctl enable --now paopaogw paopaogw-subscription paopaogw-settings
# 观测 API 和历史采集通过 Web 开关管理；按更新前记录恢复维护 timer 状态
```


首次交互安装支持手动设置并确认管理密码（至少 8 个字符，隐藏输入）；留空或 `--yes` 则生成 8 位随机密码。升级沿用原密码。安装完成后可在“网关设置 → 访问与统计 → 修改管理密码”修改，提交前再次确认，应用后重新登录。密码不作为命令行参数传递，也不出现在安装计划中。
