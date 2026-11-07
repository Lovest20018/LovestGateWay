// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use std::hash::{DefaultHasher, Hasher};
use std::sync::Arc;

/// The body and its validator are published together and shared by all readers.
pub struct ProxySnapshot {
    body: String,
    etag: String,
}

impl ProxySnapshot {
    pub fn new(body: String) -> Self {
        let mut hash = DefaultHasher::new();
        hash.write(body.as_bytes());
        let etag = format!("\"{:016x}\"", hash.finish());
        Self { body, etag }
    }

    pub fn body(&self) -> &str {
        &self.body
    }

    pub fn etag(&self) -> &str {
        &self.etag
    }
}

pub trait ProxiesSource: Send + Sync {
    fn proxies_snapshot(&self, fresh: bool) -> Arc<ProxySnapshot>;

    fn mode_json(&self) -> String;

    fn warm(&self);

    fn proxy_detail(&self, _name: &str) -> (u16, String) {
        (404, r#"{"message":"Resource not found"}"#.to_string())
    }

    fn smart_fix(&self, _group: &str, _node: Option<&str>) -> (u16, String) {
        (404, r#"{"message":"Resource not found"}"#.to_string())
    }
}
