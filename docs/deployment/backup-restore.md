# 数据库备份与恢复

MySQL 里存的是代码重建不出来的真实数据（用户、会话、材料与价格、观测记录）。Docker 卷
能防"容器删除"，但防不了磁盘故障、误删卷（`docker compose down -v`）或误执行删除。
因此需要一份**独立的备份副本**，并能验证可以还原。

## 方式一：Docker 部署（推荐）

db 容器（`mysql:8.4`）自带 `mysqldump` / `mysql`，直接用它备份，无需额外安装：

```bash
# 备份到宿主机 backups/ 目录（带时间戳）
mkdir -p backups
docker compose exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --no-tablespaces shijing' \
  | gzip > backups/shijing_$(date +%Y%m%d_%H%M%S).sql.gz
```

```bash
# 恢复（覆盖现有数据，先确认）
gunzip -c backups/shijing_xxx.sql.gz \
  | docker compose exec -T db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" shijing'
```

> `-T` 关闭 TTY，这样才支持管道输入/输出。`$MYSQL_ROOT_PASSWORD` 是 db 容器内的
> 环境变量，由 compose 注入，无需在命令行明文写密码。

## 方式二：Python 脚本（非 Docker，或需要脚本化）

运行环境需有 `mysqldump`/`mysql`（app 镜像已内置，或宿主机自行安装 mysql client），
连接目标由 `DATABASE_URL` 决定：

```bash
# 备份到 data/backups/
python scripts/backup_db.py

# 恢复
python scripts/restore_db.py data/backups/shijing_xxx.sql.gz
```

脚本通过 `MYSQL_PWD` 环境变量传密码，不暴露在进程命令行。

## 定时备份（生产必做）

在服务器上用 crontab 每天凌晨自动备份，并保留最近若干份：

```cron
0 3 * * * cd /path/to/project && docker compose exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --no-tablespaces shijing' | gzip > backups/shijing_$(date +\%Y\%m\%d_\%H\%M\%S).sql.gz
```

建议再把 `backups/` 同步到对象存储（如 MinIO 的另一个桶）或异地，实现"备份不在同一块盘上"。

## 验证恢复（关键）

备份文件本身不算数，能还原才算。定期做一次演练：

1. 起一个**临时空库**（或另一套 compose），执行恢复命令；
2. 确认表数量、用户数、材料条数与预期一致；
3. 删除临时库。

只有验证过"能成功还原"，备份才有意义。
