// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use tokio::sync::broadcast;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogSource {
    Sniffbox,
    Clash,
}

impl LogSource {
    pub fn as_str(self) -> &'static str {
        match self {
            LogSource::Sniffbox => "sniffbox",
            LogSource::Clash => "clash",
        }
    }
}

#[derive(Debug, Clone)]
pub struct LogEvent {
    pub seq: u64,
    pub ts_ms: u64,
    pub source: LogSource,
    pub level: String,
    pub msg: String,
}

impl LogEvent {
    pub fn new(source: LogSource, level: impl Into<String>, msg: impl Into<String>) -> Self {
        Self {
            seq: 0,
            ts_ms: now_ms(),
            source,
            level: level.into(),
            msg: msg.into(),
        }
    }

    pub fn to_json(&self) -> String {
        serde_json::json!({
            "seq": self.seq,
            "source": self.source.as_str(),
            "level": self.level,
            "ts": self.ts_ms,
            "msg": self.msg,
        })
        .to_string()
    }
}

pub type LogTx = Arc<LogHub>;

const MAX_MESSAGE_BYTES: usize = 4096;

pub struct LogHub {
    sender: broadcast::Sender<Arc<LogEvent>>,
    history: Mutex<History>,
    epoch: u64,
    max_events: usize,
    max_bytes: usize,
}

#[derive(Default)]
struct History {
    events: VecDeque<Arc<LogEvent>>,
    bytes: usize,
    last_seq: u64,
    evicted_through: u64,
}

pub struct LogReplay {
    pub after: u64,
    pub gap: bool,
    pub events: Vec<Arc<LogEvent>>,
}

impl LogHub {
    pub fn new(broadcast_capacity: usize, max_events: usize, max_bytes: usize) -> LogTx {
        let (sender, _) = broadcast::channel(broadcast_capacity.max(1));
        Arc::new(Self {
            sender,
            history: Mutex::new(History::default()),
            epoch: now_ms(),
            max_events,
            max_bytes,
        })
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Arc<LogEvent>> {
        self.sender.subscribe()
    }

    pub fn receiver_count(&self) -> usize {
        self.sender.receiver_count()
    }

    pub fn emit(&self, source: LogSource, level: impl Into<String>, msg: impl Into<String>) {
        let mut event = LogEvent::new(source, level, msg);
        let keep = !matches!(event.level.as_str(), "trace" | "debug");
        if !keep && self.receiver_count() == 0 {
            return;
        }
        if event.msg.len() > MAX_MESSAGE_BYTES {
            let mut end = MAX_MESSAGE_BYTES - '…'.len_utf8();
            while !event.msg.is_char_boundary(end) {
                end -= 1;
            }
            event.msg.truncate(end);
            event.msg.push('…');
        }
        let mut history = self
            .history
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        history.last_seq += 1;
        event.seq = history.last_seq;
        let event = Arc::new(event);
        if keep {
            history.bytes += event_cost(&event);
            history.events.push_back(event.clone());
            while history.events.len() > self.max_events || history.bytes > self.max_bytes {
                let old = history.events.pop_front().expect("history accounting");
                history.bytes -= event_cost(&old);
                history.evicted_through = old.seq;
            }
        }
        // Publish in the same order as sequence assignment, including concurrent writers.
        let _ = self.sender.send(event);
    }

    pub fn replay(&self, after: u64) -> LogReplay {
        let history = self
            .history
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let after = if after > history.last_seq { 0 } else { after };
        let start = history.events.partition_point(|event| event.seq <= after);
        LogReplay {
            after,
            gap: after < history.evicted_through,
            events: history.events.range(start..).cloned().collect(),
        }
    }
}

fn event_cost(event: &LogEvent) -> usize {
    event.msg.len() + event.level.len() + 64
}

pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn history_reports_eviction_and_keeps_sequence_order() {
        let hub = LogHub::new(8, 2, 4096);
        hub.emit(LogSource::Sniffbox, "info", "first");
        hub.emit(LogSource::Sniffbox, "debug", "idle debug");
        let mut live = hub.subscribe();
        hub.emit(LogSource::Clash, "info", "second");
        hub.emit(LogSource::Clash, "debug", "live debug");
        hub.emit(LogSource::Clash, "warn", "third");

        let backlog = hub.replay(0);
        assert!(backlog.gap);
        assert_eq!(
            backlog.events.iter().map(|row| row.seq).collect::<Vec<_>>(),
            [2, 4]
        );
        assert!(!hub.replay(1).gap);
        assert_eq!(hub.replay(2).events[0].msg, "third");
        assert_eq!(hub.replay(999).after, 0);
        for seq in 2..=4 {
            assert_eq!(live.try_recv().unwrap().seq, seq);
        }
    }

    #[test]
    fn history_byte_limit_is_strict_and_live_text_keeps_utf8_boundaries() {
        let hub = LogHub::new(8, 10, 500);
        let mut live = hub.subscribe();
        hub.emit(LogSource::Sniffbox, "info", "€".repeat(2400));
        let message = &live.try_recv().unwrap().msg;
        assert!(message.len() <= MAX_MESSAGE_BYTES && message.ends_with('…'));
        assert!(hub.replay(0).events.is_empty());
        assert!(hub.replay(0).gap);
        hub.emit(LogSource::Sniffbox, "info", "brief");
        assert_eq!(hub.replay(1).events[0].msg, "brief");
    }

    #[test]
    fn json_shape_and_escaping() {
        let ev = LogEvent {
            seq: 7,
            ts_ms: 1700000000000,
            source: LogSource::Clash,
            level: "info".into(),
            msg: "a\"b\nc".into(),
        };
        let v: serde_json::Value = serde_json::from_str(&ev.to_json()).unwrap();
        assert_eq!(v["source"], "clash");
        assert_eq!(v["level"], "info");
        assert_eq!(v["ts"], 1700000000000u64);
        assert_eq!(v["seq"], 7);
        assert_eq!(v["msg"], "a\"b\nc");
    }
}
