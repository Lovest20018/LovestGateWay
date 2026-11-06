# 上游跟进与同步台账

LovestGateWay 保持为 [kkkgo/PaoPaoGateWay](https://github.com/kkkgo/PaoPaoGateWay) 的派生项目，保留来源、版权及 GPL-3.0 许可。我们发布独立版本，不承诺与上游每次发布同步上线。

## 当前关系

机器可读记录见 [.github/upstream.json](../.github/upstream.json)：

| 字段 | 含义 | 当前记录 |
| --- | --- | --- |
| `merged_base` | 确实进入本项目 Git 祖先链的上游基线 | `4a5757e93a46669a54c9e5864d7b4a8d5484da76` |
| `reviewed_head` | 已完成逐项判断的上游提交，不表示全部采用 | `f45e3d9f523c3a09c2093cc95e31b5f42c8458dc` |
| `reviewed_release` | 已审查的上游稳定发布 | `20261030-f45e3d9` |

`f45e3d9` 的规则格式自动识别后端已移植；本项目继续维护自己的面板源码，不采用该提交的面板 bundle 和离线编辑器；`Cargo.lock` 调整暂缓，需单独检查依赖兼容和安全影响。详情保存在台账 `decisions`，暂缓事项不会因为上游检查显示“无新版本”而消失。

## 自动发现

[Review upstream updates](../.github/workflows/upstream.yml) 每周一 02:23 UTC（北京时间 10:23）检查上游 `main` 和最新稳定 Release，也可在 GitHub Actions 手动运行。GitHub 的定时任务可能延迟；仓库长期不活跃时也可能被平台停用，届时手动启用工作流。

工作流只 fetch Git 对象并读取 Release 元数据，不执行上游代码。每次生成 Markdown/JSON artifact 和运行摘要。如果提交或稳定 Release 标签变化，会在保留分支 `codex/upstream-review` 上创建或更新一个草稿 PR，其中只含 `docs/upstream-pending.md`。没有变化则不创建 PR。发现历史重写时明确标记，不能静默重置基线。

机器人需要 `contents: write` 和 `pull-requests: write`，仓库 Actions 设置须允许 GitHub Actions 创建 PR。它不推送 `main`、不自动合并、不修改同步台账、不触发生产部署。不要把人工功能提交放到这个保留分支。机器人创建 PR 后的 CI 是否需要人工批准由 GitHub 的仓库策略决定；查看并批准待运行检查，不能把未运行当作通过。

## 每次更新的处理

1. 阅读报告及上游原始 diff，逐项判断规则/核心、依赖、前端和发行脚本的影响。为安全修复安排优先处理。
2. 从本项目 `main` 新建 `codex/` 功能分支。适合直接应用的独立提交可 cherry-pick；与 Debian 运行、管理事务或自有 UI 冲突的改动按功能移植。不要在 GitHub 点击“Sync fork”后直接覆盖本地主线。
3. 在台账中记录原始上游 SHA、已移植范围、不采用理由和暂缓事项。完成审查才推进 `reviewed_head` / `reviewed_release`；仅在上游提交确实成为祖先时推进 `merged_base`。解决旧暂缓项也要更新记录。
4. 运行相关回归和 Debian CI；涉及运行行为的改动按 [测试说明](testing.md) 做隔离、服务和实际网络验收。
5. 合并代码和台账后，关闭报告 PR 并删除保留分支，使下一次从最新主线生成报告。报告本身无需合入产品主线。
6. 按 [发布流程](releasing.md) 发布独立 LovestGateWay 版本。服务器升级是另一个明确操作，不由上游检查或 Release 自动执行。

本地只读检查示例（需已获取相应 Git 对象）：

```sh
git fetch --no-tags upstream main
python3 scripts/check-upstream.py --release-tag 20261030-f45e3d9 \
  --output dist/upstream-report.md --json-output dist/upstream-status.json
```

`initial_release_commit` 仅用于补录早于台账的首次正式发布 `6614810`。后续发布必须读取其构建提交内的台账，不能借用发布时主线的新基线。
