// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use crate::dohdot::{sanitize_dns_list, sanitize_mihomo_bootstrap_ip, sanitize_mihomo_dns_spec};
use crate::ppsub::ystr;
use crate::{dnsutil, dohdot};
use std::collections::HashSet;
use std::net::IpAddr;
use yaml_rust2::yaml::Hash;
use yaml_rust2::{Yaml, YamlEmitter, YamlLoader};

#[derive(Debug, thiserror::Error)]
pub enum YamlTxErr {
    #[error("read {0}: {1}")]
    Read(String, String),
    #[error("parse {0}: {1}")]
    Parse(String, String),
    #[error("{0}: not a yaml map")]
    NotMap(String),
    #[error("No 'proxies' found in the YAML file")]
    NoProxies,
    #[error("emit: {0}")]
    Emit(String),
}

pub fn combine(files: &[String]) -> Result<String, YamlTxErr> {
    let mut result = Hash::new();
    let hosts_key = ystr("hosts");
    let dns_key = ystr("dns");
    for f in files {
        let doc = load(&read(f)?, f)?;
        let map = doc.as_hash().ok_or_else(|| YamlTxErr::NotMap(f.clone()))?;
        for (k, v) in map {
            if k == &dns_key {
                continue;
            }
            let combined = if k == &hosts_key
                && let (Some(existing), Some(base)) =
                    (result.get(k).and_then(Yaml::as_hash), v.as_hash())
            {
                Yaml::Hash(merge_hosts(existing, base))
            } else {
                v.clone()
            };
            result.insert(k.clone(), combined);
        }
        // Resolve use-hosts against the final hosts map for this file, independent of YAML order.
        if let Some(v) = map.get(&dns_key) {
            let combined = match (result.get(&dns_key).and_then(Yaml::as_hash), v.as_hash()) {
                (Some(existing), Some(base)) => Yaml::Hash(merge_dns(
                    existing,
                    base,
                    result.get(&hosts_key).and_then(Yaml::as_hash),
                )),
                _ => v.clone(),
            };
            result.insert(dns_key.clone(), combined);
        }
    }
    emit(&Yaml::Hash(result))
}

