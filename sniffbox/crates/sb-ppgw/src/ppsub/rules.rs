// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use super::{PpsubErr, RuleSet, pp_log, pp_warn, ystr};
use std::collections::{BTreeMap, HashSet};
use yaml_rust2::yaml::Hash;
use yaml_rust2::{Yaml, YamlLoader};

const BUILTIN_TARGETS: &[&str] = &[
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "COMPATIBLE",
    "PASS",
    "PASS-RULE",
    "GLOBAL",
];

pub(super) fn priority_order(rule_sets: &[RuleSet]) -> Vec<&RuleSet> {
    let mut sets: Vec<_> = rule_sets.iter().collect();
    sets.sort_by_key(|rule| rule.priority);
    sets
}

pub(super) fn provider_winners<'a>(
    sorted: &[&'a RuleSet],
    mut duplicate: impl FnMut(&str),
) -> Vec<&'a RuleSet> {
    let mut seen = HashSet::new();
    sorted
        .iter()
        .copied()
        .filter(|row| {
            if row.typ != "rule-set" || row.name.is_empty() || row.url.is_empty() {
                return false;
            }
            let first = seen.insert(row.name.as_str());
            if !first {
                duplicate(&row.name);
            }
            first
        })
        .collect()
}

pub fn process_rules(
    rule_sets: &[RuleSet],
    proxy_groups: &[Yaml],
) -> Result<(Vec<String>, Hash), PpsubErr> {
    let sets = priority_order(rule_sets);

    let group_names: HashSet<&str> = proxy_groups
        .iter()
        .filter_map(|g| g["name"].as_str())
        .collect();

    let defined = provider_winners(&sets, |name| {
        pp_warn(&format!(
            "duplicate rule-set provider [{name}]: kept the first by priority"
        ));
    });
    let provider_names: HashSet<_> = defined.iter().map(|rs| rs.name.as_str()).collect();

    let mut all_rules = Vec::new();
    let mut used: HashSet<String> = HashSet::new();
    let mut bad_targets: BTreeMap<String, usize> = BTreeMap::new();
    let mut bad_providers: BTreeMap<String, usize> = BTreeMap::new();
    let mut malformed = 0;
    for rs in sets.into_iter().filter(|rs| rs.typ != "rule-set") {
        let rules = if rs.typ == "url" || (rs.typ.is_empty() && !rs.url.is_empty()) {
            pp_log(&format!(
                "downloading rules{} {}",
                if rs.is_forced { " (forced)" } else { "" },
                rs.url
            ));
            match download_rules(&rs.url) {
                Ok(r) => {
                    pp_log(&format!(
                        "downloaded {} rule line(s) from {}",
                        r.len(),
                        rs.url
                    ));
                    r
                }
                Err(e) => {
                    pp_warn(&format!("rule download failed ({}): {e}", rs.url));
                    if rs.is_forced {
                        return Err(PpsubErr::ForcedRuleFailed(rs.url.clone(), e));
                    }
                    continue;
                }
            }
        } else {
            rs.fixrule.clone()
        };
        for rule in &rules {
            let rule = rule.trim();
            if rule.is_empty() || rule.starts_with('#') {
                continue;
            }
            let Some((kind, payload, target)) = rule_fields(rule) else {
                malformed += 1;
                continue;
            };
            let Some(refs) = rule_provider_names(&kind, payload) else {
                malformed += 1;
                continue;
            };
            if !BUILTIN_TARGETS.contains(&target) && !group_names.contains(target) {
                *bad_targets.entry(target.to_string()).or_default() += 1;
                continue;
            }
            if let Some(name) = refs.iter().find(|name| !provider_names.contains(**name)) {
                *bad_providers.entry((*name).to_string()).or_default() += 1;
                continue;
            }
            used.extend(refs.into_iter().map(String::from));
            all_rules.push(rule.to_string());
        }
    }

    if malformed != 0 {
        pp_warn(&format!(
            "dropped {malformed} malformed or unsupported rule(s)"
        ));
    }
    for (target, n) in bad_targets {
        pp_warn(&format!(
            "dropped {n} rule(s) targeting [{target}]: no such proxy group"
        ));
    }
    for (name, n) in bad_providers {
        pp_warn(&format!(
            "dropped {n} rule(s) referencing [{name}]: no such rule-set provider"
        ));
    }

    let final_providers = defined
        .into_iter()
        .filter(|rs| used.contains(&rs.name))
        .map(|rs| {
            pp_log(&format!("rule-set provider [{}]", rs.name));
            (ystr(&rs.name), build_provider(rs))
        })
        .collect();
    Ok((all_rules, final_providers))
}

