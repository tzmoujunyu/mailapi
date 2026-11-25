"""Run explicitly configured test backup jobs; never installs a scheduler."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mailapi_shared.operational_backups import BackupManager, read_status, record_initialization_failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('action', choices=('create', 'verify', 'restore', 'status', 'run-due'))
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--archive', help='Registered archive filename; defaults to latest')
    args = parser.parse_args()
    os.umask(0o077)
    config, manager = None, None
    try:
        config = json.loads(args.config.read_text())
        manager = BackupManager(config, restore_only=args.action in {'restore', 'verify', 'status'})
        if args.action == 'status':
            result = read_status(manager.directory)
        elif args.action == 'restore':
            if args.destination is None:
                parser.error('restore requires --destination inside the configured restore_root')
            result = manager.restore(args.destination, args.archive)
        else:
            result = getattr(manager, {'create': 'create', 'verify': 'verify', 'run-due': 'run_due'}[args.action])()
        print(json.dumps(result, ensure_ascii=False))
        return 1 if args.action == 'run-due' and any(item.get('success') is False for item in result.values()) else 0
    except Exception:
        if manager is None and config and args.action != 'status':
            record_initialization_failure(config, 'integrity' if args.action in {'restore', 'verify'} else 'backup')
        print(json.dumps({'success': False, 'summary': '备份任务未完成，请检查隔离测试配置或管理员备份状态'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
