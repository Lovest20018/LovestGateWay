# 浏览器管理会话

## 目标与兼容边界

浏览器不再把管理密码的 SHA256 作为长期 Bearer 凭据持久保存。服务端验证一次登录证明后，签发随机、可撤销、有期限的会话。既有 `PaoPaoGateWay` 存储结构及非认证偏好保留，旧认证字段清空；升级后重新登录。旧 HTML 入口跳转到当前界面。

长期管理 Bearer 仅用于本机回环的内部服务，且有 Origin 或 Sec-Fetch-Site 的浏览器请求不能使用它。远端命令行管理客户端必须迁移到会话登录；只读观察 API 的独立 Token 不受影响。Rust 网关验证会话后，向回环 Python worker 转发内部凭据，不向浏览器泄漏该凭据。不得为迁移保留远端永久 Bearer 旁路。

## 协议与存储

- `POST /sniffbox/auth/login`：同源 JSON `{proof: sha256(password)}`，成功后设置随机 256 位会话 Cookie。浏览器只在登录请求期间构造证明，不持久保存或记录；服务端沿用现有密码校验值。
- `GET /sniffbox/auth/session`：返回认证状态、剩余绝对期限及非秘密会话上下文标识；不返回 Cookie 或内部凭据。前端刷新后据此恢复登录。
- `POST /sniffbox/auth/logout`：服务端撤销当前会话并清 Cookie；失败时保留退出错误，不假称已完成服务端注销。
- Cookie 使用 `HttpOnly; SameSite=Strict; Path=/`，HTTPS 来源额外使用 `Secure`。不设置 Domain，不接受 URL 参数形式的会话，不把会话写入 localStorage/sessionStorage。Cookie 固定绝对期限，不因轮询无限延长。
- LAN 明文 HTTP 继续兼容，因此 Cookie 在该模式下不能提供链路保密。SHA256 登录证明也仍可重放到登录端点；完整防窃听需要部署 HTTPS，不能声称会话改造解决了明文传输问题。TLS 反向代理应与网关位于同机或受保护网络，保留原 Host、Origin 和 Sec-Fetch-Site，并剥离外来 Authorization 后再转发管理请求。反向代理后的 TCP 来源可能变成本机回环，不能把它当作外部客户端的真实来源；未按上述配置的代理不属于内部 Bearer 例外的安全边界。

### HTTP 契约

| 请求 | 成功响应 | 拒绝与失败 |
| --- | --- | --- |
| `POST /sniffbox/auth/login`，同源 JSON，仅含 `proof` 字段 | 200，`{"authenticated":true}`，Set-Cookie | 401 登录证明错误；403 来源/方法不允许；429 超限并返回 `Retry-After: 60`；503 容量或随机数来源不可用 |
| `GET /sniffbox/auth/session` | 200，未认证为 `{"authenticated":false}`；已认证额外含 `context` 字符串和 `expires_in` 秒数 | 403 来源/方法不允许；过期 Cookie 按未认证返回 |
| `POST /sniffbox/auth/logout`，同源 JSON `{}` | 200，`{"authenticated":false}`，Set-Cookie 清除；无会话也可重复调用 | 403 来源/方法不允许；网络失败时客户端显示退出未确认 |
| 现有受保护管理路径 | 通过 Cookie 认证后沿用原响应 | 会话缺失、过期、已撤销或远端永久管理 Bearer 返回 401；写请求缺少匹配的 Origin 也拒绝 |

认证端点响应均使用 `Cache-Control: no-store`。请求体上限 1024 字节、读取超时 10 秒，超出或格式传输不完整返回 400 并断开连接。会话 ID 只在 Set-Cookie/Cookie 传递，`context` 是公开的缓存隔离标识，不能用于认证。远端脚本应保留登录的 Cookie，并为登录、注销和管理写请求发送与目标 Host 匹配的 Origin。`docs/openapi-v1.json` 仅描述独立观察 API，继续使用 read-token，不与浏览器会话混用。

## 生命周期与限制

- 12 小时绝对期限、30 分钟无认证请求期限；后台轮询可能延长无请求期限，但无法突破绝对期限。使用单调时钟。
- 修改管理密码、进程重启、注销会使相关会话失效。会话仅保存在进程内，服务端保存随机 ID 的 SHA256。
- 最多 128 个会话；到达容量时拒绝新登录，不驱逐正在工作的会话。
- 按来源 IP 限制登录尝试，每分钟最多 10 次，并使用全局每分钟 100 次上限；限速状态最多保留 1024 个来源。返回 429 和 Retry-After，不记录密码或证明。
- Cookie 请求拒绝跨站 Fetch；所有写操作及登录/注销要求 Origin 与 Host 匹配。重复 Cookie、重复 Origin/Host 及不合法标识拒绝。
- 长连接在注销、密码变化或到期后关闭。已经进入事务的管理操作不因会话失效强行回滚；重新登录后核对原任务 ID，不自动重放写请求。

## 验收

实际 HTTP 测试覆盖登录限速、随机性、期限、注销和并发撤销、密码变化、重启失效、远端永久 Bearer 拒绝、本机内部认证、同源检查及长连接撤销。浏览器覆盖刷新恢复、旧凭据清理与偏好保留、过期后草稿保留、重新登录、不重放写请求；API 契约说明会话与内部凭据边界。

## 写路由与内部回环通道

`/sniffbox/traffic/clear`、`/sniffbox/nodes/clear`、`/sniffbox/reload`、`/sniffbox/reload/core`、`/sniffbox/clash/up` 仅接受 POST。合法会话的 GET/HEAD 返回 405，不执行操作；Cookie POST 必须有匹配 Host（含端口）的 Origin，不能依赖 SameSite 区分同主机不同端口。内部 clash/up 继续仅允许无浏览器来源头的回环请求。

Mihomo 的节点/组 delay 与 provider healthcheck 虽然上游使用 GET，也会主动发起探测。浏览器须通过 POST 调用这些精确白名单路径，网关认证后才转换为上游 GET；Cookie GET 不执行探测。回环内部 Bearer 保留原生 GET 契约。PUT/PATCH/DELETE 等原有写方法仍执行同源校验。

公开 Web 监听为指定非回环 IPv4/IPv6 地址时，核心额外绑定同地址族、同端口的 127.0.0.1/::1，内部 Python 服务及 clash-up 使用该回环地址。通配监听直接使用其覆盖的同族回环地址；已有回环别名（例如 127.0.0.2）保持原地址。两个监听共用认证、ACL、后台任务和关闭信号，附加绑定失败会使整个 Web 服务启动失败。安装前检查也包含附加回环端口。此变更没有允许非回环永久 Bearer。
