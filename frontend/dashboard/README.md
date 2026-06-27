# Product Pipeline ERP Frontend

这是一个独立生成的原生前端项目，没有修改现有 `product_pipeline_v2` 或 `temu-collector` 代码。

## 技术

- HTML
- CSS
- JavaScript
- 无 React/Vue/构建工具

## 默认连接

前端默认连接你的局域网管线服务：

```text
http://192.168.0.104:5000
```

可在登录页或系统设置中修改。

## 登录

当前登录只是前端本地演示：

```text
账号：admin
密码：admin123
```

真实上线前需要在 Flask 后端增加账号表、密码哈希、Session/JWT 和接口鉴权。

## 使用

直接用浏览器打开：

```text
E:\workplace\product_pipeline_erp_frontend\index.html
```

确保 `product_pipeline_v2` 服务已启动，并且局域网可以访问 `http://192.168.0.104:5000`。
