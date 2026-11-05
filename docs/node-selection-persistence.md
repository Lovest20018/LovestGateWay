# Debian 冷启动保留节点选择

`profile.store-selected` 已经开启。此前 `load_clash yes` 在 Mihomo 还原选择后又执行 `ppgw -fastnode`，因此服务重启可能换到新的测速节点。修复只改变 Debian 的冷启动路径；故障检查触发的重载继续走原自动选点流程。

冷启动前，只读检查停止状态下的 `cache.db` 中是否保存过 GLOBAL 选择。Mihomo 就绪后，用现有 `-now_node` 确认实际恢复到同一选择；相同则跳过本轮测速选点。显式选择 DIRECT 也会保留。空缓存、只有预检记录的缓存、已被移除的节点以及读取失败的缓存继续使用原选点流程。周期健康检查保持启用；保存的节点确实失效时仍可触发重载和自动选点。

`integration/clash_selection.py` 不修改缓存，也不创建第二份节点选择状态。它仅支持 bbolt v2，验证元数据校验和、页界限和树遍历，读取量限制为 128 MiB；未知格式按未确认选择处理。节点名只留在进程变量中，不进入日志或公开报告。以后升级 Mihomo 更换缓存格式时，需要重新验收这一路径。

实现依据：[Mihomo 缓存实现](https://github.com/MetaCubeX/mihomo/blob/Meta/component/profile/cachefile/cache.go) 和 [启动恢复逻辑](https://github.com/MetaCubeX/mihomo/blob/Meta/hub/executor/executor.go)。本项目启动脚本的覆盖行为是对本仓库调用顺序的检查结果。

复验命令：

```sh
python3 -m unittest discover -s tests -p test_clash_selection.py -v
python3 scripts/verify-selection-preservation.py --binary /path/to/mihomo --output /tmp/selection-evidence.json
```

第二条命令应在隔离测试容器中运行。只使用临时合成配置和 Unix 控制套接字，不读取生产订阅或开启代理、DNS 监听；覆盖未选择的缓存、显式 DIRECT、普通节点、SIGKILL、节点移除、大缓存、损坏元数据和截断文件。单元测试执行实际启动函数，覆盖 Debian 保留、未确认选择回退、旧系统行为和故障重载选点。
