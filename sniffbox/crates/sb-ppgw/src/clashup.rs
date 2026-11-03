// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::Io;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::time::{Duration, Instant};

const READY_WAIT: Duration = Duration::from_secs(15);
const REQ_TIMEOUT: Duration = Duration::from_secs(10);

pub fn cmd_clash_up(io: &mut Io) -> i32 {
    crate::rt::block_on(run(io))
}

async fn run(io: &mut Io<'_>) -> i32 {
    let address = match control_address(
        std::env::var("PPGW_DEBIAN").as_deref() == Ok("1"),
        &env_or("PPGW_WEB_LISTEN", "0.0.0.0:80"),
        &env_or("clash_web_port", "80"),
    ) {
        Ok(address) => address,
        Err(e) => {
            let _ = writeln!(io.err, "Invalid local control listener: {e}");
            return 1;
        }
    };
    let url = format!("http://{address}/sniffbox/clash/up");
    let client = match control_client(address) {
        Ok(client) => client,
        Err(e) => {
            let _ = writeln!(io.err, "Local control client: {e}");
            return 1;
        }
    };
    let deadline = Instant::now() + READY_WAIT;
    loop {
        match client.post(&url).timeout(REQ_TIMEOUT).send().await {

            Ok(resp) => {
                let code = resp.status().as_u16();
                if (200..300).contains(&code) {

                    crate::procinfo::touch_ready_marker();
                    let _ = writeln!(
                        io.out,
                        "{}clash up (HTTP {code})",
                        crate::term::green("[PaoPaoGW Clash]")
                    );
                    return 0;
                }
                let _ = writeln!(
                    io.err,
                    "{}clash-up {url} -> HTTP {code}",
                    crate::term::orange("[PaoPaoGW Clash]")
                );
                return 1;
            }

            Err(e) => {
                if Instant::now() >= deadline {
                    let _ = writeln!(
                        io.err,
                        "{}sniffbox not ready after {}s: {e}",
                        crate::term::orange("[PaoPaoGW Clash]"),
                        READY_WAIT.as_secs()
                    );
                    return 1;
                }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }
}

fn control_address(debian: bool, listen: &str, legacy_port: &str) -> Result<SocketAddr, String> {
    let mut address: SocketAddr = if debian {
        listen.parse().map_err(|e: std::net::AddrParseError| e.to_string())?
    } else {
        SocketAddr::new(Ipv4Addr::LOCALHOST.into(), legacy_port.parse::<u16>().map_err(|e| e.to_string())?)
    };
    if address.ip().is_unspecified() {
        address.set_ip(loopback(address));
    }
    Ok(address)
}

fn loopback(address: SocketAddr) -> IpAddr {
    if address.is_ipv4() { Ipv4Addr::LOCALHOST.into() } else { Ipv6Addr::LOCALHOST.into() }
}

fn control_client(address: SocketAddr) -> Result<wreq::Client, wreq::Error> {
    wreq::Client::builder()
        .no_proxy()
        .redirect(wreq::redirect::Policy::none())
        // The control endpoint is restricted to loopback peers even when the UI
        // binds a specific LAN address. Both endpoints remain on this host.
        .local_address(loopback(address))
        .build()
}

fn env_or(key: &str, default: &str) -> String {
    match std::env::var(key) {
        Ok(v) if !v.is_empty() => v,
        _ => default.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_control_follows_the_configured_listener() {
        for (listen, expected) in [
            ("0.0.0.0:18781", "127.0.0.1:18781"),
            ("192.0.2.10:18080", "192.0.2.10:18080"),
            ("[::]:8081", "[::1]:8081"),
            ("[fd00::1]:8081", "[fd00::1]:8081"),
        ] {
            assert_eq!(control_address(true, listen, "80").unwrap().to_string(), expected);
        }
        assert!(control_address(true, "invalid", "80").is_err());
        assert_eq!(control_address(false, "invalid", "8080").unwrap().to_string(), "127.0.0.1:8080");
    }

    #[test]
    fn control_request_uses_loopback_peer_on_a_custom_port() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, peer) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
            let mut buf = [0u8; 2048];
            let n = stream.read(&mut buf).unwrap();
            assert!(String::from_utf8_lossy(&buf[..n]).starts_with("POST /sniffbox/clash/up "));
            assert!(peer.ip().is_loopback());
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        crate::rt::block_on(async {
            let response = control_client(address).unwrap()
                .post(format!("http://{address}/sniffbox/clash/up"))
                .timeout(Duration::from_secs(5)).send().await.unwrap();
            assert_eq!(response.status().as_u16(), 200);
        });
        server.join().unwrap();
    }
}
