# 正式发布流程

[GitHub Releases](https://github.com/Lovest20018/LovestGateWay/releases) 发布 LovestGateWay 自己的 Debian amd64 版本。上游版本仅作为同步记录，不能代替本项目版本和测试证据。源码及产物沿用 GPL-3.0，保留上游版权。

## 维护者操作

1. 在 `main` 合入通过 review 的源码、上游台账和 `docs/releases/VERSION.md` 发布说明。说明功能变化、验证范围和已知限制，不填写真实部署地址、登录信息、订阅或私人验收记录。
2. 在 Actions 的 **Publish verified Debian release** 中选择 `main`，填写版本（不含 `v`）及已经进入主线的完整 40 位提交号。版本格式如 `0.2.0-management.20261106.3`，Git 标签使用 `v` 前缀。构建提交可以早于发布说明提交。
3. 默认 `publish=false`：完整运行 Debian CI，再创建草稿 Release。确认运行结果、附件、清单和发布说明后，在 GitHub 发布草稿；已完成验收时也可显式选 `publish=true`，检查全部通过后直接公开。
4. 安装包、对应源码包、两份 SHA256 文件及 `release-manifest.json` 必须齐全。清单记录版本、提交、架构、上游已审查基线、源码内容摘要和附件哈希。上传后验证服务器返回的大小与 SHA256，之后才公开。
5. 发布不升级任何服务器。部署前按 [Debian 部署](debian-deployment.md) 核对包摘要、备份与适用架构，部署后验收配置、服务和流量。

工作流复用 [Debian CI](../.github/workflows/debian.yml)，执行公开源码路径检查、Python/前端/Rust 测试、API 契约检查、Linux 构建和打包。正式版本编入核心与包元数据；普通 CI 使用 `dev-提交号`。目前正式包仅支持 amd64，不发布 ISO，也不推送 Docker Hub。

## 一致性检查和失败处理

[release-assets.py](../scripts/release-assets.py) 检查外层摘要、安装包内部 manifest、架构、版本、提交号及源码完整文件集；源码文件必须来自指定 Git tree，且重新计算的源码摘要与安装包一致。源码附件带完整构建脚本，不能只附修改文件或二进制。

现有 Release 不自动替换附件；同名标签指向其他提交时立即失败。上传中断时 Release 留在草稿状态，不能直接公开不完整草稿。维护者核查后可删除失败草稿再重跑；正确指向同一提交的标签可以保留。已经公开的版本有问题时发布新版本，不覆盖原始包。

`GITHUB_TOKEN` 的写权限仅用于发布 job 的标签、Release 和附件。不要把私有部署目录作为上传路径；正式附件采用固定文件名清单。当前工作流没有签名/构建证明，也不宣称二进制可逐字节复现；校验和用于核对产物完整性。

## 首次正式发布的补录

`0.2.0-management.20261106.3` 使用提交 `661481054bc0c9fe1107ba0200288d788a75b4ab` 已完成两台原生 Debian 升级验收的原安装包，不重新构建。该提交早于发布工作流和台账，首次通过同一校验脚本及 GitHub API 发布。

原源码包来自 Windows checkout，其中未受 `.gitattributes` 约束的文本保留 CRLF；与 Git tree 逐文件核对时仅允许这一换行差异。原始源码包摘要必须仍与安装包的 `source_sha256` 一致，清单用 `source_export=git-tree-checkout-crlf` 标记。这个兼容例外仅限台账指定的首次提交。后续 CI 使用 `git archive` 导出指定提交，逐字节核对源码，记录 `source_export=git-archive`。
