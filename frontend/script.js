const LOGOS = [
  {
    src: "https://svgl.app/library/procure.svg",
    alt: "Procure",
    gradient: ["#38bdf8", "#2563eb"],
  },
  {
    src: "https://svgl.app/library/shopify.svg",
    alt: "Shopify",
    gradient: ["#fde047", "#f59e0b"],
  },
  {
    src: "https://svgl.app/library/blender.svg",
    alt: "Blender",
    gradient: ["#60a5fa", "#1d4ed8"],
  },
  {
    src: "https://svgl.app/library/figma.svg",
    alt: "Figma",
    gradient: ["#c084fc", "#7c3aed"],
  },
  {
    src: "https://svgl.app/library/spotify.svg",
    alt: "Spotify",
    gradient: ["#fb7185", "#dc2626"],
  },
  {
    src: "https://svgl.app/library/lottielab.svg",
    alt: "Lottielab",
    gradient: ["#fde047", "#22c55e"],
  },
  {
    src: "https://svgl.app/library/google-cloud.svg",
    alt: "Google Cloud",
    gradient: ["#bae6fd", "#38bdf8"],
  },
  {
    src: "https://svgl.app/library/bing.svg",
    alt: "Bing",
    gradient: ["#67e8f9", "#14b8a6"],
  },
];

const track = document.querySelector("#marqueeTrack");
const doubled = [...LOGOS, ...LOGOS];

track.innerHTML = doubled.map((logo) => `
  <a class="logo-card" href="#" aria-label="${logo.alt}">
    <span class="logo-glow" style="background: linear-gradient(135deg, ${logo.gradient[0]}, ${logo.gradient[1]});"></span>
    <img src="${logo.src}" alt="${logo.alt}" loading="lazy">
  </a>
`).join("");

const overlay = document.querySelector("#authOverlay");
const panels = document.querySelectorAll("[data-auth-panel]");

function getActivePanel() {
  return overlay.querySelector(".auth-modal.active");
}

function showAuthMessage(message, panel = getActivePanel()) {
  console.log("[auth]", message);
  if (!panel) return;
  const messageBox = panel.querySelector(".auth-message");
  messageBox.textContent = message;
  messageBox.classList.add("show");
  clearTimeout(showAuthMessage.timer);
  showAuthMessage.timer = window.setTimeout(() => {
    messageBox.classList.remove("show");
  }, 2600);
}

function openAuth(type) {
  overlay.classList.add("open");
  overlay.setAttribute("aria-hidden", "false");
  panels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.authPanel === type);
    const messageBox = panel.querySelector(".auth-message");
    if (messageBox) messageBox.classList.remove("show");
  });
  const activeInput = overlay.querySelector(".auth-modal.active input");
  if (activeInput) {
    window.setTimeout(() => activeInput.focus(), 80);
  }
}

function closeAuth() {
  overlay.classList.remove("open");
  overlay.setAttribute("aria-hidden", "true");
  panels.forEach((panel) => panel.classList.remove("active"));
}

document.querySelectorAll("[data-open-auth]").forEach((button) => {
  button.addEventListener("click", () => openAuth(button.dataset.openAuth));
});

document.querySelectorAll("[data-close-auth]").forEach((button) => {
  button.addEventListener("click", closeAuth);
});

document.querySelectorAll("[data-switch-auth]").forEach((button) => {
  button.addEventListener("click", () => openAuth(button.dataset.switchAuth));
});

async function authRequest(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

// ── SMS verification code for register ────────────────────────────
const SMS_COOLDOWN_SECONDS = 60;

function startSmsCountdown(button, seconds) {
  let remaining = seconds;
  button.disabled = true;
  const originalText = button.textContent;
  button.dataset.originalText = originalText;
  button.textContent = `${remaining}s 后重发`;
  const timer = window.setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      window.clearInterval(timer);
      button.disabled = false;
      button.textContent = button.dataset.originalText || "获取验证码";
      delete button.dataset.timer;
    } else {
      button.textContent = `${remaining}s 后重发`;
    }
  }, 1000);
  button.dataset.timer = timer;
}

async function sendSmsCode(account, button, panel) {
  if (!account) {
    showAuthMessage("请先输入手机号或邮箱", panel);
    return false;
  }
  button.disabled = true;
  const prevText = button.textContent;
  button.textContent = "发送中…";
  try {
    const data = await authRequest("/api/auth/sms/send", { account });
    startSmsCountdown(button, SMS_COOLDOWN_SECONDS);
    if (data && data.dev_code) {
      showAuthMessage(`验证码：${data.dev_code}（开发模式）`, panel);
    } else {
      showAuthMessage("验证码已发送，请注意查收", panel);
    }
    return true;
  } catch (error) {
    button.disabled = false;
    button.textContent = prevText;
    showAuthMessage(error.message, panel);
    return false;
  }
}

// 注册页"联系管理员"按钮: 测试阶段不发送短信, 提示用户找管理员拿验证码
document.querySelectorAll("[data-send-sms]").forEach((button) => {
  button.addEventListener("click", () => {
    const panel = button.closest(".auth-modal");
    showAuthMessage("请联系管理员获取验证码", panel);
  });
});

document.querySelectorAll("[data-auth-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const panel = form.closest(".auth-modal");
    const type = form.dataset.authForm;

   if (type === "login") {
     const account = form.elements.account.value.trim();
     const password = form.elements.password.value;
     const captcha = (form.elements.captcha?.value || "").trim();
     if (!account) { showAuthMessage("请输入手机号", panel); return; }
     if (!password) { showAuthMessage("请输入密码", panel); return; }
     if (captcha !== "4305") { showAuthMessage("验证码错误", panel); return; }
     try {
       await authRequest("/api/auth/login", { account, password });
       window.location.href = "/dashboard";
     } catch (error) {
       showAuthMessage(error.message, panel);
     }
     return;
   }

    if (type === "enterprise") {
      const enterprise_name = form.elements.enterprise_name.value.trim();
      const contact_name = form.elements.contact_name.value.trim();
      const contact_phone = form.elements.contact_phone.value.trim();
      const account = form.elements.account.value.trim();
      const password = form.elements.password.value;
      if (password !== form.elements.confirmPassword.value) {
        showAuthMessage("两次输入的密码不一致。", panel);
        return;
      }
      try {
        await authRequest("/api/enterprise/onboard", {
          enterprise_name, contact_name, contact_phone,
          account, password, display_name: account,
        });
        window.location.href = "/dashboard";
      } catch (error) {
        showAuthMessage(error.message, panel);
      }
      return;
    }

    const account = (form.elements.account.value || "").trim();
    const password = form.elements.password.value;
   const confirmPassword = form.elements.confirmPassword.value;
   if (password !== confirmPassword) {
     showAuthMessage("Passwords do not match.", panel);
     return;
   }
    const invite_code = (form.elements.inviteCode?.value || "").trim();
    const sms_code = (form.elements.smsCode?.value || "").trim();
   if (!sms_code) {
     showAuthMessage("请先获取并输入验证码", panel);
     return;
   }
   try {
      await authRequest("/api/auth/register", { account, password, display_name: account, invite_code, sms_code });
      window.location.href = "/dashboard";
    } catch (error) {
      showAuthMessage(error.message, panel);
    }
  }, true);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAuth();
});


// ── 密码可见性切换 ───────────────────────────────
document.querySelectorAll("[data-pwd-toggle]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = btn.parentElement.querySelector("input");
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.classList.toggle("showing", show);
  });
});
