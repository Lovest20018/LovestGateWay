# LovestGateWay · Debian 脚本管理

`sudo ppgw-admin` 打开中文菜单。无终端时必须使用子命令。脚本管理网关自身，不接管宿主 IP、默认路由、NTP、全局防火墙或 Docker 端口映射。

## 安装与升级

从可信渠道取得本地 amd64 发布包及 SHA256，先核对外部摘要，再解压。包中 `install.sh` 会检查全部 manifest 文件，执行依赖预检、备份、安装及 readiness 验收。

```sh
sha256sum -c lovestgateway-VERSION-debian-amd64.tar.gz.sha256
tar -xzf lovestgateway-VERSION-debian-amd64.tar.gz
cd lovestgateway-VERSION-debian-amd64
sudo sh install.sh --interface eth0 --install-deps --yes
```

交互安装可运行 `sudo sh install.sh`。程序先校验本地包，再列出网卡及 IPv4 地址，选择服务网卡、Web/DNS 监听和启动方式，显示安装摘要后输入 `yes` 或“是”确认。缺少依赖时会询问是否使用 apt 安装；命令行 `--install-deps` 可直接指定。Python 3 必须先安装才能进入此入口。无人值守安装需要 `--yes` 和首次服务网卡；省略启动方式时，全新安装采用 `auto`，升级采用 `preserve`。

| 启动方式 | 安装后立即运行 | 下次开机启动 |
| --- | --- | --- |
| `--startup auto` | 是 | 是 |
| `--startup manual` | 是 | 否 |
| `--startup disabled` | 否，只安装 | 否 |
| `--startup preserve` | 沿用原状态，仅升级 | 沿用原状态，仅升级 |

启动计划应用于网关核心、订阅管理及设置服务。可选观测 API 受 `api.enabled` 控制，默认关闭；API 历史采集还受独立开关控制，关闭 API 时一并停止。旧配置未明确设置 `api.enabled=true` 时，升级会停止 API 及其采集并取消自启。自动更新也按配置开启。安装会核对实际运行和自启状态；验收失败时恢复安装前文件与服务状态。只安装模式未做运行验收，成功提示会明确说明。

全新安装可传入 `--web-listen 0.0.0.0:8080 --dns-listen 192.0.2.10:53`；将文档示例地址替换为实际 LAN 地址。未指定时默认使用 `0.0.0.0:80` 和 `0.0.0.0:53`，启动前检查监听占用及网关保留网络资源。API 默认 `127.0.0.1:8788`，内部订阅/设置服务为 `127.0.0.1:8789/8790`。升级保留已有网卡和监听配置，不能借安装选项修改它们，日常修改请使用 Web 或配置管理命令。

新安装使用直连模式，显式代理、UDP、核心统计、IPv6、历史采集和自动更新关闭；随机管理密码和私网/回环访问范围保留。初始密码位于仅管理员可读的 `/etc/paopaogw/files/ppgw.ini`。安装成功后显示实际 Web 访问地址、密码位置及下一步操作。既有安装保留 UUID、Token、密码、订阅、策略、历史和原有服务状态，旧初始化标记缺失也不会重置配置。升级首次引入设置服务时，其运行和自启状态跟随原核心，即使核心暂时停止但已设置自启，也会保留该计划。

只安装示例：

```sh
sudo sh install.sh --interface ens18 --startup disabled --install-deps --yes
# 稍后启动（仅本次运行）
sudo systemctl start paopaogw paopaogw-subscription paopaogw-settings
# 或启动并设为开机自启
sudo systemctl enable --now paopaogw paopaogw-subscription paopaogw-settings
```

安装确认、帮助、完成提示、管理菜单与错误说明均使用中文；错误后保留稳定诊断码，批量管理 JSON 的字段名和状态值保持不变。apt/systemd 的系统原生输出由 Debian 的语言设置决定。

日常管理使用[Web 网关设置与订阅策略](debian-web-settings.md)，脚本继续负责批量配置、安装升级、备份恢复和救援。Mihomo 在 Web 的“维护与 API”中单独安装、更新和回退，使用它不要求开启观测 API。

已安装系统可直接升级，无需手动停止服务：

```sh
sudo ppgw-admin deploy --archive /path/release.tar.gz --sha256 TRUSTED_64_HEX --yes
sudo ppgw-admin status
```

