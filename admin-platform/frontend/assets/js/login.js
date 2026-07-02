/**
 * 超管登录页逻辑：登录成功跳转 /dashboard。
 */
(function () {
  "use strict";

  var form = document.getElementById("loginForm");
  var errorEl = document.getElementById("loginError");

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    errorEl.textContent = "";
    var username = document.getElementById("username").value.trim();
    var password = document.getElementById("password").value;
    if (!username || !password) {
      errorEl.textContent = "请输入用户名和密码";
      return;
    }
    var btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "登录中...";
    try {
      var resp = await fetch("/api/admin/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, password: password }),
      });
      var data = await resp.json();
      if (!resp.ok || !data.ok) {
        errorEl.textContent = (data.error) || "登录失败";
        btn.disabled = false;
        btn.textContent = "登录";
        return;
      }
      window.location.href = "/dashboard";
    } catch (err) {
      errorEl.textContent = "网络错误，请重试";
      btn.disabled = false;
      btn.textContent = "登录";
    }
  });
})();
