// Copyright (c) 2026, https://blog.03k.org. All rights reserved.

use arc_swap::ArcSwap;
use ipnet::IpNet;
use std::net::IpAddr;
use std::sync::Arc;

pub type AdminHandle = Arc<ArcSwap<AdminAcl>>;

pub fn admin_handle(acl: AdminAcl) -> AdminHandle {
    Arc::new(ArcSwap::from_pointee(acl))
}

#[derive(Debug, Clone, Default)]
pub struct AdminAcl {
    nets: Vec<IpNet>,
}

impl AdminAcl {
    pub fn new(nets: Vec<IpNet>) -> Self {
        Self { nets }
    }

    pub fn nets(&self) -> &[IpNet] {
        &self.nets
    }

    pub fn allows(&self, ip: IpAddr) -> bool {

        if self.nets.is_empty() {
            return true;
        }
        let ip = match ip {
            IpAddr::V6(addr) => addr.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
            _ => ip,
        };
        ip.is_loopback() || self.nets.iter().any(|net| net.contains(&ip))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_allows_all() {
        let acl = AdminAcl::default();
        assert!(acl.allows("8.8.8.8".parse().unwrap()));
    }

    #[test]
    fn empty_allows_all_including_plain_ipv6() {
        let acl = AdminAcl::default();
        assert!(acl.allows("2001:db8::1".parse().unwrap()));
        assert!(acl.allows("::1".parse().unwrap()));
        assert!(acl.allows("::ffff:8.8.8.8".parse().unwrap()));
    }

    #[test]
    fn non_empty_acl_still_only_admits_loopback_for_plain_ipv6() {
        let acl = AdminAcl::new(vec!["10.0.0.0/8".parse().unwrap()]);
        assert!(acl.allows("::1".parse().unwrap()));
        assert!(!acl.allows("2001:db8::1".parse().unwrap()));
        assert!(acl.allows("::ffff:10.1.2.3".parse().unwrap()));
        assert!(!acl.allows("::ffff:192.168.1.1".parse().unwrap()));
    }

    #[test]
    fn mixed_acl_enforces_ipv6_ranges() {
        let acl = AdminAcl::new(vec!["10.0.0.0/8".parse().unwrap(), "2001:db8::/64".parse().unwrap()]);
        assert!(acl.allows("2001:db8::10".parse().unwrap()));
        assert!(!acl.allows("2001:db8:1::10".parse().unwrap()));
        assert!(!acl.allows("203.0.113.10".parse().unwrap()));
    }

    #[test]
    fn loopback_always_allowed() {
        let acl = AdminAcl::new(vec!["10.0.0.0/8".parse().unwrap()]);
        assert!(acl.allows("127.0.0.1".parse().unwrap()));
        assert!(acl.allows("10.1.2.3".parse().unwrap()));
        assert!(!acl.allows("192.168.1.1".parse().unwrap()));
    }
}
