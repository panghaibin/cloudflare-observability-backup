# Cloudflare Observability Backup

把 Cloudflare Workers Observability 中可查询的日志定期拉到 VPS 上的 SQLite。通过参数选择账户和 Worker，可将多个 Worker 归档到同一个数据库。

## 安装和试拉

需要 Python 3.10 或更新版本。在项目目录下安装。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
```

在 Cloudflare 创建仅限目标账户的 API Token，授予查询接口要求的 `Workers Observability Write` 权限。将凭据配置为环境变量 `CLOUDFLARE_API_TOKEN`，将账户 ID 配置为 `CLOUDFLARE_ACCOUNT_ID` 或通过 `--account-id` 指定。目标 Worker 需要开启 Workers Logs。 [查询接口](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/) · [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)

```sh
umask 077
.venv/bin/cf-observability-backup --worker example-worker --db archive.sqlite3
```

把 `example-worker` 替换为已部署的脚本名。用 `wcalc-new` 验证时，只需将它传给 `--worker`。

- 每个账户和 Worker 的首次同步默认回看 24 小时，可用 `--initial-hours` 调整。选择 Cloudflare 仍保留日志的范围。
- 查询按最多一小时切段，同一段使用固定起止时间翻页。整段翻完才推进进度。失败后重跑会从上次完成位置继续，按事件 ID 去重。
- 若 API 报告查询结果经过采样，本次同步会报错并保留该段的进度点。
- 后续同步会从进度点往前重叠 30 分钟，以收集延迟可见的日志。Cloudflare 已过期或未摄入的日志无法补回。
- 数据库保存完整事件 JSON，以及来源、事件 ID 和毫秒时间戳。业务字段可在查询时提取。

## 在 VPS 上定时运行

下面的示例假设工具安装在 `/opt/cloudflare-observability-backup/.venv`。先创建用于运行采集的系统账户。

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin cf-observability-backup
```

把账户 ID 和 Token 放进由 root 持有的 `/etc/cf-observability-backup/example-worker.env`，文件权限设为 `0600`。

```ini
CLOUDFLARE_ACCOUNT_ID=your-account-id
CLOUDFLARE_API_TOKEN=your-api-token
```

将以下服务保存为 `/etc/systemd/system/cf-observability-backup@.service`。

```ini
[Unit]
Description=Archive Cloudflare Workers Observability logs for %i
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=cf-observability-backup
StateDirectory=cf-observability-backup
StateDirectoryMode=0700
UMask=0077
EnvironmentFile=/etc/cf-observability-backup/%i.env
ExecStart=/opt/cloudflare-observability-backup/.venv/bin/cf-observability-backup --worker %i --db /var/lib/cf-observability-backup/archive.sqlite3
```

每增加一个 Worker，为它创建同名的 `.env` 文件并启用对应的定时器实例。

将以下定时器保存为 `/etc/systemd/system/cf-observability-backup@.timer`。

```ini
[Unit]
Description=Run the Cloudflare Observability archive every ten minutes for %i

[Timer]
OnCalendar=*-*-* *:00/10:00
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl start cf-observability-backup@example-worker.service
sudo systemctl enable --now cf-observability-backup@example-worker.timer
sudo journalctl -u cf-observability-backup@example-worker.service
```

## 本地查询和数据库快照

这条 SQLite 查询统计某 Worker 最近一天的日志条数。

```sql
SELECT count(*)
FROM events
WHERE account_id = 'your-account-id' AND worker = 'example-worker'
  AND timestamp_ms >= (strftime('%s', 'now', '-1 day') * 1000);
```

查看 `sync_progress` 表的 `through_ms` 可知道每个来源同步到的时间。该时间记录已完成的查询范围。

用 SQLite 在线备份生成包含日志和同步进度的快照，再把快照复制到 VPS 之外。

```sh
sqlite3 archive.sqlite3 '.backup archive-snapshot.sqlite3'
```
