# Mihomo 局部加载评估

2026-11-05 的合成实验没有证明通用配置 reload 可以安全替代重启，因此产品仍保留原有重启路径。无变化、安全 INI 字段和辅助服务修改继续使用已有分类；本次没有增加配置 reload 的产品入口。

实验使用公开离线 Mihomo Meta v1.19.33（linux amd64 with gvisor），二进制 SHA256：`817f50b3ac6a8b1c4360c5cf2961906057f3a883d970ebbcb0028a01632b620f`。在无生产挂载、无宿主端口映射、非 privileged 的独立容器中，所有目标均为回环地址的合成 HTTP provider、带认证 SOCKS 上游和 TCP echo。未向生产服务注入失败。

两个 runner 均实际运行至结束，退出码 0。基础 runner 的退出码代表全部断言通过；provider runner 的退出码代表观察流程完成，其中连接中断和旧凭据继续使用是记录到的反例，并非安全验收通过。

| 实验 | 观察结果 | 含义 |
| --- | --- | --- |
| DIRECT selector 的候选配置先 `-t`，再 `PUT /configs?force=false` | 204；同一进程、controller connection ID 和 TCP echo 保留；显式选择 DIRECT 保留 | 这个受限配置可以保留连接 |
| 新增 DOMAIN REJECT 规则 | 匹配的新连接断流，其他新连接可以 echo | 新规则实际作用于新连接 |
| 非法 proxy 类型候选 | controller 返回错误；旧连接、选择、规则和新连接仍可用 | 这个失败候选未破坏原运行状态 |
| HTTP provider 节点选中后重新加载相同配置 | 204；进程保留，但旧 TCP echo 失败、原 connection ID 消失；新连接可用 | 进程不重启不能证明连接不中断 |
| 只变更合成上游的密码，不 reload | 已认证旧 TCP echo 和 connection ID 保留 | 排除密码变化本身必然关闭旧 TCP 的解释 |
| provider 响应改为新密码后，增加组并加载候选 | `-t` 通过、controller 204；选择仍为原节点；缓存和实际新认证仍用旧密码，新 TCP 失败 | config 204 不证明 provider 内容已更新 |
| 显式刷新 provider | 204；缓存和新连接实际使用新密码，新 TCP 恢复；此前中断的旧连接未恢复 | provider 更新另有执行和验收边界 |
| provider 返回非法节点或 HTTP 503 | 显式刷新返回 503；随后 config reload 仍返回 204，并使用已有缓存建立新连接 | controller 接受配置不等于候选 provider 已加载成功 |

没有验证 TUN、DNS、UDP、真实代理协议、监听/认证变更、系统路由、防火墙以及网关生成完整候选配置的等价性。provider 旧连接在本实验中断流的内部原因未进一步归因；该现象已足以阻止“所有 YAML 都安全 reload”的结论。

## 复现

仅在独立网络命名空间或无生产挂载的隔离容器内运行；脚本使用固定回环端口，基础实验为 19080/19190，provider 实验为 19081/19191。需要 Python 3 标准库和可信的 Mihomo 可执行文件，不需要外部 provider 或公网访问。`--work-dir` 必须不存在，避免上一轮 provider 缓存引用已经失效的临时端口。

```sh
python3 scripts/evaluate-mihomo-reload.py \
  --binary /experiment/mihomo --work-dir /experiment/basic-new-run
python3 scripts/evaluate-mihomo-provider-reload.py \
  --binary /experiment/mihomo --work-dir /experiment/provider-new-run
```

每个目录生成 `report.json` 与 `mihomo.log`。本次最终证据保存在发布证据目录 `reload-experiment/` 下的 `basic-final-report.json`、`basic-final-mihomo.log`、`basic-final.log`、`basic-final.exit`，以及同名前缀 `provider-final` 的四个文件。报告记录具体二进制版本与摘要，provider 中的密码 `one`/`two` 和 controller token 全是本地合成值。

早期失败轮也保留：基础实验最初将候选放在 Mihomo 工作目录外，修正后又将 REJECT 错误地限定为 SOCKS 握手失败，最终改验实际数据流；provider 第一轮复用了过期动态端口缓存，第二轮观察结束时未处理 controller 的空连接列表。最终 runner 使用独立空目录并处理空列表，以上最终证据均来自修正后的脚本实跑。
