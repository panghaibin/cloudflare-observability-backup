# Cloudflare Observability Backup

把 Cloudflare Workers Observability 中可查询的日志定期拉到 VPS 上的 SQLite。用一个 TOML 文件配置账户、凭据和要归档的 Worker。

## 安装和试拉

需要 Python 3.10 或更新版本。在项目目录下安装。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
```

在 Cloudflare 创建仅限目标账户的 API Token，授予查询接口要求的 `Workers Observability Write` 权限。目标 Worker 需要开启 Workers Logs。 [查询接口](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/) · [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)

首次配置时，把 `backup.example.toml` 复制为 `backup.toml`。在 Linux 上先执行 `umask 077`，并确认配置文件权限为 `0600`。填入账户 ID、Token 和该账户下实际部署的 Worker 名称。

```toml
account_id = "your-account-id"
api_token = "your-api-token"
workers = ["example-worker", "api-worker"]
db = "archive.sqlite3"
initial_hours = 24
```

相对数据库路径以 TOML 文件所在目录为准。手动运行一次，确认配置和权限。

```sh
umask 077
.venv/bin/cf-observability-backup --config backup.toml
```

Windows 上可运行 `\.venv\Scripts\cf-observability-backup.exe --config backup.toml`。原来的单 Worker 用法也可以继续使用，传入 `--worker` 和 `--db`，凭据从环境变量或当前目录的 `.env` 读取。

- 每个账户和 Worker 的首次同步默认回看 24 小时，可通过 `initial_hours` 调整。选择 Cloudflare 仍保留日志的范围。
- 查询按最多一小时切段，同一段使用固定起止时间翻页。整段翻完才推进进度。失败后重跑会从上次完成位置继续，按事件 ID 去重。
- 一个 Worker 失败后，仍会尝试同步列表里的其他 Worker，整次命令以非零状态退出。
- 若 API 报告查询结果经过采样，本次同步会报错并保留该段的进度点。
- 后续同步会从进度点往前重叠 30 分钟，以收集延迟可见的日志。Cloudflare 已过期或未摄入的日志无法补回。
- 数据库保存完整事件 JSON，以及来源、事件 ID 和毫秒时间戳。业务字段可在查询时提取。

## 在 VPS 上定时运行

在运行备份的用户下执行 `crontab -e`，加入下面一行。示例假设项目和配置位于该用户的 `~/cloudflare-observability-backup`。

```cron
*/10 * * * * umask 077; cd "$HOME/cloudflare-observability-backup" && /usr/bin/flock -n backup.lock ./.venv/bin/cf-observability-backup --config backup.toml >> backup.log 2>&1
```

每 10 分钟运行一次。`flock` 在上一轮仍运行时跳过这次触发，下一轮从 SQLite 里的同步进度继续。以后增减目标时只需修改 `backup.toml` 里的 `workers`。 [crontab 手册](https://man7.org/linux/man-pages/man5/crontab.5.html) · [flock 手册](https://man7.org/linux/man-pages/man1/flock.1.html)

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
