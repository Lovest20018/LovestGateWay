// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use std::path::{Component, Path, PathBuf};

// None means that neither available representation is acceptable.
pub fn prefer_gzip(accept: Option<&str>, available: bool) -> Option<bool> {
    let mut gzip: Option<f32> = None;
    let mut identity: Option<f32> = None;
    let mut wildcard: Option<f32> = None;
    for part in accept.unwrap_or("").split(',') {
        let mut fields = part.trim().split(';');
        let name = fields.next().unwrap_or("").trim().to_ascii_lowercase();
        let mut quality = 1.0f32;
        for field in fields {
            if let Some((key, value)) = field.trim().split_once('=') {
                if key.trim().eq_ignore_ascii_case("q") {
                    quality = value.trim().parse::<f32>().ok()
                        .filter(|q| q.is_finite() && (0.0..=1.0).contains(q)).unwrap_or(0.0);
                }
            }
        }
        let target = match name.as_str() {
            "gzip" => &mut gzip, "identity" => &mut identity, "*" => &mut wildcard, _ => continue,
        };
        *target = Some(target.unwrap_or(quality).min(quality));
    }
    let gz = if available { gzip.or(wildcard).unwrap_or(0.0) } else { 0.0 };
    let plain = identity.unwrap_or(if wildcard == Some(0.0) { 0.0 } else { 1.0 });
    if gz > 0.0 && gz >= plain { Some(true) }
    else if plain > 0.0 { Some(false) }
    else { None }
}

pub fn resolve(webroot: &Path, url_path: &str) -> Option<PathBuf> {
    resolve_prefixed(webroot, url_path, "/ui")
}

pub fn resolve_prefixed(webroot: &Path, url_path: &str, prefix: &str) -> Option<PathBuf> {
    let rel = url_path.strip_prefix(prefix).unwrap_or(url_path);
    let rel = rel.strip_prefix('/').unwrap_or(rel);
    let rel = if rel.is_empty() { "index.html" } else { rel };

    let mut safe = PathBuf::new();
    for comp in Path::new(rel).components() {
        match comp {
            Component::Normal(c) => safe.push(c),

            _ => return None,
        }
    }
    let full = webroot.join(&safe);

    Some(if full.is_dir() {
        full.join("index.html")
    } else {
        full
    })
}

pub fn content_type(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
        .as_deref()
    {
        Some("html" | "htm") => "text/html; charset=utf-8",
        Some("js" | "mjs") => "application/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("json") => "application/json; charset=utf-8",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("ico") => "image/x-icon",
        Some("woff2") => "font/woff2",
        Some("woff") => "font/woff",
        Some("ttf") => "font/ttf",
        Some("map") => "application/json; charset=utf-8",
        Some("wasm") => "application/wasm",
        Some("txt") => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

pub fn cache_control(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_ascii_lowercase())
        .as_deref()
    {
        Some(
            "js" | "mjs" | "css" | "svg" | "png" | "jpg" | "jpeg" | "gif" | "webp" | "ico"
            | "woff2" | "woff" | "ttf" | "wasm",
        ) => "public, max-age=31536000, immutable",
        _ => "no-cache",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gzip_negotiation_respects_quality_and_explicit_exclusions() {
        for (header, available, expected) in [
            (None, true, Some(false)), (Some(""), true, Some(false)),
            (Some("gzip"), true, Some(true)), (Some("GZip; q=1"), true, Some(true)),
            (Some("gzip;q=0,*;q=1"), true, Some(false)),
            (Some("gzip;q=0.8,identity;q=0.2"), true, Some(true)),
            (Some("gzip;q=0.2,identity;q=0.8"), true, Some(false)),
            (Some("*;q=0"), true, None), (Some("*;q=1,identity;q=0"), true, Some(true)),
            (Some("gzip,identity;q=0"), false, None),
            (Some("gzip;q=0,gzip;q=1"), true, Some(false)),
            (Some("gzip;q=NaN,identity;q=0"), true, None),
        ] { assert_eq!(prefer_gzip(header, available), expected, "{header:?}"); }
    }

    #[test]
    fn root_maps_to_index() {

        let wr = Path::new("/srv/www/ui");
        assert_eq!(
            resolve(wr, "/ui/").unwrap(),
            PathBuf::from("/srv/www/ui/index.html")
        );
        assert_eq!(
            resolve(wr, "/ui").unwrap(),
            PathBuf::from("/srv/www/ui/index.html")
        );
    }

    #[test]
    fn normal_files() {
        let wr = Path::new("/srv/www/ui");
        assert_eq!(
            resolve(wr, "/ui/assets/app.js").unwrap(),
            PathBuf::from("/srv/www/ui/assets/app.js")
        );
    }

    #[test]
    fn rejects_traversal() {
        let wr = Path::new("/srv/www/ui");
        assert!(resolve(wr, "/ui/../../etc/passwd").is_none());
        assert!(resolve(wr, "/ui/../secret").is_none());

        assert!(resolve(wr, "/ui//etc/passwd").is_none());

        assert_eq!(
            resolve(wr, "/ui/a//b.js").unwrap(),
            PathBuf::from("/srv/www/ui/a/b.js")
        );
    }

    #[test]
    fn data_prefix() {

        let root = Path::new("/srv/ui/data");
        assert_eq!(
            resolve_prefixed(root, "/data/version", "/data").unwrap(),
            PathBuf::from("/srv/ui/data/version")
        );
        assert!(resolve_prefixed(root, "/data/../../etc/passwd", "/data").is_none());
    }

    #[test]
    fn content_types() {
        assert_eq!(
            content_type(Path::new("a.html")),
            "text/html; charset=utf-8"
        );
        assert_eq!(
            content_type(Path::new("a.JS")),
            "application/javascript; charset=utf-8"
        );
        assert_eq!(
            content_type(Path::new("a.unknown")),
            "application/octet-stream"
        );
    }

    #[test]
    fn cache_control_immutable_for_assets() {
        let long = "public, max-age=31536000, immutable";
        assert_eq!(cache_control(Path::new("bundle.D4w4yfhp.js")), long);
        assert_eq!(cache_control(Path::new("a.CSS")), long);
        assert_eq!(cache_control(Path::new("logo.svg")), long);
        assert_eq!(cache_control(Path::new("font.woff2")), long);

        assert_eq!(cache_control(Path::new("index.html")), "no-cache");
        assert_eq!(cache_control(Path::new("data.json")), "no-cache");
    }
}
