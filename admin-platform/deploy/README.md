# 部署说明

## systemd 安装（已部署）

超管后台已配置为 systemd 服务 `admin-platform.service`，**开机自启 + 崩溃自动重启**。

### service 文件位置

```
/etc/systemd/system/admin-platform.service
```

### 常用命令

```bash
# 启动 / 停止 / 重启
systemctl start admin-platform.service
systemctl stop admin-platform.service
systemctl restart admin-platform.service    # 改代码后执行

# 查看状态与日志
systemctl status admin-platform.service
tail -f /var/log/admin-platform.log
```

### 首次部署（新机器才需要）

```bash
# 1. 复制 service 文件
cp admin-platform/deploy/admin-platform.service /etc/systemd/system/

# 2. 修改其中的路径（WorkingDirectory / ExecStart）匹配你的环境

# 3. 加载 + 启动 + 开机自启
systemctl daemon-reload
systemctl enable admin-platform.service
systemctl start admin-platform.service

# 4. 查看状态与日志
systemctl status admin-platform.service
tail -f /var/log/admin-platform.log
```

## Caddy 反代（独立端口入口）

超管后台使用**独立端口 `8444`**，与用户端 `8443` 完全隔离：

```caddyfile
# 用户/企业端 (3级)
https://wangshilin888.com:8443 {
    encode zstd gzip
    reverse_proxy 127.0.0.1:6688
}

# 超级管理员系统 (1级) - 单独端口隔离
https://wangshilin888.com:8444 {
    encode zstd gzip
    reverse_proxy 127.0.0.1:6689
}
```

- TLS 证书复用 `wangshilin888.com`（8443 已有，8444 自动覆盖，无需额外申请）
- 需在**阿里云安全组**放行 `8444/TCP` 入方向
- ufw 防火墙已放行 `8444/tcp`

## 首次登录

1. 默认账号 `admin` / `admin123`（或你在 service 里设的 `ADMIN_DEFAULT_PASSWORD`）
2. **首次登录后立即修改密码**（当前需改库）：

```sql
-- 手动改超管密码（用 Python 算 hash 后填入）
UPDATE platform_admins SET password_hash = '<新hash>' WHERE username = 'admin';
```

## 一键管理脚本

服务器已安装 `/usr/local/bin/` 下的统一管理脚本：

| 脚本 | 作用 |
|------|------|
| `start-all.sh` | 启动全部服务（web + worker + 超管 + caddy） |
| `stop-all.sh` | 停止全部服务 |
| `restart-all.sh` | 重启全部服务 |

这些脚本在任何目录都能直接执行。
