// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::runtime::SharedState;
use sb_stats::cleanup::{CleanState, maybe_clean};
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Duration;
use tokio::sync::watch;

const FLUSH_INTERVAL: Duration = Duration::from_secs(3);

pub async fn run_housekeeping(shared: Arc<SharedState>, mut shutdown: watch::Receiver<bool>) {
    let mut clean_state = CleanState::default();
    let mut ticker = tokio::time::interval(FLUSH_INTERVAL);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    tracing::info!(
        ?FLUSH_INTERVAL,
        max_rec = shared.max_rec(),
        stats = shared.stats_enabled(),
        "housekeeping loop started"
    );

    loop {
        tokio::select! {
            biased;
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    flush_once(&shared, &mut clean_state);
                    break;
                }
            }
            _ = ticker.tick() => flush_once(&shared, &mut clean_state),
        }
    }
    tracing::info!("housekeeping loop exited");
}

fn flush_once(shared: &SharedState, clean_state: &mut CleanState) {
    let max_rec = shared.max_rec();
    let net_cleanday = shared.net_cleanday();
    shared.flush_traffic();

    maybe_clean(net_cleanday, clean_state, || {
        shared.clear_traffic_stats(true);
    });

    shared.sniff_neg.gc();

    let demand = {
        let last = shared.traffic_last_access.load(Ordering::Relaxed);
        last != 0
            && sb_stats::now_epoch_ms().saturating_sub(last)
                <= crate::runtime::TRAFFIC_DEMAND_WINDOW_MS
    };
    if !shared.stats_enabled() {
        return;
    }
    if demand {
        shared.agg.rebuild(max_rec);
    } else {
        shared.agg.prune(max_rec);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{Config, StatsCfg};
    use sb_stats::types::{ConnRecord, SniffedProto};
    use sb_web::{ConnectionsSource, TrafficSource};

    fn shared_net_rec_off() -> Arc<SharedState> {
        Arc::new(SharedState::new(&Config::default()))
    }

    fn shared_net_rec_on() -> Arc<SharedState> {
        let cfg = Config {
            stats: Some(StatsCfg {
                enabled: true,
                ..Default::default()
            }),
            ..Default::default()
        };
        Arc::new(SharedState::new(&cfg))
    }

    fn rec_with_bytes(id: u64, up: u64, down: u64) -> Arc<ConnRecord> {
        let r = Arc::new(ConnRecord::new(
            sb_stats::ConnId(id),
            ("10.0.0.9".parse().unwrap(), 40000 + id as u16),
            ("1.2.3.4".parse().unwrap(), 443),
            SniffedProto::Tls,
            Some("leak.example".into()),
        ));
        r.add_up(up);
        r.add_down(down);
        r
    }

    #[test]
    fn housekeeping_reclaims_closed_records_when_net_rec_is_off() {
        let shared = shared_net_rec_off();
        assert!(!shared.stats_enabled());
        let mut clean = CleanState::default();
        for id in 1..=50 {
            let r = rec_with_bytes(id, 100, 200);
            shared.conn_table.insert(Arc::clone(&r));
            shared.conn_table.close(r.id);
        }
        assert_eq!(shared.conn_table.len(), 50);
        flush_once(&shared, &mut clean);
        assert_eq!(
            shared.conn_table.len(),
            0,
            "closed records must be reclaimed even with net_rec off"
        );
        assert_eq!(shared.traffic.totals(), (50 * 200, 50 * 100));
        assert_eq!(
            shared.agg.len(),
            0,
            "net_rec off must not build time-window aggregate rows"
        );
    }

    #[test]
    fn net_rec_off_skips_the_aggregate_but_keeps_live_records() {
        let shared = shared_net_rec_off();
        let mut clean = CleanState::default();
        let r = rec_with_bytes(1, 7, 11);
        shared.conn_table.insert(Arc::clone(&r));
        flush_once(&shared, &mut clean);
        assert_eq!(shared.conn_table.len(), 1, "active record stays");
        assert_eq!(shared.traffic.totals(), (11, 7));
        assert_eq!(shared.agg.len(), 0);
    }

    #[test]
    fn net_rec_on_still_folds_the_aggregate() {
        let shared = shared_net_rec_on();
        assert!(shared.stats_enabled());
        let mut clean = CleanState::default();
        let r = rec_with_bytes(1, 7, 11);
        shared.conn_table.insert(Arc::clone(&r));
        flush_once(&shared, &mut clean);
        assert_eq!(shared.agg.len(), 1, "net_rec on must fold into agg");
        assert_eq!(shared.traffic.totals(), (11, 7));
    }

    #[test]
    fn housekeeping_is_spawned_unconditionally_in_run() {
        let src = include_str!("lib.rs");
        let body = src.split_once("async fn run(").expect("run() must exist").1;
        let body = body.split("\nfn ").next().unwrap();
        let line = body
            .lines()
            .find(|l| l.contains("start_housekeeping("))
            .expect("run() must start the housekeeping loop");
        assert!(
            line.starts_with("    start_housekeeping(") && !line.starts_with("     "),
            "housekeeping must be a top-level statement of run(), not gated on net_rec; got: {line:?}"
        );
    }

    #[test]
    fn drain_accumulates_totals_then_zero_on_idle() {
        let shared = Arc::new(SharedState::new(&Config::default()));
        let r = Arc::new(ConnRecord::new(
            sb_stats::ConnId(1),
            ("10.0.0.1".parse().unwrap(), 1),
            ("1.2.3.4".parse().unwrap(), 443),
            SniffedProto::Tls,
            Some("example.com".into()),
        ));
        shared.conn_table.insert(Arc::clone(&r));
        r.upload.store(500, Ordering::Relaxed);
        r.download.store(1000, Ordering::Relaxed);
        shared.flush_traffic();
        assert_eq!(shared.traffic.totals(), (1000, 500));

        shared.flush_traffic();
        assert_eq!(shared.traffic.totals(), (1000, 500));
    }

    #[test]
    fn close_path_tail_bytes_counted_in_totals() {
        let shared = Arc::new(SharedState::new(&Config::default()));
        let r = Arc::new(ConnRecord::new(
            sb_stats::ConnId(7),
            ("10.0.0.7".parse().unwrap(), 55555),
            ("1.2.3.4".parse().unwrap(), 443),
            SniffedProto::Tls,
            Some("ex.com".into()),
        ));
        shared.conn_table.insert(Arc::clone(&r));
        r.add_up(900);
        r.add_down(1_073_741_824);

        shared.finish_connection(&r);

        shared.flush_traffic();
        assert_eq!(shared.traffic.totals(), (1_073_741_824, 900));
    }

    #[test]
    fn clearing_keeps_live_connections_and_counts_only_new_bytes() {
        for monthly in [false, true] {
            let shared = shared_net_rec_on();
            let traffic = crate::runtime::WebTraffic {
                shared: Arc::clone(&shared),
            };
            let connections = crate::runtime::WebConnections {
                shared: Arc::clone(&shared),
            };
            let live = rec_with_bytes(1, 100, 200);
            let closed = rec_with_bytes(2, 10, 20);
            shared.conn_table.insert(Arc::clone(&live));
            shared.flush_traffic();
            shared.conn_table.insert(Arc::clone(&closed));
            shared.finish_connection(&closed);
            live.add_down(400);

            if monthly {
                shared.clear_traffic_stats(true);
            } else {
                traffic.clear();
            }

            assert_eq!(traffic.totals(), (0, 0));
            assert!(shared.conn_table.get(closed.id).is_none());
            let view: serde_json::Value =
                serde_json::from_str(&connections.connections_json()).unwrap();
            assert_eq!(view["connections"][0]["id"], "1");
            assert_eq!(view["connections"][0]["download"], 600);
            assert_eq!(view["downloadTotal"], 0);
            assert_eq!(*shared.lock_traffic(), 1);

            live.add_down(7);
            live.add_up(5);
            shared.flush_traffic();
            assert_eq!(traffic.totals(), (7, 5));
            let rows: serde_json::Value = serde_json::from_str(&traffic.snapshot_json()).unwrap();
            assert_eq!(rows[0]["down"], 7);
            assert_eq!(rows[0]["up"], 5);
            assert_eq!(rows[0]["conns"], 1);
        }
    }

    #[test]
    fn accounting_and_close_cannot_restore_pre_clear_traffic() {
        let shared = shared_net_rec_on();
        let rec = rec_with_bytes(1, 100, 200);
        shared.conn_table.insert(Arc::clone(&rec));
        let barrier = std::sync::Barrier::new(3);
        std::thread::scope(|scope| {
            scope.spawn(|| {
                barrier.wait();
                shared.finish_connection(&rec);
            });
            scope.spawn(|| {
                barrier.wait();
                shared.flush_traffic();
            });
            barrier.wait();
            shared.clear_traffic_stats(false);
        });
        shared.flush_traffic();
        assert_eq!(shared.traffic.totals(), (0, 0));
        assert!(shared.conn_table.is_empty());
        assert!(shared.agg.is_empty());
    }
}
