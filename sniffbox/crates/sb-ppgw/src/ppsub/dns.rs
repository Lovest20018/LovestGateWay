// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use super::hosts::{self, Mapping};
use super::{SubResult, proxies, ystr};
use crate::dohdot::{
    dns_spec_hostname, sanitize_dns_list, sanitize_mihomo_bootstrap_ip, sanitize_mihomo_dns_spec,
};
use std::collections::{BTreeMap, BTreeSet, btree_map::Entry};
use std::net::IpAddr;
use yaml_rust2::yaml::Hash;
use yaml_rust2::{Yaml, YamlLoader};

pub(crate) struct DnsBundle {
    pub dns: Option<Yaml>,
    pub hosts: Option<Yaml>,
}

enum HostTarget {
    Ips(Vec<IpAddr>),
    Alias(String),
}

pub(crate) fn build_dns_and_hosts(sub_results: &[SubResult], ipv6: bool, ex_dns: &str) -> DnsBundle {
    let gateway = proxies::collect_dns_servers("").into_iter().next();
    build_bundle(sub_results, ipv6, gateway.as_deref(), ex_dns)
}

fn build_bundle(
    sub_results: &[SubResult],
    ipv6: bool,
    gateway: Option<&str>,
    ex_dns: &str,
) -> DnsBundle {
    let mut policy = BTreeMap::new();
    let mut hosts_map = BTreeMap::<String, HostTarget>::new();
    let mut subscription_bootstrap = Vec::new();

    for sub in sub_results.iter().filter(|sub| sub.success) {
        let Ok(docs) = YamlLoader::load_from_str(&sub.raw_yaml) else {
            continue;
        };
        let Some(doc) = docs.first() else {
            continue;
        };
        let mut targets: BTreeSet<_> = proxies::sub_domains(sub)
            .iter()
            .filter_map(|domain| normalize_hostname(domain))
            .collect();
        let dns = &doc["dns"];
        if dns.as_hash().is_some() && add_policy(sub, dns, &mut policy, &mut targets) {
            extend_unique(
                &mut subscription_bootstrap,
                sanitize_dns_list(&dns["default-nameserver"], |server| {
                    sanitize_mihomo_bootstrap_ip(server, false)
                }),
            );
        }
        let targets: Vec<_> = targets.into_iter().collect();
        for (domain, mapping) in hosts::parse(doc, &targets).map {
            if domain.eq_ignore_ascii_case("paopaogateway.dns") {
                continue;
            }
            match mapping {
                Mapping::Ips(ips) => {
                    let ips = proxies::usable_static(&ips, ipv6);
                    if !ips.is_empty() {
                        match hosts_map.entry(domain) {
                            Entry::Vacant(entry) => {
                                entry.insert(HostTarget::Ips(ips));
                            }
                            Entry::Occupied(mut entry) => match entry.get_mut() {
                                HostTarget::Ips(existing) => extend_unique(existing, ips),
                                HostTarget::Alias(_) => {
                                    entry.insert(HostTarget::Ips(ips));
                                }
                            },
                        }
                    }
                }
                Mapping::Alias(target) => {
                    let Some(target) = normalize_hostname(&target) else {
                        continue;
                    };
                    if target.eq_ignore_ascii_case("paopaogateway.dns")
                        || creates_alias_cycle(&hosts_map, &domain, &target)
                    {
                        continue;
                    }
                    hosts_map.entry(domain).or_insert(HostTarget::Alias(target));
                }
            }
        }
    }

    let mut dns = Hash::new();
    let hosts = if hosts_map.is_empty() {
        None
    } else {
        dns.insert(ystr("use-hosts"), Yaml::Boolean(true));
        Some(Yaml::Hash(
            hosts_map
                .into_iter()
                .map(|(domain, target)| {
                    let value = match target {
                        HostTarget::Ips(ips) => match ips.as_slice() {
                            [ip] => Yaml::String(ip.to_string()),
                            _ => Yaml::Array(
                                ips.iter().map(|ip| Yaml::String(ip.to_string())).collect(),
                            ),
                        },
                        HostTarget::Alias(alias) => Yaml::String(alias),
                    };
                    (Yaml::String(domain), value)
                })
                .collect(),
        ))
    };
    if !policy.is_empty() {
        dns.insert(
            ystr("proxy-server-nameserver-policy"),
            Yaml::Hash(
                policy
                    .into_iter()
                    .map(|(domain, servers)| (Yaml::String(domain), server_array(servers)))
                    .collect(),
            ),
        );
        let gateway = gateway.and_then(|server| sanitize_mihomo_bootstrap_ip(server, true));
        if let Some(server) = &gateway {
            dns.insert(
                ystr("proxy-server-nameserver"),
                server_array(vec![server.clone()]),
            );
        }
        let mut bootstrap: Vec<_> = gateway.into_iter().collect();
        extend_unique(
            &mut bootstrap,
            ex_dns
                .split(',')
                .filter_map(|server| sanitize_mihomo_bootstrap_ip(server, true)),
        );
        extend_unique(&mut bootstrap, subscription_bootstrap);
        if !bootstrap.is_empty() {
            dns.insert(ystr("default-nameserver"), server_array(bootstrap));
        }
    }
    DnsBundle {
        dns: (!dns.is_empty()).then_some(Yaml::Hash(dns)),
        hosts,
    }
}

