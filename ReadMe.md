# LovestGateWay

<img src="lovestgateway.svg" width="360" height="108" alt="LovestGateWay logo">

源自 [kkkgo/PaoPaoGateWay](https://github.com/kkkgo/PaoPaoGateWay) 的 Rust FakeIP 网关，改为 Debian 原生部署，提供 Web 配置、订阅与策略管理。当前仓库为 [Lovest20018/LovestGateWay](https://github.com/Lovest20018/LovestGateWay)。

- [Debian 安装、升级、运行与卸载](docs/debian-deployment.md)
- [中文脚本管理：配置、备份、恢复与升级](docs/debian-management.md)
- [与上游的差异、热切换范围及实现状态](docs/project-differences-and-web-settings.md)
- [正式版本与安装包下载](https://github.com/Lovest20018/LovestGateWay/releases)
- [上游同步台账与定期审查](docs/upstream-maintenance.md)
- [正式发布流程与源码校验](docs/releasing.md)
- [Web 网关设置与订阅策略](docs/debian-web-settings.md)
- [可选观测 API 契约及模式能力](docs/integration-api-v1.md)
- [OpenAPI 3.1](docs/openapi-v1.json)
- [确定性 HTTP 样例](integration/fixtures/samples.json)
- [测试与验证方法](docs/testing.md)
- [上游配置和 ISO 文档归档](docs/upstream-iso-reference.md)

对外观测 API 默认关闭，可在“网关设置 → 维护与 API”按需开启；关闭时不监听端口，并停止 API 历史采集，Web 管理、订阅和代理转发独立运行。观测凭据不能重启、切换节点、断开连接或修改配置。可选 API 提供状态/指标、FakeIP 缓存、有界 TCP 记录、Mihomo 快照、历史查询和白名单探测；轮询和事件缓冲都有覆盖缺口，不能保证完整流量历史或远端 DNS。

Debian 版移除 Cloudflared，mihomo 直接从官方 GitHub 稳定版在线安装、校验、更新和回退。PPsub 支持读取及编辑完整 Clash 配置，自动刷新冲突时保留最后成功配置。新安装按需开启显式代理并配置认证，管理访问默认限制在私网；服务调度统一为 Asia/Shanghai。

本地发布包可用 `sudo sh install.sh --interface eth0 --install-deps --yes` 安装；`sudo ppgw-admin` 提供中文菜单与可自动化子命令。配置修改带校验、版本检查、运行验收和失败回退；安装升级与 Web 写操作共用跨进程锁。日常通过“网关设置”和“订阅与策略”管理；后者由同一套 PPsub 编辑器统一维护订阅、分组和规则，保留高级功能及旧格式兼容。新安装不自动套用测试配置。

CI 构建 Debian amd64 安装包和校验文件，正式 Release 另附对应完整源码和发布清单。每周检查上游并生成待审查报告，维护者评审、移植和验收后发布独立版本。不再构建/发布 ISO 或推送上游 Docker Hub 镜像。旧 ISO 脚本保留以便追踪上游；Debian 运行和发布均不调用这些脚本。

项目品牌为 LovestGateWay。已有安装继续使用 `ppgw-admin`、`paopaogw` 服务与配置路径；API 的 `kind=paopaogateway`、PPsub 格式标记和浏览器登录存储键保持兼容。上游项目名称与来源归属保留。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/export-api-contract.py
python3 scripts/check-api-contract.py  # 需要 python3-jsonschema
cd sniffbox
cargo test --workspace --locked
cargo build --release --locked -p sniffbox
```

保留上游版权与 GPL-3.0 许可，见 [LICENSE](LICENSE)。
