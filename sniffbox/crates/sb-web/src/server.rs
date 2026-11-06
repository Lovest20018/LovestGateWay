// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::acl::AdminAcl;
use crate::events::LogTx;
use crate::screen::ScreenHistory;
use crate::traffic::TrafficSource;
use crate::{
    clash_logs, connections, gate, http, native, proxy_unix, respond, router, screen_poll, session,
    static_files,
};
use arc_swap::ArcSwap;
use std::io;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use std::time::Duration;
use tokio::io::AsyncReadExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;

pub struct ServerConfig {
    pub listen: SocketAddr,

    pub token: crate::auth::TokenHandle,
    pub sessions: Arc<session::Sessions>,

    pub admin: Arc<ArcSwap<AdminAcl>>,
    pub clash_sock: PathBuf,

    pub clash_enabled: bool,
    pub webroot: PathBuf,

    pub screen_dev: PathBuf,

    pub screen_history: Arc<ScreenHistory>,

    pub log_tx: Option<LogTx>,

    pub traffic: Option<Arc<dyn TrafficSource>>,

    pub connections: Option<Arc<dyn crate::connections::ConnectionsSource>>,

    pub nodes: Option<Arc<dyn crate::nodes::NodesSource>>,

    pub info: Option<Arc<dyn crate::info::InfoSource>>,

    pub proxies: Option<Arc<dyn crate::proxies::ProxiesSource>>,

    pub reload_cmd: Option<Vec<String>>,

    pub reload_inflight: Arc<AtomicBool>,

    pub group_delay_inflight: Arc<std::sync::Mutex<std::collections::HashSet<String>>>,

    pub probe: Option<Arc<dyn crate::probe::ProbeSource>>,

    pub clash_control: Option<Arc<dyn crate::clash_ctl::ClashControl>>,

    pub geo: Option<Arc<dyn crate::geo::GeoControl>>,

    pub cloudflared: Option<Arc<dyn crate::cloudflared::CloudflaredSource>>,

    pub cloudflared_log: Option<Arc<ScreenHistory>>,
}

const IDLE_TIMEOUT: Duration = Duration::from_secs(60);

const MAX_NATIVE_BODY: usize = 32 * 1024;

const MAX_DRAIN_BODY: u64 = 1024 * 1024;

fn internal_address(mut address: SocketAddr) -> Option<SocketAddr> {
    if address.ip().is_unspecified() || address.ip().is_loopback() { return None; }
    address.set_ip(if address.is_ipv4() { std::net::Ipv4Addr::LOCALHOST.into() }
        else { std::net::Ipv6Addr::LOCALHOST.into() });
    Some(address)
}

async fn bind_internal(address: SocketAddr) -> io::Result<Option<TcpListener>> {
    match internal_address(address) {
        Some(address) => Ok(Some(TcpListener::bind(address).await?)),
        None => Ok(None),
    }
}

pub async fn serve(cfg: Arc<ServerConfig>, shutdown: watch::Receiver<bool>) -> io::Result<()> {
    let listener = TcpListener::bind(cfg.listen).await?;
    run(listener, cfg, shutdown).await
}

pub async fn run(
    listener: TcpListener,
    cfg: Arc<ServerConfig>,
    mut shutdown: watch::Receiver<bool>,
) -> io::Result<()> {
    // Bind before starting any tasks: a missing private channel is a startup
    // failure, never an apparently healthy UI with broken management calls.
    let internal = bind_internal(listener.local_addr()?).await?;
    tracing::info!(
        listen = %cfg.listen,
        clash_sock = %cfg.clash_sock.display(),
        webroot = %cfg.webroot.display(),
        "web server listening"
    );

    if cfg.clash_enabled {
        if let Some(tx) = cfg.log_tx.clone() {
            clash_logs::spawn(cfg.clash_sock.clone(), tx, shutdown.clone());
        }
    } else {
        tracing::info!(
            "clash not running (outbound mode bypasses it); /logs consumer + clash reverse-proxy disabled"
        );
    }

    screen_poll::spawn(
        cfg.screen_dev.clone(),
        cfg.screen_history.clone(),
        shutdown.clone(),
    );
    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                if *shutdown.borrow() { break; }
            }
            accepted = async {
                match &internal {
                    Some(private) => tokio::select! {
                        accepted = listener.accept() => accepted,
                        accepted = private.accept() => accepted,
                    },
                    None => listener.accept().await,
                }
            } => {
                let (stream, peer) = match accepted {
                    Ok(x) => x,
                    Err(e) => { tracing::warn!(%e, "web accept failed"); continue; }
                };
                let cfg = cfg.clone();

                let sd = shutdown.clone();
                tokio::spawn(async move {
                    if let Err(e) = handle_conn(stream, peer, cfg, sd).await {
                        tracing::debug!(%peer, %e, "web conn closed with error");
                    }
                });
            }
        }
    }
    tracing::info!("web server shutting down");
    Ok(())
}

