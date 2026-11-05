# 测试与验证

公开仓库仅保留使用合成数据的测试和可复现的验证方法。真实部署的地址、实例标识、订阅、服务状态、操作日志、截图和验收记录应保存在仓库之外。

## 本地检查

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-public-source.py
python3 scripts/export-api-contract.py
python3 scripts/check-api-contract.py
git diff --exit-code -- docs/openapi-v1.json integration/fixtures
node --check integration/gateway.js
node --check integration/ppsub_panel.js
```

API 契约校验需要 `jsonschema`，YAML 相关功能需要 `PyYAML`。确定性样例来自合成核心与实际 HTTP handler，不代表外部网络或真实部署已经通过验收。

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
node scripts/verify-subscription-flow.cjs dist/test-ui
node scripts/verify-subscription-drafts.cjs dist/test-ui
node scripts/verify-observer-api-fixture.cjs dist/test-ui
```

`PPGW_PLAYWRIGHT_MODULE` 和 `PPGW_PYTHON` 可指定本地工具位置。生成资源、截图和日志保存在被忽略的 `dist/` 中。

订阅草稿验证同时检查结构化变化预览、来源覆盖说明、敏感内容隐藏、名称按文本显示、后台生效计划及手机预览尺寸；API 使用合成响应，不触碰真实订阅或服务。

## 隔离运行验证

安装包可用暂存根目录检查文件布局、摘要及升级保留行为。真实 systemd、服务权限和透明转发验证需要独立 Debian 环境及可丢弃客户端；不能以本机 API 或显式代理成功代替完整转发路径验证。

`scripts/verify-observer-api-linux.py` 只接受带标记的一次性 systemd 容器，覆盖 API 默认关闭、管理员启用、Token 轮换、停用后释放端口、关闭采集和启用失败回退。其他使用 API 的隔离网络脚本需要先在测试配置中明确开启 API。

测试输入应使用虚构名称、文档示例地址和合成凭据。公开构建仅从源码生成通用安装包，不包含运行配置、订阅缓存或部署验收记录。

## 发布前检查

首次使用克隆后运行 `git config core.hooksPath .githooks`，启用推送前检查。检查覆盖本次推送的所有引用及其可达历史，拒绝部署记录目录、私有归档和运行凭据文件；CI 也执行同一检查。检查是对文件位置的保护，不能替代对源码内容的脱敏审查。

历史清理后，旧克隆应重新克隆，或仅将审查后的修改移植到新的主线。不要将旧分支合并回来或恢复公开推送旧备份。真实部署的截图、日志和记录继续保存在仓库之外。
