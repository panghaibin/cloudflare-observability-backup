# Cloudflare Observability Backup

定时将 Cloudflare Workers Observability 日志归档到 SQLite。每个 Worker 一张表，保留原始事件 JSON。

## 安装与配置

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
umask 077
cp backup.example.toml backup.toml
chmod 600 backup.toml
```

需要 Python 3.10+。在 `backup.toml` 填入账户 ID、带 `Workers Observability Write` 权限的 Token 和 Worker 名称。 [Cloudflare 查询接口](https://developers.cloudflare.com/api/resources/workers/subresources/observability/subresources/telemetry/methods/query/)

```toml
account_id = "your-account-id"
api_token = "your-api-token"
workers = ["example-worker", "api-worker"]
db = "archive.sqlite3"
initial_hours = 24
```

在项目目录运行 `python3 backup.py`，Windows 用 `\.venv\Scripts\python.exe backup.py`。默认读取当前目录的 `backup.toml`，可用 `--config` 指定路径。相对数据库路径和运行日志均以配置文件所在目录为准。

## 定时运行

用 `crontab -e` 加入每 10 分钟运行的任务，并按需修改项目路径。

```cron
*/10 * * * * umask 077; cd "$HOME/cloudflare-observability-backup" && /usr/bin/flock -n backup.lock ./.venv/bin/python backup.py
```

程序同时写控制台和 `backup.log`，跨日首次写入时按服务器本地日期轮换到 `logs/backup.log.yyyy-MM-dd`。

## 查询与备份

用 `worker_tables` 查表名，`sync_progress` 查同步进度。

```sql
SELECT worker, table_name FROM worker_tables;
```

```sh
sqlite3 archive.sqlite3 '.backup archive-snapshot.sqlite3'
```
