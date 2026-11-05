// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use sb_dns::FakeIpPool;
use sb_dns::is_valid_fakeip_domain;
use sb_dns::message::{
    self, CLASS_IN, RCODE_FORMERR, RCODE_NOTIMP, RCODE_NXDOMAIN, TYPE_A, parse_query,
};
use std::io;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::{TcpListener, TcpSocket, UdpSocket};
use tokio::sync::watch;
use tokio::task::JoinSet;
use tokio::time::timeout;

const TCP_CONNECTION_LIMIT: usize = 128;
const TCP_IO_TIMEOUT: Duration = Duration::from_secs(10);

pub struct DnsSockets {
    udp: UdpSocket,
    tcp: TcpListener,
}

pub fn bind_dns(addr: SocketAddr) -> io::Result<DnsSockets> {
    let udp = sb_tproxy::udp::bind_udp_reuseaddr(addr)?;
    let tcp = if addr.is_ipv6() {
        TcpSocket::new_v6()?
    } else {
        TcpSocket::new_v4()?
    };
    tcp.set_reuseaddr(true)?;
    // Use the same port for both transports, including ephemeral-port fixtures.
    tcp.bind(udp.local_addr()?)?;
    let tcp = tcp.listen(128)?;
    // A TCP bind failure drops UDP too; do not silently start a partial service.
    Ok(DnsSockets { udp, tcp })
}

pub async fn run_dns_server(
    sockets: DnsSockets,
    pool: Arc<FakeIpPool>,
    mut shutdown: watch::Receiver<bool>,
) {
    let DnsSockets { udp, tcp } = sockets;
    let mut clients = JoinSet::new();
    let mut buf = [0u8; 1500];
    loop {
        if *shutdown.borrow() {
            break;
        }
        tokio::select! {
            changed = shutdown.changed() => {
                if changed.is_err() || *shutdown.borrow() { break; }
            }
            _ = clients.join_next(), if !clients.is_empty() => {}
            res = udp.recv_from(&mut buf) => match res {
                Ok((n, peer)) => {
                    let resp = handle_query(&buf[..n], &pool);
                    if let Err(e) = udp.send_to(&resp, peer).await {
                        tracing::debug!(%peer, ?e, "dns send_to failed");
                    }
                }
                Err(e) => {
                    tracing::warn!(?e, "dns recv_from error; backoff 50ms");
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                }
            },
            res = tcp.accept() => match res {
                Ok((stream, peer)) => {
                    if clients.len() >= TCP_CONNECTION_LIMIT {
                        drop(stream);
                        continue;
                    }
                    let pool = Arc::clone(&pool);
                    clients.spawn(async move {
                        let _ = stream.set_nodelay(true);
                        if let Err(e) = serve_tcp_connection(stream, pool).await {
                            tracing::debug!(%peer, ?e, "dns tcp connection closed");
                        }
                    });
                }
                Err(e) => {
                    tracing::warn!(?e, "dns tcp accept failed; backoff 50ms");
                    tokio::time::sleep(Duration::from_millis(50)).await;
                }
            }
        }
    }
    clients.abort_all();
    while clients.join_next().await.is_some() {}
    tracing::info!("dns server stopped");
}

async fn serve_tcp_connection<S>(mut stream: S, pool: Arc<FakeIpPool>) -> io::Result<()>
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let mut query = Vec::new();
    loop {
        // RFC 7766: persistent, framed messages may arrive in multiple reads.
        // One deadline covers the whole frame, so partial bytes cannot extend it.
        let frame = timeout(TCP_IO_TIMEOUT, async {
            let length = stream.read_u16().await? as usize;
            if length < 12 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "short DNS frame",
                ));
            }
            query.resize(length, 0);
            stream.read_exact(&mut query).await?;
            Ok::<_, io::Error>(())
        })
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "DNS TCP frame timeout"))?;
        match frame {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => return Ok(()),
            Err(error) => return Err(error),
        }
        let response = handle_query(&query, &pool);
        let mut framed = Vec::with_capacity(response.len() + 2);
        framed.extend_from_slice(&(response.len() as u16).to_be_bytes());
        framed.extend_from_slice(&response);
        timeout(TCP_IO_TIMEOUT, stream.write_all(&framed))
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "DNS TCP write timeout"))??;
    }
}

