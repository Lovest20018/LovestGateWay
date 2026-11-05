/* Native gateway settings and Clash policy UI. Credentials never enter job metadata. */
(() => {
  'use strict';
  let current = null;
  function mount(root, options = {}) {
    root.replaceChildren(document.getElementById('ppgw-gateway-view').content.cloneNode(true));
    const $ = id => root.querySelector('#' + id);
    const lifetime = new AbortController();
    const timers = new Set();
    function later(callback, ms) { const id = setTimeout(() => { timers.delete(id); if (!stopped) callback(); }, ms); timers.add(id); }
    function pause(ms) { return new Promise(resolve => { const finish = () => { clearTimeout(id); lifetime.signal.removeEventListener('abort', finish); resolve(); }; const id = setTimeout(finish, ms); lifetime.signal.addEventListener('abort', finish, {once: true}); }); }
    const dirty = new Map();
    const openHelp = new Set();
    let model, category = 'access', subscriptionPage = 'editor', subscriptionControls = null, sourceDraft = null, sourceDetails = null;
    let passwordConfirmation = '';
    let prepared, running = false, editorBusy = false, postUpload = null, stopped = false, modelRequest = 0, corePolling = null;
    const bridge = {setEditorBusy: busy => { if(editorBusy!==busy){editorBusy=busy;updateDirty();} },
      syncEditor: data => {
        if (!data.settings_revision) throw Error('配置已变化，请重新读取后再保存。');
        return load(data.settings_revision, data.source_details);
      },
      discardEditorOptions: () => { for(const key of ['ini.subtime','ini.subcron'])dirty.delete(key);renderSubscriptionGlobals();updateDirty(); },
      editorOptionsChanged: () => ['ini.subtime','ini.subcron'].some(key=>dirty.has(key)),
      hasChanges: () => dirty.size > 0, busy: () => running, reload: () => load(),
      editorPatch: revision => {
        if (!model || model.revision !== revision) throw Error('配置已变化，请重新读取后再保存。');
        if (sourceDraft?.changed) throw Error('请先应用或取消来源切换，再保存当前编辑。');
        const body = patchBody();
        if (Object.keys(body.patch).some(ns => ns !== 'ini') || Object.keys(body.patch.ini || {}).some(key => !['subtime','subcron'].includes(key)))
          throw Error('请先在网关设置中应用其他修改，再保存订阅与策略。');
        return body.patch;
      },
      previewEditor: (data, submit) => {
        prepared = {submit};
        $('preview-content').replaceChildren(...previewDescription(data, 'config'), el('p', {class:'subtext', text:
          data.save_target === 'local_policy' ? '全局选项、提供商、节点组、规则与更新计划将统一保存。本地覆盖策略不会改写源 YAML。' : 'PPsub 配置与更新计划将统一保存。加载失败时整批恢复。'}));
        openPreview();
      },
      editorApplied: async () => { dirty.clear(); sourceDraft = null; await refreshRuntime(); await load(); },
      attachOptions: host => {
        subscriptionControls = host; renderSubscriptionGlobals(); updateDirty();
        return () => { if (subscriptionControls === host) subscriptionControls = null; };
      }};
    current = bridge;
    const labels = {
      mode: ['网关出站模式', '订阅与本地配置使用 Mihomo；直连、SOCKS5 和 OpenVPN 是独立出站模式。'],
      fake_cidr: ['FakeIP 网段', '网段须与现有 LAN、VPN 和其他 DNS 服务避开。'],
      dns_ip: ['DNS 上游地址', '信任 DNS：网关本地解析使用的基础上游。'], dns_port: ['DNS 上游端口', '通常为 53；与上游 DNS 实际端口一致。'],
      clash_web_password: ['管理密码', '留空保留；至少 8 个字符。应用后需要重新登录。'],
      openport: ['允许局域网显式代理', '启用后允许范围内的设备连接 HTTP / SOCKS5 代理，必须同时设置认证。'],
      openport_auth: ['代理认证', '格式为 用户名:密码；留空保留已保存的认证。'],
      admin_cidr: ['管理访问范围', '逗号分隔 CIDR；留空允许所有来源。请保留当前设备所在网段。'],
      proxy_cidr: ['代理访问范围', '逗号分隔 CIDR；留空允许所有来源。'],
      net_rec: ['核心流量统计', '记录核心流量与分布；与 API 历史采集分别控制。'],
      max_rec: ['流量记录上限', '核心流量统计开启后可以热更新。'],
      net_cleanday: ['每月清理日', '1–31；留空关闭定期清理。'],
      traffic_window_min: ['流量统计窗口（分钟）', '统计近期流量的时间范围；留空使用默认值。'],
      udp_enable: ['UDP 转发', 'UDP 开关和会话额度在重启后生效。'],
      udp_max_sessions: ['UDP 会话上限', '留空按设备资源自动选择。'],
      udp_reserved_sessions: ['UDP 保留额度', '必须小于会话上限。'],
      PPGW_INTERFACE: ['服务网卡', '填写 Debian 网卡名，如 ens33；不修改宿主 IP 或默认路由。'],
      PPGW_IPV6: ['网关 IPv6', '需要宿主网络已具备可用 IPv6。'],
      PPGW_WEB_LISTEN: ['管理页面监听', '格式为 IP:端口；修改后需打开新地址。'],
      PPGW_DNS_LISTEN: ['FakeIP DNS 监听', '格式为 IP:端口；独立 DNS 已占用端口时请指定网关 LAN 地址。'],
      TZ: ['调度时区', '如 Asia/Shanghai；同步用于订阅与维护调度。'],
      enabled: ['启用对外观测 API', '默认关闭，不监听端口。启用后允许持有专用 Token 的客户端读取状态和执行受限诊断。'],
      history_enabled: ['API 历史采集', '仅在对外观测 API 开启时采集；关闭后已有历史保留。'],
      history_retention_days: ['历史保留天数', '1–90 天；由历史采集服务执行。'],
      auto_update: ['Mihomo 自动更新', '启用官方稳定版检查与维护定时器。'],
      core_variant: ['Mihomo CPU 型号', '自动选择系统确认支持的 v3/v2/v1；检测不确定时使用 v1。先应用设置，再检查或安装核心；保存型号不会立即替换核心。'],
      name: ['API 实例名称', '观测客户端中显示的设备名称。'],
      listen: ['API 监听地址', '另一台设备连接时填写网关的局域网 IP；127.0.0.1 仅限本机。'],
      port: ['API 端口', ''], probe_urls: ['允许探测的地址', '每行一个公共 HTTP(S) URL，最多 16 个。'],
      read_token: ['更换 API Token', '留空保留当前 Token；应用后旧 Token 失效，请同步更新使用该 API 的客户端。'],
      subtime: ['订阅更新间隔', '如 30m、6h、1d。'],
      subcron: ['每天定时更新（小时）', '填写 0–23；留空则按间隔更新。'],
      yamlfile: ['本地 YAML 文件', '先在下方导入，再选择文件并预览。'],
      ovpnfile: ['OpenVPN 文件', '只允许内联证书和受限路由配置。'],
      ovpn_username: ['OpenVPN 用户名', ''], ovpn_password: ['OpenVPN 密码', '留空保留。'],
      socks5_ip: ['SOCKS5 上游 IP', 'IPv6 地址加方括号。'], socks5_port: ['SOCKS5 上游端口', ''],
      socks5_username: ['SOCKS5 用户名', ''], socks5_password: ['SOCKS5 密码', '留空保留。'],
      splice_threshold: ['快速转发阈值（字节）', '留空使用默认值；安全字段可热更新。'],
      fast_node: ['自动选点方式', '普通订阅可用 yes / check / no；原生 PPsub 使用节点组测速。'],
      test_node_url: ['节点测试地址', '用于节点连通性与延迟测试的 HTTP(S) 地址。'], ext_node: ['测速排除关键词', '普通订阅自动选点时排除匹配节点；多个关键词用 | 分隔。'],
      fall_direct: ['选点失败时直连', '启用后可能绕过代理，请按实际需求选择。'],
      dns_burn: ['节点 DNS 预解析', '提前解析机场节点域名，增加固定 IP 的可选入口。'], ex_dns: ['额外 DNS 服务器', '扩展 DNS：补充节点解析结果，逗号分隔 IP:端口。'],
      pplog: ['流量上报地址', '可选 IP:端口；留空停用。'], pplog_uuid: ['流量上报 UUID', '可选。'],
    };
    const helpTopics = {
      enabled: [
        ['默认状态', '全新安装和未明确启用 API 的旧配置均关闭此功能，不监听 API 端口。Web 管理、订阅和代理转发可以独立使用。'],
        ['启停方式', '预览并应用后启停观测 API 及其开机自启。关闭时也停止 API 历史采集，已保存的数据和连接设置保留。'],
      ],
      listen: [
        ['局域网连接', '客户端在另一台设备时，填写本机可被该设备访问的局域网 IPv4 地址；0.0.0.0 表示监听所有 IPv4 网卡。'],
        ['客户端配置', '连接地址使用 http://网关局域网IP:端口，认证使用下面的专用 API Token。监听 0.0.0.0 时，连接地址仍需填写实际 IP。'],
        ['生效方式', 'API 已开启时，预览并应用后仅重启观测 API；关闭时只保存连接设置。代理转发继续运行。'],
      ],
      read_token: [
        ['作用', '观测客户端使用这个独立 Token 读取网关状态和执行允许的诊断，不能凭它修改网关配置。'],
        ['更换方式', '填写 32–256 个无空格的 ASCII 字符，或点击“生成新 Token”。复制新值到客户端，预览并应用后生效。'],
      ],
      dns_ip: [
        ['作用', '网关需要本地解析节点等域名时使用的基础 DNS，也会参与节点 DNS 预解析。“信任”表示期望返回真实 IP，程序不会自动验证结果是否无污染。'],
        ['填写方式', '填写 IP 地址，端口在下一项设置；IPv6 地址加方括号。代理访问网站时，目标域名的解析位置由出站模式决定。'],
        ['与扩展 DNS 的关系', '高级选项中的“额外 DNS 服务器”用于补充节点的解析结果；开启节点 DNS 预解析后，两者一起收集可用 IP。'],
      ],
      fake_cidr: [
        ['作用', '为域名分配虚拟 IP，供网关识别连接并恢复对应域名。虚拟 IP 与机场节点的真实入口 IP 用途不同。'],
        ['何时调整', '现有 LAN、VPN 或其他 DNS 服务已使用同一网段时，需要选择互不重叠的网段，并同步检查路由。'],
      ],
      PPGW_DNS_LISTEN: [
        ['作用', '设置网关接收 FakeIP DNS 查询的本机地址和端口。客户端或前置 DNS 需要将相应查询发送到这里。'],
        ['填写示例', '0.0.0.0:53 表示监听所有 IPv4 网卡；指定 LAN 地址可限制监听网卡。修改前确认端口未被其他 DNS 服务占用。'],
      ],
      dns_burn: [
        ['作用', '用信任 DNS 和扩展 DNS 查询机场节点域名，保留原节点，并为可用 IP 添加节点副本，方便代理组测速和选择入口。'],
        ['示例', '同一个节点域名查到两个不同 IP，就可能多出两个固定 IP 版本；它们沿用原节点的协议和账号。'],
        ['适用范围', '原生 PPsub 直接支持，也会尝试订阅自带的 DNS；其额外入口可能带 @subdns 后缀。普通订阅和本地 YAML 需要同时启用自动选点（fast_node=yes）。'],
      ],
      ex_dns: [
        ['作用', '也称扩展 DNS。节点 DNS 预解析会向这些服务器补充查询，收集不同地区或运营商返回的节点入口；部分解析流程也会将其作为备用。'],
        ['填写示例', '223.5.5.5:53,1.0.0.1:53。使用英文逗号分隔，每项填写 IP:端口，IPv6 写为 [地址]:端口。'],
        ['何时调整', '节点使用分区域解析，或希望增加可测速的 IP 入口时调整。它主要影响机场节点的解析，客户端使用的 DNS 仍需单独配置。'],
      ],
      net_rec: [
        ['作用', '记录转发核心中的域名、客户端与流量，供面板的流量记录和分布页面查看。核心重启后，这部分内存记录会清空。'],
        ['与历史采集的关系', 'API 历史采集由独立服务保存历史数据，在“维护与 API”中单独控制；对外观测 API 关闭时不采集。'],
      ],
      history_enabled: [
        ['作用', '由独立采集服务保存 API 历史数据，保留期限由“历史保留天数”控制；需同时开启对外观测 API。'],
        ['关闭后', '停止后续采集，已有历史保留；面板的核心流量统计由另一个开关控制。'],
      ],
      fast_node: [
        ['可选值', 'yes：测速并自动选择节点；check：检查连通性，异常时更新配置但不主动测速选点；no：手动选择。'],
        ['适用范围', '用于普通订阅和本地 YAML 的传统选点流程。原生 PPsub 由各节点组的测速模式控制，此选项会被忽略。'],
        ['留空时', '普通配置根据是否包含规则自动判断。应用前请确认需要自动切换还是保留手动选点。'],
      ],
      fall_direct: [
        ['作用', '普通订阅自动选点时，全部节点测速失败且直连探测可用，允许回退到直连。'],
        ['开启前', '确认业务允许流量直接出网。原生 PPsub 忽略此选项，应在节点组和规则中明确配置 DIRECT。'],
      ],
      udp_enable: [
        ['作用', '允许网关转发 UDP 流量；实际连通还取决于节点协议、上游支持及防火墙。'],
        ['配合设置', '会话上限控制资源使用，保留额度必须小于上限。高级选项中的 QUIC 阻断会单独影响 QUIC 流量。'],
      ],
      openport: [
        ['作用', '让局域网设备显式连接网关的 HTTP / SOCKS5 代理。'],
        ['配合设置', '同时填写代理认证，并用“代理访问范围”限制允许连接的来源；“管理访问范围”单独控制管理接口。'],
      ],
      subtime: [
        ['填写示例', '30m 表示每 30 分钟，6h 表示每 6 小时，1d 表示每天。填写每天定时更新的小时后，按该定时计划执行。'],
        ['原生 PPsub', '这是全局默认计划；每个机场可选择跟随全局、仅手动或独立自动更新。各机场单独设置的计划优先。'],
      ],
      subcron: [
        ['作用', '按调度时区，在每天指定小时内更新；例如填写 5 表示每天 05 点这一小时内。留空使用更新间隔。'],
        ['原生 PPsub', '仅影响跟随全局计划的机场；仅手动和独立自动更新的机场继续按各自设置执行。'],
      ],
    };
    const modeNames = {free: '直连', socks5: 'SOCKS5', ovpn: 'OpenVPN', yaml: '本地 YAML', suburl: '订阅'};
    const errors = {
      LOGIN_REQUIRED: '登录已失效，请先返回管理面板重新登录。',
      CONFIG_REVISION_CONFLICT: '配置已被其他操作修改。请刷新配置后重新编辑。',
      STALE_POLICY_SNAPSHOT: '运行配置或订阅已变化，请重新载入策略后再编辑。',
      SOURCE_CONFLICT: '更换来源与现有本地策略或 PPsub 冲突。确认清除选项后重新预览。',
      MANAGEMENT_BUSY: '其他管理操作正在进行，请稍后重试。',
      RECOVERY_REQUIRED: '上一次事务需要恢复。请在宿主运行 sudo ppgw-admin recover --yes，再刷新页面。',
      MIHOMO_REQUIRED: '请先在“维护与 API”中安装 Mihomo。',
      SUBSCRIPTION_REQUIRED: '请填写订阅链接，或先导入本地配置。',
      ASSET_REQUIRED: '未找到所选配置文件，请先导入。',
      ASSET_EXISTS: '同名文件已存在，请选择新名称或明确允许覆盖。',
      INVALID_ASSET_CONTENT: '文件格式不正确，或包含不支持的指令。',
      EXPLICIT_PROXY_AUTH_REQUIRED: '开启显式代理时必须设置 用户名:密码。',
      DASHBOARD_PASSWORD_TOO_SHORT: '管理密码至少需要 8 个字符。',
      APPLY_FAILED_ROLLED_BACK: '应用验收失败，已恢复原配置。请检查网卡、监听端口和资源。',
      ROLLBACK_UNCONFIRMED: '原保存文件已还原，运行恢复尚未通过验证；请检查系统服务和恢复提示。',
      EXTERNAL_CONFIG_CONFLICT: '检测到外部配置变化，未覆盖它。请通过管理脚本检查并恢复。',
      INTERRUPTED_CHECK_RECOVERY: '管理任务曾中断。请用管理脚本检查事务状态，必要时执行 recover。',
      SYSTEM_OPERATION_FAILED: '系统或 Mihomo 校验未通过，请检查字段和节点/规则引用。',
      INVALID_UDP_BUDGET: 'UDP 保留额度必须小于会话上限。',
      POLICY_NOT_LOADED: '未能确认策略已加载。',
      OPERATION_FAILED: '操作未完成，请检查运行状态或使用管理脚本诊断。',
    };
    function message(code) { return errors[code] || (code?.startsWith('INVALID_') ? '字段格式或范围不正确，请检查后重试。' : '操作未完成，请检查配置后重试。'); }
    function el(tag, attributes = {}, children = []) {
      const node = document.createElement(tag);
      for (const [key, value] of Object.entries(attributes)) {
        if (key === 'text') node.textContent = value;
        else if (key === 'class') node.className = value.split(' ').map(name => ({button: 'btn', primary: 'btn-brand'})[name] || name).join(' ');
        else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
        else if (key in node) node[key] = value;
        else node.setAttribute(key, value);
      }
      node.append(...children.filter(Boolean)); return node;
    }
    function secret() {
      try { const saved = JSON.parse(localStorage.getItem('PaoPaoGateWay') || '{}'); return saved.clashAPIConfigs?.[0]?.secret || saved.secret || ''; }
      catch { return ''; }
    }
    async function api(path, body, namespace = 'settings') {
      if (stopped) throw new DOMException('View closed', 'AbortError');
      const token = secret();
      if (!token) throw Object.assign(Error(message('LOGIN_REQUIRED')), {status: 401});
      const response = await fetch('/sniffbox/' + namespace + '/' + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {Authorization: 'Bearer ' + token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
        body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(15000)]),
      });
      const data = await response.json().catch(() => ({}));
      if (stopped) throw new DOMException('View closed', 'AbortError');
      if (!response.ok) throw Object.assign(Error(response.status === 401 ? message('LOGIN_REQUIRED') : (namespace === 'subscription' && typeof data.message === 'string' ? data.message : message(data.error))), {status: response.status, code: data.error});
      return data;
    }
    function notice(text, kind = '') {
      if (stopped) return;
      const node = $('notice'); node.replaceChildren(el('span', {text})); node.className = 'notice ' + kind; node.hidden = !text;
      if (text === message('LOGIN_REQUIRED')) node.append(el('a', {href: '/ui/#/overview', text: ' 返回面板登录 ↗'}));
    }
    async function refreshRuntime() {
      if (stopped || !options.refreshRuntime) return;
      try { await options.refreshRuntime(() => !stopped); } catch { /* Keep a successful apply result if the follow-up read fails. */ }
    }
    function pending() { try { return JSON.parse(sessionStorage.getItem('lovestgateway-job') || 'null'); } catch { return null; } }
    function pendingCore() {
      try {
        const record = JSON.parse(sessionStorage.getItem('lovestgateway-core-job') || 'null');
        return /^mihomo_[0-9]+$/.test(record?.id || '') && ['check','update','rollback','install-local','scheduled'].includes(record.action) ? record : null;
      } catch { return null; }
    }
    function identifier() { return [...crypto.getRandomValues(new Uint8Array(16))].map(v => v.toString(16).padStart(2, '0')).join(''); }
    function valueOf(namespace, key) { return dirty.has(namespace + '.' + key) ? dirty.get(namespace + '.' + key) : model.settings[namespace][key]; }
    function setValue(namespace, key, value) {
      const id = namespace + '.' + key;
      if (JSON.stringify(value) === JSON.stringify(model.settings[namespace][key])) dirty.delete(id);
      else dirty.set(id, value);
      updateDirty();
    }
    function editorOption(key) { return key === 'ini.subtime' || key === 'ini.subcron'; }
    function otherSettingsChanged() { return [...dirty.keys()].some(key => !editorOption(key)); }
    function updateDirty() {
      if (stopped) return;
      const subscriptions = /^#\/(subscriptions|ppsub)(?:[?]|$)/.test(location.hash);
      const otherSettings = otherSettingsChanged();
      const editorDraft = [...dirty.keys()].some(editorOption) || sourceDraft?.changed || window.ppgwPpsub?.hasChanges();
      const draftLocked = subscriptions ? otherSettings : editorDraft;
      window.ppgwPpsub?.setSettingsBusy(running || otherSettings);
      const count = dirty.size;
      $('savebar').hidden = !model || subscriptions || draftLocked || (!count && !running);
      $('dirty-count').textContent = running ? '正在应用并验证，请稍候…' : `${count} 项设置待应用`;
      $('preview').disabled = running || draftLocked || !count;
      $('discard').disabled = running;
      $('reload').disabled = running;
      const conflict = $('draft-conflict');
      conflict.hidden = !model || !draftLocked || running;
      if (!conflict.hidden) conflict.replaceChildren(
        el('span', {text: subscriptions ? '网关设置有未保存修改，当前订阅与策略暂时只读。请先应用或放弃原草稿。' :
          '订阅与策略有未保存修改，当前网关设置暂时只读。请先保存、重新读取或取消来源切换。'}),
        el('a', {href: subscriptions ? '/ui/#/settings' : '/ui/#/subscriptions', text: subscriptions ? '继续编辑网关设置' : '继续编辑订阅与策略'}));
      root.querySelectorAll('#content input,#content select,#content textarea,#content button').forEach(node => {
        if (node.hasAttribute('data-setting-help')) return;
        if (running || editorBusy || (draftLocked && node.getAttribute('role') !== 'tab')) { if (!('wasDisabled' in node.dataset)) node.dataset.wasDisabled = node.disabled ? '1' : '0'; node.disabled = true; }
        else if ('wasDisabled' in node.dataset) { node.disabled = node.dataset.wasDisabled === '1'; delete node.dataset.wasDisabled; }
      });
    }
    function field(namespace, key, options = {}) {
      const spec = model.schema[namespace][key], meta = labels[key] || [key, ''];
      const id = 'setting-' + namespace + '-' + key, saved = valueOf(namespace, key);
      const wrap = el('div', {class: 'field' + (options.wide ? ' wide' : '')});
      let input;
      const label = el('label', {htmlFor: id, text: meta[0]});
      const help = el('small', {id: id + '-hint', text: meta[1]});
      const topic = helpTopics[key];
      const helpId = id + '-help', expanded = openHelp.has(id);
      const detail = topic ? el('div', {id: helpId, class: 'setting-help-panel', hidden: !expanded},
        topic.map(([title, text]) => el('p', {}, [el('strong', {text: title + '：'}), document.createTextNode(text)]))) : null;
      const title = el('div', {class: 'setting-field-heading'}, [label, topic ? el('button', {
        type: 'button', class: 'setting-help-trigger', text: '?', 'data-setting-help': '',
        'aria-label': meta[0] + '的说明', 'aria-controls': helpId, 'aria-expanded': String(expanded),
        onclick: event => {
          const show = detail.hidden;
          detail.hidden = !show;
          event.currentTarget.setAttribute('aria-expanded', String(show));
          if (show) openHelp.add(id); else openHelp.delete(id);
        },
      }) : null]);
      const describeInput = () => { if (meta[1]) input.setAttribute('aria-describedby', help.id); };
      if (spec.type === 'bool') {
        wrap.classList.add('toggle-field');
        input = el('input', {id, type: 'checkbox', checked: saved});
        describeInput();
        wrap.append(el('div', {}, [title, help, detail]), input);
        input.onchange = () => setValue(namespace, key, input.checked);
        return wrap;
      }
      if (spec.type === 'choice') {
        const names = key === 'core_variant' ? {auto:'自动（按 CPU 选择）',v1:'v1（基础兼容）',v2:'v2',v3:'v3'} : modeNames;
        input = el('select', {id}, spec.constraints[0].map(value => el('option', {value, text: names[value] || value, selected: saved === value})));
      } else if (spec.type === 'asset') {
        const names = [...new Set([saved, ...model.assets.filter(v => key === 'ovpnfile' ? v.endsWith('.ovpn') : /\.ya?ml$/.test(v))])];
        input = el('select', {id}, names.map(value => el('option', {value, text: value, selected: saved === value})));
      } else if (spec.type === 'probe_urls') input = el('textarea', {id, value: saved.join('\n'), rows: 3});
      else input = el('input', {id, type: spec.secret ? 'password' : ['int', 'optional_int'].includes(spec.type) ? 'number' : 'text',
        value: spec.secret ? '' : saved, autocomplete: spec.secret ? 'new-password' : 'off',
        placeholder: spec.secret ? saved ? '已保存，留空保留' : '尚未设置' : ''});
      if (['int', 'optional_int'].includes(spec.type)) {
        input.min = spec.constraints[0]; input.max = spec.constraints[1]; input.step = 1;
        input.required = spec.type === 'int';
      }
      if (spec.type === 'observer_token') { input.minLength = 32; input.maxLength = 256; input.value = dirty.get(namespace + '.' + key) || ''; }
      input.addEventListener('change', () => {
        let value = input.value;
        if (spec.secret && value === '') { dirty.delete(namespace + '.' + key); updateDirty(); return; }
        if (spec.type === 'probe_urls') value = value.split('\n').map(v => v.trim()).filter(Boolean);
        else if (['int', 'optional_int'].includes(spec.type) && value !== '') value = Number(value);
        setValue(namespace, key, value);
      });
      describeInput();
      wrap.append(title, input, help, ...(detail ? [detail] : []));
      if (spec.secret && saved && !['secret', 'observer_token'].includes(spec.type)) {
        wrap.append(el('button', {class: 'button small secondary', type: 'button', text: '清空已保存的值', onclick: () => { input.value = ''; setValue(namespace, key, ''); }}));
      }
      return wrap;
    }
    function card(title, description, number, children) {
      return el('section', {class: 'card glass'}, [el('div', {class: 'card-heading'}, [
        el('h2', {text: title}), el('p', {class: 'subtext', text: description})]), ...children]);
    }
    function fields(items) { return el('div', {class: 'field-grid'}, items.map(([ns, key, wide]) => field(ns, key, {wide}))); }
    function tabs(items, selected, select, label) {
      const choose = key => { select(key); navigationHost.querySelector('[aria-selected="true"]')?.focus({preventScroll:true}); };
      return el('div', {class:'tabs page-tabs', role:'tablist', 'aria-label':label}, items.map(([key, text], index) => el('button', {
        id:'gateway-tab-'+key, type:'button', role:'tab', class:key===selected?'active':'', 'aria-selected':String(key===selected),
        'aria-controls':key==='editor'?'subscription-editor-panel':'content', tabIndex:key===selected?0:-1, text,
        onclick:()=>choose(key), onkeydown:event=>{
          const next={ArrowRight:(index+1)%items.length,ArrowLeft:(index+items.length-1)%items.length,Home:0,End:items.length-1}[event.key];
          if(next!==undefined){event.preventDefault();choose(items[next][0]);}
        },
      })));
    }
    function passwordCard() {
      const input = el('input', {id: 'setting-password-confirm', type: 'password', autocomplete: 'new-password',
        value: passwordConfirmation, placeholder: '再次输入新密码'});
      input.oninput = () => { passwordConfirmation = input.value; };
      return card('修改管理密码', '填写新密码并再次确认，然后点击底部“预览变更”，在预览中“确认应用”。留空保留原密码；应用后请用新密码重新登录。', '01', [
        el('div', {class: 'field-grid'}, [field('ini', 'clash_web_password'), el('div', {class: 'field'}, [
          el('label', {htmlFor: input.id, text: '确认新管理密码'}), input,
        ])]),
      ]);
    }
    async function copyValue(value) {
      if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(value);
      const input = el('textarea', {value, readOnly:true, class:'clipboard-helper'});
      const focused = document.activeElement;
      root.append(input); input.select();
      try { if (!document.execCommand('copy')) throw Error('复制失败，请手动选择并复制。'); }
      finally { input.remove(); if (focused?.isConnected) focused.focus(); }
    }
    function observerApiCard() {
      const section = card('对外观测 API', '可选功能，默认关闭。启停和连接设置独立应用，Web 管理与代理转发继续运行。', '03', []);
      const connection = el('div', {class:'observer-api-address', 'aria-live':'polite'});
      function showAddress() {
        const host = valueOf('api','listen'), port = valueOf('api','port');
        connection.replaceChildren(el('strong', {text:valueOf('api','enabled') ? '启用后的 API 连接地址' : 'API 已关闭：不监听端口'}),
          el('code', {text:`http://${host === '0.0.0.0' ? '网关局域网IP' : host}:${port}`}),
          el('small', {text:host.startsWith('127.') ? '仅限本机。其他设备连接时，请改为网关的局域网 IP。' : host === '0.0.0.0' ? '监听全部 IPv4 网卡；客户端请填写网关的实际局域网 IP。' : '将此地址与专用 Token 一起填入观测客户端。'}));
      }
      const toggle = field('api','enabled',{wide:true});
      toggle.querySelector('input').addEventListener('change', showAddress);
      const grid = fields([['api','name'], ['api','listen'], ['api','port'], ['api','probe_urls'],
        ['maintenance','history_enabled'], ['maintenance','history_retention_days']]);
      for (const key of ['listen','port']) grid.querySelector('#setting-api-'+key).addEventListener('change', showAddress);
      const saved = el('input', {id:'observer-api-current-token',type:'password',readOnly:true,autocomplete:'off',placeholder:'点击查看后显示当前 Token',value:''});
      let version = 0;
      const status = el('small', {'aria-live':'polite',text:'当前 Token 默认隐藏，查看后 60 秒自动隐藏。'});
      const reveal = el('button', {type:'button',class:'btn',text:'查看当前 Token',onclick:async()=>{
        if (saved.value) { hide(); return; }
        reveal.disabled = true;
        try {
          const data = await api('observer-token',{});
          if (!section.isConnected) return;
          saved.value = data.token; saved.type = 'text'; reveal.textContent = '隐藏 Token'; copy.disabled = false;
          const stamp = ++version; later(()=>{if(version===stamp)hide();},60000);
          status.textContent = '当前已保存的 Token；60 秒后自动隐藏。';
        } catch(error) { if(section.isConnected) status.textContent = error.message; }
        finally { if(section.isConnected) {reveal.disabled=false; updateDirty();} }
      }});
      function hide() { version++; saved.value=''; saved.type='password'; reveal.textContent='查看当前 Token'; copy.disabled=true; status.textContent='当前 Token 已隐藏。'; }
      const copy = el('button', {type:'button',class:'btn',text:'复制当前 Token',disabled:true,onclick:async()=>{
        try { await copyValue(saved.value); status.textContent='当前 Token 已复制。'; } catch(error) { status.textContent=error.message; }
      }});
      const currentField = el('div',{class:'field wide'},[el('label',{htmlFor:saved.id,text:'当前 API Token'}),saved,el('div',{class:'button-row'},[reveal,copy]),status]);
      const replacement = field('api','read_token',{wide:true});
      const newInput = replacement.querySelector('input');
      const newStatus = el('small',{'aria-live':'polite',text:''});
      replacement.append(el('div',{class:'button-row'},[
        el('button',{type:'button',class:'btn',text:'生成新 Token',onclick:()=>{
          const token=[...crypto.getRandomValues(new Uint8Array(32))].map(v=>v.toString(16).padStart(2,'0')).join('');
          newInput.value=token;setValue('api','read_token',token);newStatus.textContent='已生成，预览并应用后生效。请先复制新 Token。';
        }}),
        el('button',{type:'button',class:'btn',text:'复制新 Token',onclick:async()=>{
          try { if(!newInput.value)throw Error('请先填写或生成新 Token。'); await copyValue(newInput.value);newStatus.textContent='新 Token 已复制，尚需预览并应用。'; }
          catch(error){newStatus.textContent=error.message;}
        }}),
      ]),newStatus);
      section.append(toggle,connection,grid,el('div',{class:'observer-api-token-grid'},[currentField,replacement]));
      showAddress(); return section;
    }
    function settingsContent() {
      const result = [];
      if (category === 'access') {
        result.push(passwordCard());
        result.push(card('管理与代理访问', '管理认证和访问范围统一在这里维护。', '02', [fields([
          ['ini', 'openport_auth'], ['ini', 'admin_cidr'], ['ini', 'proxy_cidr'], ['ini', 'openport'],
        ])]));
        result.push(card('流量记录', '核心统计与 API 持久历史分别控制。', '03', [fields([
          ['ini', 'net_rec'], ['ini', 'max_rec'], ['ini', 'net_cleanday'], ['ini', 'traffic_window_min'],
        ])]));
      } else if (category === 'maintenance') {
        result.push(card('维护计划', '按需开启 Mihomo 自动更新，调度时区同时用于订阅和维护。', '01', [fields([
          ['maintenance', 'auto_update'], ['service', 'TZ'],
        ])]));
        const status = el('p', {class: 'subtext', id: 'maintenance-state', text: (model.mihomo_installed ? 'Mihomo 已安装' : '尚未安装 Mihomo') + ' · 尚未检查官方版本，可点击下方“检查稳定版”；安装按钮也会先检查版本。'});
        const actions = el('div', {class: 'button-row'}, [['检查稳定版', 'check'], ['安装 / 更新 Mihomo', 'update'], ['回退上一版本', 'rollback']].map(([label, action]) => el('button', {
          class: 'button secondary', type: 'button', text: label, onclick: () => coreAction(action),
        })));
        result.push(card('Mihomo 核心', '自动维护只升级更高稳定版；同版本切换 CPU 型号请点击安装 / 更新。', '02', [
          ...(model.schema.maintenance.core_variant ? [fields([['maintenance','core_variant']])] : []),
          status, el('div', {class: 'divider'}), actions, localCoreForm()]));
        result.push(observerApiCard());
      } else if (category === 'network') {
        result.push(card('网卡与监听', 'Debian 继续管理宿主 IP、默认路由和时钟。', '01', [fields([
          ['service', 'PPGW_INTERFACE'], ['service', 'PPGW_IPV6'], ['service', 'PPGW_WEB_LISTEN'], ['service', 'PPGW_DNS_LISTEN'],
        ])]));
        result.push(card('DNS 与 UDP', '涉及监听和会话额度的变更需要重启网关。', '02', [fields([
          ['ini', 'dns_ip'], ['ini', 'dns_port'], ['ini', 'fake_cidr'], ['ini', 'udp_enable'], ['ini', 'udp_max_sessions'], ['ini', 'udp_reserved_sessions'],
        ])]));
        result.push(card('出站模式', 'Clash/Mihomo 订阅在“订阅与策略”中设置。', '03', [fields([
          ['ini', 'mode'], ['ini', 'socks5_ip'], ['ini', 'socks5_port'], ['ini', 'socks5_username'], ['ini', 'socks5_password'],
          ['ini', 'ovpnfile'], ['ini', 'ovpn_username'], ['ini', 'ovpn_password'],
        ])]));
        result.push(assetCard());
      } else {
        const box = Object.fromEntries(String(valueOf('ini', 'box')).split(',').filter(Boolean).map(v => v.split('=')));
        const defaults = {'route.block_bittorrent': true, 'route.block_quic': true, 'route.block_unknown': false};
        const toggles = el('div', {class: 'field-grid'});
        for (const [key, title] of [['route.block_bittorrent', '阻断 BitTorrent'], ['route.block_quic', '阻断 QUIC'], ['route.block_unknown', '阻断未知流量']]) {
          const id = 'box-' + key, input = el('input', {id, type: 'checkbox', checked: key in box ? box[key] === 'true' : defaults[key]});
          input.onchange = () => { box[key] = String(input.checked); setValue('ini', 'box', Object.entries({...Object.fromEntries(Object.entries(defaults).map(([k,v]) => [k,String(v)])), ...box}).map(([k,v]) => `${k}=${v}`).join(',')); };
          toggles.append(el('div', {class: 'field toggle-field'}, [el('div', {}, [el('label', {htmlFor: id, text: title}), el('small', {text: key === 'route.block_quic' ? '同时涉及 UDP 防火墙规则，当前按重启应用。' : '按重启应用并校验。'})]), input]));
        }
        result.push(card('流量控制', '仅开放当前 Debian 版支持的阻断选项。', '01', [toggles]));
        result.push(card('连接与订阅高级选项', '未修改的现有值会保留。', '02', [fields([
          ['ini', 'splice_threshold'], ['ini', 'fast_node'], ['ini', 'test_node_url'], ['ini', 'ext_node'],
          ['ini', 'fall_direct'], ['ini', 'dns_burn'], ['ini', 'ex_dns'], ['ini', 'pplog'], ['ini', 'pplog_uuid'],
        ])]));
      }
      return result;
    }
    function assetCard() {
      const kind = el('select', {id: 'asset-kind'}, [el('option', {value: 'yaml', text: 'Clash / Mihomo YAML'}), el('option', {value: 'ovpn', text: 'OpenVPN 配置'})]);
      const filename = el('input', {id: 'asset-name', value: 'custom.yaml', maxLength: 100});
      kind.onchange = () => { filename.value = kind.value === 'yaml' ? 'custom.yaml' : 'custom.ovpn'; };
      const file = el('input', {type: 'file', id: 'asset-file', accept: '.yaml,.yml,.ovpn'});
      const overwrite = el('input', {type: 'checkbox', id: 'asset-overwrite'});
      const upload = el('button', {class: 'button secondary', type: 'button', text: '导入文件', onclick: async () => {
        try {
          const selectingSource = dirty.size === 1 && dirty.get('ini.mode') === (kind.value === 'yaml' ? 'yaml' : 'ovpn');
          if ((dirty.size && !selectingSource)) throw Error('请先应用或放弃当前修改，再导入文件。');
          if (!file.files[0] || file.files[0].size > 1024 * 1024) throw Error('请选择不超过 1 MiB 的配置文件。');
          postUpload = {kind: kind.value, name: filename.value};
          await startJob('asset', {type: kind.value, name: filename.value, content: await file.files[0].text(), overwrite: overwrite.checked});
        } catch (error) { postUpload = null; notice(error.message, 'error'); }
      }});
      return card('导入本地配置', '导入只保存文件；完成后预览并确认切换模式。PPsub JSON 可在下方编辑器中导入。', '04', [
        el('div', {class: 'field-grid'}, [el('div', {class: 'field'}, [el('label', {htmlFor: kind.id, text: '配置类型'}), kind]),
          el('div', {class: 'field'}, [el('label', {htmlFor: filename.id, text: '保存名称'}), filename]),
          el('div', {class: 'field wide'}, [el('label', {htmlFor: file.id, text: '选择文件'}), file])]),
        el('label', {class: 'checkline'}, [overwrite, document.createTextNode('允许覆盖同名文件（覆盖前创建备份）')]),
        el('div', {class: 'button-row asset-form'}, [upload, el('button', {class: 'button secondary', text: '重启应用资源', onclick: () => preview(true)})]),
      ]);
    }
    function subscriptionGlobals() {
      if (model.settings.ini.mode !== 'suburl') return [];
      const manual = valueOf('ini', 'subtime') === 'manual';
      const toggle = el('input', {type:'checkbox', id:'subscription-manual', checked:manual});
      toggle.onchange = () => { setValue('ini','subtime',toggle.checked?'manual':'1d'); if(toggle.checked)setValue('ini','subcron',''); renderSubscriptionGlobals(); };
      const native = model.source.native_ppsub || model.settings.ini.suburl.startsWith('ppsub@');
      return [el('section', {class:'subscription-schedule'}, [
        native ? el('label',{class:'checkline'},[toggle,document.createTextNode('全局默认仅手动更新')]) : null,
        !manual ? fields([['ini','subtime'], ['ini','subcron']]) : null,
        el('small', {class:'subtext', text:native?'下方每个机场可选择跟随全局、仅手动或独立自动更新。重启使用已保存节点，更新失败保留旧节点；点击“更新此订阅”可单独获取。':manual?'重启使用已保存配置。':'与下方编辑内容一起保存。此计划用于主订阅，配置内提供商按各自设置更新。'})])];
    }
    function sourceCard() {
      const ini = {...model.settings.ini, suburl:sourceDetails?.url || model.source.url || ''};
      const native = ini.mode === 'suburl' && (ini.suburl.startsWith('ppsub@') || model.source.native_ppsub);
      const kind = ini.mode === 'yaml' ? 'yaml' : native ? 'ppsub' : ini.mode === 'suburl' ? 'remote' : 'other';
      const names = {yaml:'本地 YAML', ppsub:'原生 PPsub', remote:'远程完整配置', other:modeNames[ini.mode] || ini.mode};
      const locationText = kind === 'yaml' ? ini.yamlfile : kind === 'remote' ? ini.suburl : kind === 'ppsub' ?
        (model.source.native_ppsub ? '本机 PPsub 配置' : ini.suburl.slice(6)) : '请先切换到配置来源，再维护订阅与策略。';
      const description = kind === 'yaml' ? '在“策略编辑”中保存本地覆盖，源 YAML 文件保持原样。文件内提供商继续按各自计划更新。' :
        kind === 'remote' ? '“策略编辑”中的修改叠加为本地策略，主订阅继续按计划更新。提供商链接属于配置内部资源。' :
        kind === 'ppsub' ? '在“策略编辑”中维护 PPsub 配置，可添加、删除或重命名订阅提供商。' : '当前使用其他出站模式。';
      const section = card('当前配置来源', description, '', [el('div', {class:'source-summary'}, [
        el('strong', {class:'source-kind', text:names[kind]}), el('code', {class:'source-location', text:locationText})])]);
      section.classList.add('source-card');
      const explanation = sourceExplanation(sourceDetails);
      if (explanation) section.append(explanation);
      const details = el('section', {class:'source-switch'});
      details.append(el('h3', {text:sourceDraft?.changed ? '切换配置来源 · 尚未应用' : '切换配置来源'}));
      const draft = sourceDraft || {kind:kind === 'other' ? 'remote' : kind, url:kind === 'remote' ? ini.suburl : '', yaml:ini.yamlfile, changed:false, reset:false, ppsub:null};
      const type = el('select', {id:'source-type'}, [['remote','远程完整配置'],['yaml','本地 YAML'],['ppsub','原生 PPsub']].map(([value,text]) => el('option',{value,text,selected:draft.kind===value})));
      type.onchange = () => { sourceDraft = {...draft, kind:type.value, changed:true}; render(); };
      const form = el('div',{class:'source-form'},[el('div',{class:'field'},[el('label',{htmlFor:type.id,text:'目标来源'}),type])]);
      if (draft.kind === 'remote') {
        const url = el('input',{id:'source-url',type:'url',required:true,value:draft.url,placeholder:'https://example.com/config.yaml'});
        url.oninput = () => { draft.url=url.value; draft.changed=true; sourceDraft=draft; };
        form.append(el('div',{class:'field wide'},[el('label',{htmlFor:url.id,text:'主配置订阅地址'}),url,
          el('small',{text:'下载整份 Clash / Mihomo 配置的地址；配置内的提供商链接在“策略编辑”中维护。'})]));
      } else if (draft.kind === 'yaml') {
        const file = el('select',{id:'source-yaml'},model.assets.filter(name=>/\.ya?ml$/.test(name)).map(value=>el('option',{value,text:value,selected:value===draft.yaml})));
        if (!file.options.length) file.append(el('option',{value:'',text:'请先导入 YAML 文件'}));
        file.onchange = () => { draft.yaml=file.value; draft.changed=true; sourceDraft=draft; };
        form.append(el('div',{class:'field wide'},[el('label',{htmlFor:file.id,text:'本机 YAML 文件'}),file]));
      } else {
        const file = el('input',{id:'source-ppsub',type:'file',accept:'.json,application/json'});
        file.onchange = async () => {
          try { if (!file.files[0] || file.files[0].size>1024*1024) throw Error('请选择不超过 1 MiB 的 PPsub JSON。');
            draft.ppsub=await file.files[0].text(); JSON.parse(draft.ppsub); draft.changed=true; sourceDraft=draft;
            notice('PPsub 文件已选中，预览并确认后才会切换来源。');
          } catch(error) { draft.ppsub=null; notice(error.message,'error'); }
        };
        form.append(el('div',{class:'field wide'},[el('label',{htmlFor:file.id,text:'导入 PPsub JSON 来源'}),file,
          el('small',{text:draft.ppsub ? '已选择 PPsub 文件，等待预览确认。' : model.source.native_ppsub ? '留空使用已保存的本机 PPsub；选择文件将替换它。' : '切换到原生 PPsub 需导入配置文件。'})]));
      }
      if (model.source.local_policy || model.source.native_ppsub) {
        const reset=el('input',{id:'reset-source',type:'checkbox',checked:draft.reset});
        reset.onchange=()=>{draft.reset=reset.checked;sourceDraft=draft;};
        form.append(el('label',{class:'checkline wide'},[reset,document.createTextNode('允许切换时备份并清除冲突的旧本地 PPsub / 覆盖策略')]));
      }
      form.append(el('div',{class:'button-row wide'},[
        el('button',{class:'btn',type:'button',text:'取消切换',onclick:()=>{sourceDraft=null;notice('');render();}}),
        el('button',{class:'btn btn-brand',type:'button',text:'预览来源切换',onclick:async()=>{
          try {
            if (dirty.size || window.ppgwPpsub?.hasChanges()) throw Error('请先保存或放弃当前编辑，再切换配置来源。');
            if ([...form.querySelectorAll('input,select')].some(node=>!node.reportValidity())) return;
            const target = {mode:draft.kind==='yaml'?'yaml':'suburl',fast_node:'no'};
            if (draft.kind==='yaml') { if (!$('source-yaml').value) throw Error('请先导入 YAML 文件。'); target.yamlfile=$('source-yaml').value; }
            else if (draft.kind==='remote') target.suburl=$('source-url').value;
            else { if (!draft.ppsub && !model.source.native_ppsub) throw Error('请先选择 PPsub JSON 文件。'); target.suburl='ppsub@http://127.0.0.1/ppsub.json'; }
            const body={base_revision:model.revision,patch:{ini:target},reset_source:draft.reset,
              ...(draft.kind==='ppsub'&&draft.ppsub?{source_ppsub:draft.ppsub}:{})};
            const snapshot=previewSnapshot();
            const data=await api('preview',body);
            prepared={kind:'config',body,source:true,snapshot};
            $('preview-content').replaceChildren(...previewDescription(data,'config'),el('p',{class:'subtext',text:'确认后切换主配置来源。成功后会重新读取新来源的编辑内容。'}));
            openPreview();
          } catch(error){notice(error.message,'error');}
        }})]));
      details.append(form); section.append(details);
      if (draft.kind==='yaml') details.append(assetCard());
      return section;
    }
    function renderSubscriptionGlobals() {
      if (subscriptionControls) subscriptionControls.replaceChildren(...(model ? subscriptionGlobals() : []));
    }
    let sessionReset = false;
    const navigationHost = el('div', {class:'page-navigation'});
    const statusHost = el('section', {class:'ppgw-management', 'aria-label':'运行状态'});
    const recentHost = el('section', {class:'management-history card glass', 'aria-label':'最近操作'});
    root.querySelector('#content').before(navigationHost, statusHost);
    const phaseNames = {queued:'等待执行',validating:'校验配置',fetching:'获取订阅',saving:'保存配置',applying:'应用配置',verifying:'验证运行',rolling_back:'恢复原配置',complete:'已结束',interrupted:'结果待核实'};
    const statusNames = {succeeded:'成功',complete:'成功',applied:'已应用',unchanged:'内容未变',failed:'失败',interrupted:'结果待核实',running:'进行中',applying:'进行中',queued:'等待执行'};
    let statusFingerprint = '';
    function renderManagement() {
      const data = model?.management;
      const fingerprint=JSON.stringify([data,model?.running_mode,model?.source.local_policy]);
      if(fingerprint===statusFingerprint&&statusHost.childElementCount)return;
      statusFingerprint=fingerprint;
      const names = {loaded:'已生效（本次管理操作已验证）',saved_pending:'已保存，尚未确认加载',retained:'候选校验未通过，沿用上次成功配置',recovery_required:'需要恢复，请检查管理事务',unknown:'暂时无法确认配置是否已全部生效'};
      const runtime = model?.runtime?.data || model?.runtime || {};
      const rows = data?.recent || [];
      const policyText = data?.policy_state==='loaded'?' · 本地覆盖策略已加载':model?.source.local_policy?' · 已保存本地覆盖策略，加载结果待确认':'';
      const heading = el('p',{role:'status',text:'运行模式：' + (modeNames[model?.running_mode] || model?.running_mode || '暂时无法确认') + ' · ' + (names[data?.state] || names.unknown) + policyText});
      const records = el('ol',{class:'operation-list'});
      for (const row of rows) {
        const kind = {config:'配置修改',policy:'策略保存',policy_reset:'恢复原策略',asset:'资源导入',refresh_nodes:'订阅更新',check:'Mihomo 检查',update:'Mihomo 更新',rollback:'Mihomo 回退',scheduled:'自动维护'}[row.kind] || '订阅与策略';
        const when = new Date((row.started_at || row.created_at || row.updated_at)*1000).toLocaleString();
        const outcome={core_inactive:'已保存待加载',staged:'已暂存待加载',retained:'部分获取失败，沿用旧节点',unchanged:'内容未变，无需重启',updated:'已更新并加载'}[row.outcome];
        records.append(el('li',{},[el('div',{class:'operation-heading'},[el('strong',{text:kind}),el('span',{class:'operation-state',text:statusNames[row.status] || '待核对'})]),el('p',{class:'subtext',text:`${when} · ${phaseNames[row.phase || row.stage] || '已记录'}${outcome?' · '+outcome:''}${row.error?' · '+message(row.error):''}${row.recovery==='check_required'?' · 请核实运行与恢复状态':''}${row.backup?' · 备份 '+row.backup:''}`})]));
      }
      statusHost.replaceChildren(heading);
      recentHost.replaceChildren(el('div',{class:'card-heading'},[el('h2',{text:'最近操作'}),el('p',{class:'subtext',text:'展示最近 '+rows.length+' 条已记录的配置、订阅与核心维护操作。'})]),rows.length?records:el('p',{class:'empty-state',text:'暂无操作记录。'}));
    }
    async function pollManagement() {
      try {
        const data = await api('status');
        if (model && !stopped) { model.management=data.management; model.runtime=data.runtime; model.running_mode=data.running_mode; model.recovery_required=data.recovery_required; if(data.source)model.source.local_policy=data.source.local_policy; renderManagement(); }
      } catch { if (!stopped) { statusFingerprint=''; const heading=statusHost.querySelector('[role=status]'); if(heading)heading.textContent='运行状态暂时无法读取，正在重新连接…'; } }
      if (!stopped) later(pollManagement, 5000);
    }
    function render() {
      if (stopped) return;
      const page = /^#\/(subscriptions|ppsub)(?:[?]|$)/.test(location.hash) ? 'subscriptions' : 'settings';
      const editing = page === 'subscriptions';
      const showEditor = editing && subscriptionPage === 'editor';
      root.closest('.ppgw-workspace').dataset.page = page;
      $('page-title').textContent = page === 'subscriptions' ? '订阅与策略' : '网关设置';
      $('page-description').textContent = page === 'subscriptions' ? '在同一编辑器中维护订阅、分组与规则，保存后应用到当前网关。' : '按需配置网关，应用前查看生效方式与影响。';
      document.title = $('page-title').textContent + ' · LovestGateWay';
      $('mode-label').textContent = model ? modeNames[model.settings.ini.mode] || model.settings.ini.mode : '连接中';
      renderManagement();
      navigationHost.replaceChildren(editing ? tabs([['editor','策略编辑'],['source','配置来源'],['recent','最近操作']],subscriptionPage,key=>{subscriptionPage=key;render();},'订阅与策略分栏') :
        tabs([['access','访问与统计'],['maintenance','维护与 API'],['network','网络与出站'],['advanced','高级选项'],['recent','最近操作']],category,key=>{category=key;render();},'网关设置分栏'));
      $('content').setAttribute('role','tabpanel');
      $('content').setAttribute('aria-labelledby','gateway-tab-'+(editing?subscriptionPage:category));
      $('reload').hidden = editing;
      if (options.editorHost) {
        options.editorHost.id = 'subscription-editor-panel';
        options.editorHost.setAttribute('role','tabpanel');
        options.editorHost.setAttribute('aria-labelledby','gateway-tab-editor');
        options.editorHost.hidden = !showEditor;
        if (editing) { options.createEditor(); window.ppgwPpsub?.refreshIfNeeded(); }
      }
      const items = [];
      if (!model) {
        if (!editing) items.push(el('div', {class: 'card glass empty-state', text: '正在读取网关配置…'}));
      } else if ((editing?subscriptionPage:category)==='recent') items.push(recentHost);
      else if (!editing) items.push(...settingsContent());
      else if (subscriptionPage==='source') items.push(sourceCard());
      else if(dirty.size) items.push(el('div',{class:'subscription-draft-actions'},[el('span',{class:'subtext',text:'更新计划有未保存修改，将与编辑器一起应用。'}),el('button',{class:'btn',text:'放弃更新计划',onclick:()=>{for(const key of ['ini.subtime','ini.subcron'])dirty.delete(key);render();}})]));
      renderSubscriptionGlobals();
      $('content').replaceChildren(...items);
      $('content').setAttribute('aria-busy', String(!model));
      $('policy-actions').hidden = !showEditor || !model?.source.local_policy;
      updateDirty();
    }
    function patchBody() {
      const patch = {};
      for (const [path, value] of dirty) { const [ns, key] = path.split('.'); (patch[ns] ||= {})[key] = value; }
      return {base_revision: model.revision, patch, reset_source: sessionReset};
    }
    function newAddress(listen) {
      if (!listen) return null;
      const url = new URL('http://' + listen);
      if (url.hostname === '0.0.0.0' || url.hostname === '[::]') url.hostname = location.hostname;
      url.pathname = '/ui/'; url.hash = location.hash || '#/settings';
      return url.href;
    }
    const policySectionNames = {'proxy-providers':'配置内节点提供商','proxy-groups':'节点组','rule-providers':'规则订阅',rules:'规则',features:'全局选项',reference_renames:'分组引用更名'};
    function sourceExplanation(details, proposed = false) {
      const policy = details?.local_policy;
      if (!policy?.present) return null;
      const sections = (policy.sections || []).map(key => policySectionNames[key] || key);
      if (policy.feature_keys?.length && !sections.includes('全局选项')) sections.push('全局选项');
      return el('div', {class:'source-override'}, [
        el('strong', {text:policy.state === 'invalid' ? '本地策略需要检查' : policy.applies ? (proposed ? '此次将使用本地覆盖' : '已保存与当前来源绑定的本地覆盖') : '存在未作用于当前来源的本地策略'}),
        el('p', {text:policy.state === 'invalid' ? '暂时无法读取本地策略，无法确认覆盖范围。请检查配置或恢复提示。' : policy.applies ? (sections.length ? '覆盖范围：' + sections.join('、') + '。' : '本地策略已绑定当前配置来源。') + '更新或重新生成配置后仍会应用这些本地修改；“恢复订阅原策略”可撤销覆盖。' : '当前来源与本地策略不匹配，这份策略不会合并到当前配置。'})
      ]);
    }
    function structuredChanges(summary) {
      if (!summary || summary.schema !== 1) return [];
      const sections = [];
      for (const [key, title] of [['subscriptions','订阅提供商'],['node_groups','节点组'],['rule_providers','规则订阅'],['rules','规则']]) {
        const change = summary[key];
        if (!change?.changed) continue;
        const counts = change.counts;
        const row = el('section', {class:'preview-change', 'data-change-section':key}, [
          el('div', {class:'preview-change-heading'}, [el('strong', {text:title}), el('span', {text:`${counts.before} → ${counts.after}` + (change.unit === 'blocks' ? ' 个规则块' : change.unit === 'lines' ? ' 条' : '')})]),
          el('p', {class:'preview-change-counts', text:[counts.added && `新增 ${counts.added}`, counts.removed && `删除 ${counts.removed}`, counts.modified && `修改 ${counts.modified}`, change.order_changed && '顺序已调整', change.priority_changed && '优先级已调整'].filter(Boolean).join(' · ') || '内容已修改'})
        ]);
        for (const [operation, label] of [['added','新增'],['removed','删除'],['modified','修改']]) {
          const names = change.names?.[operation];
          if (names?.length) row.append(el('p', {class:'preview-change-names',text:label + '：' + names.join('、')}));
        }
        if (change.renamed?.length) row.append(el('p', {class:'preview-change-names',text:'更名：' + change.renamed.map(item => item.before + ' → ' + item.after).join('、')}));
        if (change.names_truncated || change.renames_truncated) row.append(el('p', {class:'subtext',text:'名称较多，仅展示部分；数量包含全部变更。'}));
        if (key === 'rules') {
          if (change.inline_rules?.changed) row.append(el('p', {class:'subtext',text:`手写规则：${change.inline_rules.counts.before} → ${change.inline_rules.counts.after} 条。`}));
          row.append(el('p', {class:'subtext',text:'规则内容与目标地址不在预览中展示。'}));
        }
        sections.push(row);
      }
      const features = summary.features;
      if (features?.changed) sections.push(el('section', {class:'preview-change', 'data-change-section':'features'}, [
        el('strong', {text:'全局选项'}), el('p', {class:'preview-change-counts', text:`已修改 ${features.modified} 项：` + (features.changed_keys || []).map(key => ({global_monitor:'全局监测',restart_cron:'定时重启',shadowsocks_in:'Shadowsocks 入站','node-groups':'节点组选项'})[key] || key).join('、') + '。敏感内容不显示。'})
      ]));
      if (summary.other_fields?.changed) sections.push(el('p', {class:'subtext', text:`另有 ${summary.other_fields.modified} 项编辑配置已修改。`}));
      return sections.length ? [el('div', {class:'preview-structure'}, sections)] : [];
    }
    function previewDescription(data, kind) {
      const items = [];
      const plan = data.application;
      const action = plan?.action || data.effects?.core || data.action;
      items.push(el('div', {class: 'notice', 'data-application-action':action, text: plan?.runtime_state === 'unknown' ? '运行状态暂无法确认，后台将按实际状态保存或加载配置，应用结果以任务验收为准。' : plan?.runtime_state === 'staged' ? '此次仅保存到离线目录，尚未验证运行加载。' : plan?.pending_load ? '网关当前未运行。此次仅保存配置，启动后再加载；保存成功不代表已加载。' :
        action === 'restart' ? '将重启网关并验证新配置，现有连接可能中断。' :
        action === 'reload' ? '将热更新转发核心，保留核心进程和现有 Mihomo 配置。' : action === 'auxiliary' ? (plan?.core_active === true ? '仅调整辅助服务，转发核心保持运行。' : plan?.core_active === false ? '仅调整辅助服务，转发核心当前未运行，此次不会启动。' : '仅调整辅助服务，不改变转发核心的运行状态。') : '无需重启转发核心。'}));
      const services = plan?.services || data.effects?.services;
      if (services?.length) items.push(el('p', {class: 'subtext', text: '调整辅助服务：' + services.map(v => ({api: '对外观测 API', history: '历史采集', maintenance_timer: '自动维护', subscription: '订阅服务',settings:'设置服务'})[v] || v).join('、')}));
      if (plan?.services_uncertain) items.push(el('p', {class:'subtext',text:'辅助服务的运行状态尚未确认，具体调整范围以执行时状态为准。'}));
      if (plan?.reload_fallback_possible) items.push(el('p', {class:'subtext',text:'若热更新条件不满足，后台会改用重启并验证；届时现有连接可能中断。'}));
      items.push(...structuredChanges(data.change_summary));
      const explanation = sourceExplanation(data.source_details, true);
      if (explanation) items.push(explanation);
      if (kind === 'config') {
        const list = el('ul', {class: 'change-list'});
        for (const [ns, fields] of Object.entries(data.changes || {})) for (const [key, value] of Object.entries(fields)) {
          list.append(el('li', {}, [el('strong', {text: labels[key]?.[0] || (key === 'box' ? '流量阻断选项' : key)}),
            el('span', {text: model.schema[ns]?.[key]?.secret ? '已更新（不显示内容）' : typeof value === 'boolean' ? value ? '开启' : '关闭' : Array.isArray(value) ? value.join('、') || '清空' : String(value) || '清空'})]));
        }
        items.push(list);
        if (data.effects?.source_reset) items.push(el('p', {class: 'subtext', text: '旧本地 PPsub / 策略将清除，并包含在此次备份中。'}));
        if (data.effects?.access_changed) items.push(el('p', {class: 'subtext', text: '修改管理范围可能使当前设备无法访问；修改管理密码后请重新登录。'}));
        const address = newAddress(data.effects?.web_listen);
        if (address) items.push(el('p', {class: 'subtext', text: '新的管理地址：' + address}));
      } else if (data.counts) items.push(el('p', {class: 'subtext', text: `应用后：${data.counts.groups} 个节点组、${data.counts.providers} 个规则订阅、${data.counts.rules} 条规则。未展示的原始字段保留。`}));
      return items;
    }
    async function preview(forceRestart = false) {
      if (dirty.has('ini.clash_web_password')) {
        const password = dirty.get('ini.clash_web_password');
        if (password.length < 8) { notice('管理密码至少需要 8 个字符。', 'error'); return; }
        if (password !== passwordConfirmation) { notice('两次输入的管理密码不一致，请重新确认。', 'error'); return; }
      }
      const controls = [...root.querySelectorAll('#content input,#content select,#content textarea'), ...(subscriptionControls?.querySelectorAll('input,select,textarea') || [])];
      if (controls.filter(node => !node.closest('.core-local-form')).some(node => !node.reportValidity())) return;
      $('preview').disabled = true;
      try {
        if (window.ppgwPpsub?.hasChanges()) throw Error('请先保存或重新读取编辑器中的修改，再应用全局选项或网关设置。');
        const kind = 'config';
        const body = {...patchBody(), ...(forceRestart ? {restart: true} : {})};
        const snapshot=previewSnapshot();
        const data = await api('preview', body);
        if (data.action === 'none' && !Object.keys(data.changes || {}).length && !data.changed) { notice('配置没有变化，无需应用。'); return; }
        prepared = {kind, body, snapshot, address: newAddress(data.effects?.web_listen)};
        $('preview-content').replaceChildren(...previewDescription(data, kind)); openPreview();
      } catch (error) { notice(error.message, 'error'); }
      finally { updateDirty(); }
    }
    async function resetPolicy() {
      if (dirty.size || window.ppgwPpsub?.hasChanges() || window.ppgwPpsub?.isBusy()) { notice('请先完成当前订阅、规则或全局选项修改。', 'error'); return; }
      prepared = {kind: 'policy_reset', body: {base_revision: model.revision}};
      $('preview-content').replaceChildren(el('div', {class: 'notice', text: '将备份并移除本地策略，重启后使用订阅中的原始分组与规则。现有连接可能中断。'}));
      openPreview();
    }
    async function startJob(kind, payload, address) {
      if (running || window.ppgwPpsub?.hasChanges() || window.ppgwPpsub?.isBusy()) throw Error('请先保存或重新读取编辑器中的修改，再应用全局选项或网关设置。');
      const id = identifier();
      if (address) { const target = new URL(address); target.searchParams.set('job', id); address = target.href; }
      const record = {id, address: address || null, kind};
      sessionStorage.setItem('lovestgateway-job', JSON.stringify(record));
      running = true; updateDirty();
      try { await api('apply', {id, kind, payload}); }
      catch (error) {
        if (error.status && error.status < 500) { sessionStorage.removeItem('lovestgateway-job'); running = false; updateDirty(); throw error; }
        // The response can be lost after the server accepted a restart. Poll by ID.
      }
      dirty.clear();
      passwordConfirmation = '';
      root.querySelectorAll('#content input[type=password]').forEach(input => { input.value = ''; });
      notice('任务已提交，正在等待实际加载与验收结果。');
      if (address && new URL(address).origin !== location.origin) {
        showReconnect(address); running = false; updateDirty(); return;
      }
      await pollJob(record);
    }
    function showReconnect(address) {
      $('reconnect').replaceChildren(document.createTextNode('管理监听正在切换。请打开新地址登录后查看任务结果： '), el('a', {href: address, text: address}));
      $('reconnect').hidden = false;
    }
    async function pollJob(record) {
      const deadline = Date.now() + 150000;
      while (!stopped && Date.now() < deadline) {
        try {
          const job = await api('jobs/' + record.id);
          if (job.status === 'succeeded' || job.status === 'failed') {
            sessionStorage.removeItem('lovestgateway-job'); running = false;
            if (job.status === 'failed') {
              postUpload = null; await load(); notice(message(job.error), 'error'); updateDirty(); return;
            }
            const uploaded = postUpload; postUpload = null; sourceDraft = null;
            dirty.clear();
            window.ppgwPpsub?.invalidate();
            await refreshRuntime();
            await load();
            if (uploaded) {
              if (/^#\/(subscriptions|ppsub)(?:[?]|$)/.test(location.hash)) sourceDraft={kind:'yaml',yaml:uploaded.name,url:'',ppsub:null,changed:true,reset:false};
              else { setValue('ini', 'mode', uploaded.kind === 'yaml' ? 'yaml' : 'ovpn');
              setValue('ini', uploaded.kind === 'yaml' ? 'yamlfile' : 'ovpnfile', uploaded.name); }
              render(); notice('文件已保存。请预览并确认切换到导入的配置；同名资源也可通过“重启应用资源”生效。', 'success');
            } else notice(job.result?.runtime?.reason === 'core_inactive' ? '配置已保存，转发核心当前未加载此次变更。请通过管理脚本检查服务状态。' : '配置已应用，运行状态验收通过。', 'success');
            return;
          }
        } catch (error) {
          if (error.status === 401) { running = false; notice(message('LOGIN_REQUIRED'), 'error'); updateDirty(); return; }
          if (error.code === 'JOB_NOT_FOUND') { sessionStorage.removeItem('lovestgateway-job'); running = false; notice('未查到任务记录。请刷新配置核对状态后重试。', 'error'); updateDirty(); return; }
        }
        await pause(1500);
      }
      running = false; notice('仍未确认任务结果。任务号已保留，刷新或重新登录后继续查询；也可使用管理脚本检查状态。'); updateDirty();
    }
    function maintenanceState(data) {
      const failed = ['failed','interrupted'].includes(data.operation?.status), busy = ['running','queued'].includes(data.operation?.status);
      const retryAt = data.operation?.retry_at;
      const reason = data.operation?.error === 'RELEASE_RATE_LIMITED' ? 'GitHub 版本查询已限流。' + (retryAt ? `可在 ${new Date(retryAt * 1000).toLocaleString()} 后重试。` : '请稍后重试。') + '已安装的 Mihomo 可继续使用，也可使用下方本地文件更新。' : (data.operation?.message || '核心维护未完成，请检查维护服务状态。');
      const detail = failed ? reason + (data.operation?.error ? `（${data.operation.error}）` : '') : '';
      const cpu = data.core.architecture ? ` · 当前型号 ${data.core.installed_architecture || '未识别'} · ${data.core.selection === 'auto' ? '自动目标' : '指定目标'} ${data.core.architecture}` : '';
      const selection = data.core.selection_supported === false ? ' · 当前系统未确认支持指定型号，请选择自动或较低型号' :
        data.core.cpu_detection === 'baseline-fallback' ? ' · CPU 检测未确认更高等级，按 v1 兼容处理' : '';
      const available = data.core.version_unrecognized ? ' · 当前版本无法比较，自动维护保留现有核心' : data.core.installed_newer ? ' · 当前版本较新，不自动降级' :
        data.core.update_available ? ' · 可安装新稳定版' : data.core.variant_change_available ? ' · 可手动切换同版本 CPU 型号' : '';
      if ($('maintenance-state')) $('maintenance-state').textContent = `当前 Mihomo ${data.core.installed_version || '未安装'} · 官方稳定版 ${data.core.latest_version || (failed ? (data.operation?.error === 'RELEASE_RATE_LIMITED' ? '查询受限' : '未取得版本信息') : '尚未检查')}` + cpu + selection + available + (busy ? ' · 后台正在处理，请稍候…' : detail ? ' · ' + detail : '');
      return {failed, busy, detail};
    }
    async function pollCore(record) {
      for (let attempts = 0; attempts < 100 && !stopped; attempts++) {
        await pause(attempts ? 3000 : 1500);
        if (stopped) return;
        try {
          let data = await api('maintenance', undefined, 'subscription');
          if (data.operation?.id !== record.id) {
            // Another tab can start a newer task before this browser reconnects.
            // Only the saved task's result may finish this browser's tracking.
            const status = await api('status');
            const own = status.management?.recent?.find(row => row.source === 'mihomo' && row.id === record.id);
            if (!own) { notice('尚未查到本次维护任务的结果，正在继续核对；不会重复提交安装。'); continue; }
            data = {...data, operation: own};
          }
          const state = maintenanceState(data);
          if (state.busy) { notice('维护任务正在后台执行，重启期间会自动重新连接并继续查询。'); continue; }
          if (!['complete','failed','interrupted'].includes(data.operation?.status)) continue;
          if (record.action !== 'check') window.ppgwPpsub?.invalidate();
          if (!state.failed && record.action !== 'check') await refreshRuntime();
          if (pendingCore()?.id === record.id) sessionStorage.removeItem('lovestgateway-core-job');
          notice(state.failed ? state.detail : '核心维护已完成。', state.failed ? 'error' : 'success');
          return;
        } catch (error) {
          if (stopped) return;
          notice(error.status === 401 ? message('LOGIN_REQUIRED') : '网关正在重启或暂时断连，正在重新连接并查询维护结果；请勿重复安装。', error.status === 401 ? 'error' : '');
        }
      }
      if (!stopped) {
        notice('尚未确认维护结果，任务号已保留。可继续查询、刷新或重新登录；不会重复提交安装。');
        $('notice').append(el('button', {type:'button', class:'btn', text:'继续查询维护结果', onclick:() => followCore(record)}));
      }
    }
    async function followCore(operation, action = operation?.action) {
      const record = {id: operation?.id, action};
      if (!/^mihomo_[0-9]+$/.test(record.id || '')) throw Error('维护任务号未确认，请刷新页面核对后台状态后再操作。');
      if (corePolling) return corePolling;
      sessionStorage.setItem('lovestgateway-core-job', JSON.stringify(record));
      running = true; updateDirty();
      corePolling = pollCore(record).finally(() => { corePolling = null; running = Boolean(pendingCore()); updateDirty(); });
      return corePolling;
    }
    function coreAvailable() {
      if (dirty.size || running || window.ppgwPpsub?.hasChanges() || window.ppgwPpsub?.isBusy()) { notice('请先完成当前配置操作。', 'error'); return false; }
      return true;
    }
    async function coreAction(action) {
      if (!coreAvailable()) return;
      running = true; updateDirty();
      try {
        const operation = await api('core/' + action, {}, 'subscription');
        if ($('maintenance-state')) $('maintenance-state').textContent = '维护已提交，正在处理…';
        await followCore(operation, action);
      } catch (error) { if (!stopped) notice(error.status ? error.message : '提交响应暂时中断，维护可能已在后台启动。请刷新页面核对维护状态，勿重复安装。', 'error'); }
      finally { running = Boolean(pendingCore()); updateDirty(); }
    }
    function localCoreForm() {
      const file = el('input', {id: 'core-local-file', type: 'file', accept: '.gz,application/gzip', required: true});
      const version = el('input', {id: 'core-local-version', type: 'text', placeholder: 'v1.19.33', required: true, pattern: 'v[0-9]+\\.[0-9]+\\.[0-9]+', autocomplete: 'off'});
      const sha = el('input', {id: 'core-local-sha', type: 'text', placeholder: '64 位官方 SHA256', required: true, pattern: '[0-9a-fA-F]{64}', autocomplete: 'off', spellcheck: false});
      const progress = el('p', {id: 'core-upload-progress', class: 'subtext', role: 'status', text: ''});
      const field = (label, input) => el('div', {class: 'field'}, [el('label', {htmlFor: input.id, text: label}), input]);
      const form = el('form', {class: 'core-local-form'}, [
        el('p', {class: 'subtext', text: '遇到 GitHub 限流或服务器无法下载时，可从官方 Releases 下载适用于 Linux amd64 和本机 CPU 等级的 Mihomo .gz；不确定时选 v1（旧名称 compatible）。复制对应官方 SHA256，上传安装无需在线查询版本。'}),
        el('a', {href: 'https://github.com/MetaCubeX/mihomo/releases', target: '_blank', rel: 'noopener noreferrer', text: '打开 Mihomo 官方发布页 ↗'}),
        el('div', {class: 'field-grid'}, [field('Mihomo 官方 .gz 文件', file), field('核心版本', version), field('官方 SHA256', sha)]),
        el('p', {class: 'subtext', text: '最大 64 MiB。上传完成后验证摘要、架构和版本；安装可能重启网关，启动验收失败会回退。'}),
        el('button', {class: 'button primary', type: 'submit', text: '上传并安装 Mihomo'}), progress,
      ]);
      file.onchange = () => { const match = file.files[0]?.name.match(/-(v[0-9]+\.[0-9]+\.[0-9]+)\.gz$/); if (match) version.value = match[1]; };
      form.onsubmit = async event => {
        event.preventDefault();
        if (!coreAvailable()) return;
        const archive = file.files[0], coreVersion = version.value.trim(), checksum = sha.value.trim().toLowerCase();
        if (!archive || !archive.name.endsWith('.gz') || !archive.size || archive.size > 64 * 1024 * 1024 || !/^v[0-9]+\.[0-9]+\.[0-9]+$/.test(coreVersion) || !/^[0-9a-f]{64}$/.test(checksum)) { notice('请选择不超过 64 MiB 的官方 .gz 文件，并填写有效版本和 SHA256。', 'error'); return; }
        running = true; updateDirty();
        const id = identifier(); let committed = false, committing = false;
        const send = body => api('core/update', body, 'subscription');
        try {
          const upload = await send({operation: 'begin', id, version: coreVersion, sha256: checksum, size: archive.size});
          let offset = upload.received;
          while (offset < archive.size && !stopped) {
            const bytes = new Uint8Array(await archive.slice(offset, offset + upload.chunk_bytes).arrayBuffer());
            let binary = ''; for (let n = 0; n < bytes.length; n += 32768) binary += String.fromCharCode(...bytes.subarray(n, n + 32768));
            const result = await send({operation: 'chunk', id, offset, data: btoa(binary)});
            if (result.received <= offset || result.received > archive.size) throw Error('上传进度异常，请重新选择文件上传。');
            offset = result.received;
            progress.textContent = `已上传 ${Math.round(offset / archive.size * 100)}%`;
          }
          if (stopped) return;
          progress.textContent = '上传完成，正在校验并提交后台安装…';
          committing = true;
          const operation = await send({operation: 'commit', id}); committed = true;
          progress.textContent = '后台正在安装。刷新页面不会中断安装，可在此查看结果。';
          await followCore(operation, 'install-local');
          progress.textContent = pendingCore() ? '安装结果尚未确认，已保留任务号继续核对。' : '后台任务已返回结果，请查看上方维护状态。';
        } catch (error) {
          if (error.status && error.status < 500) committing = false;
          progress.textContent = '上传或安装未完成，请查看上方提示。';
          if (!stopped) notice(committing && (!error.status || error.status >= 500) ? '提交响应中断，安装可能已在后台启动。请刷新页面检查维护状态后再重试。' : error.message, 'error');
        } finally {
          if (!committed && !committing && !stopped) { try { await send({operation: 'cancel', id}); } catch {} }
          running = Boolean(pendingCore()); updateDirty();
        }
      };
      later(async () => {
        try {
          const record = pendingCore();
          if (record) { await followCore(record); return; }
          const data = await api('maintenance', undefined, 'subscription');
          const state = maintenanceState(data);
          if (state.busy && !running) await followCore(data.operation);
        } catch {}
      }, 0);
      return el('details', {class: 'core-local'}, [el('summary', {text: '本地文件更新（手动）'}), form]);
    }
    async function load(expectedRevision, details) {
      const request = ++modelRequest;
      $('reload').disabled = true;
      try {
        const value = await api('config');
        if (request !== modelRequest) {
          if (expectedRevision) throw Error('配置已变化，请重新读取后再保存。');
          return false;
        }
        if ((expectedRevision && value.revision !== expectedRevision) ||
            (model && otherSettingsChanged() && value.revision !== model.revision))
          throw Error('配置已变化，请刷新网关设置后重新编辑。');
        model = value;
        if (details !== undefined) sourceDetails = details;
        else if (value.source_details) sourceDetails = {...value.source_details, url:sourceDetails?.url};
        render();
        if (model.recovery_required) notice(message('RECOVERY_REQUIRED'), 'error');
        else if (model.latest_job?.status === 'failed' && !pending()) notice(message(model.latest_job.error), 'error');
        return true;
      } catch (error) {
        notice(error.status === 401 ? message('LOGIN_REQUIRED') : error.message, 'error');
        if (expectedRevision) throw error;
        return false;
      }
      finally { if (!stopped) $('reload').disabled = running; }
    }
    function previewSnapshot(){return JSON.stringify({dirty:[...dirty],source:sourceDraft,passwordConfirmation});}
    function openPreview(){
      prepared.snapshot??=previewSnapshot();
      const panel=$('preview-dialog');panel.hidden=false;panel.scrollIntoView({behavior:'smooth',block:'start'});panel.focus({preventScroll:true});
    }
    $('preview').onclick = () => preview();
    $('cancel-preview').onclick = () => { prepared = null; $('preview-dialog').hidden = true; };
    $('apply').onclick = async () => {
      if (!prepared || running) return;
      const action = prepared; prepared = null; $('preview-dialog').hidden = true;
      if(action.snapshot!==previewSnapshot()){notice('编辑内容已变化，请重新预览后再确认应用。','error');return;}
      try { if (action.submit) await action.submit(); else await startJob(action.kind, action.body, action.address); }
      catch (error) { notice(error.message, 'error'); }
    };
    $('discard').onclick = () => { passwordConfirmation = ''; dirty.clear(); sessionReset = false; notice(''); render(); };
    $('reload').onclick = async () => {
      if ((dirty.size) && !confirm('刷新会放弃未保存的修改，继续吗？')) return;
      passwordConfirmation = ''; dirty.clear(); sessionReset = false; notice(''); await load();
    };
    function destroy() {
      stopped = true;
      lifetime.abort();
      for (const id of timers) clearTimeout(id);
      timers.clear();
      $('preview-dialog').hidden = true;
      if (current === bridge) current = null;
      document.title = 'LovestGateWay';
    }
    destroy.refresh = render;
    destroy.allowNavigation = (url, type) => {
      const sameView = url && url.origin === location.origin && url.pathname === '/ui/' && /^#\/(settings|subscriptions)(?:$|[?])/.test(url.hash);
      if (sameView) {
        if (running || window.ppgwPpsub?.isBusy()) { notice('正在应用并验证，请等待任务完成后切换。'); return false; }
        return true;
      }
      if (running) { notice('正在应用并验证，请等待任务完成后再离开。'); return false; }
      if (window.ppgwPpsub?.isBusy()) { notice('正在保存并应用，请等待任务完成后再离开。'); return false; }
      if (!dirty.size && !sourceDraft?.changed && !window.ppgwPpsub?.hasChanges()) return true;
      if (type === 'leave') return false;
      return confirm('离开会放弃未保存的修改，继续吗？');
    };
    const incoming = new URL(location.href);
    if (/^[0-9a-f]{32}$/.test(incoming.searchParams.get('job') || '')) {
      sessionStorage.setItem('lovestgateway-job', JSON.stringify({id: incoming.searchParams.get('job'), address: null, kind: 'config'}));
      incoming.searchParams.delete('job'); history.replaceState(history.state, '', incoming.href);
    }
    $('reset-policy').onclick = resetPolicy;
    render();
    later(pollManagement, 5000);
    load().then(async () => {
      if (stopped) return;
      await refreshRuntime();
      if (stopped) return;
      const record = pending();
      if (record) { running = true; updateDirty(); if (record.address && new URL(record.address).origin !== location.origin) { showReconnect(record.address); running = false; updateDirty(); } else pollJob(record); }
      else if (pendingCore()) followCore(pendingCore());
    });
    return destroy;
  }
  window.ppgwGateway = {mount,
    attachOptions: host => current?.attachOptions(host),
    syncEditor: data => current?.syncEditor(data),
    setEditorBusy: busy => current?.setEditorBusy(busy),
    editorOptionsChanged: () => current?.editorOptionsChanged(),
    discardEditorOptions: () => current?.discardEditorOptions(),
    editorPatch: revision => current?.editorPatch(revision) || {},
    previewEditor: (data,submit) => current?.previewEditor(data,submit),
    editorApplied: () => current?.editorApplied(),
  };
})();
