# 部署说明

## systemd 安装

```bash
# 1. 复制 service 文件
cp admin-platform.service /etc/systemd/system/

# 2. 修改其中的 ADMIN_SECRET_KEY / ADMIN_DEFAULT_PASSWORD / DATABASE_URL / 路径

# 3. 加载 + 启动
systemctl daemon-reload
systemctl enable admin-platform.service
systemctl start admin-platform.service

# 4. 查看状态与日志
systemctl status admin-platform.service
journalctl -u admin-platform.service -f
```

## Caddy 反代（独立入口）

超管后台建议用独立子域名或独立端口，与主应用入口分离：

```caddyfile
admin.wangshilin888.com {
    reverse_proxy 127.0.0.1:6689
}
```

## 首次登录

1. 默认账号 `admin` / `admin123`（或你在 service 里设的 `ADMIN_DEFAULT_PASSWORD`）
2. **首次登录后立即修改密码**（P4 会加修改密码接口，当前可改库）

```sql
-- 手动改超管密码（用 Python 算 hash 后填入）
UPDATE platform_admins SET password_hash = '<新hash>' WHERE username = 'admin';
```
