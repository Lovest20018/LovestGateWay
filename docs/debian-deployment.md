# 安装与升级

支持 Debian 12/13、amd64、systemd。需要管理员权限及可用的 GitHub 网络连接。安装不会替你修改宿主 IP、默认路由或主路由设置。

## 在线安装

```sh
curl -fsSL https://raw.githubusercontent.com/Lovest20018/LovestGateWay/main/install.sh | sudo sh
```

脚本下载最新正式版，核对发布清单、SHA256、包内文件和架构，再打开中文安装向导。没有 curl 时先安装：

```sh
sudo apt-get update && sudo apt-get install -y curl ca-certificates
```

按提示选择服务网卡、Web/DNS 监听和启动方式。管理密码可自行设置，留空则生成随机密码；安装结束会显示 Web 地址和密码存放位置。现有机器的 80/53 端口被占用时，在向导中改用空闲端口或专用 LAN 地址。

无人值守首次安装示例（将 `ens18` 换成实际网卡）：

```sh
curl -fsSL https://raw.githubusercontent.com/Lovest20018/LovestGateWay/main/install.sh | sudo sh -s -- --interface ens18 --install-deps --yes
```

## 升级

在“设置 → 维护与 API → LovestGateWay 更新”检查本项目 GitHub 正式版，查看发行说明并确认升级。检查只读取版本信息；确认后下载并校验指定版本，再备份和安装。重启期间管理页面与转发会短暂断开，重新登录后继续查询原任务，不要重复提交。完成后刷新页面加载新版界面。

旧版没有此入口时，先重新运行在线安装命令升级一次；命令行升级以后仍可使用。升级沿用原有网卡、监听、密码、配置、订阅和启动设置，不必手工停服务。自动化升级可加 `--yes`，无需再传网卡。

Web 更新仅支持较新的 amd64 正式发行版，检查结果 30 分钟内有效。下载失败不会替换程序；安装或启动验收失败会走原有恢复流程，若提示恢复尚未确认，请在“诊断与恢复”核对事务状态。

网关程序升级与 Mihomo 核心更新相互独立。Mihomo 在“网关设置 → 维护与 API”管理；v1/v2/v3 表示 CPU 指令集型号，通常选择“自动”。已运行的网关会为官方下载提供出站路径，无需开启对外显式代理。

## 离线安装或指定版本

从 [Releases](https://github.com/Lovest20018/LovestGateWay/releases) 下载安装包及同名 `.sha256`，传到目标机器。以下以 `0.2.4` 为例：

```sh
sha256sum -c lovestgateway-0.2.4-debian-amd64.tar.gz.sha256
tar -xzf lovestgateway-0.2.4-debian-amd64.tar.gz
cd lovestgateway-0.2.4-debian-amd64
sudo sh install.sh
```

离线包使用同一个安装向导，也可以升级现有安装。源码包用于开发，不是安装包。

## 安装后

打开 Web，在“维护与 API”安装 Mihomo，再到“订阅与策略”添加配置。新安装默认直连，UDP、IPv6、统计、自动更新和观测 API 均按需启用。管理密码保存在 `/etc/paopaogw/files/ppgw.ini` 的 `clash_web_password`，仅管理员可读；升级沿用原密码。

```sh
sudo ppgw-admin                 # 中文管理菜单
sudo ppgw-admin status          # 服务与事务状态
sudo journalctl -u paopaogw -n 80 --no-pager
```

配置主路由的 DNS 转发和 FakeIP/真实地址段路由后，用实际终端验证网络。服务启动成功不等于主路由已配置完成。

## 常见问题与恢复

- **GitHub 访问失败**：已安装时先确认网关可正常出站；若连在线脚本本身也下载失败，可在上面的 curl 命令中增加 `--proxy socks5h://127.0.0.1:1079`。新机器需有可用的 GitHub 网络连接，或使用离线安装包。下载失败不会替换程序。
- **安装被中断**：先运行 `sudo ppgw-admin status`；提示需要恢复时运行 `sudo ppgw-admin recover --yes`。
- **只安装不启动**：在向导中选择，或传入 `--startup disabled`；其他启动方式见[命令行管理](debian-management.md)。
- **卸载**：在解压后的安装包目录运行 `sudo sh uninstall.sh`，配置和数据保留。

详细服务路径、隔离机制和低层操作放在[运行机制参考](reference/debian-runtime.md)；日常设置见 [Web 使用说明](debian-web-settings.md)。
