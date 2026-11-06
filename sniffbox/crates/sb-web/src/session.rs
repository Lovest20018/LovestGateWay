//! Revocable browser sessions. The password-derived bearer is never a remote session.
use crate::{auth, http::ReqHead};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, VecDeque};
use std::io::{self, Read};
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::sync::watch;

pub const COOKIE: &str = "ppgw_session";
pub const ABSOLUTE_SECONDS: u64 = 12 * 60 * 60;
const IDLE_SECONDS: u64 = 30 * 60;
const MAX_SESSIONS: usize = 128;
const MAX_SOURCES: usize = 1024;

struct Session {
    created: Instant,
    used: Instant,
    revoke: watch::Sender<bool>,
}
#[derive(Default)]
struct State {
    credential: String,
    sessions: HashMap<String, Session>,
    attempts: HashMap<IpAddr, VecDeque<Instant>>,
    global_attempts: VecDeque<Instant>,
}
pub struct Sessions {
    state: Mutex<State>,
    absolute: Duration,
    idle: Duration,
}
pub struct Lease {
    key: String,
    pub context: String,
    pub remaining: u64,
    pub revoked: watch::Receiver<bool>,
}

impl Default for Sessions {
    fn default() -> Self {
        Self { state: Mutex::new(State::default()), absolute: Duration::from_secs(ABSOLUTE_SECONDS), idle: Duration::from_secs(IDLE_SECONDS) }
    }
}
fn digest(value: &str) -> String { hex::encode(Sha256::digest(value.as_bytes())) }
impl Sessions {
    #[cfg(test)]
    pub(crate) fn with_limits(absolute: Duration, idle: Duration) -> Self {
        Self { absolute, idle, ..Default::default() }
    }
    fn fresh(&self, session: &Session, now: Instant) -> bool {
        now.duration_since(session.created) < self.absolute && now.duration_since(session.used) < self.idle
    }
    fn synchronize(&self, state: &mut State, credential: &str, now: Instant) {
        if state.credential != credential {
            for session in state.sessions.values() { let _ = session.revoke.send(true); }
            state.sessions.clear();
            state.credential = credential.to_owned();
        }
        state.sessions.retain(|_, session| {
            let keep = self.fresh(session, now);
            if !keep { let _ = session.revoke.send(true); }
            keep
        });
    }
    // Every login attempt counts, including successful requests. Memory is bounded.
    pub fn attempt(&self, ip: IpAddr, now: Instant) -> bool {
        let mut state = self.state.lock().unwrap();
        let recent = |time: &Instant| now.duration_since(*time) < Duration::from_secs(60);
        state.global_attempts.retain(recent);
        state.attempts.retain(|_, rows| { rows.retain(recent); !rows.is_empty() });
        if state.global_attempts.len() >= 100 { return false; }
        if !state.attempts.contains_key(&ip) && state.attempts.len() >= MAX_SOURCES { return false; }
        let rows = state.attempts.entry(ip).or_default();
        if rows.len() >= 10 { return false; }
        rows.push_back(now);
        state.global_attempts.push_back(now);
        true
    }
    pub fn issue(&self, credential: &str, old: Option<&str>, now: Instant) -> io::Result<String> {
        let mut state = self.state.lock().unwrap();
        self.synchronize(&mut state, credential, now);
        if let Some(old) = old {
            if let Some(session) = state.sessions.remove(&digest(old)) { let _ = session.revoke.send(true); }
        }
        if state.sessions.len() >= MAX_SESSIONS { return Err(io::Error::other("session capacity")); }
        let mut bytes = [0u8; 32];
        std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
        let raw = hex::encode(bytes);
        let (revoke, _) = watch::channel(false);
        state.sessions.insert(digest(&raw), Session { created: now, used: now, revoke });
        Ok(raw)
    }
    pub fn authorize(&self, raw: &str, credential: &str, now: Instant) -> Option<Lease> {
        let mut state = self.state.lock().unwrap();
        self.synchronize(&mut state, credential, now);
        let key = digest(raw);
        let session = state.sessions.get_mut(&key)?;
        session.used = now;
        Some(Lease { context: key[..24].into(), key, remaining: (self.absolute - now.duration_since(session.created)).as_secs(), revoked: session.revoke.subscribe() })
    }
    pub fn valid(&self, lease: &Lease, credential: &str, now: Instant) -> bool {
        let mut state = self.state.lock().unwrap();
        self.synchronize(&mut state, credential, now);
        state.sessions.contains_key(&lease.key)
    }
    pub fn revoke(&self, raw: &str) {
        if let Some(session) = self.state.lock().unwrap().sessions.remove(&digest(raw)) { let _ = session.revoke.send(true); }
    }
    pub fn revoke_all(&self) {
        let mut state = self.state.lock().unwrap();
        for session in state.sessions.values() { let _ = session.revoke.send(true); }
        state.sessions.clear();
    }
}

