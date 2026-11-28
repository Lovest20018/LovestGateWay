# LovestGateWay

<img src="https://cdn.jsdelivr.net/gh/Lovest20018/LovestGateWay@v0.2.9/lovestgateway.svg" width="320" height="96" alt="LovestGateWay">

[![正式版](https://img.shields.io/github/v/release/Lovest20018/LovestGateWay)](https://github.com/Lovest20018/LovestGateWay/releases/latest)
[![Debian CI](https://github.com/Lovest20018/LovestGateWay/actions/workflows/debian.yml/badge.svg)](https://github.com/Lovest20018/LovestGateWay/actions/workflows/debian.yml)
[![GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

运行在 **Debian 12/13 · amd64 · systemd** 上的 Rust FakeIP 网关，通过 Web 管理订阅、节点、分流策略和运行状态。支持原生 Debian 部署，源自 [PaoPaoGateWay](https://github.com/kkkgo/PaoPaoGateWay)，保留上游来源与 GPL-3.0 许可。

[安装](#一键安装) · [升级](#升级) · [与上游的区别](#与上游的区别) · [使用文档](docs/README.md) · [更新记录](CHANGELOG.md) · [问题反馈](https://github.com/Lovest20018/LovestGateWay/issues)

## 能做什么

| 功能 | 使用方式 |
| --- | --- |
| 网关转发 | FakeIP DNS、透明 TCP/UDP；保留直连、SOCKS5、OpenVPN、YAML 和订阅五种出站模式 |
| 订阅与分流 | 在 LovestSub 编辑器中管理订阅提供商、节点组、规则和更新计划 |
| 节点管理 | 手动切换、测速和提供商更新。开启故障转移的手动组显示为「手动 · 故障转移」 |
| 运行观测 | 实时连接、流量、分布、日志；可选保存历史流量、资源使用、建连结果和出口检测延迟 |
| 局域网代理 | HTTP / SOCKS5 共用 `1080` 端口，独立填写代理用户名、密码和允许访问范围 |
| 维护与恢复 | 配置预览、后台任务、网关升级、Mihomo 独立更新、备份及失败恢复 |

桌面与手机使用同一套面板，支持侧栏收起、亮暗主题和大列表分批展示。日常使用无需开启对外观测 API。

## 与上游的区别

**LovestGateWay 独立维护和发版。** GitHub Actions 每周检查 PaoPaoGateWay 的 `main` 和最新稳定版，有变化时只创建或更新差异报告草稿 PR。上游代码由维护者评审后按需手动移植，通过验收后再合并。 [上游跟进流程](docs/upstream-maintenance.md)

以下以已审查的 PaoPaoGateWay `20261120-8a165c2` 为对照。 [上游源码](https://github.com/kkkgo/PaoPaoGateWay/tree/8a165c2b1dcbb3f21426791e155ec88e4c5e9a84)

| 范围 | PaoPaoGateWay | LovestGateWay |
| --- | --- | --- |
| 节点 DNS | PPsub 开启 `dns_burn` 时可用订阅提供的 DNS 预解析节点，生成 `@subdns` 等固定 IP 副本 | LovestSub 将订阅专属 DoH/DoT 按节点域名写入 Mihomo 的 `proxy-server-nameserver-policy`，连接节点时按对应策略实时解析 |
| 订阅与配置 | PPsub 编辑器导出 JSON，由 `ppgw.ini` 或镜像指定来源 | LovestSub 支持每个订阅独立更新和手动组故障转移。网页支持切换到 LovestSub 并导入其 JSON，已有完整 Clash/Mihomo 配置可继续运行 |
| 部署 | 面向虚拟机的 ISO，可用 Docker 定制 | Debian 12/13 amd64 原生安装包，由 systemd 管理服务，安装沿用宿主 IP 和默认路由 |
| 管理与维护 | 上游 Web 面板及配套脚本 | 自维护 Web 面板支持配置预览、加载验收、备份和失败回退。网关程序与 Mihomo 分别更新 |
| 运行观测 | 面板提供实时连接和流量信息 | 增加独立的可选观测 API 和持久运行历史 |

**LovestSub 使用原始域名节点时可以关闭节点 DNS 预解析。** 关闭后停止生成 `@subdns` 等固定 IP 副本，已配置的订阅专属 DoH/DoT 和引导 DNS 继续生效。需要对多个固定 IP 入口测速或选点时，可以开启预解析。`dns_burn` 在 v0.2.9 默认开启。

如果节点组正使用固定 IP 副本，先切回对应的原始节点，再到 **设置 → 高级选项 → 节点 DNS 预解析** 关闭并应用。应用后核对常用节点组的选点、FakeIP DNS 查询和 HTTPS 代理出站。已在 Debian 实例验证，关闭预解析后通过 UDP 和 TCP 查询均返回 FakeIP，原始域名节点可正常代理出站。

LovestSub 合并订阅的有效 `hosts`，网关的基础 DNS 和 `paopaogateway.dns` 仍由 `base.yaml` 决定。节点使用 `hosts` 域名别名时会展开到目标域名并保留原 TLS SNI。 [完整差异与上游评审记录](docs/project-differences-and-web-settings.md)

## v0.2.9

- 代理与规则页显示「手动 · 故障转移」并保留手动切换；顶部模式切换收口为「规则」与「全局」，外部直连时提示恢复。
- 订阅与策略编辑器收口为原生 LovestSub（默认导出 `lovestsub.json`，兼容旧 `ppsub.json`），支持三模式选择与手动组故障转移。
- 多订阅私有 DoH/DoT 按节点域名隔离写入 Mihomo `proxy-server-nameserver-policy`，保留并展开 `hosts` 域名别名且保护 TLS SNI。

升级沿用已有的配置、订阅及节点组设置。详细说明见 [v0.2.9 发布说明](docs/releases/0.2.9.md)。

## 界面预览

以下为 v0.2.9 的界面截图，使用虚构演示数据。 [仓库中的截图文件](docs/images)

![LovestGateWay 概览与运行历史](https://cdn.jsdelivr.net/gh/Lovest20018/LovestGateWay@v0.2.9/docs/images/overview.png)

<details>
<summary>查看代理页面与暗色手机界面</summary>

![代理页面](https://cdn.jsdelivr.net/gh/Lovest20018/LovestGateWay@v0.2.9/docs/images/proxies.png)

<img src="https://cdn.jsdelivr.net/gh/Lovest20018/LovestGateWay@v0.2.9/docs/images/mobile-dark.png" width="290" alt="暗色手机界面">

</details>

## 一键安装

在目标 Debian 机器执行：

```sh
curl -fsSL https://raw.githubusercontent.com/Lovest20018/LovestGateWay/main/install.sh | sudo sh
```

脚本下载最新正式版，校验安装包后进入中文向导，安装完成会显示 Web 地址。没有 curl 时，先运行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates`。

1. 打开安装器显示的 Web 地址，使用设置的管理密码登录。
2. 在 **设置 → 维护与 API** 安装 Mihomo；CPU 型号通常保持“自动”。
3. 在 **订阅 → 配置来源** 导入 LovestSub JSON，再按需设置节点组和规则。
4. 配置主路由的 DNS 与路由，按需开启 UDP、IPv6 和统计，再从实际终端验证网络。

新安装默认直连，可选功能按需启用。安装器不修改宿主 IP、默认路由或主路由。离线安装、端口占用与恢复方法见[安装与升级](docs/debian-deployment.md)。

## 常用设置

| 想做什么 | 入口与说明 |
| --- | --- |
| 使用 HTTP / SOCKS5 代理 | **设置 → 访问与统计 → 局域网代理**：开启后设置用户名、密码和来源范围，客户端连接网关 LAN IP 的 `1080` 端口 |
| 节点失败后自动切换 | 在 **订阅 → 策略编辑 → 节点组** 选择「手动选择」，再勾选「故障转移」。日常可手动切换，全部候选失败时保留当前节点 |
| 切换 Mihomo 运行模式 | **代理**和**规则**页顶部提供「规则」和「全局」。核心被外部设为 `direct` 时，页面会提示直连状态，点击「规则」即可恢复分流 |
| 查看过去 1 小时 / 24 小时指标 | **设置 → 维护与 API**：开启观测 API 和 API 历史采集，然后在**概览 → 运行历史**查看；只供本机使用时 API 保留 `127.0.0.1` 监听 |
| 更换管理密码或监听地址 | **设置**：预览影响后确认应用；密码变更或核心重启后重新登录继续查询原任务 |

历史从开启后积累，不补造过去数据。连接失败率仅代表已观测 TCP 建连结果，不等同于网站成功率；延迟来自默认出口已有的近期检测，采集不会主动测速。[完整 Web 使用说明](docs/debian-web-settings.md)

## 升级

在 **设置 → 维护与 API → LovestGateWay 更新** 检查并确认升级，或重新运行上面的一键命令。升级保留配置、密码、订阅和服务启动设置；重启期间会短暂断连，重新登录后继续查看原任务，完成后刷新页面。

网关程序与 Mihomo 核心分别更新。旧版没有 Web 更新入口时，先用一键命令升级一次。正式附件包含 Debian amd64 安装包、对应源码、SHA256 文件、安装入口和发布清单，见 [Releases](https://github.com/Lovest20018/LovestGateWay/releases/latest)。发布新版本不会自动升级已有服务器。

## 文档与开发

| 需求 | 入口 |
| --- | --- |
| 安装、升级与常见问题 | [安装指南](docs/debian-deployment.md) |
| 面板、订阅、策略与历史指标 | [Web 使用说明](docs/debian-web-settings.md) |
| 终端备份、恢复和管理 | [命令行参考](docs/debian-management.md) |
| 所有文档与 API | [文档导航](docs/README.md) |
| 开发环境、目录与反馈规范 | [参与开发](CONTRIBUTING.md) |
| 实现状态与验收边界 | [项目状态](docs/optimization-roadmap.md) |

终端管理可运行 `sudo ppgw-admin`。源码主线持续开发，部署应使用正式 Release；目前正式包仅提供 Debian amd64，根目录保留的 ISO / ARM64 / Docker 构建脚本属于上游兼容资料，不代表本项目发布这些产物。