fn creates_alias_cycle(
    hosts_map: &BTreeMap<String, HostTarget>,
    domain: &str,
    target: &str,
) -> bool {
    let mut current = target;
    for _ in 0..8 {
        if current == domain {
            return true;
        }
        match hosts_map.get(current) {
            Some(HostTarget::Alias(next)) => current = next.as_str(),
            _ => return false,
        }
    }
    true
}

fn add_policy(
    sub: &SubResult,
    dns: &Yaml,
    policy: &mut BTreeMap<String, Vec<String>>,
    targets: &mut BTreeSet<String>,
) -> bool {
    let proxy_policy = parse_policy(&dns["proxy-server-nameserver-policy"]);
    let proxy_dns = sanitize_dns_list(&dns["proxy-server-nameserver"], sanitize_mihomo_dns_spec);
    let general_policy = parse_policy(&dns["nameserver-policy"]);
    let general_dns = sanitize_dns_list(&dns["nameserver"], sanitize_mihomo_dns_spec);
    let mut alias_sources: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (source, mapping) in &sub.hosts.map {
        if let Mapping::Alias(target) = mapping
            && let (Some(source), Some(target)) =
                (normalize_hostname(source), normalize_hostname(target))
        {
            extend_unique(alias_sources.entry(target).or_default(), [source]);
        }
    }
    let mut selected = BTreeSet::new();
    for domain in proxies::lookup_domains(sub) {
        let Some(domain) = normalize_hostname(&domain) else {
            continue;
        };
        let sources = alias_sources.get(&domain).map(Vec::as_slice).unwrap_or(&[]);
        let servers = matching_servers(&proxy_policy, &domain)
            .or_else(|| {
                sources
                    .iter()
                    .find_map(|source| matching_servers(&proxy_policy, source))
            })
            .or_else(|| (!proxy_dns.is_empty()).then_some(proxy_dns.as_slice()))
            .or_else(|| matching_servers(&general_policy, &domain))
            .or_else(|| {
                sources
                    .iter()
                    .find_map(|source| matching_servers(&general_policy, source))
            })
            .unwrap_or(general_dns.as_slice());
        if servers.is_empty() {
            continue;
        }
        extend_unique(policy.entry(domain).or_default(), servers.iter().cloned());
        selected.extend(servers.iter().map(String::as_str));
    }
    let contributed = !selected.is_empty();
    targets.extend(selected.into_iter().filter_map(|server| {
        dns_spec_hostname(server).and_then(|hostname| normalize_hostname(&hostname))
    }));
    contributed
}

fn parse_policy(value: &Yaml) -> BTreeMap<String, Vec<String>> {
    // Separate YAML keys can expand to the same pattern. Make their order stable.
    let entries: BTreeMap<_, _> = value
        .as_hash()
        .into_iter()
        .flatten()
        .filter_map(|(key, servers)| Some((key.as_str()?, servers)))
        .collect();
    let mut policy = BTreeMap::new();
    for (key, value) in entries {
        let mut patterns = key
            .split(',')
            .map(|pattern| pattern.trim().trim_end_matches('.'))
            .filter(|pattern| !pattern.is_empty() && *pattern != "*" && !pattern.contains(':'))
            .peekable();
        if patterns.peek().is_none() {
            continue;
        }
        let servers = sanitize_dns_list(value, sanitize_mihomo_dns_spec);
        if !servers.is_empty() {
            for pattern in patterns {
                extend_unique(
                    policy.entry(pattern.to_lowercase()).or_default(),
                    servers.iter().cloned(),
                );
            }
        }
    }
    policy
}

