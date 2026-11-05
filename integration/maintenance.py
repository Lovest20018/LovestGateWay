#!/usr/bin/env python3
"""Run owner-requested official core maintenance in an independent service."""
from pathlib import Path
import argparse
import json
import time
from mihomo_update import Updater, STATE, atomic_json, error_details
from manage_common import Layout
from manage_operations import begin_mihomo, finish_mihomo


def main(layout=None):
    layout = layout or Layout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('scheduled', 'check', 'update', 'rollback', 'install-local'))
    args = parser.parse_args()
    updater = Updater()
    operation = begin_mihomo(layout, STATE, args.action)
    upload_id = None
    try:
        if args.action == 'check':
            updater.check()
        elif args.action == 'rollback':
            updater.rollback()
        elif args.action == 'update':
            updater.update()
        elif args.action == 'install-local':
            from mihomo_upload import UploadStore
            store = UploadStore()
            metadata, archive = store.pending()
            upload_id = metadata['id']
            updater.install_local(archive, metadata['sha256'], metadata['version'])
        else:
            config = json.loads(Path('/etc/paopaogw/maintenance.json').read_bytes())
            updater.check()
            if config.get('auto_update', True) and updater.status().get('update_available'):
                updater.update()
        finish_mihomo(layout, STATE, operation, status='complete')
    except Exception as error:
        details = error_details(error)
        finish_mihomo(layout, STATE, operation, status='failed', **details)
        print(json.dumps(details, ensure_ascii=False))
        raise SystemExit(1)
    finally:
        if upload_id:
            try: store.cancel(upload_id)
            except Exception: pass  # A concurrent management operation can defer staging cleanup.


if __name__ == '__main__':
    main()