// Reject duplicate values rather than accepting the first of two authorities.
fn one<'a>(req: &'a ReqHead, name: &str) -> Option<&'a str> {
    let mut rows = req.headers.iter().filter(|(key, _)| key.eq_ignore_ascii_case(name));
    let (_, value) = rows.next()?;
    if rows.next().is_some() { return None; }
    std::str::from_utf8(value).ok()
}
pub fn same_origin(req: &ReqHead) -> bool {
    let (Some(origin), Some(host)) = (one(req, "origin"), one(req, "host")) else { return false; };
    origin.eq_ignore_ascii_case(&format!("http://{host}")) || origin.eq_ignore_ascii_case(&format!("https://{host}"))
}
pub fn cookie_allowed(req: &ReqHead) -> bool {
    if one(req, "host").is_none() { return false; }
    if req.headers.iter().filter(|(key, _)| key.eq_ignore_ascii_case("sec-fetch-site")).count() > 1 { return false; }
    if one(req, "sec-fetch-site").is_some_and(|value| value != "same-origin" && value != "none") { return false; }
    if req.headers.iter().any(|(key, _)| key.eq_ignore_ascii_case("origin")) && !same_origin(req) { return false; }
    (matches!(req.method.as_str(), "GET" | "HEAD") && !crate::router::active_get(req.path_only())) || same_origin(req)
}
pub fn cookie(req: &ReqHead) -> Option<&str> {
    let cookies = one(req, "cookie")?;
    let mut found = None;
    for entry in cookies.split(';') {
        let Some((name, value)) = entry.trim().split_once('=') else { continue; };
        if name == COOKIE {
            if found.is_some() || value.len() != 64 || !value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) { return None; }
            found = Some(value);
        }
    }
    found
}
pub fn cookie_header(req: &ReqHead, raw: &str, max_age: u64) -> String {
    let secure = one(req, "origin").is_some_and(|origin| origin.starts_with("https://"));
    format!("{COOKIE}={raw}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{}", if secure { "; Secure" } else { "" })
}
pub fn internal(req: &ReqHead, ip: IpAddr, expected: &str) -> bool {
    ip.is_loopback() && !req.headers.iter().any(|(key, _)| ["origin", "sec-fetch-site", "forwarded", "x-forwarded-for", "x-real-ip"].iter().any(|name| key.eq_ignore_ascii_case(name)))
        && req.headers.iter().filter(|(key, _)| key.eq_ignore_ascii_case("authorization")).count() == 1
        && auth::check_bearer(req.header("authorization"), expected)
}

// Dropping the in-flight socket future closes SSE/WebSocket streams. No detached task
// outlives a connection; password rotation is checked even if no requests arrive.
pub async fn guard<T>(cfg: &crate::server::ServerConfig, lease: Option<&Lease>, work: impl std::future::Future<Output = io::Result<T>>) -> io::Result<T> {
    let Some(lease) = lease else { return work.await; };
    if !cfg.sessions.valid(lease, cfg.token.load().as_str(), Instant::now()) { return Err(io::Error::new(io::ErrorKind::PermissionDenied, "session expired")); }
    let mut revoked = lease.revoked.clone();
    tokio::pin!(work);
    loop {
        tokio::select! {
            value = &mut work => return value,
            _ = revoked.changed() => return Err(io::Error::new(io::ErrorKind::PermissionDenied, "session revoked")),
            _ = tokio::time::sleep(Duration::from_secs(1)) => {
                if !cfg.sessions.valid(lease, cfg.token.load().as_str(), Instant::now()) { return Err(io::Error::new(io::ErrorKind::PermissionDenied, "session expired")); }
            }
        }
    }
}