fn build_provider(rs: &RuleSet) -> Yaml {
    let mut p = Hash::new();
    p.insert(ystr("type"), ystr("http"));
    p.insert(ystr("url"), ystr(&rs.url));
    let mut interval = if rs.interval > 0 { rs.interval } else { 86400 };
    if interval < 60 {
        interval = 60;
    }
    p.insert(ystr("interval"), Yaml::Integer(interval));
    p.insert(
        ystr("behavior"),
        ystr(if rs.behavior.is_empty() {
            "classical"
        } else {
            &rs.behavior
        }),
    );
    let mut header = Hash::new();
    header.insert(
        ystr("User-Agent"),
        Yaml::Array(vec![ystr(super::groups::ua_ruleset())]),
    );
    p.insert(ystr("header"), Yaml::Hash(header));

    let format = if rs.format.is_empty() {
        crate::ruleset::guess_format(&rs.url)
    } else {
        &rs.format
    };
    p.insert(ystr("format"), ystr(format));
    if !rs.proxy.is_empty() {
        p.insert(ystr("proxy"), ystr(&rs.proxy));
    }

    let path = if rs.path.is_empty() {
        crate::ruleset::default_path(&rs.url, &rs.format)
    } else {
        rs.path.clone()
    };
    p.insert(ystr("path"), ystr(&path));
    Yaml::Hash(p)
}

fn logical_kind(kind: &str) -> bool {
    matches!(kind, "AND" | "OR" | "NOT")
}

// Mihomo treats targets differently for MATCH and rules whose payload can contain commas.
fn rule_fields(rule: &str) -> Option<(String, &str, &str)> {
    let (kind, rest) = rule.split_once(',')?;
    let kind = kind.trim().to_ascii_uppercase();
    if kind.is_empty() || kind == "SUB-RULE" {
        return None;
    }
    let (payload, target) = if kind == "MATCH" {
        ("", rest.split(',').next()?.trim())
    } else {
        let (payload, target) = if logical_kind(&kind)
            || matches!(
                kind.as_str(),
                "DOMAIN-REGEX" | "PROCESS-NAME-REGEX" | "PROCESS-PATH-REGEX"
            ) {
            rest.rsplit_once(',')?
        } else {
            let (payload, rest) = rest.split_once(',')?;
            (payload, rest.split(',').next()?)
        };
        let payload = payload.trim();
        if payload.is_empty() {
            return None;
        }
        (payload, target.trim())
    };
    (!target.is_empty()).then_some((kind, payload, target))
}

fn logical_children(payload: &str) -> Option<Vec<&str>> {
    let content = payload.trim().strip_prefix('(')?.strip_suffix(')')?;
    let mut children = Vec::new();
    let mut depth = 0usize;
    let mut start = 0;
    for (index, ch) in content.char_indices() {
        match ch {
            '(' => {
                if depth == 0 {
                    start = index + 1;
                }
                depth += 1;
            }
            ')' => {
                depth = depth.checked_sub(1)?;
                if depth == 0 {
                    children.push(&content[start..index]);
                }
            }
            ',' => {}
            _ if depth == 0 && !ch.is_whitespace() => return None,
            _ => {}
        }
    }
    (depth == 0 && !children.is_empty()).then_some(children)
}

fn rule_provider_names<'a>(kind: &str, payload: &'a str) -> Option<Vec<&'a str>> {
    let mut names = Vec::new();
    let mut pending = vec![(kind.to_string(), payload)];
    while let Some((kind, payload)) = pending.pop() {
        if kind == "RULE-SET" {
            let name = payload.split(',').next()?.trim();
            if name.is_empty() {
                return None;
            }
            names.push(name);
        } else if logical_kind(&kind) {
            let children = logical_children(payload)?;
            if kind == "NOT" && children.len() != 1 {
                return None;
            }
            for child in children.into_iter().rev() {
                let (kind, payload) = child.split_once(',')?;
                let kind = kind.trim().to_ascii_uppercase();
                let payload = payload.trim();
                if kind.is_empty()
                    || payload.is_empty()
                    || matches!(kind.as_str(), "MATCH" | "SUB-RULE")
                {
                    return None;
                }
                pending.push((kind, payload));
            }
        }
    }
    Some(names)
}

