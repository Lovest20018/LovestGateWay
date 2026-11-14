# 参与开发

LovestGateWay 的正式交付目标是 Debian 12/13、amd64、systemd。日常安装使用 [Release](https://github.com/Lovest20018/LovestGateWay/releases/latest)；源码开发、测试和发布按以下入口进行。

## 代码与文档目录

| 位置 | 内容 |
| --- | --- |
| `sniffbox/` | Rust 工作区：转发核心、DNS、运行接口及 PPsub CLI |
| `frontend/` | 面板源码、构建器和前端单元测试 |
| `integration/` | Python 管理、配置事务、订阅、观测 API 和历史采集；设置/订阅兼容视图源码 |
| `deploy/debian/` | Debian 安装、升级、卸载和 systemd 服务 |
| `FILES/` | 继承的运行资源与控制脚本；Debian 打包仍使用其中的指定文件 |
| `scripts/`、`tests/` | 打包、契约、合成浏览器/CLI 验证及 Python 测试 |
| `docs/` | 使用、开发、专项参考与各版本发布说明 |
| `.github/` | CI、正式发布、上游检查及已审查上游台账 |
| `dist/` | 本地生成的包、截图和测试证据，已忽略 |

根目录 ISO、ARM64、Docker 构建脚本和 `rust-musl-builder/` 保留上游来源与兼容用途，当前不作为 Debian 正式发布入口；相关脚本沿用原路径和相互引用。历史文档集中在 `docs/reference/` 与 `docs/upstream-iso-reference.md`。

## 开发环境与检查

完整验证使用 Linux、Rust（以工作区与 CI 配置为准）、Node.js 24 和 Python 3。YAML / API 契约检查需要 PyYAML、jsonschema；浏览器验证另需 Playwright 和 Chrome。Node.js 只用于构建与测试，不是生产服务依赖。

建议在 Python 虚拟环境中安装开发依赖：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install PyYAML jsonschema
python -m unittest discover -s tests -v
node --test frontend/tests/*.test.mjs
node frontend/build.mjs --output dist/dev-ui
python scripts/check-public-source.py
python scripts/export-api-contract.py
python scripts/check-api-contract.py
git diff --exit-code -- docs/openapi-v1.json integration/fixtures
```

```sh
cd sniffbox
cargo test --workspace --locked
cargo build --release --locked -p sniffbox
```

Windows 可运行 Python、前端单元及合成浏览器检查，涉及 systemd、Unix socket 和真实网络的验证需要 Linux。浏览器安装命令和各场景入口见[测试与验收](docs/testing.md)；自动执行范围以 [Debian CI](.github/workflows/debian.yml) 为准。

## 修改与反馈

提交应说明具体触发条件、修改后的行为和相关验证。面板变化同步更新使用说明，影响安装、配置兼容或升级的变化写入对应版本说明。保持 `paopaogw` 服务名、既有配置/API 标识和 PPsub 兼容约定；不要把品牌更新当作存储路径迁移。

问题反馈请附本项目版本、Debian/架构、操作步骤、预期与实际结果，以及必要的脱敏错误。真实订阅、节点地址、密码、Token、运行实例标识及私人部署记录不进入 Issue、截图或源码。合成测试和演示图片不连接运行中的网关。

正式版本由维护者按[发布流程](docs/releasing.md)构建，经过测试、源码与安装包一致性及上传摘要校验后公开；已发布附件保持不变，修复通过新版本交付。上游更新先评审并记录[台账](.github/upstream.json)，详见[上游跟进](docs/upstream-maintenance.md)。