pub async fn handle(stream: &mut tokio::net::TcpStream, req: &ReqHead, cfg: &crate::server::ServerConfig, ip: IpAddr, body: &[u8], ka: bool) -> io::Result<()> {
    use crate::respond;
    let mut set_cookie = None;
    let path = req.path_only();
    let (status, reason, json) = match (req.method.as_str(), path) {
        ("GET", "/sniffbox/auth/session") if cookie_allowed(req) => {
            let lease = cookie(req).and_then(|raw| cfg.sessions.authorize(raw, cfg.token.load().as_str(), Instant::now()));
            (200, "OK", match lease {
                Some(lease) => serde_json::json!({"authenticated":true,"context":lease.context,"expires_in":lease.remaining}),
                None => serde_json::json!({"authenticated":false}),
            })
        }
        ("POST", "/sniffbox/auth/login") if same_origin(req) && cookie_allowed(req) => {
            if !cfg.sessions.attempt(ip, Instant::now()) {
                return respond::send(stream, 429, "Too Many Requests", &[("Cache-Control", "no-store"), ("Retry-After", "60"), ("Content-Type", "application/json")], br#"{"error":"LOGIN_RATE_LIMITED"}"#, ka).await;
            }
            let parsed = serde_json::from_slice::<serde_json::Value>(body).ok();
            let proof = parsed.as_ref().filter(|v| v.as_object().is_some_and(|v| v.len() == 1)).and_then(|v| v.get("proof")).and_then(|v| v.as_str());
            let credential = cfg.token.load();
            if proof.is_some_and(|proof| proof.len() == 64 && auth::check_bearer(Some(format!("Bearer {proof}").as_bytes()), credential.as_str())) {
                match cfg.sessions.issue(credential.as_str(), cookie(req), Instant::now()) {
                    Ok(raw) => { set_cookie = Some(cookie_header(req, &raw, ABSOLUTE_SECONDS)); (200, "OK", serde_json::json!({"authenticated":true})) },
                    Err(_) => (503, "Service Unavailable", serde_json::json!({"error":"SESSION_UNAVAILABLE"})),
                }
            } else { (401, "Unauthorized", serde_json::json!({"error":"LOGIN_REQUIRED"})) }
        }
        ("POST", "/sniffbox/auth/logout") if same_origin(req) && cookie_allowed(req) => {
            if let Some(raw) = cookie(req) { cfg.sessions.revoke(raw); }
            set_cookie = Some(cookie_header(req, "", 0));
            (200, "OK", serde_json::json!({"authenticated":false}))
        }
        (_, "/sniffbox/auth/login" | "/sniffbox/auth/logout" | "/sniffbox/auth/session") => (403, "Forbidden", serde_json::json!({"error":"ORIGIN_REJECTED"})),
        _ => (404, "Not Found", serde_json::json!({"error":"NOT_FOUND"})),
    };
    let mut headers = vec![("Content-Type", "application/json"), ("Cache-Control", "no-store")];
    if let Some(cookie) = set_cookie.as_deref() { headers.push(("Set-Cookie", cookie)); }
    respond::send(stream, status, reason, &headers, json.to_string().as_bytes(), ka).await
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request(method: &str, headers: &[(&str, &str)]) -> ReqHead {
        ReqHead { method: method.into(), path: "/sniffbox/auth/login".into(), http11: true,
            headers: headers.iter().map(|(key, value)| ((*key).into(), value.as_bytes().to_vec())).collect() }
    }
    #[test]
    fn real_random_sessions_expire_revoke_and_change_password() {
        let sessions = Sessions { absolute: Duration::from_secs(12), idle: Duration::from_secs(3), ..Default::default() };
        let now = Instant::now();
        let first = sessions.issue("password-proof", None, now).unwrap();
        let second = sessions.issue("password-proof", None, now).unwrap();
        assert_eq!(first.len(), 64); assert_ne!(first, second);
        let first_lease = sessions.authorize(&first, "password-proof", now).unwrap();
        sessions.revoke(&first);
        assert!(*first_lease.revoked.borrow()); assert!(!sessions.valid(&first_lease, "password-proof", now));
        assert!(sessions.authorize(&second, "password-proof", now + Duration::from_secs(2)).is_some());
        assert!(sessions.authorize(&second, "password-proof", now + Duration::from_secs(5)).is_none());
        let third = sessions.issue("password-proof", None, now).unwrap();
        let lease = sessions.authorize(&third, "password-proof", now).unwrap();
        assert!(!sessions.valid(&lease, "changed-proof", now)); assert!(*lease.revoked.borrow());
        assert!(Sessions::default().authorize(&third, "password-proof", now).is_none());
        let raw = sessions.issue("changed-proof", None, now).unwrap();
        for step in [2, 4, 6, 8, 10] { assert!(sessions.authorize(&raw, "changed-proof", now + Duration::from_secs(step)).is_some()); }
        assert!(sessions.authorize(&raw, "changed-proof", now + Duration::from_secs(12)).is_none());
    }
    #[test]
    fn limits_are_bounded_and_do_not_evict_existing_sessions() {
        let sessions = Sessions::default(); let now = Instant::now(); let ip = "192.0.2.1".parse().unwrap();
        for _ in 0..10 { assert!(sessions.attempt(ip, now)); }
        assert!(!sessions.attempt(ip, now)); assert!(sessions.attempt(ip, now + Duration::from_secs(60)));
        let first = sessions.issue("proof", None, now).unwrap();
        for _ in 1..MAX_SESSIONS { sessions.issue("proof", None, now).unwrap(); }
        assert!(sessions.issue("proof", None, now).is_err());
        assert!(sessions.authorize(&first, "proof", now).is_some());
        for value in 1..=100 { let _ = sessions.attempt(format!("192.0.2.{value}").parse().unwrap(), now); }
        assert!(!sessions.attempt("198.51.100.1".parse().unwrap(), now));
    }
    #[test]
    fn cookie_csrf_and_internal_boundaries_are_strict() {
        let raw = "a".repeat(64);
        let base = [("Host", "gateway.test"), ("Origin", "https://gateway.test")];
        let req = request("POST", &base);
        assert!(same_origin(&req)); assert!(cookie_allowed(&req));
        assert!(cookie_header(&req, &raw, 1).contains("HttpOnly; SameSite=Strict; Max-Age=1; Secure"));
        for headers in [vec![("Host", "gateway.test"), ("Origin", "https://evil.test")], vec![("Host", "gateway.test")], vec![("Host", "gateway.test"), ("Origin", "https://gateway.test"), ("Origin", "https://gateway.test")]] { assert!(!cookie_allowed(&request("POST", &headers))); }
        assert!(!cookie_allowed(&request("GET", &[("Sec-Fetch-Site", "cross-site")])));
        let header = format!("ppgw_session={raw}; ppgw_session={raw}");
        assert!(cookie(&request("GET", &[("Cookie", &header)])).is_none());
        let header = format!("ppgw_session={raw}"); let req = request("GET", &[("Cookie", &header)]);
        assert_eq!(cookie(&req), Some(raw.as_str()));
        let req = request("GET", &[("Authorization", "Bearer proof")]);
        assert!(internal(&req, "127.0.0.1".parse().unwrap(), "proof"));
        assert!(!internal(&req, "192.0.2.1".parse().unwrap(), "proof"));
        assert!(!internal(&request("GET", &[("Authorization", "Bearer proof"), ("Origin", "http://localhost")]), "127.0.0.1".parse().unwrap(), "proof"));
        assert!(!internal(&request("GET", &[("Authorization", "Bearer proof"), ("X-Forwarded-For", "192.0.2.1")]), "127.0.0.1".parse().unwrap(), "proof"));
    }
}
