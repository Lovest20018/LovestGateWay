# Debian 冷启动保留节点选择

`profile.store-selected` 已经开启。此前 `load_clash yes` 在 Mihomo 还原选择后又执行 `ppgw -fastnode`，因此服务重启可能换到新的测速节点。修复只改变 Debian 的冷启动路径；故障检查触发的重载继续走原自动选点流程。

冷启动前，只读检查停止状态下的 `cache.db` 中是否保存过 GLOBAL 选择。API 能返回不代表 Mihomo 已应用缓存：启动脚本通过私有 Unix controller 最多等待 15 秒，实际 GLOBAL 选择恢复后才确认保留。显式选择 DIRECT 也会保留。只有已发布的 global 模式、GLOBAL 成员以及无动态 provider 的候选配置共同证明原节点被移除时，才重新选点；动态 provider 尚未加载、缺失 GLOBAL、暂时默认值、超时或读失败不触发本轮测速覆盖，并记录“未确认”提示。等待过程只读，不设置模式或节点，不覆盖同时进行的管理员操作。空缓存、只有预检记录的缓存以及冷启动前无法读取的缓存继续使用原选点流程。周期健康检查保持启用；保存的节点确实失效时仍可触发重载和自动选点。

`integration/clash_selection.py` 不修改缓存，也不创建第二份节点选择状态。它仅支持 bbolt v2，验证元数据校验和、页界限和树遍历，读取量限制为 128 MiB；未知格式按未确认选择处理。节点名只留在进程变量中，不进入日志或公开报告。以后升级 Mihomo 更换缓存格式时，需要重新验收这一路径。

实现依据：[Mihomo 缓存实现](https://github.com/MetaCubeX/mihomo/blob/Meta/component/profile/cachefile/cache.go) 和 [启动恢复逻辑](https://github.com/MetaCubeX/mihomo/blob/Meta/hub/executor/executor.go)。本项目启动脚本的覆盖行为是对本仓库调用顺序的检查结果。

复验命令：

```sh
python3 -m unittest discover -s tests -p test_clash_selection.py -v
python3 scripts/verify-selection-preservation.py --binary /path/to/mihomo --output /tmp/selection-evidence.json
python3 scripts/verify-selection-startup.py --mihomo /path/to/mihomo --sniffbox /path/to/sniffbox --output /tmp/startup-evidence.json
```

第二条命令应在隔离测试容器中运行。只使用临时合成配置和 Unix 控制套接字，不读取生产订阅或开启代理、DNS 监听；覆盖未选择的缓存、显式 DIRECT、普通节点、SIGKILL、节点移除、大缓存、损坏元数据和截断文件。单元测试执行实际启动函数，覆盖延迟恢复、中文/emoji、GLOBAL 尚未发布、移除、超时/读失败不覆盖、空缓存回退、旧系统行为和故障重载选点。

第三条在 `--network none` 的隔离容器内执行真实 `load_clash` 函数与程序二进制，通过短暂锁住合成缓存扩大 API/profile 初始化窗口；覆盖延迟恢复 REJECT、DIRECT、中文/emoji，以及原节点确实移除后的原选点路径。成功恢复的检查同时断言未调用测速选点，不能仅以最终选择恰好相同代替。