async fn handle_conn(
    mut stream: TcpStream,
    peer: SocketAddr,
    cfg: Arc<ServerConfig>,
    shutdown: watch::Receiver<bool>,
) -> io::Result<()> {

    if !cfg.admin.load().allows(peer.ip()) {
        return Ok(());
    }
    let _ = stream.set_nodelay(true);

    let mut carry: Vec<u8> = Vec::new();
    loop {
        let head_len = match tokio::time::timeout(
            IDLE_TIMEOUT,
            http::read_head(&mut stream, &mut carry),
        )
        .await
        {
            Ok(Ok(n)) => n,
            Ok(Err(e)) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(()),
            Ok(Err(e)) => return Err(e),
            Err(_) => return Ok(()),
        };
        let body_prefix = carry.split_off(head_len);
        let mut req = match http::parse_request(&carry) {
            Ok(r) => r,
            Err(_) => {
                let _ = respond::send(
                    &mut stream,
                    400,
                    "Bad Request",
                    &[],
                    b"bad request\n",
                    false,
                )
                .await;
                return Ok(());
            }
        };
        carry.clear();

        let route = router::classify(&req.method, req.path_only());
        let ka = req.keep_alive();

        let local_ctl = req.path_only() == "/sniffbox/clash/up";
        if local_ctl && (!peer.ip().is_loopback() || req.headers.iter().any(|(key, _)| key.eq_ignore_ascii_case("origin") || key.eq_ignore_ascii_case("sec-fetch-site"))) {
            respond::forbidden(&mut stream, "loopback only\n", ka).await?;
            carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
            if !ka {
                return Ok(());
            }
            continue;
        }

        if req.path_only().starts_with("/sniffbox/auth/") {
            let read = http::read_body_bounded(&mut stream, req.framing()?, body_prefix, 1024);
            let (body, leftover) = match tokio::time::timeout(Duration::from_secs(10), read).await {
                Ok(Ok(value)) => value,
                _ => { respond::send(&mut stream, 400, "Bad Request", &[], b"invalid login request", false).await?; return Ok(()); }
            };
            session::handle(&mut stream, &req, &cfg, peer.ip(), &body, ka).await?;
            carry = leftover;
            if !ka { return Ok(()); }
            continue;
        }
        let lease = if route.needs_auth() && session::cookie_allowed(&req) {
            session::cookie(&req).and_then(|raw| cfg.sessions.authorize(raw, cfg.token.load().as_str(), std::time::Instant::now()))
        } else { None };
        if route.needs_auth() && !local_ctl && lease.is_none()
            && !session::internal(&req, peer.ip(), cfg.token.load().as_str()) {
            respond::unauthorized(&mut stream, ka).await?;
            carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
            if !ka {
                return Ok(());
            }
            continue;
        }

        if lease.is_some() && router::active_get(req.path_only()) && req.method != "POST" {
            respond::send(&mut stream, 405, "Method Not Allowed", &[("Allow", "POST")], b"", ka).await?;
            carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
            if !ka { return Ok(()); }
            continue;
        }
        if router::active_get(req.path_only()) && req.method == "POST" {
            req.method = "GET".to_string();
        }
        match route {
            router::Route::ProxyClash => {

                let path = req.path_only();

                let _gate = match router::group_delay_target(&req.method, path) {
                    Some(g) => match gate::Gate::try_acquire(&cfg.group_delay_inflight, &g) {
                        Some(guard) => Some(guard),
                        None => {
                            tracing::info!(%peer, group = %g, "group delay already in flight");
                            respond::send(
                                &mut stream,
                                429,
                                "Too Many Requests",
                                &[("Content-Type", "application/json"), ("Retry-After", "2")],
                                br#"{"message":"group delay already in flight"}"#,
                                ka,
                            )
                            .await?;
                            carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
                            if !ka {
                                return Ok(());
                            }
                            continue;
                        }
                    },
                    None => None,
                };
                let is_conn = path == "/connections" || path.starts_with("/connections/");
                let is_delete = req.method.eq_ignore_ascii_case("DELETE");
                let self_serve = is_conn && !(cfg.clash_enabled && is_delete);
                if let Some(src) = cfg.connections.as_ref().filter(|_| self_serve) {

                    match session::guard(&cfg, lease.as_ref(), connections::handle(&mut stream, &req, path, src, ka)).await? {
                        connections::Outcome::KeepAlive => {
                            carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
                            continue;
                        }
                        connections::Outcome::Close => return Ok(()),
                    }
                } else if !cfg.clash_enabled {

                    respond::send(
                        &mut stream,
                        503,
                        "Service Unavailable",
                        &[("Content-Type", "text/plain")],
                        b"clash not running (sniffbox outbound mode bypasses it)\n",
                        ka,
                    )
                    .await?;
                } else {

                    let (outcome, leftover) =
                        session::guard(&cfg, lease.as_ref(), proxy_unix::proxy(&mut stream, &req, &body_prefix, &cfg.clash_sock)).await?;
                    match outcome {
                        proxy_unix::ProxyOutcome::KeepAlive => {
                            carry = leftover;
                            continue;
                        }
                        proxy_unix::ProxyOutcome::Close => return Ok(()),
                    }
                }
            }
            router::Route::RedirectRoot => {
                respond::redirect(&mut stream, "/ui", ka).await?;
            }
            router::Route::Static => {
                let ui_root = cfg.webroot.join("ui");
                serve_static(&mut stream, &ui_root, &req, "/ui", ka).await?;
            }
            router::Route::DataFiles => {

                let root = cfg.webroot.join("data");
                serve_static(&mut stream, &root, &req, "/data", ka).await?;
            }
            router::Route::Blocked => {
                tracing::info!(%peer, method = %req.method, path = %req.path_only(), "blocked dangerous clash API");
                respond::forbidden(&mut stream, "blocked by sniffbox\n", ka).await?;
            }
            router::Route::GeoUpdate => {
                geo_update(&mut stream, &cfg, ka).await?;
            }
            router::Route::ConfigPatch => {

                let (body, leftover) = match http::read_body_bounded(
                    &mut stream,
                    req.framing()?,
                    body_prefix,
                    MAX_NATIVE_BODY,
                )
                .await
                {
                    Ok(v) => v,
                    Err(e) if e.kind() == io::ErrorKind::InvalidData => {
                        respond::send(
                            &mut stream,
                            413,
                            "Payload Too Large",
                            &[],
                            b"body too large\n",
                            false,
                        )
                        .await?;
                        return Ok(());
                    }
                    Err(e) => return Err(e),
                };
                if lease.as_ref().is_some_and(|lease| !cfg.sessions.valid(lease, cfg.token.load().as_str(), std::time::Instant::now())) {
                    respond::unauthorized(&mut stream, false).await?; return Ok(());
                }
                if !router::configs_patch_mode_only(&body) {
                    tracing::info!(%peer, path = %req.path_only(), "blocked clash PATCH /configs (only mode change allowed)");
                    respond::forbidden(&mut stream, "only mode change allowed\n", ka).await?;
                } else if !cfg.clash_enabled {
                    respond::send(
                        &mut stream,
                        503,
                        "Service Unavailable",
                        &[("Content-Type", "text/plain")],
                        b"clash not running (sniffbox outbound mode bypasses it)\n",
                        ka,
                    )
                    .await?;
                } else {

                    let (outcome, _) =
                        proxy_unix::proxy(&mut stream, &req, &body, &cfg.clash_sock).await?;
                    match outcome {
                        proxy_unix::ProxyOutcome::KeepAlive => {}
                        proxy_unix::ProxyOutcome::Close => return Ok(()),
                    }
                }
                carry = leftover;
                if !ka {
                    return Ok(());
                }
                continue;
            }
            router::Route::ConfigGet => {

                if let Some(src) = cfg.proxies.clone() {
                    let body = tokio::task::spawn_blocking(move || src.mode_json())
                        .await
                        .unwrap_or_else(|_| r#"{"mode":"rule"}"#.to_string());
                    respond::send(
                        &mut stream,
                        200,
                        "OK",
                        &[("Content-Type", "application/json")],
                        body.as_bytes(),
                        ka,
                    )
                    .await?;
                } else {

                    respond::send(
                        &mut stream,
                        503,
                        "Service Unavailable",
                        &[("Content-Type", "text/plain")],
                        b"clash not running (sniffbox outbound mode bypasses it)\n",
                        ka,
                    )
                    .await?;
                }
            }
            router::Route::Native => {
                let max_body = if req.path_only() == "/sniffbox/subscription/apply"
                    || req.path_only().starts_with("/sniffbox/settings/") {
                    crate::subscription::MAX_BODY
                } else {
                    MAX_NATIVE_BODY
                };
                let (body, leftover) = match http::read_body_bounded(
                    &mut stream,
                    req.framing()?,
                    body_prefix,
                    max_body,
                )
                .await
                {
                    Ok(v) => v,
                    Err(e) if e.kind() == io::ErrorKind::InvalidData => {
                        respond::send(
                            &mut stream,
                            413,
                            "Payload Too Large",
                            &[],
                            b"body too large\n",
                            false,
                        )
                        .await?;
                        return Ok(());
                    }
                    Err(e) => return Err(e),
                };

                if session::guard(&cfg, lease.as_ref(), native::handle(&mut stream, &req, &cfg, ka, &body, &shutdown)).await? {
                    carry = leftover;
                    if !ka {
                        return Ok(());
                    }
                    continue;
                }
                return Ok(());
            }
        }

        carry = consume_body(&mut stream, req.framing()?, body_prefix).await?;
        if !ka {
            return Ok(());
        }
    }
}

async fn geo_update(stream: &mut TcpStream, cfg: &ServerConfig, ka: bool) -> io::Result<()> {
    let Some(src) = cfg.geo.clone() else {
        return respond::not_found(stream, ka).await;
    };
    let json = src.update().await;
    respond::send(
        stream,
        200,
        "OK",
        &[("Content-Type", "application/json")],
        json.as_bytes(),
        ka,
    )
    .await
}

async fn serve_static(
    stream: &mut TcpStream,
    webroot: &Path,
    req: &http::ReqHead,
    prefix: &str,
    ka: bool,
) -> io::Result<()> {
    let Some(file_path) = static_files::resolve_prefixed(webroot, req.path_only(), prefix) else {
        return respond::forbidden(stream, "bad path\n", ka).await;
    };
    match tokio::fs::read(&file_path).await {
        Ok(bytes) => {
            let ct = static_files::content_type(&file_path);
            let cc = static_files::cache_control(&file_path);
            let accepts = req.headers.iter().filter(|(name, _)| name.eq_ignore_ascii_case("accept-encoding"))
                .map(|(_, value)| String::from_utf8_lossy(value)).collect::<Vec<_>>();
            let accepted = accepts.join(",");
            let accepted = if accepts.is_empty() { None } else { Some(accepted.as_str()) };
            let compressed = if prefix == "/ui" && static_files::prefer_gzip(accepted, true) == Some(true) && matches!(file_path.extension().and_then(|v| v.to_str()), Some("html" | "js" | "css")) {
                let mut name = file_path.as_os_str().to_owned(); name.push(".gz");
                tokio::fs::read(Path::new(&name)).await.ok()
            } else { None };
            match static_files::prefer_gzip(accepted, compressed.is_some()) {
                Some(gzip) => respond::encoded_file(stream, ct, cc, if gzip { compressed.as_deref().unwrap() } else { &bytes }, gzip, req.method == "HEAD", ka).await,
                None => respond::send(stream, 406, "Not Acceptable", &[("Vary", "Accept-Encoding")], b"", ka).await,
            }
        }
        Err(_) => respond::not_found(stream, ka).await,
    }
}

#[cfg(test)]
mod session_http_tests {
    use super::*;
    use tokio::io::AsyncWriteExt;
    fn config() -> Arc<ServerConfig> {
        Arc::new(ServerConfig {
            listen: "127.0.0.1:0".parse().unwrap(), token: crate::auth::token_handle(crate::auth::derive_secret("synthetic-password")),
            sessions: Arc::new(session::Sessions::default()), admin: crate::acl::admin_handle(AdminAcl::default()),
            clash_sock: PathBuf::from("/missing-fixture.sock"), clash_enabled: false, webroot: PathBuf::from("/missing-fixture-root"),
            screen_dev: PathBuf::from("/missing-fixture-screen"), screen_history: ScreenHistory::new(10),
            log_tx: None, traffic: None, connections: None, nodes: None, info: None, proxies: None,
            reload_cmd: None, reload_inflight: Arc::new(AtomicBool::new(false)), group_delay_inflight: Arc::new(std::sync::Mutex::new(Default::default())),
            probe: None, clash_control: None, geo: None, cloudflared: None, cloudflared_log: None,
        })
    }
    async fn exchange(cfg: Arc<ServerConfig>, wire: String, remote: bool) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap(); let address = listener.local_addr().unwrap();
        let serve = async {
            let (stream, mut peer) = listener.accept().await.unwrap();
            if remote { peer.set_ip("192.0.2.10".parse().unwrap()); }
            let (_tx, rx) = watch::channel(false);
            handle_conn(stream, peer, cfg, rx).await.unwrap();
        };
        let read = async {
            let mut stream = TcpStream::connect(address).await.unwrap();stream.write_all(wire.as_bytes()).await.unwrap();
            let mut bytes = vec![]; stream.read_to_end(&mut bytes).await.unwrap();String::from_utf8(bytes).unwrap()
        };
        let (_, result) = tokio::join!(serve, read); result
    }
    fn request(method: &str, path: &str, extra: &str, body: &str) -> String {
        format!("{method} {path} HTTP/1.1\r\nHost: gateway.test\r\nConnection: close\r\nContent-Length: {}\r\n{extra}\r\n{body}", body.len())
    }
    struct MutationCounter(std::sync::atomic::AtomicUsize);
    impl crate::traffic::TrafficSource for MutationCounter {
        fn snapshot_json(&self) -> Arc<String> { Arc::new("[]".to_string()) }
        fn totals(&self) -> (u64, u64) { (0, 0) }
        fn clear(&self) { self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }
    }
    impl crate::nodes::NodesSource for MutationCounter {
        fn nodes_json(&self) -> String { "[]".to_string() }
        fn clear(&self) { self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }
    }
    #[tokio::test]
    async fn http_cookie_mutations_require_post_and_same_origin_even_without_fetch_metadata() {
        let mut cfg = config();
        let count = Arc::new(MutationCounter(std::sync::atomic::AtomicUsize::new(0)));
        let inner = Arc::get_mut(&mut cfg).unwrap();
        inner.traffic = Some(count.clone()); inner.nodes = Some(count.clone());
        let proof = cfg.token.load().to_string();
        let raw = cfg.sessions.issue(&proof, None, std::time::Instant::now()).unwrap();
        let cookie = format!("Cookie: ppgw_session={raw}\r\n");
        for path in ["/sniffbox/traffic/clear", "/sniffbox/nodes/clear", "/sniffbox/reload"] {
            for method in ["GET", "HEAD"] {
                let response = exchange(cfg.clone(), request(method, path, &cookie, ""), true).await;
                assert!(response.starts_with("HTTP/1.1 405"), "{method} {path}: {response}");
            }
            for origin in ["", "Origin: http://gateway.test:9000\r\n", "Origin: https://evil.test\r\n"] {
                let response = exchange(cfg.clone(), request("POST", path, &format!("{cookie}{origin}"), ""), true).await;
                assert!(response.starts_with("HTTP/1.1 401"));
            }
        }
        assert_eq!(count.0.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert!(!cfg.reload_inflight.load(std::sync::atomic::Ordering::SeqCst));
        for path in ["/sniffbox/traffic/clear", "/sniffbox/nodes/clear"] {
            let response = exchange(cfg.clone(), request("POST", path,
                &format!("{cookie}Origin: http://gateway.test\r\n"), ""), true).await;
            assert!(response.starts_with("HTTP/1.1 200"));
            let response = exchange(cfg.clone(), request("POST", path,
                &format!("Authorization: Bearer {proof}\r\n"), ""), false).await;
            assert!(response.starts_with("HTTP/1.1 200"));
        }
        assert_eq!(count.0.load(std::sync::atomic::Ordering::SeqCst), 4);
        for path in ["/proxies/A/delay", "/group/A/delay", "/providers/proxies/A/healthcheck", "/providers/proxies/A/B/healthcheck"] {
            let response = exchange(cfg.clone(), request("GET", path, &cookie, ""), true).await;
            assert!(response.starts_with("HTTP/1.1 401"));
            let response = exchange(cfg.clone(), request("GET", path, &format!("{cookie}Origin: http://gateway.test\r\n"), ""), true).await;
            assert!(response.starts_with("HTTP/1.1 405"));
            let response = exchange(cfg.clone(), request("POST", path, &format!("{cookie}Origin: http://gateway.test\r\n"), ""), true).await;
            assert!(response.starts_with("HTTP/1.1 503"));
            let response = exchange(cfg.clone(), request("GET", path, &format!("Authorization: Bearer {proof}\r\n"), ""), false).await;
            assert!(response.starts_with("HTTP/1.1 503"));
        }
    }

    #[tokio::test]
    async fn companion_listener_binds_and_serves_local_bearer_then_releases_port() {
        for public in ["192.0.2.10:0", "[fd00::10]:0"] {
            let private = bind_internal(public.parse().unwrap()).await.unwrap().unwrap();
            let address = private.local_addr().unwrap();
            let cfg = config(); let proof = cfg.token.load().to_string();
            let served = cfg.clone();
            let task = tokio::spawn(async move {
                let (stream, peer) = private.accept().await.unwrap();
                assert!(peer.ip().is_loopback());
                let (_tx, rx) = watch::channel(false);
                handle_conn(stream, peer, served, rx).await.unwrap();
            });
            let mut client = TcpStream::connect(address).await.unwrap();
            client.write_all(request("GET", "/configs", &format!("Authorization: Bearer {proof}\r\n"), "").as_bytes()).await.unwrap();
            let mut response = Vec::new(); client.read_to_end(&mut response).await.unwrap();
            assert!(response.starts_with(b"HTTP/1.1 503"));
            task.await.unwrap(); drop(client);
            let rebound = TcpListener::bind(address).await.unwrap();
            let mut specified: SocketAddr = public.parse().unwrap(); specified.set_port(address.port());
            assert!(bind_internal(specified).await.is_err(), "occupied private channel must fail startup");
            drop(rebound);
        }
    }

    #[tokio::test]
    async fn shared_server_shutdown_releases_the_listener() {
        let cfg = config();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (tx, rx) = watch::channel(false);
        let task = tokio::spawn(run(listener, cfg, rx));
        tx.send(true).unwrap();
        tokio::time::timeout(Duration::from_secs(2), task).await.unwrap().unwrap().unwrap();
        let _rebound = TcpListener::bind(address).await.unwrap();
    }

    #[test]
    fn private_listener_address_contract() {
        for (public, private) in [
            ("192.0.2.10:18080", Some("127.0.0.1:18080")),
            ("[fd00::10]:18080", Some("[::1]:18080")),
            ("0.0.0.0:18080", None), ("[::]:18080", None),
            ("127.0.0.2:18080", None), ("[::1]:18080", None),
        ] {
            assert_eq!(internal_address(public.parse().unwrap()).map(|a| a.to_string()), private.map(str::to_owned));
        }
    }

    #[tokio::test]
    async fn remote_login_cookie_logout_and_permanent_bearer_boundary() {
        let cfg = config(); let proof = crate::auth::derive_secret("synthetic-password");
        let bearer = format!("Authorization: Bearer {proof}\r\n");
        let denied = exchange(cfg.clone(), request("GET", "/configs", &bearer, ""), true).await;
        assert!(denied.starts_with("HTTP/1.1 401"));
        let local = exchange(cfg.clone(), request("GET", "/configs", &bearer, ""), false).await;
        assert!(local.starts_with("HTTP/1.1 503"), "local bearer reaches the authenticated route");
        let payload = serde_json::json!({"proof":proof}).to_string();
        let denied = exchange(cfg.clone(), request("POST", "/sniffbox/auth/login", "Origin: https://evil.test\r\n", &payload), true).await;
        assert!(denied.starts_with("HTTP/1.1 403"));
        let login = exchange(cfg.clone(), request("POST", "/sniffbox/auth/login", "Origin: https://gateway.test\r\n", &payload), true).await;
        assert!(login.starts_with("HTTP/1.1 200")); assert!(login.contains("HttpOnly; SameSite=Strict")); assert!(login.contains("; Secure\r\n"));
        let cookie = login.lines().find_map(|line| line.strip_prefix("Set-Cookie: ")).unwrap().split(';').next().unwrap();
        assert!(!cookie.contains(&proof));
        let header = format!("Cookie: {cookie}\r\n");
        let status = exchange(cfg.clone(), request("GET", "/sniffbox/auth/session", &header, ""), true).await;
        assert!(status.contains("\"authenticated\":true")); assert!(!status.contains(cookie)); assert!(!status.contains(&proof));
        let protected = exchange(cfg.clone(), request("GET", "/configs", &header, ""), true).await;
        assert!(protected.starts_with("HTTP/1.1 503"));
        let csrf = exchange(cfg.clone(), request("PATCH", "/configs", &header, "{}"), true).await;
        assert!(csrf.starts_with("HTTP/1.1 401"));
        let logout = exchange(cfg.clone(), request("POST", "/sniffbox/auth/logout", &format!("{header}Origin: https://gateway.test\r\n"), "{}"), true).await;
        assert!(logout.contains("Max-Age=0"));
        let denied = exchange(cfg.clone(), request("GET", "/configs", &header, ""), true).await;
        assert!(denied.starts_with("HTTP/1.1 401"));
        for _ in 0..9 { let _ = exchange(cfg.clone(), request("POST", "/sniffbox/auth/login", "Origin: http://gateway.test\r\n", "{}"), true).await; }
        let limited = exchange(cfg, request("POST", "/sniffbox/auth/login", "Origin: http://gateway.test\r\n", &payload), true).await;
        assert!(limited.starts_with("HTTP/1.1 429")); assert!(limited.contains("Retry-After: 60"));
    }
    #[tokio::test]
    async fn real_sse_socket_closes_on_logout_password_rotation_and_expiry() {
        for kind in ["logout", "password", "expiry"] {
            let mut cfg = config();
            if kind == "expiry" {
                Arc::get_mut(&mut cfg).unwrap().sessions = Arc::new(session::Sessions::with_limits(Duration::from_millis(250), Duration::from_millis(250)));
            }
            let proof = cfg.token.load().to_string();
            let raw = cfg.sessions.issue(&proof, None, std::time::Instant::now()).unwrap();
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap(); let served = cfg.clone();
            let task = tokio::spawn(async move {
                let (stream, mut peer) = listener.accept().await.unwrap(); peer.set_ip("192.0.2.10".parse().unwrap());
                let (_tx, rx) = watch::channel(false); handle_conn(stream, peer, served, rx).await
            });
            let mut client = TcpStream::connect(address).await.unwrap();
            client.write_all(request("GET", "/sniffbox/screen/sse", &format!("Cookie: ppgw_session={raw}\r\n"), "").as_bytes()).await.unwrap();
            let mut head = Vec::new();
            tokio::time::timeout(Duration::from_secs(2), async {
                while !head.ends_with(b"\r\n\r\n") { head.push(client.read_u8().await.unwrap()); }
            }).await.unwrap();
            assert!(head.starts_with(b"HTTP/1.1 200"));
            if kind == "logout" {
                let response = exchange(cfg.clone(), request("POST", "/sniffbox/auth/logout", &format!("Cookie: ppgw_session={raw}\r\nOrigin: http://gateway.test\r\n"), "{}"), true).await;
                assert!(response.starts_with("HTTP/1.1 200"));
            } else if kind == "password" { cfg.token.store(Arc::new(crate::auth::derive_secret("changed-password"))); }
            let mut remaining = Vec::new();
            tokio::time::timeout(Duration::from_secs(3), client.read_to_end(&mut remaining)).await.unwrap().unwrap();
            assert_eq!(task.await.unwrap().unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        }
    }
    #[tokio::test]
    async fn revocation_closes_pending_streams_and_password_change_invalidates_cookie() {
        let cfg = config(); let proof = cfg.token.load().to_string();
        let raw = cfg.sessions.issue(&proof, None, std::time::Instant::now()).unwrap();
        let lease = cfg.sessions.authorize(&raw, &proof, std::time::Instant::now()).unwrap();
        let work = session::guard(&cfg, Some(&lease), std::future::pending::<io::Result<()>>());
        let cancel = async { tokio::task::yield_now().await; cfg.sessions.revoke(&raw); };
        let (result, _) = tokio::time::timeout(Duration::from_secs(2), async { tokio::join!(work, cancel) }).await.unwrap();
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        let raw = cfg.sessions.issue(&proof, None, std::time::Instant::now()).unwrap();
        cfg.token.store(Arc::new(crate::auth::derive_secret("changed-password")));
        let denied = exchange(cfg, request("GET", "/configs", &format!("Cookie: ppgw_session={raw}\r\n"), ""), true).await;
        assert!(denied.starts_with("HTTP/1.1 401"));
    }
}

#[cfg(test)]
mod static_response_tests {
    use super::*;

    async fn exchange(method: &str, encoding: Option<&str>, with_gzip: bool) -> Vec<u8> {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("app.js"), b"fixture-raw").unwrap();
        if with_gzip { std::fs::write(root.path().join("app.js.gz"), b"fixture-gzip").unwrap(); }
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let req = http::ReqHead { method: method.into(), path: "/ui/app.js".into(), http11: true,
            headers: encoding.map(|v| vec![("Accept-Encoding".into(), v.as_bytes().to_vec())]).unwrap_or_default() };
        let serve = async { let (mut stream, _) = listener.accept().await.unwrap(); serve_static(&mut stream, root.path(), &req, "/ui", false).await.unwrap(); };
        let read = async { let mut stream = TcpStream::connect(address).await.unwrap(); let mut bytes = Vec::new(); stream.read_to_end(&mut bytes).await.unwrap(); bytes };
        let (_, bytes) = tokio::join!(serve, read); bytes
    }

    #[tokio::test]
    async fn static_http_negotiates_encoding_and_preserves_head_metadata() {
        let wire = String::from_utf8(exchange("GET", Some("gzip"), true).await).unwrap();
        assert!(wire.contains("Content-Encoding: gzip\r\n"));
        assert!(wire.contains("Content-Type: application/javascript; charset=utf-8\r\n"));
        assert!(wire.contains("Content-Length: 12\r\n"));
        assert!(wire.ends_with("\r\n\r\nfixture-gzip"));
        let head = String::from_utf8(exchange("HEAD", Some("gzip"), true).await).unwrap();
        assert!(head.contains("Content-Length: 12\r\n")); assert!(head.ends_with("\r\n\r\n"));
        for encoding in [None, Some("gzip;q=0,*;q=1"), Some("gzip;q=0.5,identity;q=1")] {
            let raw = String::from_utf8(exchange("GET", encoding, true).await).unwrap();
            assert!(!raw.contains("Content-Encoding:")); assert!(raw.contains("Vary: Accept-Encoding\r\n"));
            assert!(raw.ends_with("\r\n\r\nfixture-raw"));
        }
        for (encoding, available) in [("*;q=0", true), ("gzip,identity;q=0", false)] {
            let wire = String::from_utf8(exchange("GET", Some(encoding), available).await).unwrap();
            assert!(wire.starts_with("HTTP/1.1 406 "));
        }
    }
}

async fn consume_body(
    stream: &mut TcpStream,
    framing: http::Framing,
    prefix: Vec<u8>,
) -> io::Result<Vec<u8>> {
    use http::Framing;
    match framing {
        Framing::None => Ok(prefix),
        Framing::Length(n) => {
            if n > MAX_DRAIN_BODY {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "declared body too large to drain",
                ));
            }
            let n = n as usize;
            if prefix.len() >= n {
                Ok(prefix[n..].to_vec())
            } else {
                let mut need = n - prefix.len();
                let mut sink = [0u8; 8192];
                while need > 0 {
                    let r = stream.read(&mut sink[..need.min(8192)]).await?;
                    if r == 0 {
                        return Err(io::Error::new(
                            io::ErrorKind::UnexpectedEof,
                            "eof draining body",
                        ));
                    }
                    need -= r;
                }
                Ok(Vec::new())
            }
        }
        Framing::Chunked => {

            let mut sink = tokio::io::sink();
            let mut bounded = AsyncReadExt::take(stream, MAX_DRAIN_BODY);
            http::relay_body(&mut bounded, &mut sink, Framing::Chunked, &prefix).await
        }
        Framing::Eof => Ok(Vec::new()),
    }
}
