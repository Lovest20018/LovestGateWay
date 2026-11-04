//! Fixed authenticated routes for the independent Debian settings worker.
use crate::{http, respond, subscription};
use std::io;
use tokio::net::TcpStream;

fn target(method: &str, path: &str) -> Option<String> {
    let fixed = match (method, path) {
        ("GET", "/sniffbox/settings/config") => Some("/api/config"),
        ("GET", "/sniffbox/settings/policy") => Some("/api/policy"),
        ("POST", "/sniffbox/settings/preview") => Some("/api/preview"),
        ("POST", "/sniffbox/settings/policy/preview") => Some("/api/policy/preview"),
        ("POST", "/sniffbox/settings/apply") => Some("/api/apply"),
        ("POST", "/sniffbox/settings/observer-token") => Some("/api/observer-token"),
        _ => None,
    };
    if let Some(path) = fixed { return Some(path.to_string()); }
    if method == "GET" {
        let identifier = path.strip_prefix("/sniffbox/settings/jobs/")?;
        if identifier.len() == 32 && identifier.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
            return Some(format!("/api/jobs/{identifier}"));
        }
    }
    None
}

pub(crate) async fn handle(
    stream: &mut TcpStream, req: &http::ReqHead, token: &str, ka: bool, body: &[u8],
) -> io::Result<()> {
    let Some(path) = target(&req.method, &req.path) else {
        return respond::send(stream, 405, "Method Not Allowed", &[], b"", ka).await;
    };
    subscription::forward(stream, &req.method, &path, token, ka, body, 8790).await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn settings_routes_never_forward_arbitrary_paths_or_queries() {
        assert_eq!(target("POST", "/sniffbox/settings/apply").as_deref(), Some("/api/apply"));
        assert_eq!(target("GET", "/sniffbox/settings/jobs/0123456789abcdef0123456789abcdef").as_deref(),
                   Some("/api/jobs/0123456789abcdef0123456789abcdef"));
        for path in ["/sniffbox/settings/config?url=http://example.com", "/sniffbox/settings/jobs/../config",
                     "/sniffbox/settings/jobs/0123456789abcdef0123456789abcdef?x=1", "/sniffbox/settings/recover"] {
            assert!(target("GET", path).is_none());
        }
        assert!(target("GET", "/sniffbox/settings/apply").is_none());
        assert!(target("POST", "/sniffbox/settings/config").is_none());
        assert_eq!(target("POST", "/sniffbox/settings/observer-token").as_deref(), Some("/api/observer-token"));
        assert!(target("GET", "/sniffbox/settings/observer-token").is_none());
        assert!(target("POST", "/sniffbox/settings/observer-token?reveal=true").is_none());
    }
}
