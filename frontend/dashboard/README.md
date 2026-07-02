# frontend/dashboard · 用户工作台前端

3级普通用户使用的工作台（Dashboard）前端，纯原生实现。

## 技术

- HTML + CSS + JavaScript
- 无 React / Vue / 构建工具
- 由主应用 `backend/` 的 FastAPI `StaticFiles` 直接托管

## 访问地址

```
https://wangshilin888.com:8443
```

前端通过相对路径请求同源的 `/api/*` 接口，无需单独配置后端地址。

## 登录

普通用户注册/登录（3级），鉴权由主应用 `backend/core/app.py` 的 Session cookie 管理。

> 超级管理员后台（1级）在独立端口 `:8444`（`admin-platform/`），登录态与本前端完全隔离。

## 依赖

确保主应用后端已启动（`product-pipeline.service`），否则页面能打开但接口会报错。