fn matching_servers<'a>(
    policy: &'a BTreeMap<String, Vec<String>>,
    domain: &str,
) -> Option<&'a [String]> {
    if let Some(servers) = policy.get(domain) {
        return Some(servers.as_slice());
    }
    if policy.is_empty() {
        return None;
    }
    if let Some(servers) = policy.get(&format!("+.{domain}")) {
        return Some(servers.as_slice());
    }
    // Check the longest suffix first. Only the first split removes exactly one label.
    for (i, (dot, _)) in domain.match_indices('.').enumerate() {
        let suffix = &domain[dot..];
        if i == 0
            && let Some(servers) = policy.get(&format!("*{suffix}"))
        {
            return Some(servers.as_slice());
        }
        if let Some(servers) = policy
            .get(suffix)
            .or_else(|| policy.get(&format!("+{suffix}")))
        {
            return Some(servers.as_slice());
        }
    }
    None
}

fn normalize_hostname(domain: &str) -> Option<String> {
    let domain = domain.trim_end_matches('.');
    // These keys are literal hostnames, even when a provider's input is a pattern.
    if domain.split('.').any(str::is_empty)
        || domain.contains(['*', '+', ',', ':'])
        || domain.chars().any(|c| c.is_whitespace() || c.is_control())
        || domain.parse::<IpAddr>().is_ok()
    {
        return None;
    }
    Some(domain.to_lowercase())
}

fn extend_unique<T: PartialEq>(out: &mut Vec<T>, items: impl IntoIterator<Item = T>) {
    for item in items {
        if !out.contains(&item) {
            out.push(item);
        }
    }
}