只支持本地指定包；拒绝路径穿越、链接、特殊文件、重复/额外文件、摘要错误、过大包与错误 ELF 架构。不从 URL 自动获取网关程序。

## 查看与修改配置

```sh
sudo ppgw-admin config show
sudo ppgw-admin config schema
sudo ppgw-admin status
```

`show` 返回持久配置及 `revision`，密码和订阅 URL 脱敏。`status` 同时展示服务 active/enabled、核心 boot_id/config_revision 和事务阶段；保存配置与核心运行是两种状态。核心未运行时仅保存配置，不声称已加载。

配置补丁是以下 JSON 结构。先复制 `show` 中的 revision，文件使用 `chmod 600`，也可 `--file -` 从 stdin 输入。凭据没有命令行明文参数；菜单对敏感字段使用隐藏输入。不要把 `********` 脱敏占位符写回。

```json
{
  "base_revision": "COPY_CURRENT_REVISION",
  "patch": {
    "ini": {"max_rec": 6000, "udp_enable": true},
    "service": {"PPGW_INTERFACE": "eth0", "PPGW_IPV6": true},
    "maintenance": {"history_enabled": false, "auto_update": false}
  }
}
```

```sh
chmod 600 patch.json
sudo ppgw-admin config check --file patch.json
sudo ppgw-admin config apply --file patch.json --yes
```

`check`/`apply --dry-run` 不写文件、不重启。应用时在跨进程锁内再次核对 revision，过期补丁拒绝。INI/env 的未知字段和独立注释保留，JSON 保留未知字段；只修改白名单键。显式 null 仅清除默认空值的可选字段。

分组范围：

| 分组 | 内容 |
| --- | --- |
| ini | free/socks5/ovpn/yaml/suburl 五模式；DNS/FakeIP；UDP 配额；代理/管理 CIDR 与认证；上游/VPN/订阅；调度；统计；受限 box 设置 |
| service | PPGW_INTERFACE、PPGW_IPV6、TZ、PPGW_WEB_LISTEN、PPGW_DNS_LISTEN；IPv6 同步兼容 network 标记 |
| api | enabled（默认 false）、listen、port、name、probe_urls（最多16个公共HTTP(S)地址）、read_token（专用观测凭据） |
| maintenance | 历史采集/保留天数、官方 Mihomo 自动更新、时区 |

准确类型、范围和 `box` 白名单以 schema/校验结果为准。不支持 Cloudflared 或 Debian 禁用的 NIC/RPS 调优。更换模式/订阅源时，现存本地 PPsub/策略会触发冲突；确实要清除旧源时先备份，增加 `--reset-source`，其删除也参与失败回退。

安全字段（访问 CIDR、管理密码、代理认证、转发阈值）走 `/sniffbox/reload/core`，仅更新核心，保留已加载的 Mihomo 配置；记录上限和清理日在统计开启时也走此路径。验收检查 boot_id 不变且 revision 改变；其他核心/监听/模式修改重启，QUIC 等阻断项同时涉及防火墙，仍需重启。真实长连接保持需单独验收。冷启动最长等90秒，YAML/订阅还检查代理及 PPsub readiness。修改监听后内部客户端跟随 service.env，不使用 HTTP_PROXY。修改历史/自动更新开关会实际 enable/disable 相应服务/timer；停用采集保留已有历史。

## 本地资源

```sh
sudo ppgw-admin assets import --type yaml --file ./config.yaml --name custom.yaml --yes
sudo ppgw-admin assets import --type ppsub --file ./ppsub.json --name ppsub.json --yes
sudo ppgw-admin assets import --type ovpn --file ./client.ovpn --name custom.ovpn --yes
```

资源最大1MiB，名称不接受目录，覆盖同名文件需要 `--overwrite`。导入只保存文件；再应用对应模式/来源配置。OpenVPN 接受内联证书，拒绝脚本、插件、外部 include/证书路径等；账户密码通过 INI 管理。证书类型 `cert` 接受 PEM。YAML 模式应用时需本机 Mihomo 校验，OVPN 需 openvpn 与 TUN。

已使用同名资源且配置字段无变化时，可在菜单选择“重启并验证持久配置/资源”，或给空补丁 `{ "base_revision": "当前版本", "patch": {} }` 使用 `config apply --file patch.json --restart --yes`。`subcron` 是每天更新的小时（0..23），为空时使用 subtime 周期；不是五段 cron。当前 Debian production 的 box 仅接受 route.block_bittorrent、route.block_quic、route.block_unknown。API 监听目前限定 IPv4；核心 Web 监听可用 IPv4/IPv6。

