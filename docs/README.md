# 文档导航

适用于 LovestGateWay Debian 正式版。首次使用从安装指南开始；日常设置通过 Web 完成。

版本 [v0.2.7](releases/0.2.7.md) · [更新记录](../CHANGELOG.md) · [正式安装包](https://github.com/Lovest20018/LovestGateWay/releases/latest)

| 需求 | 文档 |
| --- | --- |
| 安装、升级、离线包与常见问题 | [安装与升级](debian-deployment.md) |
| 面板导航、订阅、策略及设置 | [Web 使用说明](debian-web-settings.md) |
| 代理账号、故障转移和运行历史 | [首页常用设置](../README.md#常用设置) · [Web 详细说明](debian-web-settings.md) |
| 命令行管理、备份和恢复 | [命令行参考](debian-management.md) |
| 已完成内容、待验收场景 | [项目状态](optimization-roadmap.md) |

## 开发与维护

- [参与开发与目录说明](../CONTRIBUTING.md) · [测试与验收](testing.md)
- [前端源码](../frontend/README.md) · [截图复现](images/README.md)
- [发布流程](releasing.md) · [版本变化](../CHANGELOG.md) · [GitHub Releases](https://github.com/Lovest20018/LovestGateWay/releases)
- [上游跟进](upstream-maintenance.md) · [与上游的差异](project-differences-and-web-settings.md)
- [可选观测 API](integration-api-v1.md) · [OpenAPI](openapi-v1.json)

## 专项参考

按遇到的问题查阅，无需逐份阅读。

- [Debian 运行机制](reference/debian-runtime.md)
- [Mihomo 热加载边界](mihomo-reload-assessment.md) · [新连接窗口](mihomo-selection-window.md)
- [节点选择保留](node-selection-persistence.md) · [浏览器管理会话](browser-sessions.md)
- [UI 优化与逐页布局](ui-optimization-plan.md)

<details>
<summary>历史设计与上游资料</summary>

- [UI 优化原始设计](reference/ui-optimization-history.md)：保留当时的判断、阶段状态与测量，不作为当前待办。
- [上游 ISO 说明](upstream-iso-reference.md)：仅供来源追踪，不适用于本项目 Debian 安装步骤。

</details>