fn server_array(servers: Vec<String>) -> Yaml {
    Yaml::Array(servers.into_iter().map(Yaml::String).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ppsub::sub::parse_payload;

    fn subscription(name: &str, yaml: &str) -> SubResult {
        let (proxies, hosts) = parse_payload(yaml, name);
        SubResult {
            name: name.to_string(),
            success: true,
            proxies,
            hosts,
            userinfo: None,
            cached_at: None,
            raw_yaml: yaml.to_string(),
            error: String::new(),
        }
    }

    fn servers(value: &Yaml) -> Vec<&str> {
        value
            .as_vec()
            .unwrap()
            .iter()
            .map(|entry| entry.as_str().unwrap())
            .collect()
    }

    #[test]
    fn subscriptions_keep_their_own_dns_and_nodes_without_dns_use_gateway() {
        let nexitally = subscription(
            "nexitally",
            r#"
proxies:
  - {name: one, server: NY.Nexitally.example}
  - {name: literal, server: 1.2.3.4}
dns:
  nameserver:
    - 'https://doh.nexitally.example/dns-query/nex-path#GROUP'
    - 'tls://dot.nexitally.example#GROUP'
"#,
        );
        let kuromis = subscription(
            "kuromis",
            r#"
proxies: [{name: two, server: jp.kuromis.example}]
dns:
  proxy-server-nameserver: 'https://doh.kuromis.example/dns-query/kuro-path#h3=true'
"#,
        );
        let no_dns = subscription("plain", "proxies: [{name: three, server: plain.example}]");
        let mut failed = subscription(
            "failed",
            "proxies: [{name: four, server: failed.example}]\ndns: {nameserver: 223.5.5.5}",
        );
        failed.success = false;
        let bundle = build_bundle(
            &[nexitally, kuromis, no_dns, failed],
            false,
            Some("10.10.10.2:53"),
            "",
        );
        let dns = bundle.dns.unwrap();
        let policy = &dns["proxy-server-nameserver-policy"];
        assert_eq!(policy.as_hash().unwrap().len(), 2);
        assert_eq!(
            servers(&policy["ny.nexitally.example"]),
            [
                "https://doh.nexitally.example/dns-query/nex-path",
                "tls://dot.nexitally.example:853",
            ]
        );
        assert_eq!(
            servers(&policy["jp.kuromis.example"]),
            ["https://doh.kuromis.example/dns-query/kuro-path"]
        );
        assert_eq!(servers(&dns["proxy-server-nameserver"]), ["10.10.10.2:53"]);
        assert!(bundle.hosts.is_none());
    }

    #[test]
    fn shared_node_domains_merge_resolvers_in_subscription_order() {
        let first = subscription(
            "first",
            r#"
proxies: [{name: one, server: shared.example}]
dns:
  nameserver: ['https://dns.first.example/dns-query#group', 'tls://dot.first.example']
"#,
        );
        let second = subscription(
            "second",
            r#"
proxies: [{name: two, server: Shared.EXAMPLE}]
dns:
  nameserver: ['https://dns.first.example/dns-query', 'https://dns.second.example/dns-query']
"#,
        );
        let dns = build_bundle(&[first, second], false, None, "").dns.unwrap();
        assert_eq!(
            servers(&dns["proxy-server-nameserver-policy"]["shared.example"]),
            [
                "https://dns.first.example/dns-query",
                "tls://dot.first.example:853",
                "https://dns.second.example/dns-query",
            ]
        );
    }

    #[test]
    fn proxy_dns_precedes_general_dns_and_fallback_is_ignored() {
        let proxy = subscription(
            "proxy",
            r#"
proxies:
  - {name: explicit, server: explicit.example}
  - {name: proxy, server: proxy.example}
  - {name: invalid, server: '*.example'}
dns:
  proxy-server-nameserver-policy:
    EXPLICIT.EXAMPLE: 'tls://explicit.dns.example#GROUP'
    proxy.example: system
    '*': 1.1.1.1
  proxy-server-nameserver: [8.8.8.8]
  nameserver-policy:
    '+.example': 'tls://ignored.example'
  nameserver: [8.8.4.4]
  fallback: [1.0.0.1]
"#,
        );
        let general = subscription(
            "general",
            r#"
proxies:
  - {name: specific, server: specific.example}
  - {name: general, server: general.example}
dns:
  proxy-server-nameserver: [system]
  nameserver-policy:
    specific.example: 'https://specific.dns.example/dns-query'
    general.example: 'quic://ignored.example'
  nameserver: [9.9.9.9]
  fallback: [1.0.0.1]
"#,
        );
        let fallback = subscription(
            "fallback",
            "proxies: [{name: fallback, server: fallback.example}]\ndns: {fallback: [1.0.0.1]}",
        );
        let dns = build_bundle(&[proxy, general, fallback], false, None, "")
            .dns
            .unwrap();
        let policy = &dns["proxy-server-nameserver-policy"];
        assert_eq!(policy.as_hash().unwrap().len(), 4);
        assert_eq!(
            servers(&policy["explicit.example"]),
            ["tls://explicit.dns.example:853"]
        );
        assert_eq!(servers(&policy["proxy.example"]), ["8.8.8.8:53"]);
        assert_eq!(
            servers(&policy["specific.example"]),
            ["https://specific.dns.example/dns-query"]
        );
        assert_eq!(servers(&policy["general.example"]), ["9.9.9.9:53"]);
    }

    #[test]
    fn policy_supports_exact_comma_and_wildcard_patterns() {
        let doc = &YamlLoader::load_from_str(
            r#"
    'Exact.B.example, ANOTHER.example': 1.1.1.1
    '+.b.example': 8.8.8.8
    '*.b.example': 9.9.9.9
    '.c.example': 1.0.0.1
    '*': 4.2.2.2
    'geosite:global': 4.2.2.2
    'rule-set:global': 4.2.2.2
"#,
        )
        .unwrap()[0];
        let policy = parse_policy(doc);
        assert_eq!(policy.len(), 5);
        for (host, expected) in [
            ("exact.b.example", "1.1.1.1:53"),
            ("another.example", "1.1.1.1:53"),
            ("b.example", "8.8.8.8:53"),
            ("one.b.example", "9.9.9.9:53"),
            ("two.one.b.example", "8.8.8.8:53"),
            ("one.c.example", "1.0.0.1:53"),
            ("two.one.c.example", "1.0.0.1:53"),
        ] {
            let servers = matching_servers(&policy, host).unwrap();
            assert_eq!(servers.len(), 1);
            assert_eq!(servers[0], expected);
        }
        for host in ["c.example", "notb.example", "other.example"] {
            assert!(matching_servers(&policy, host).is_none(), "{host}");
        }
    }

    #[test]
    fn hosts_include_nodes_and_selected_resolver_names_and_respect_ipv6() {
        let first = subscription(
            "first",
            r#"
proxies:
  - {name: fixed, server: Node.Example.com}
  - {name: alias, server: alias.example.com}
  - {name: gateway, server: PAOPAOGATEWAY.DNS}
dns:
  proxy-server-nameserver: ['https://Private.Dns.example/dns-query/token', 'tls://dot.dns.example']
  nameserver: ['https://unused.dns.example/dns-query']
hosts:
  node.example.com: [1.1.1.1, '2606:4700::1111', 198.18.0.1, 127.0.0.1]
  alias.example.com: target.example.com
  private.dns.example: 9.9.9.9
  '*.dns.example': 4.2.2.2
  PAOPAOGATEWAY.DNS: 8.8.8.8
"#,
        );
        let second = subscription(
            "second",
            r#"
proxies: [{name: node, server: node.example.com}]
hosts: {node.example.com: [1.1.1.1, 8.8.8.8]}
"#,
        );
        let subscriptions = [first, second];
        let bundle = build_bundle(&subscriptions, false, None, "");
        let dns = bundle.dns.unwrap();
        assert_eq!(dns["use-hosts"].as_bool(), Some(true));
        assert!(dns["proxy-server-nameserver-policy"]["target.example.com"].as_vec().is_some());

        let hosts = bundle.hosts.unwrap();
        assert_eq!(hosts.as_hash().unwrap().len(), 4);
        assert_eq!(servers(&hosts["node.example.com"]), ["1.1.1.1", "8.8.8.8"]);
        assert_eq!(
            hosts["alias.example.com"].as_str(),
            Some("target.example.com")
        );
        assert_eq!(hosts["private.dns.example"].as_str(), Some("9.9.9.9"));
        assert_eq!(hosts["dot.dns.example"].as_str(), Some("4.2.2.2"));
        let hosts = build_bundle(&subscriptions, true, None, "").hosts.unwrap();
        assert_eq!(
            servers(&hosts["node.example.com"]),
            ["1.1.1.1", "2606:4700::1111", "8.8.8.8"]
        );
        assert_eq!(
            hosts["alias.example.com"].as_str(),
            Some("target.example.com")
        );
    }

    #[test]
    fn alias_hosts_inherit_source_policy_prefer_ips_and_reject_cross_sub_cycles() {
        let first = subscription(
            "first",
            r#"
proxies:
  - {name: one, server: a.alias.example.com}
  - {name: two, server: cycle-a.example.com}
dns:
  nameserver-policy:
    '+.alias.example.com': 'https://doh.first.example/dns-query/token'
  nameserver: [223.5.5.5]
hosts:
  a.alias.example.com: target.real.example.net
  cycle-a.example.com: cycle-b.example.com
"#,
        );
        let second = subscription(
            "second",
            r#"
proxies:
  - {name: three, server: cycle-b.example.com}
  - {name: four, server: a.alias.example.com}
hosts:
  cycle-b.example.com: cycle-a.example.com
  a.alias.example.com: 9.9.9.9
"#,
        );
        let bundle = build_bundle(&[first, second], false, None, "");
        let dns = bundle.dns.unwrap();
        let policy = &dns["proxy-server-nameserver-policy"];
        assert_eq!(
            servers(&policy["a.alias.example.com"]),
            ["https://doh.first.example/dns-query/token"]
        );
        assert_eq!(
            servers(&policy["target.real.example.net"]),
            ["https://doh.first.example/dns-query/token"]
        );
        let hosts = bundle.hosts.unwrap();
        assert_eq!(hosts["a.alias.example.com"].as_str(), Some("9.9.9.9"));
        assert_eq!(
            hosts["cycle-a.example.com"].as_str(),
            Some("cycle-b.example.com")
        );
        assert!(hosts["cycle-b.example.com"].is_badvalue());
    }

    #[test]
    fn hosts_without_subscription_dns_only_enable_use_hosts() {
        let sub = subscription(
            "plain",
            "proxies: [{name: one, server: node.example}]\nhosts: {node.example: 1.1.1.1}",
        );
        let bundle = build_bundle(&[sub], false, Some("10.10.10.2:53"), "1.0.0.1");
        let dns = bundle.dns.unwrap();
        assert_eq!(dns.as_hash().unwrap().len(), 1);
        assert_eq!(dns["use-hosts"].as_bool(), Some(true));
        assert_eq!(
            bundle.hosts.unwrap()["node.example"].as_str(),
            Some("1.1.1.1")
        );
    }

    #[test]
    fn bootstrap_starts_with_gateway_then_extended_and_contributing_subscriptions() {
        let active = subscription(
            "active",
            r#"
proxies: [{name: active, server: active.example}]
dns:
  nameserver: ['https://active.dns.example/query']
  default-nameserver:
    - 8.8.8.8
    - 9.9.9.9
    - 8.8.8.8
    - 192.168.1.2
    - 198.18.0.1
    - 'https://ignored.example'
"#,
        );
        let inactive = subscription(
            "inactive",
            r#"
proxies: [{name: inactive, server: inactive.example}]
dns: {default-nameserver: [4.2.2.2]}
"#,
        );
        let dns = build_bundle(
            &[active, inactive],
            false,
            Some("10.10.10.2:53"),
            "tcp://192.168.1.1#eth0, 8.8.8.8, udp://10.10.10.2, tls://ignored.example",
        )
        .dns
        .unwrap();
        assert_eq!(servers(&dns["proxy-server-nameserver"]), ["10.10.10.2:53"]);
        assert_eq!(
            servers(&dns["default-nameserver"]),
            ["10.10.10.2:53", "192.168.1.1:53", "8.8.8.8:53", "9.9.9.9:53"]
        );
    }
}