## 备份与恢复

```sh
sudo ppgw-admin backup create
sudo ppgw-admin backup list
sudo ppgw-admin restore BACKUP_ID --yes
sudo ppgw-admin recover --yes
```

备份在 `/var/backups/paopaogw`，目录0700/数据0600，包含凭据，保留在服务器本地。列表只显示 ID。完整备份暂时停止项目服务保证状态文件一致，然后恢复原 active/enabled。恢复前校验所有数据并创建救援备份。Mihomo 二进制由独立更新器管理，不随网关备份替换。

事务日志在 `/var/lib/paopaogw/admin/transaction.json`。失败自动回退；进程被杀后用 `recover`。若配置被外部修改，条件回退拒绝覆盖，事务保留 conflict 状态，应先比较本机文件与对应备份、明确处理外部修改再恢复。CLI、PPsub Web worker 与 Mihomo 更新使用同一把非阻塞进程锁；忙时稍后重试。不要手动删除锁文件来绕过运行中的管理操作。

备份、恢复和中断回退均核对目标服务的 active/enabled；目标核心或 API 原本运行时，还需验证其实际响应。历史备份可能早于 API 的显式启用字段，恢复时仍按备份记录的活动服务验收实际接口，不凭 systemd 的 active 单独认定成功，也不会为了探测修改持久开关。验收失败保留 `rollback_unconfirmed`，修正故障后显式重试恢复。

Web 的“诊断与恢复”提供脱敏报告、分页备份校验与带短时签名的恢复影响预览，沿用同一事务执行器。Web 恢复保留当前管理任务服务以记录最终结果；与备份记录不同的管理服务运行/开机状态会明确标记为保留项，需要完整恢复这些状态时使用服务器上的 CLI。若恢复了程序文件或该服务单元，应在任务完成后运行 `sudo systemctl restart paopaogw-settings` 并刷新页面，使运行中的管理代码和启动设置与恢复后的文件一致。

升级主进程中断时，仍在写入的安装子进程会继续持锁；此时恢复返回 `MANAGEMENT_BUSY`，需等安装器退出后重试，以免安装写入与回退互相覆盖。无效 YAML 导入只显示管理错误，菜单可以继续使用。

若升级中断导致已安装管理模块不可用，重新核对并解压可信发布包，在包目录运行 `sudo sh install.sh --recover --yes`。该入口先校验包，再使用包内独立管理模块恢复持久事务，不依赖正在修复的安装目录。完整备份恢复会移除备份后新增的受管文件（包括可能覆盖远程源的本地 PPsub/策略），并恢复备份记录的服务状态；配置/资源的局部备份只恢复登记文件。

OpenVPN 资源禁止默认路由重定向；显式 route 只允许 IPv4 /32 或 IPv6 /128 单目标经 VPN 的路由。宿主默认路由仍由 Debian 管理。

## 仅手动更新订阅节点

原生 PPsub 可在“订阅与策略 → 全局选项”开启“仅手动更新订阅节点”，与编辑内容一起“保存并应用”。对应持久配置为 `subtime=manual`，优先于 `subcron`；此时普通重启、策略编辑和定时轮询均只读取本地已保存节点。`365d` 仍是有效的更新间隔，但不会阻止启动时重新获取。

需要更新时先在机场侧开启订阅获取，再点击页面顶部“更新订阅节点”。获取过程使用独立目录，保留旧缓存供失败时回退；空内容、无节点或无效配置不会替换当前节点。全部提供商获取失败时继续运行现有配置；部分失败时保留对应提供商的旧节点。成功候选经 Mihomo 验证后才发布并重启加载，加载失败恢复旧缓存。页面显示各提供商已保存节点数及本次获取失败状态。

节点缓存持久保存在 `/var/lib/paopaogw/subscriptions/`，数据权限 0600，目录权限 0700，按提供商名称和订阅地址的 SHA-256 绑定；不依赖 `/tmp`。缓存包含节点凭据，应随本机完整备份保管。首次启用手动模式前须已有成功缓存；更换订阅地址或添加新提供商后需手动获取，新来源不会套用旧来源节点。固定节点不能延长机场账号或节点本身的有效期。