fn handle_query(buf: &[u8], pool: &FakeIpPool) -> Vec<u8> {
    let q = match parse_query(buf) {
        Ok(q) => q,
        Err(e) => {
            tracing::debug!(?e, "dns parse failed; FORMERR");
            return message::error_response(buf, RCODE_FORMERR);
        }
    };
    if q.qclass != CLASS_IN {
        return q.error(RCODE_NOTIMP);
    }
    match q.qtype {
        TYPE_A => {
            if !is_valid_fakeip_domain(&q.name) {
                tracing::debug!(name = %q.name, "invalid/reserved domain; no fakeip (NXDOMAIN)");
                return q.error(RCODE_NXDOMAIN);
            }
            let ip = pool.intern(&q.name);
            tracing::debug!(name = %q.name, %ip, "fakeip assigned");
            q.answer_a(ip, pool.ttl())
        }

        _ => q.nodata(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sb_dns::FakeIpConfig;
    use sb_dns::message::{CLASS_IN, RCODE_NXDOMAIN, TYPE_A, TYPE_AAAA, TYPE_HTTPS};
    use tokio::io::duplex;
    use tokio::net::TcpStream;

    fn frame(query: &[u8]) -> Vec<u8> {
        let mut wire = Vec::with_capacity(query.len() + 2);
        wire.extend_from_slice(&(query.len() as u16).to_be_bytes());
        wire.extend_from_slice(query);
        wire
    }

    async fn read_response<S: AsyncRead + Unpin>(stream: &mut S) -> Vec<u8> {
        let length = stream.read_u16().await.unwrap() as usize;
        let mut response = vec![0; length];
        stream.read_exact(&mut response).await.unwrap();
        response
    }

    async fn udp_query(address: SocketAddr, query: &[u8]) -> Vec<u8> {
        let client = UdpSocket::bind(SocketAddr::new(address.ip(), 0))
            .await
            .unwrap();
        client.send_to(query, address).await.unwrap();
        let mut response = [0; 1500];
        let (length, _) = timeout(Duration::from_secs(2), client.recv_from(&mut response))
            .await
            .unwrap()
            .unwrap();
        response[..length].to_vec()
    }

    #[tokio::test]
    async fn tcp_udp_share_fakeip_with_fragmented_and_pipelined_queries() {
        for address in ["127.0.0.1:0", "[::1]:0"] {
            let sockets = bind_dns(address.parse().unwrap()).unwrap();
            let address = sockets.udp.local_addr().unwrap();
            assert_eq!(address, sockets.tcp.local_addr().unwrap());
            let pool = test_pool();
            let (shutdown, receiver) = watch::channel(false);
            let server = tokio::spawn(run_dns_server(sockets, Arc::clone(&pool), receiver));
            let first = make_query(0x21, "shared.example.com", TYPE_A);
            let udp = udp_query(address, &first).await;
            let mut client = TcpStream::connect(address).await.unwrap();
            let wire = frame(&first);
            client.write_all(&wire[..1]).await.unwrap();
            tokio::task::yield_now().await;
            client.write_all(&wire[1..7]).await.unwrap();
            tokio::task::yield_now().await;
            client.write_all(&wire[7..]).await.unwrap();
            assert_eq!(read_response(&mut client).await, udp);
            let second = make_query(0x22, "shared.example.com", TYPE_A);
            let third = make_query(0x23, "other.example.com", TYPE_A);
            client
                .write_all(&[frame(&second), frame(&third)].concat())
                .await
                .unwrap();
            for query in [&second, &third] {
                let response = read_response(&mut client).await;
                assert_eq!(response, handle_query(query, &pool));
                assert_eq!(response[..2], query[..2]);
            }
            shutdown.send(true).unwrap();
            timeout(Duration::from_secs(2), server)
                .await
                .unwrap()
                .unwrap();
        }
    }

    #[tokio::test]
    async fn tcp_consumes_large_frames_and_recovers_after_malformed_queries() {
        let pool = test_pool();
        let (mut client, server) = duplex(131072);
        let worker = tokio::spawn(serve_tcp_connection(server, Arc::clone(&pool)));
        let mut large = make_query(0x31, "padded.example.com", TYPE_A);
        large[11] = 1; // One OPT record with EDNS padding, larger than the UDP buffer.
        large.extend_from_slice(&[0, 0, 41, 16, 0, 0, 0, 0, 0]);
        large.extend_from_slice(&6004u16.to_be_bytes());
        large.extend_from_slice(&12u16.to_be_bytes());
        large.extend_from_slice(&6000u16.to_be_bytes());
        large.resize(large.len() + 6000, 0);
        let mut malformed = vec![0; 12];
        malformed[..2].copy_from_slice(&0x32u16.to_be_bytes());
        let last = make_query(0x33, "last.example.com", TYPE_A);
        client
            .write_all(&[frame(&large), frame(&malformed), frame(&last)].concat())
            .await
            .unwrap();
        assert_eq!(
            read_response(&mut client).await,
            handle_query(&large, &pool)
        );
        let error = read_response(&mut client).await;
        assert_eq!(&error[..2], &malformed[..2]);
        assert_eq!(error[3] & 15, RCODE_FORMERR);
        assert_eq!(read_response(&mut client).await, handle_query(&last, &pool));
        drop(client);
        worker.await.unwrap().unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn tcp_partial_bytes_do_not_reset_the_frame_deadline() {
        let (mut client, server) = duplex(128);
        let started = tokio::time::Instant::now();
        let worker = tokio::spawn(serve_tcp_connection(server, test_pool()));
        client.write_all(&[0]).await.unwrap();
        tokio::task::yield_now().await;
        tokio::time::advance(TCP_IO_TIMEOUT / 2).await;
        client.write_all(&[32]).await.unwrap();
        tokio::task::yield_now().await;
        tokio::time::advance(TCP_IO_TIMEOUT / 2).await;
        let error = worker.await.unwrap().unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(started.elapsed(), TCP_IO_TIMEOUT);
    }

    #[tokio::test(start_paused = true)]
    async fn tcp_blocked_response_has_a_write_deadline() {
        let (mut client, server) = duplex(8);
        let worker = tokio::spawn(serve_tcp_connection(server, test_pool()));
        client
            .write_all(&frame(&make_query(0x41, "write.example.com", TYPE_A)))
            .await
            .unwrap();
        tokio::task::yield_now().await;
        tokio::time::advance(TCP_IO_TIMEOUT).await;
        assert_eq!(
            worker.await.unwrap().unwrap_err().kind(),
            io::ErrorKind::TimedOut
        );
    }

    #[tokio::test]
    async fn tcp_short_frames_and_truncated_streams_close_without_allocating_fakeips() {
        for wire in [vec![0, 0], vec![0, 11], vec![0, 32, 1, 2]] {
            let pool = test_pool();
            let (mut client, server) = duplex(128);
            let worker = tokio::spawn(serve_tcp_connection(server, Arc::clone(&pool)));
            client.write_all(&wire).await.unwrap();
            client.shutdown().await.unwrap();
            let result = timeout(Duration::from_secs(2), worker)
                .await
                .unwrap()
                .unwrap();
            if wire[1] < 12 {
                assert_eq!(result.unwrap_err().kind(), io::ErrorKind::InvalidData);
            } else {
                result.unwrap();
            }
            assert_eq!(pool.len(), 0);
        }
    }

    #[tokio::test]
    async fn tcp_connection_limit_keeps_udp_available_and_shutdown_closes_clients() {
        let sockets = bind_dns("127.0.0.1:0".parse().unwrap()).unwrap();
        let address = sockets.tcp.local_addr().unwrap();
        let (shutdown, receiver) = watch::channel(false);
        let server = tokio::spawn(run_dns_server(sockets, test_pool(), receiver));
        let mut clients = Vec::new();
        for _ in 0..TCP_CONNECTION_LIMIT {
            clients.push(TcpStream::connect(address).await.unwrap());
        }
        let mut extra = TcpStream::connect(address).await.unwrap();
        let mut byte = [0];
        assert_eq!(
            timeout(Duration::from_secs(2), extra.read(&mut byte))
                .await
                .unwrap()
                .unwrap(),
            0
        );
        let response = udp_query(address, &make_query(0x51, "udp.example.com", TYPE_A)).await;
        assert_eq!(&response[6..8], &[0, 1]);
        shutdown.send(true).unwrap();
        timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
        for mut client in clients {
            assert_eq!(
                timeout(Duration::from_secs(2), client.read(&mut byte))
                    .await
                    .unwrap()
                    .unwrap(),
                0
            );
        }
        let _rebound = bind_dns(address).expect("shutdown must release both listening sockets");
    }

    #[tokio::test]
    async fn tcp_bind_conflict_releases_the_candidate_udp_listener() {
        let existing = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = existing.local_addr().unwrap();
        let error = bind_dns(address)
            .err()
            .expect("occupied TCP port must reject DNS startup");
        assert_eq!(error.kind(), io::ErrorKind::AddrInUse);
        let _udp = std::net::UdpSocket::bind(address).expect("failed bind must release UDP");
    }

    fn test_pool() -> Arc<FakeIpPool> {
        Arc::new(
            FakeIpPool::new(FakeIpConfig {
                cidr: "7.0.0.0/8".parse().unwrap(),
                max_entries: 1024,
                ttl: 3,
                shards: 16,
            })
            .unwrap(),
        )
    }

    fn make_query(id: u16, name: &str, qtype: u16) -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(&id.to_be_bytes());
        b.extend_from_slice(&0x0100u16.to_be_bytes());
        b.extend_from_slice(&1u16.to_be_bytes());
        b.extend_from_slice(&[0, 0, 0, 0, 0, 0]);
        for label in name.split('.') {
            b.push(label.len() as u8);
            b.extend_from_slice(label.as_bytes());
        }
        b.push(0);
        b.extend_from_slice(&qtype.to_be_bytes());
        b.extend_from_slice(&CLASS_IN.to_be_bytes());
        b
    }

    #[test]
    fn a_query_gets_fakeip_and_reverse_resolves() {
        let pool = test_pool();
        let resp = handle_query(&make_query(0x42, "example.com", TYPE_A), &pool);

        assert_eq!(resp[2] & 0x80, 0x80);
        assert_eq!(&resp[6..8], &[0, 1]);

        let ip = std::net::Ipv4Addr::new(
            resp[resp.len() - 4],
            resp[resp.len() - 3],
            resp[resp.len() - 2],
            resp[resp.len() - 1],
        );
        assert!(pool.contains(std::net::IpAddr::V4(ip)));

        assert_eq!(pool.lookback(ip).as_deref(), Some("example.com"));
    }

    #[test]
    fn aaaa_and_https_are_nodata() {
        let pool = test_pool();
        for qt in [TYPE_AAAA, TYPE_HTTPS] {
            let resp = handle_query(&make_query(1, "example.com", qt), &pool);
            assert_eq!(resp[2] & 0x80, 0x80, "QR set");
            assert_eq!(&resp[6..8], &[0, 0], "qtype {qt}: NODATA (ANCOUNT=0)");
        }

        assert_eq!(pool.len(), 0);
    }

    #[test]
    fn malformed_query_is_formerr_no_panic() {
        let pool = test_pool();
        let resp = handle_query(&[0x00, 0x01, 0x02], &pool);
        assert_eq!(resp[3] & 0x0F, RCODE_FORMERR);

        for junk in [&b""[..], &[0xff; 12][..], &[0x00; 1][..]] {
            let _ = handle_query(junk, &pool);
        }
    }

    #[test]
    fn invalid_domain_nxdomain_no_alloc() {
        let pool = test_pool();
        for name in [
            "host.local",
            "x.onion",
            "noTLD",
            "1.2.3.4.in-addr.arpa",
            "under_score.com",
        ] {
            let resp = handle_query(&make_query(9, name, TYPE_A), &pool);
            assert_eq!(resp[2] & 0x80, 0x80, "QR set");
            assert_eq!(resp[3] & 0x0F, RCODE_NXDOMAIN, "{name}: expected NXDOMAIN");
            assert_eq!(&resp[6..8], &[0, 0], "{name}: no answer");
        }
        assert_eq!(
            pool.len(),
            0,
            "invalid/reserved domains must not allocate fakeip"
        );
    }

    #[test]
    fn same_name_stable_ip() {
        let pool = test_pool();
        let r1 = handle_query(&make_query(1, "stable.com", TYPE_A), &pool);
        let r2 = handle_query(&make_query(2, "stable.com", TYPE_A), &pool);
        assert_eq!(
            r1[r1.len() - 4..],
            r2[r2.len() - 4..],
            "same domain → same fakeip"
        );
    }
}
