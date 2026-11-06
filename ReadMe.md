# LovestGateWay

<img src="lovestgateway.svg" width="360" height="108" alt="LovestGateWay logo">

运行在 Debian 上的 Rust FakeIP 网关，提供 Web 配置、订阅、节点与分流策略管理。源自 [PaoPaoGateWay](https://github.com/kkkgo/PaoPaoGateWay)，使用原生 systemd 部署。

## 一键安装

支持 **Debian 12/13 · amd64**。在目标机器运行：

```sh
curl -fsSL https://raw.githubusercontent.com/Lovest20018/LovestGateWay/main/install.sh | sudo sh
```

脚本下载最新正式版，校验安装包后进入中文向导：选择网卡、监听地址和管理密码，确认后安装。安装完成会显示 Web 地址。没有 curl 时，先执行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates`。

离线安装、非交互参数和故障处理见[安装与升级](docs/debian-deployment.md)。

## 升级

在 **网关设置 → 维护与 API → LovestGateWay 更新** 点击“检查网关更新”，查看发行说明后确认升级。页面会显示下载、校验及安装进度；重启期间暂时断连，重新连接后继续查询结果。

也可以重新运行上面的一键命令。升级保留原配置、密码、订阅、节点和服务启动设置，安装器负责备份、服务切换及失败恢复。旧版没有 Web 更新入口时，先用一键命令升级一次。

正式版本使用简短编号，例如 **v0.2.1**；构建提交和上游基线放在发布清单中。下载指定版本见 [Releases](https://github.com/Lovest20018/LovestGateWay/releases)。

## 开始使用

1. 打开安装器显示的 Web 地址，使用设置的管理密码登录。
2. 在“网关设置 → 维护与 API”安装 Mihomo；v1/v2/v3 是 CPU 型号，通常保持“自动”。
3. 在“订阅与策略”配置订阅、节点组与规则。
4. 按需开启 UDP、IPv6、统计等功能，再配置主路由的 DNS 和网关路由。

日常主要使用 Web；终端管理运行 `sudo ppgw-admin`。对外观测 API 默认关闭，日常管理不需要开启。

## 文档

- [安装、升级与常见问题](docs/debian-deployment.md)
- [Web 设置与订阅策略](docs/debian-web-settings.md)
- [命令行管理与恢复](docs/debian-management.md)
- [开发、API、上游同步等参考文档](docs/README.md)

LovestGateWay 保留上游版权与 [GPL-3.0 许可](LICENSE)，独立发布 Debian 安装包。每周检查上游更新，经过评审、移植和验收后发布本项目版本。
