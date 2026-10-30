// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::ConfigSource;
use crate::runtime::SharedState;
use std::sync::Arc;
use tokio::signal::unix::{SignalKind, signal};

fn persist_core_token(path: &std::path::Path, secret: &str) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let pending = path.with_extension("reload");
    std::fs::write(&pending, secret)?;
    // launch.py creates 0640 root:paopaogw-api. Preserve that reader contract
    // even under the controller's restrictive 0077 umask during hot reload.
    std::fs::set_permissions(&pending, std::fs::Permissions::from_mode(0o640))?;
    std::fs::rename(pending, path)
}

pub async fn run_reload_loop(shared: Arc<SharedState>, source: ConfigSource) {
    let mut sighup = match signal(SignalKind::hangup()) {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!(?e, "failed to register SIGHUP handler");
            return;
        }
    };
    while sighup.recv().await.is_some() {
        tracing::info!(source = %source.describe(), "SIGHUP — reloading config");
        match source.load() {
            Ok(new) => {
                if std::env::var("PPGW_DEBIAN").as_deref() == Ok("1") && !shared.integration.permits_hot_reload(&new) {
                    tracing::warn!("Changed listener/mode/statistics enablement requires restart; keeping loaded config");
                    continue;
                }
                shared.reload(&new);
                source.write_running_snapshot();
                if std::env::var("PPGW_DEBIAN").as_deref() == Ok("1") {
                    let _ = persist_core_token(
                        std::path::Path::new("/var/lib/paopaogw/core-token"),
                        &sb_web::derive_secret(&new.web.password),
                    );
                }
                tracing::info!("config reloaded");
            }
            Err(e) => {
                tracing::warn!(?e, "reload failed; keep old config");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn hot_reload_token_retains_observer_group_read_permission() {
        let name = format!("ppgw-token-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
        let directory = std::env::temp_dir().join(name);
        std::fs::create_dir(&directory).unwrap();
        let token = directory.join("core-token");
        // An existing pending file can have 0600 after an interrupted reload.
        std::fs::write(token.with_extension("reload"), "old").unwrap();
        std::fs::set_permissions(token.with_extension("reload"), std::fs::Permissions::from_mode(0o600)).unwrap();
        persist_core_token(&token, "new-derived-token").unwrap();
        assert_eq!(std::fs::read_to_string(&token).unwrap(), "new-derived-token");
        assert_eq!(std::fs::metadata(&token).unwrap().permissions().mode() & 0o777, 0o640);
        assert!(!token.with_extension("reload").exists());
        std::fs::remove_dir_all(directory).unwrap();
    }
}
