// background.js — service worker, 负责转发 content.js 的请求(绕过 CORS)
// content script 在 temu.com 页面里, 直接 fetch 后端会被 CORS 拦截
// background 的 fetch 带 host_permissions 权限, 不受 CORS 限制

const PIPELINE_URL = 'https://wangshilin888.com:8443';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type !== 'tk-fetch') return false;

  (async () => {
    try {
      const res = await fetch(PIPELINE_URL + msg.path, {
        method: msg.method || 'GET',
        headers: msg.headers || {},
        body: msg.body ? JSON.stringify(msg.body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      sendResponse({ ok: res.ok, status: res.status, data });
    } catch (e) {
      sendResponse({ ok: false, status: 0, error: e.message });
    }
  })();
  return true; // 保持消息通道, 等异步 sendResponse
});
