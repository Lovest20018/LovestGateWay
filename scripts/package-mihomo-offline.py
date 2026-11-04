#!/usr/bin/env python3
"""Package a pinned official Mihomo asset for an already installed Debian gateway."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from mihomo_update import API, MAX_COMPRESSED, MAX_BINARY, fetch, release_asset, validate_elf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    def download(url, limit):
        for attempt in range(3):
            try: return fetch(url, limit)
            except ValueError as error:
                if str(error) not in {'RELEASE_NETWORK_TIMEOUT', 'RELEASE_NETWORK_FAILED', 'RELEASE_DOWNLOAD_INTERRUPTED'} or attempt == 2: raise
                time.sleep(1)
    release = release_asset(json.loads(download(API, 2 * 1024 * 1024)), 'amd64')
    cache = args.output / 'mihomo-official-cache' / release['asset']
    raw = cache.read_bytes() if cache.is_file() else download(release['url'], MAX_COMPRESSED)
    if len(raw) != release['size'] or hashlib.sha256(raw).hexdigest() != release['sha256']:
        raise SystemExit('Official asset checksum mismatch')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        binary = stream.read(MAX_BINARY + 1)
    if len(binary) > MAX_BINARY: raise SystemExit('Binary too large')
    validate_elf(binary)
    license_url = f"https://raw.githubusercontent.com/MetaCubeX/mihomo/{release['version']}/LICENSE"
    license_api = f"https://api.github.com/repos/MetaCubeX/mihomo/contents/LICENSE?ref={release['version']}"
    license_record = json.loads(download(license_api, 256 * 1024))
    if license_record.get('encoding') != 'base64': raise SystemExit('Missing upstream license')
    license_data = base64.b64decode(''.join(license_record['content'].split()), validate=True)
    if not license_data: raise SystemExit('Missing upstream license')
    name = f"lovestgateway-mihomo-offline-{release['version']}-amd64"
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mihomo-offline-') as temporary:
        stage = Path(temporary) / name
        stage.mkdir()
        (stage / release['asset']).write_bytes(raw)
        (stage / 'mihomo_update.py').write_bytes((ROOT / 'integration/mihomo_update.py').read_bytes())
        (stage / 'release.json').write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
        (stage / 'LICENSE').write_bytes(license_data)
        source = f"https://github.com/MetaCubeX/mihomo/tree/{release['version']}"
        (stage / 'SOURCE.txt').write_text(source + '\n' + license_url + '\n', encoding='utf-8')
        install = f"""#!/bin/sh
set -eu
export LANG=C.UTF-8 LC_ALL=C.UTF-8
if [ "$(id -u)" -ne 0 ]; then echo '请运行 sudo sh install.sh。' >&2; exit 1; fi
bundle_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
cd "$bundle_dir"
if [ ! -f /usr/local/lib/paopaogw/manage_common.py ] || [ ! -f /usr/local/lib/paopaogw/gateway_api.py ]; then
    echo '请先安装 LovestGateWay，再安装离线 Mihomo 核心。' >&2; exit 1
fi
sha256sum -c SHA256SUMS
printf '%s\\n' '即将安装官方 Mihomo {release['version']}（amd64 compatible）。运行中的网关会重启一次以验收。'
export PYTHONPATH=/usr/local/lib/paopaogw
exec /usr/bin/python3 -B -X utf8 "$bundle_dir/mihomo_update.py" install-local \\
    --archive "$bundle_dir/{release['asset']}" \\
    --sha256 {release['sha256']} --version {release['version']}
"""
        (stage / 'install.sh').write_text(install, encoding='utf-8', newline='\n')
        readme = f"""# LovestGateWay 离线 Mihomo 核心

适用：已安装 LovestGateWay 的 Debian amd64。离线包包含官方 MetaCubeX/mihomo {release['version']} 的 amd64 compatible 资产，不是网关完整安装包。

在可信电脑取得此包和外部 .sha256，核对整个包后拷到 Debian，解压进入目录并运行：

```sh
sudo sh install.sh
```

安装期间无需访问 GitHub。脚本校验包内清单及固定官方 SHA256，再检查 ELF、实际版本和已有运行配置。安装成功后重启原本活动的网关并验收；未运行的网关保持停止。原有核心会备份，验收失败使用同一事务逻辑撤销或恢复；重复安装同一版本不重启。

不修改订阅、代理模式、自动更新或开机自启设置。完成后刷新 Web 页面；应显示已安装的 Mihomo 版本，再配置订阅或本地 YAML。在线“检查稳定版”仍要求 Debian 能访问 GitHub，因此离线安装成功不代表在线检查已恢复。

官方资产：{release['url']}
官方压缩文件 SHA256：{release['sha256']}
官方源码：{source}
LICENSE 为上游许可证。release.json 保留官方资产来源、大小与摘要。
"""
        (stage / 'README.zh-CN.md').write_text(readme, encoding='utf-8', newline='\n')
        manifest = ''.join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in sorted(stage.iterdir()) if path.is_file())
        (stage / 'SHA256SUMS').write_text(manifest, encoding='ascii', newline='\n')
        archive = args.output / (name + '.tar.gz')
        def permissions(member):
            member.mode = 0o755 if member.isdir() or member.name.endswith('/install.sh') else 0o644
            member.uid = member.gid = 0
            member.uname = member.gname = 'root'
            return member
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(stage, arcname=name, filter=permissions)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'sha256': checksum, 'release': release}, ensure_ascii=False))


if __name__ == '__main__':
    main()
