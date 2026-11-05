//! Fixed loopback bridge for the Debian administrator UI, after native bearer/ACL checks.
use crate::{http, respond};
use std::io;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

pub(crate) const MAX_BODY: usize = 1_100_000;
const MAX_RESPONSE: usize = 2 * 1024 * 1024;

pub(crate) fn origin_allowed(req: &http::ReqHead) -> bool {
    if req.method != "POST" { return true; }
    let origins: Vec<_> = req.headers.iter().filter(|(name, _)| name.eq_ignore_ascii_case("origin")).collect();
    // Bearer-authenticated command-line clients do not send Origin. Browsers
    // that do send it must match the public authority before loopback rewrite.
    if origins.is_empty() { return true; }
    let hosts: Vec<_> = req.headers.iter().filter(|(name, _)| name.eq_ignore_ascii_case("host")).collect();
    if origins.len() != 1 || hosts.len() != 1 { return false; }
    let (Ok(origin), Ok(host)) = (std::str::from_utf8(&origins[0].1), std::str::from_utf8(&hosts[0].1)) else {
        return false;
    };
    origin.eq_ignore_ascii_case(&format!("http://{host}")) || origin.eq_ignore_ascii_case(&format!("https://{host}"))
}

fn target(method: &str, path: &str) -> Option<&'static str> {
    match (method, path) {
        ("GET", "/sniffbox/subscription/status") => Some("/api/status"),
        ("GET", "/sniffbox/subscription/providers") => Some("/api/providers"),
        ("GET", "/sniffbox/subscription/config") => Some("/api/config"),
        ("POST", "/sniffbox/subscription/apply") => Some("/api/subscription"),
        ("GET", "/sniffbox/subscription/maintenance") => Some("/api/maintenance"),
        ("POST", "/sniffbox/subscription/core/check") => Some("/api/core/check"),
        ("POST", "/sniffbox/subscription/core/update") => Some("/api/core/update"),
        ("POST", "/sniffbox/subscription/core/rollback") => Some("/api/core/rollback"),
        ("POST", "/sniffbox/subscription/policy/reset") => Some("/api/policy/reset"),
        _ => None,
    }
}

async fn request(method: &str, target: &str, token: &str, body: &[u8], port: u16) -> io::Result<Vec<u8>> {
    let mut upstream = TcpStream::connect(("127.0.0.1", port)).await?;
    let head = format!(
        "{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    upstream.write_all(head.as_bytes()).await?;
    upstream.write_all(body).await?;
    let mut raw = Vec::new();
    upstream.take((MAX_RESPONSE + 1) as u64).read_to_end(&mut raw).await?;
    if raw.len() > MAX_RESPONSE {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "admin response too large"));
    }
    Ok(raw)
}

pub(crate) async fn handle(
    stream: &mut TcpStream,
    req: &http::ReqHead,
    token: &str,
    ka: bool,
    body: &[u8],
) -> io::Result<()> {
    if !origin_allowed(req) {
        return respond::send(stream, 403, "Forbidden",
            &[("Content-Type", "application/json"), ("Cache-Control", "no-store")],
            br#"{"error":"ORIGIN_REJECTED"}"#, ka).await;
    }
    let Some(path) = target(&req.method, &req.path) else {
        return respond::send(stream, 405, "Method Not Allowed", &[], b"", ka).await;
    };
    forward(stream, &req.method, path, token, ka, body, 8789).await
}

pub(crate) async fn forward(
    stream: &mut TcpStream, method: &str, path: &str, token: &str,
    ka: bool, body: &[u8], port: u16,
) -> io::Result<()> {
    let result = tokio::time::timeout(Duration::from_secs(12), request(method, path, token, body, port)).await;
    if let Ok(Ok(raw)) = result {
        if let Some(end) = raw.windows(4).position(|v| v == b"\r\n\r\n").map(|v| v + 4) {
            if let Ok(head) = http::parse_response(&raw[..end]) {
                let body = &raw[end..];
                if matches!(head.framing(false), Ok(http::Framing::Length(n)) if n == body.len() as u64)
                    && serde_json::from_slice::<serde_json::Value>(body).is_ok()
                {
                    return respond::send(stream, head.status, &head.reason,
                        &[("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")],
                        body, ka).await;
                }
            }
        }
    }
    respond::send(stream, 503, "Service Unavailable",
        &[("Content-Type", "application/json"), ("Cache-Control", "no-store")],
        br#"{"error":"Subscription service is unavailable"}"#, ka).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_post_origin_is_checked_before_loopback_rewrite() {
        let request = |headers: &str| http::parse_request(format!(
            "POST /sniffbox/subscription/apply HTTP/1.1\r\n{headers}\r\n").as_bytes()).unwrap();
        assert!(origin_allowed(&request("Host: gateway.test:8080\r\n")));
        for origin in ["http://gateway.test:8080", "https://GATEWAY.test:8080"] {
            assert!(origin_allowed(&request(&format!("Host: gateway.test:8080\r\nOrigin: {origin}\r\n"))));
        }
        assert!(origin_allowed(&request("Host: [::1]:8080\r\nOrigin: http://[::1]:8080\r\n")));
        for origin in ["https://foreign.invalid", "null", "http://gateway.test:8081", "http://gateway.test:8080/"] {
            assert!(!origin_allowed(&request(&format!("Host: gateway.test:8080\r\nOrigin: {origin}\r\n"))));
        }
        assert!(!origin_allowed(&request("Host: gateway.test\r\nOrigin: http://gateway.test\r\nOrigin: http://gateway.test\r\n")));
        assert!(!origin_allowed(&request("Host: gateway.test\r\nHost: foreign.invalid\r\nOrigin: http://gateway.test\r\n")));
        assert!(!origin_allowed(&request("Origin: http://gateway.test\r\n")));
    }

    #[test]
    fn only_fixed_admin_operations_are_forwarded() {
        assert_eq!(target("GET", "/sniffbox/subscription/status"), Some("/api/status"));
        assert_eq!(target("GET", "/sniffbox/subscription/providers"), Some("/api/providers"));
        assert!(target("POST", "/sniffbox/subscription/providers").is_none());
        assert!(target("GET", "/sniffbox/subscription/providers?config=true").is_none());
        assert_eq!(target("GET", "/sniffbox/subscription/config"), Some("/api/config"));
        assert_eq!(target("POST", "/sniffbox/subscription/apply"), Some("/api/subscription"));
        assert!(target("GET", "/sniffbox/subscription/apply").is_none());
        assert!(target("POST", "/sniffbox/subscription/status").is_none());
        assert!(target("GET", "/sniffbox/subscription/status?url=http://example.com").is_none());
        assert!(target("POST", "/api/login").is_none());
    }
}
