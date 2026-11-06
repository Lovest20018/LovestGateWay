// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use std::io;
use tokio::io::{AsyncWrite, AsyncWriteExt};

const SECURITY_HEADERS: &str = concat!(
    "X-Content-Type-Options: nosniff\r\n",
    "Referrer-Policy: no-referrer\r\n",
    "X-Frame-Options: DENY\r\n",
    "Content-Security-Policy: frame-ancestors 'none'; object-src 'none'; base-uri 'self'\r\n",
);


pub async fn send<W: AsyncWrite + Unpin>(
    w: &mut W,
    status: u16,
    reason: &str,
    extra: &[(&str, &str)],
    body: &[u8],
    keep_alive: bool,
) -> io::Result<()> {
    send_representation(w, status, reason, extra, body, false, keep_alive).await
}

async fn send_representation<W: AsyncWrite + Unpin>(
    w: &mut W, status: u16, reason: &str, extra: &[(&str, &str)], body: &[u8],
    head_only: bool, keep_alive: bool,
) -> io::Result<()> {
    let mut head = format!("HTTP/1.1 {status} {reason}\r\n");
    head.push_str(&format!("Content-Length: {}\r\n", body.len()));
    head.push_str(if keep_alive {
        "Connection: keep-alive\r\n"
    } else {
        "Connection: close\r\n"
    });
    for (k, v) in extra {
        head.push_str(k);
        head.push_str(": ");
        head.push_str(v);
        head.push_str("\r\n");
    }
    head.push_str(SECURITY_HEADERS);
    head.push_str("\r\n");
    w.write_all(head.as_bytes()).await?;
    if !head_only { w.write_all(body).await?; }
    w.flush().await
}

pub async fn not_modified<W: AsyncWrite + Unpin>(
    w: &mut W,
    etag: &str,
    keep_alive: bool,
) -> io::Result<()> {
    let head = format!(
        "HTTP/1.1 304 Not Modified\r\nETag: {etag}\r\nCache-Control: no-cache\r\n{}{SECURITY_HEADERS}\r\n",
        if keep_alive {
            "Connection: keep-alive\r\n"
        } else {
            "Connection: close\r\n"
        }
    );
    w.write_all(head.as_bytes()).await?;
    w.flush().await
}

pub async fn redirect<W: AsyncWrite + Unpin>(
    w: &mut W,
    location: &str,
    keep_alive: bool,
) -> io::Result<()> {
    send(w, 302, "Found", &[("Location", location)], b"", keep_alive).await
}

pub async fn unauthorized<W: AsyncWrite + Unpin>(w: &mut W, keep_alive: bool) -> io::Result<()> {
    send(
        w,
        401,
        "Unauthorized",
        &[("WWW-Authenticate", "Bearer")],
        b"unauthorized\n",
        keep_alive,
    )
    .await
}

pub async fn forbidden<W: AsyncWrite + Unpin>(
    w: &mut W,
    msg: &str,
    keep_alive: bool,
) -> io::Result<()> {
    send(
        w,
        403,
        "Forbidden",
        &[("Content-Type", "text/plain")],
        msg.as_bytes(),
        keep_alive,
    )
    .await
}

pub async fn not_found<W: AsyncWrite + Unpin>(w: &mut W, keep_alive: bool) -> io::Result<()> {
    send(
        w,
        404,
        "Not Found",
        &[("Content-Type", "text/plain")],
        b"not found\n",
        keep_alive,
    )
    .await
}

pub async fn file<W: AsyncWrite + Unpin>(
    w: &mut W,
    content_type: &str,
    cache_control: &str,
    body: &[u8],
    keep_alive: bool,
) -> io::Result<()> {
    send(
        w,
        200,
        "OK",
        &[
            ("Content-Type", content_type),
            ("Cache-Control", cache_control),
        ],
        body,
        keep_alive,
    )
    .await
}

pub async fn encoded_file<W: AsyncWrite + Unpin>(
    w: &mut W, content_type: &str, cache_control: &str, body: &[u8],
    gzip: bool, head_only: bool, keep_alive: bool,
) -> io::Result<()> {
    let mut headers = vec![("Content-Type", content_type), ("Cache-Control", cache_control), ("Vary", "Accept-Encoding")];
    if gzip { headers.push(("Content-Encoding", "gzip")); }
    send_representation(w, 200, "OK", &headers, body, head_only, keep_alive).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn encoded_get_and_head_have_matching_representation_metadata() {
        for head_only in [false, true] {
            let mut out = Vec::new();
            encoded_file(&mut out, "text/css", "no-cache", b"fixture-gzip", true, head_only, true).await.unwrap();
            let wire = String::from_utf8(out).unwrap();
            assert!(wire.contains("Content-Length: 12\r\n"));
            assert!(wire.contains("Content-Encoding: gzip\r\n"));
            assert!(wire.contains("Vary: Accept-Encoding\r\n"));
            assert!(wire.contains("Content-Type: text/css\r\n"));
            assert_eq!(wire.ends_with("\r\n\r\n"), head_only);
        }
    }

    #[tokio::test]
    async fn static_assets_and_entry_keep_security_headers_and_framing() {
        for mime in ["text/html; charset=utf-8", "application/javascript", "text/css"] {
            let mut out = Vec::new();
            file(&mut out, mime, "no-cache", b"fixture", true).await.unwrap();
            let wire = String::from_utf8(out).unwrap();
            assert!(wire.contains(SECURITY_HEADERS));
            assert!(wire.contains(&format!("Content-Type: {mime}\r\n")));
            assert!(wire.contains("Content-Length: 7\r\n"));
            assert!(wire.ends_with("\r\n\r\nfixture"));
        }
        let mut out = Vec::new();
        not_modified(&mut out, "fixture", true).await.unwrap();
        assert!(String::from_utf8(out).unwrap().contains(SECURITY_HEADERS));
    }

    #[tokio::test]
    async fn redirect_frame() {
        let mut out = Vec::new();
        redirect(&mut out, "/ui", true).await.unwrap();
        let s = String::from_utf8(out).unwrap();
        assert!(s.starts_with("HTTP/1.1 302 Found\r\n"));
        assert!(s.contains("Location: /ui\r\n"));
        assert!(s.contains("Connection: keep-alive\r\n"));
        assert!(s.ends_with("\r\n\r\n"));
    }

    #[tokio::test]
    async fn unauthorized_frame() {
        let mut out = Vec::new();
        unauthorized(&mut out, false).await.unwrap();
        let s = String::from_utf8(out).unwrap();
        assert!(s.starts_with("HTTP/1.1 401 Unauthorized\r\n"));
        assert!(s.contains("Connection: close\r\n"));
    }
}