fn merge_hosts(existing: &Hash, base: &Hash) -> Hash {
    let canonical = |host: &str| host.trim_end_matches('.').to_lowercase();
    let base_domains: HashSet<_> = base
        .keys()
        .filter_map(Yaml::as_str)
        .map(canonical)
        .collect();
    existing
        .iter()
        .filter(|(key, _)| {
            key.as_str()
                .is_none_or(|host| !base_domains.contains(&canonical(host)))
        })
        .chain(base)
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

fn merge_dns(existing: &Hash, base: &Hash, merged_hosts: Option<&Hash>) -> Hash {
    let mut dns = base.clone();
    let policy_key = ystr("proxy-server-nameserver-policy");
    let policy: Hash = existing
        .get(&policy_key)
        .and_then(Yaml::as_hash)
        .into_iter()
        .flatten()
        .filter_map(|(key, value)| {
            key.as_str().filter(|key| !key.trim().is_empty())?;
            let servers = sanitize_dns_list(value, sanitize_mihomo_dns_spec);
            (!servers.is_empty()).then(|| (key.clone(), server_array(servers)))
        })
        .collect();
    if !policy.is_empty() {
        dns.insert(policy_key.clone(), Yaml::Hash(policy));
    }
    let has_policy = dns
        .get(&policy_key)
        .and_then(Yaml::as_hash)
        .is_some_and(|policy| !policy.is_empty());
    let base_nameservers = base.get(&ystr("nameserver"));

    let proxy_key = ystr("proxy-server-nameserver");
    let proxy_servers = existing
        .get(&proxy_key)
        .filter(|value| value.as_vec().is_some())
        .map(|value| {
            sanitize_dns_list(value, |server| {
                sanitize_mihomo_bootstrap_ip(server, true)
                    .or_else(|| sanitize_mihomo_dns_spec(server))
            })
        })
        .unwrap_or_default();
    if !proxy_servers.is_empty() {
        dns.insert(proxy_key, server_array(proxy_servers));
    } else if has_policy
        && dns
            .get(&proxy_key)
            .and_then(Yaml::as_vec)
            .is_none_or(Vec::is_empty)
        && let Some(servers) =
            base_nameservers.filter(|value| value.as_vec().is_some_and(|v| !v.is_empty()))
    {
        dns.insert(proxy_key, servers.clone());
    }

    let default_key = ystr("default-nameserver");
    let sanitize_bootstrap = |value: &Yaml| {
        sanitize_dns_list(value, |server| sanitize_mihomo_bootstrap_ip(server, true))
    };
    let mut bootstrap = existing
        .get(&default_key)
        .filter(|value| value.as_vec().is_some())
        .map(sanitize_bootstrap)
        .unwrap_or_default();
    if bootstrap.is_empty()
        && has_policy
        && dns
            .get(&default_key)
            .and_then(Yaml::as_vec)
            .is_none_or(Vec::is_empty)
        && let Some(nameservers) = base_nameservers
    {
        bootstrap = sanitize_bootstrap(nameservers);
    }
    if !bootstrap.is_empty() {
        dns.insert(default_key, server_array(bootstrap));
    }

    let use_hosts_key = ystr("use-hosts");
    if existing.get(&use_hosts_key) == Some(&Yaml::Boolean(true))
        && merged_hosts.is_some_and(|hosts| {
            hosts.keys().filter_map(Yaml::as_str).any(|host| {
                !host
                    .trim_end_matches('.')
                    .eq_ignore_ascii_case("paopaogateway.dns")
            })
        })
    {
        dns.insert(use_hosts_key, Yaml::Boolean(true));
    }
    dns
}

fn server_array(servers: Vec<String>) -> Yaml {
    Yaml::Array(servers.into_iter().map(Yaml::String).collect())
}

pub fn dns_burn(
    input_file: &str,
    dnslist: &str,
    ipv6_enabled: bool,
) -> Result<(String, usize), YamlTxErr> {
    let doc = load(&read(input_file)?, input_file)?;
    let mut config = doc
        .as_hash()
        .ok_or_else(|| YamlTxErr::NotMap(input_file.to_string()))?
        .clone();
    let proxies = doc["proxies"].as_vec().ok_or(YamlTxErr::NoProxies)?.clone();

    let dns_servers: Vec<String> = dnslist
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    let mut domains: Vec<String> = Vec::new();
    let mut domain_seen: HashSet<String> = HashSet::new();
    for p in &proxies {
        if let Some(server) = p["server"].as_str()
            && server.parse::<IpAddr>().is_err()
            && domain_seen.insert(server.to_string())
        {
            domains.push(server.to_string());
        }
    }

    let ex_resolved = dnsutil::resolve_domains_concurrent(&domains, &dns_servers, ipv6_enabled);

    let sub_specs = dohdot::extract_dns_servers_for_all_nodes(&doc["dns"]);
    let sub_resolved = if !sub_specs.is_empty() {
        let got = resolve_subdns_batch(&domains, &sub_specs, ipv6_enabled);
        crate::ppsub::proxies::subtract_known(got, &ex_resolved, "subdns")
    } else {
        std::collections::HashMap::new()
    };

    let mut used: HashSet<String> = proxies
        .iter()
        .filter_map(|p| p["name"].as_str().map(String::from))
        .collect();

    let mut ex_nodes: Vec<Yaml> = Vec::new();
    let mut subdns_nodes: Vec<Yaml> = Vec::new();

    for p in &proxies {
        let Some(server) = p["server"].as_str() else {
            continue;
        };
        if server.parse::<IpAddr>().is_ok() {
            continue;
        }
        let Some(ph) = p.as_hash() else {
            continue;
        };
        let base = p["name"].as_str().unwrap_or("").to_string();

        for ip in ex_resolved.get(server).cloned().unwrap_or_default() {
            let ipstr = ip.to_string();
            let uniq = generate_node_name(&base, &ipstr, &used);
            used.insert(uniq.clone());
            let mut np = ph.clone();
            np.insert(Yaml::String("name".to_string()), Yaml::String(uniq));
            np.insert(Yaml::String("server".to_string()), Yaml::String(ipstr));
            ex_nodes.push(Yaml::Hash(np));
        }

        for ip in sub_resolved.get(server).cloned().unwrap_or_default() {
            let ipstr = ip.to_string();
            let uniq = generate_subdns_node_name(&base, &ipstr, &used);
            used.insert(uniq.clone());
            let mut np = ph.clone();
            np.insert(Yaml::String("name".to_string()), Yaml::String(uniq));
            np.insert(Yaml::String("server".to_string()), Yaml::String(ipstr));
            subdns_nodes.push(Yaml::Hash(np));
        }
    }

    let added = ex_nodes.len() + subdns_nodes.len();
    let mut all = subdns_nodes;
    all.extend(proxies);
    all.extend(ex_nodes);
    config.insert(Yaml::String("proxies".to_string()), Yaml::Array(all));
    Ok((emit(&Yaml::Hash(config))?, added))
}

fn resolve_subdns_batch(
    domains: &[String],
    specs: &[String],
    ipv6_enabled: bool,
) -> std::collections::HashMap<String, Vec<IpAddr>> {
    crate::resolvepool::resolve_batch(
        crate::resolvepool::DNS_LOG,
        "subdns",
        domains,
        crate::resolvepool::Budget::subdns(),
        |domain, dl| crate::dohdot::resolve_via_servers_traced(domain, specs, ipv6_enabled, dl),
    )
}

pub(crate) fn generate_node_name(base: &str, ip: &str, used: &HashSet<String>) -> String {
    generate_node_name_with_prefix(base, ip, used, "")
}

fn generate_subdns_node_name(base: &str, ip: &str, used: &HashSet<String>) -> String {
    generate_node_name_with_prefix(base, ip, used, "subdns")
}

pub(crate) fn generate_node_name_with_prefix(
    base: &str,
    ip: &str,
    used: &HashSet<String>,
    prefix: &str,
) -> String {
    let suffix = if ip.contains(':') {
        let clean: String = ip.chars().filter(|c| *c != ':').collect();
        if clean.len() > 4 {
            clean[clean.len() - 4..].to_string()
        } else {
            clean
        }
    } else {
        ip.rsplit('.').next().unwrap_or(ip).to_string()
    };
    let candidate = if prefix.is_empty() {
        format!("{base}@{suffix}")
    } else {
        format!("{base}@{prefix}{suffix}")
    };
    if !used.contains(&candidate) {
        return candidate;
    }
    let mut counter = 0i64;
    loop {
        let cand = format!("{candidate}{}", generate_suffix(counter));
        if !used.contains(&cand) {
            return cand;
        }
        counter += 1;
    }
}

pub(crate) fn generate_suffix(mut n: i64) -> String {
    let mut s = String::new();
    loop {
        let rem = (n % 26) as u8;
        s.insert(0, (b'A' + rem) as char);
        n = n / 26 - 1;
        if n < 0 {
            break;
        }
    }
    s
}

fn read(file: &str) -> Result<String, YamlTxErr> {
    std::fs::read_to_string(file).map_err(|e| YamlTxErr::Read(file.to_string(), e.to_string()))
}

fn load(content: &str, file: &str) -> Result<Yaml, YamlTxErr> {
    let mut docs = YamlLoader::load_from_str(content)
        .map_err(|e| YamlTxErr::Parse(file.to_string(), e.to_string()))?;
    if docs.is_empty() {
        return Err(YamlTxErr::Parse(
            file.to_string(),
            "empty document".to_string(),
        ));
    }
    Ok(docs.remove(0))
}

fn emit(y: &Yaml) -> Result<String, YamlTxErr> {
    let mut buf = String::new();
    YamlEmitter::new(&mut buf)
        .dump(y)
        .map_err(|e| YamlTxErr::Emit(format!("{e:?}")))?;
    Ok(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suffix_is_bijective_base26() {
        assert_eq!(generate_suffix(0), "A");
        assert_eq!(generate_suffix(25), "Z");
        assert_eq!(generate_suffix(26), "AA");
        assert_eq!(generate_suffix(27), "AB");
        assert_eq!(generate_suffix(51), "AZ");
        assert_eq!(generate_suffix(52), "BA");
    }

    #[test]
    fn node_name_octet_and_collision() {
        let mut used = HashSet::new();
        let n1 = generate_node_name("Tokyo", "1.2.3.4", &used);
        assert_eq!(n1, "Tokyo@4");
        used.insert(n1);

        let n2 = generate_node_name("Tokyo", "9.9.9.4", &used);
        assert_eq!(n2, "Tokyo@4A");
        used.insert(n2);
        let n3 = generate_node_name("Tokyo", "8.8.8.4", &used);
        assert_eq!(n3, "Tokyo@4B");
    }

    #[test]
    fn node_name_ipv6_last4_hex() {
        let used = HashSet::new();
        let n = generate_node_name("HK", "2001:db8::beef", &used);
        assert_eq!(n, "HK@beef");
    }

    #[test]
    fn combine_merges_top_level_last_wins() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.yaml");
        let b = dir.path().join("b.yaml");
        std::fs::write(&a, "proxies:\n  - {name: x}\nmode: rule\n").unwrap();
        std::fs::write(&b, "mode: global\ntproxy-port: 1081\n").unwrap();
        let out = combine(&[
            a.to_str().unwrap().to_string(),
            b.to_str().unwrap().to_string(),
        ])
        .unwrap();
        let doc = &YamlLoader::load_from_str(&out).unwrap()[0];
        assert_eq!(
            doc["mode"].as_str(),
            Some("global"),
            "latter overrides mode"
        );
        assert_eq!(doc["tproxy-port"].as_i64(), Some(1081), "keep keys from b");
        assert!(doc["proxies"].as_vec().is_some(), "keep proxies from a");
    }

    fn combine_fixture(custom: &str, base: &str) -> Yaml {
        let dir = tempfile::tempdir().unwrap();
        let custom_path = dir.path().join("a.yaml");
        let base_path = dir.path().join("base.yaml");
        std::fs::write(&custom_path, custom).unwrap();
        std::fs::write(&base_path, base).unwrap();
        let merged = combine(&[
            custom_path.to_str().unwrap().to_string(),
            base_path.to_str().unwrap().to_string(),
        ])
        .unwrap();
        YamlLoader::load_from_str(&merged).unwrap().remove(0)
    }

    fn servers(value: &Yaml) -> Vec<&str> {
        value
            .as_vec()
            .unwrap()
            .iter()
            .map(|server| server.as_str().unwrap())
            .collect()
    }

    #[test]
    fn combine_keeps_base_dns_settings_with_sanitized_subscription_dns_and_hosts() {
        let doc = combine_fixture(
            r#"
dns:
  enable: false
  use-system-hosts: true
  listen: 0.0.0.0:1053
  ipv6: true
  enhanced-mode: fake-ip
  nameserver: [1.1.1.1]
  use-hosts: true
  proxy-server-nameserver-policy:
    node.example: ['https://dns.example/query/token#PROXY', 'https://dns.example/query/token', system]
    dot.example: 'tls://dot.dns.example#PROXY'
    ignored.example: [quic://ignored.example, 198.18.0.1]
  proxy-server-nameserver: ['10.10.10.2:53', 'tls://dot.dns.example#PROXY']
  default-nameserver: ['192.168.1.1:53', 'https://dns.example/query', 'localhost:53', '198.18.0.1:53']
hosts:
  PAOPAOGATEWAY.DNS: 1.1.1.1
  node.example: 8.8.4.4
  alias.example: target.example
  dns.example: 9.9.9.9
"#,
            r#"
dns:
  enable: true
  use-system-hosts: false
  listen: ''
  ipv6: false
  enhanced-mode: normal
  use-hosts: false
  nameserver: ['10.10.10.2:53#eth0']
hosts:
  paopaogateway.dns: 127.0.0.1
  node.example: 8.8.8.8
"#,
        );
        let dns = &doc["dns"];
        assert_eq!(dns["enable"].as_bool(), Some(true));
        assert_eq!(dns["use-system-hosts"].as_bool(), Some(false));
        assert_eq!(dns["listen"].as_str(), Some(""));
        assert_eq!(dns["ipv6"].as_bool(), Some(false));
        assert_eq!(dns["enhanced-mode"].as_str(), Some("normal"));
        assert_eq!(servers(&dns["nameserver"]), ["10.10.10.2:53#eth0"]);
        assert_eq!(dns["use-hosts"].as_bool(), Some(true));
        assert_eq!(
            servers(&dns["proxy-server-nameserver"]),
            ["10.10.10.2:53", "tls://dot.dns.example:853"]
        );
        assert_eq!(servers(&dns["default-nameserver"]), ["192.168.1.1:53"]);

        let policy = &dns["proxy-server-nameserver-policy"];
        assert_eq!(policy.as_hash().unwrap().len(), 2);
        assert_eq!(servers(&policy["node.example"]), ["https://dns.example/query/token"]);
        assert_eq!(servers(&policy["dot.example"]), ["tls://dot.dns.example:853"]);
        let hosts = &doc["hosts"];
        assert_eq!(hosts.as_hash().unwrap().len(), 4);
        assert_eq!(hosts["paopaogateway.dns"].as_str(), Some("127.0.0.1"));
        assert_eq!(hosts["node.example"].as_str(), Some("8.8.8.8"));
        assert_eq!(hosts["alias.example"].as_str(), Some("target.example"));
        assert_eq!(hosts["dns.example"].as_str(), Some("9.9.9.9"));
    }

    #[test]
    fn combine_backfills_policy_resolvers_from_base_nameserver() {
        let doc = combine_fixture(
            r#"
dns:
  proxy-server-nameserver-policy:
    node.example: 'https://dns.example/dns-query'
"#,
            r#"
dns:
  nameserver: ['10.10.10.2:53#eth0']
"#,
        );
        let dns = &doc["dns"];
        assert_eq!(servers(&dns["proxy-server-nameserver"]), ["10.10.10.2:53#eth0"]);
        assert_eq!(servers(&dns["default-nameserver"]), ["10.10.10.2:53"]);
    }

    #[test]
    fn combine_checks_for_extra_hosts_after_merging_base_hosts() {
        let custom = r#"
hosts: {paopaogateway.dns: 1.1.1.1}
dns:
  use-hosts: true
  enhanced-mode: normal
  listen: 0.0.0.0:1053
  nameserver: [1.1.1.1]
"#;
        let base_dns = r#"
dns:
  use-hosts: false
  enhanced-mode: fake-ip
  listen: 127.0.0.1:5353
  nameserver: ['10.10.10.2:53#eth0']
"#;
        let base_hosts = "hosts: {paopaogateway.dns: 127.0.0.1}\n";
        let doc = combine_fixture(custom, &format!("{base_dns}{base_hosts}"));
        assert_eq!(doc["hosts"]["paopaogateway.dns"].as_str(), Some("127.0.0.1"));
        let dns = &doc["dns"];
        assert_eq!(dns["use-hosts"].as_bool(), Some(false));
        assert_eq!(dns["enhanced-mode"].as_str(), Some("fake-ip"));
        assert_eq!(dns["listen"].as_str(), Some("127.0.0.1:5353"));
        assert_eq!(servers(&dns["nameserver"]), ["10.10.10.2:53#eth0"]);
        assert!(dns["proxy-server-nameserver"].as_vec().is_none());
        assert!(dns["default-nameserver"].as_vec().is_none());

        let base_hosts = "hosts: {paopaogateway.dns: 127.0.0.1, base.example: 9.9.9.9}\n";
        for base in [format!("{base_dns}{base_hosts}"), format!("{base_hosts}{base_dns}")] {
            let doc = combine_fixture(custom, &base);
            assert_eq!(doc["dns"]["use-hosts"].as_bool(), Some(true));
        }
    }
}