fn download_rules(url: &str) -> Result<Vec<String>, String> {
    let mut last = String::from("download failed");
    for _ in 0..3 {
        let tmp = std::env::temp_dir().join(format!("ppsub_rules_{}.yaml", std::process::id()));
        let tmp_s = tmp.to_string_lossy().into_owned();

        match crate::download::Downloader::new(url, &tmp_s)
            .best_effort(crate::ruleset::PREFETCH_TIMEOUT)
            .download()
        {
            Ok(_) => {
                let data = std::fs::read_to_string(&tmp).map_err(|e| e.to_string())?;
                let _ = std::fs::remove_file(&tmp);
                let docs = YamlLoader::load_from_str(&data).map_err(|e| e.to_string())?;
                if let Some(doc) = docs.first()
                    && let Some(rules) = doc["rules"].as_vec()
                {
                    return Ok(rules
                        .iter()
                        .filter_map(|r| r.as_str().map(String::from))
                        .collect());
                }
                return Err("rules not found in downloaded data".to_string());
            }
            Err(e) => last = e.to_string(),
        }
    }
    Err(last)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn groups_with(names: &[&str]) -> Vec<Yaml> {
        names
            .iter()
            .map(|n| {
                let mut h = Hash::new();
                h.insert(ystr("name"), ystr(n));
                Yaml::Hash(h)
            })
            .collect()
    }

    #[test]
    fn parses_targets_like_mihomo() {
        for target in BUILTIN_TARGETS {
            let rule = format!("MATCH,{target}");
            assert_eq!(rule_fields(&rule).unwrap().2, *target);
        }
        assert_eq!(
            rule_fields("IP-CIDR,192.0.2.0/24,MyGroup,no-resolve")
                .unwrap()
                .2,
            "MyGroup"
        );
        assert_eq!(rule_fields("DOMAIN-REGEX,x{1,2},PROXY").unwrap().2, "PROXY");
        assert!(rule_fields("MATCH").is_none());
        assert!(rule_fields("DOMAIN,DIRECT").is_none());
        assert!(rule_fields("SUB-RULE,(NETWORK,tcp),group").is_none());
    }

    #[test]
    fn recognizes_nested_rule_set_references() {
        let (kind, payload, target) = rule_fields(
            "AND,((RULE-SET,ads),(OR,((NOT,((RULE-SET,geo))),(DOMAIN-SUFFIX,a.com)))),REJECT",
        )
        .unwrap();
        assert_eq!(target, "REJECT");
        assert_eq!(
            rule_provider_names(&kind, payload),
            Some(vec!["ads", "geo"])
        );
        assert_eq!(
            rule_provider_names("DOMAIN-REGEX", "(RULE-SET,ads)"),
            Some(vec![])
        );
        assert!(rule_provider_names("AND", "((RULE-SET,ads)").is_none());
    }

    #[test]
    fn fixrule_and_prune_providers() {
        let groups = groups_with(&["Proxy"]);
        let sets = vec![
            RuleSet {
                priority: 1,
                typ: "rule-set".into(),
                name: "ad".into(),
                url: "http://x/ad".into(),
                behavior: String::new(),
                interval: 0,
                fixrule: vec![],
                is_forced: false,
                format: String::new(),
                proxy: String::new(),
                path: String::new(),
            },
            RuleSet {
                priority: 2,
                typ: "rule-set".into(),
                name: "unused".into(),
                url: "http://x/u".into(),
                behavior: String::new(),
                interval: 0,
                fixrule: vec![],
                is_forced: false,
                format: String::new(),
                proxy: String::new(),
                path: String::new(),
            },
            RuleSet {
                priority: 3,
                typ: String::new(),
                name: String::new(),
                url: String::new(),
                behavior: String::new(),
                interval: 0,
                fixrule: vec![
                    "RULE-SET,ad,REJECT".into(),
                    "DOMAIN,a.com,Proxy".into(),
                    "DOMAIN,b.com,Ghost".into(),
                    "DOMAIN,c.com,PROXY".into(),
                    "MATCH".into(),
                    "MATCH,DIRECT".into(),
                ],
                is_forced: false,
                format: String::new(),
                proxy: String::new(),
                path: String::new(),
            },
        ];
        let (rules, providers) = process_rules(&sets, &groups).unwrap();
        assert!(rules.contains(&"RULE-SET,ad,REJECT".to_string()));
        assert!(rules.contains(&"DOMAIN,a.com,Proxy".to_string()));
        assert!(
            !rules.iter().any(|r| r.contains("Ghost")),
            "invalid group rules removed"
        );
        assert!(!rules.iter().any(|r| r.ends_with(",PROXY") || r == "MATCH"));
        assert!(rules.contains(&"MATCH,DIRECT".to_string()));

        assert!(providers.contains_key(&ystr("ad")));
        assert!(!providers.contains_key(&ystr("unused")));
    }

    #[test]
    fn earliest_provider_priority_wins_for_compound_rules() {
        let provider = |priority, url: &str| RuleSet {
            priority,
            typ: "rule-set".into(),
            name: "ads".into(),
            url: url.into(),
            behavior: String::new(),
            interval: 0,
            fixrule: vec![],
            is_forced: false,
            format: String::new(),
            proxy: String::new(),
            path: String::new(),
        };
        let mut manual = provider(9, "");
        manual.typ.clear();
        let rule = "NOT,((RULE-SET,ads)),REJECT";
        manual.fixrule = vec![rule.into(), "RULE-SET,missing,DIRECT".into()];
        let (rules, providers) = process_rules(
            &[
                provider(5, "https://example.com/later"),
                manual,
                provider(1, "https://example.com/first"),
            ],
            &[],
        )
        .unwrap();
        assert_eq!(rules, [rule]);
        assert_eq!(
            providers[&ystr("ads")]["url"].as_str(),
            Some("https://example.com/first")
        );
    }

    #[test]
    fn provider_interval_and_behavior_defaults() {
        let groups = groups_with(&["Proxy"]);
        let sets = vec![
            RuleSet {
                priority: 1,
                typ: "rule-set".into(),
                name: "a".into(),
                url: "http://a".into(),
                behavior: String::new(),
                interval: 10,
                fixrule: vec![],
                is_forced: false,
                format: String::new(),
                proxy: String::new(),
                path: String::new(),
            },
            RuleSet {
                priority: 2,
                typ: String::new(),
                name: String::new(),
                url: String::new(),
                behavior: String::new(),
                interval: 0,
                fixrule: vec!["RULE-SET,a,REJECT".into()],
                is_forced: false,
                format: String::new(),
                proxy: String::new(),
                path: String::new(),
            },
        ];
        let (_rules, providers) = process_rules(&sets, &groups).unwrap();
        let a = providers.get(&ystr("a")).unwrap();
        assert_eq!(a["interval"].as_i64(), Some(60), "interval<60 → 60");
        assert_eq!(
            a["behavior"].as_str(),
            Some("classical"),
            "default classical"
        );
        assert_eq!(a["type"].as_str(), Some("http"));
    }

    #[test]
    fn provider_format_auto_is_never_left_empty() {
        let rs = |url: &str, format: &str| RuleSet {
            priority: 1,
            typ: "rule-set".into(),
            name: "a".into(),
            url: url.into(),
            behavior: String::new(),
            interval: 0,
            fixrule: vec![],
            is_forced: false,
            format: format.into(),
            proxy: String::new(),
            path: String::new(),
        };
        let fmt = |r: &RuleSet| build_provider(r)["format"].as_str().map(String::from);
        assert_eq!(fmt(&rs("http://x/a.yaml", "")).as_deref(), Some("yaml"));
        assert_eq!(fmt(&rs("http://x/a.mrs?v=1", "")).as_deref(), Some("mrs"));
        assert_eq!(fmt(&rs("http://x/a.list", "")).as_deref(), Some("text"));
        assert_eq!(fmt(&rs("http://x/raw", "")).as_deref(), Some("yaml"));
        assert_eq!(
            fmt(&rs("http://x/a.yaml", "text")).as_deref(),
            Some("text"),
            "explicit format wins over the suffix"
        );
        assert!(
            build_provider(&rs("http://x/raw", ""))["path"]
                .as_str()
                .unwrap()
                .ends_with(".txt")
        );
    }
}
