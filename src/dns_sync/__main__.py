"""`python -m src.dns_sync` — one sync run; the CronJob in manifests/dns-sync.yaml."""

from src.dns_sync.sync import main

if __name__ == '__main__':
    raise SystemExit(main())
