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

document.querySelectorAll("[data-auth-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const panel = form.closest(".auth-modal");
    const type = form.dataset.authForm;

   if (type === "login") {
     const account = form.elements.account.value.trim();
     const password = form.elements.password.value;
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
   try {
      await authRequest("/api/auth/register", { account, password, display_name: account, invite_code });
      window.location.href = "/dashboard";
    } catch (error) {
      showAuthMessage(error.message, panel);
    }
  }, true);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAuth();
});
