//! Last successful subscription payloads, separate from temporary retry state.
use super::{SubProvider, SubResult, UserInfo};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_CACHE_BYTES: u64 = 32 * 1024 * 1024;
static NEXT_FILE: AtomicU64 = AtomicU64::new(0);

pub(crate) fn directory() -> Option<PathBuf> {
    std::env::var_os("PPGW_SUB_CACHE_DIR").filter(|v| !v.is_empty()).map(PathBuf::from)
}

fn identity(sub: &SubProvider) -> String {
    hex::encode(Sha256::digest(format!("{}\n{}", sub.name, sub.url).as_bytes()))
}

fn url_hash(sub: &SubProvider) -> String {
    hex::encode(Sha256::digest(sub.url.as_bytes()))
}

fn atomic_json(path: &Path, value: &Value) -> std::io::Result<()> {
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
    let parent = path.parent().ok_or(std::io::ErrorKind::InvalidInput)?;
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(parent)?;
    let pending = parent.join(format!(".subscription-{}-{}.new", std::process::id(),
        NEXT_FILE.fetch_add(1, Ordering::Relaxed)));
    let result = (|| {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&pending)?;
        serde_json::to_writer(&mut file, value)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        std::fs::rename(&pending, path)?;
        Ok(())
    })();
    let _ = std::fs::remove_file(pending);
    result
}

pub(crate) fn save(dir: &Path, sub: &SubProvider, result: &SubResult) -> std::io::Result<()> {
    if !result.success || result.proxies.is_empty() || result.cached_at.is_some() {
        return Ok(());
    }
    let info = result.userinfo.as_ref().map(|info| json!({
        "total": info.total, "upload": info.upload, "download": info.download, "expire": info.expire
    }));
    atomic_json(&dir.join(format!("{}.json", identity(sub))), &json!({
        "version": 1, "name": sub.name, "url_sha256": url_hash(sub),
        "saved_at": super::subinfo::now(), "raw_yaml": result.raw_yaml, "userinfo": info
    }))
}

pub(crate) fn load(dir: &Path, sub: &SubProvider) -> Option<SubResult> {
    let path = dir.join(format!("{}.json", identity(sub)));
    let meta = std::fs::symlink_metadata(&path).ok()?;
    if !meta.is_file() || meta.len() > MAX_CACHE_BYTES { return None; }
    let value: Value = serde_json::from_slice(&std::fs::read(path).ok()?).ok()?;
    if value["version"].as_i64() != Some(1) || value["name"].as_str() != Some(&sub.name)
        || value["url_sha256"].as_str() != Some(url_hash(sub).as_str()) { return None; }
    let raw = value["raw_yaml"].as_str()?;
    let at = value["saved_at"].as_i64().filter(|at| *at > 0)?;
    let (proxies, hosts) = super::sub::parse_payload(raw, &sub.name);
    if proxies.is_empty() { return None; }
    let info = value.get("userinfo").filter(|v| v.is_object()).map(|v| UserInfo {
        total: v["total"].as_i64().unwrap_or(0), upload: v["upload"].as_i64().unwrap_or(0),
        download: v["download"].as_i64().unwrap_or(0), expire: v["expire"].as_i64().unwrap_or(0)
    });
    Some(SubResult {name: sub.name.clone(), success: true, proxies, hosts, userinfo: info,
        cached_at: Some(at), raw_yaml: raw.to_string(), error: String::new()})
}

pub(crate) fn status(results: &[SubResult]) {
    let Some(dir) = directory() else { return; };
    let subscriptions: Vec<Value> = results.iter().map(|r| json!({
        "name": r.name, "nodes": r.proxies.len(),
        "source": if !r.success { "unavailable" } else if r.cached_at.is_some() { "cache" } else { "fresh" },
        "fetched_at": if r.success { r.cached_at.unwrap_or_else(super::subinfo::now) } else { 0 },
        "update_failed": !r.error.is_empty()
    })).collect();
    if let Err(error) = atomic_json(&dir.join("status.json"), &json!({
        "version": 1, "at": super::subinfo::now(), "subscriptions": subscriptions
    })) { super::pp_warn(&format!("cannot save subscription cache status: {error}")); }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn provider(name: &str, url: &str) -> SubProvider {
        SubProvider {name: name.into(), url: url.into(), is_forced: false}
    }
    fn result() -> SubResult {
        let raw = "proxies:\n  - {name: HK, type: ss, server: 1.2.3.4, port: 443, cipher: aes-128-gcm, password: fixture}\n";
        let (proxies, hosts) = super::super::sub::parse_payload(raw, "Airport");
        SubResult {name: "Airport".into(), success: true, proxies, hosts, userinfo: Some(UserInfo {
            total: 1000, upload: 10, download: 20, expire: 0}), cached_at: None,
            raw_yaml: raw.into(), error: String::new()}
    }
    #[test]
    fn successful_payload_survives_reopen_and_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let sub = provider("Airport", "https://sub.example/a?token=fixture");
        save(dir.path(), &sub, &result()).unwrap();
        let cached = load(dir.path(), &sub).unwrap();
        assert_eq!(cached.proxies.len(), 1);
        assert_eq!(cached.proxies[0]["name"].as_str(), Some("Airport_HK"));
        assert_eq!(cached.userinfo.unwrap().total, 1000);
        assert!(cached.cached_at.unwrap() > 0);
        let path = dir.path().join(format!("{}.json", identity(&sub)));
        assert_eq!(path.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        assert!(!std::fs::read_to_string(path).unwrap().contains("token=fixture"));
    }
    #[test]
    fn changed_source_and_colliding_display_names_do_not_share_nodes() {
        let dir = tempfile::tempdir().unwrap();
        let sub = provider("Airport", "https://sub.example/a");
        save(dir.path(), &sub, &result()).unwrap();
        assert!(load(dir.path(), &provider("Airport", "https://sub.example/b")).is_none());
        assert!(load(dir.path(), &provider("Air/port", "https://sub.example/a")).is_none());
    }
    #[test]
    fn failure_empty_or_cached_results_cannot_replace_last_success() {
        let dir = tempfile::tempdir().unwrap();
        let sub = provider("Airport", "https://sub.example/a");
        save(dir.path(), &sub, &result()).unwrap();
        let original = load(dir.path(), &sub).unwrap().raw_yaml;
        let mut failed = result(); failed.success = false; failed.raw_yaml = "denied".into();
        save(dir.path(), &sub, &failed).unwrap();
        let mut empty = result(); empty.proxies.clear();
        save(dir.path(), &sub, &empty).unwrap();
        assert_eq!(load(dir.path(), &sub).unwrap().raw_yaml, original);
        let path = dir.path().join(format!("{}.json", identity(&sub)));
        std::fs::write(path, "corrupt").unwrap();
        assert!(load(dir.path(), &sub).is_none());
    }
}
