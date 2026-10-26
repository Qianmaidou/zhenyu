/* 针语 · 前端公共逻辑：API 封装、登录态、导航、Toast、通用渲染 */
(function () {
  'use strict';

  const TOKEN_KEY = 'zhenyu_token';
  const USER_KEY = 'zhenyu_user';
  const DEVICE_KEY = 'zhenyu_device';

  const api = {
    base: '/api',
    async request(path, options = {}) {
      const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
      const token = localStorage.getItem(TOKEN_KEY);
      const dev = localStorage.getItem(DEVICE_KEY);
      if (token) headers['Authorization'] = 'Bearer ' + token;
      if (dev) headers['X-Device-Key'] = dev;
      let res;
      try {
        res = await fetch(this.base + path, Object.assign({}, options, { headers }));
      } catch (e) {
        toast('网络连接失败，请检查网络后重试', 'error');
        throw e;
      }
      const text = await res.text();
      let body;
      try { body = JSON.parse(text); } catch (e) { throw new Error('服务暂时不可用，请稍后重试'); }
      if (!res.ok && body.code === undefined) throw new Error('服务暂时不可用，请稍后重试');
      if (body.code !== 0) {
        if (body.code === 40101) api.clearAuth();
        throw new Error(body.message || '操作未完成，请稍后重试');
      }
      return body.data;
    },
    get(p, params) {
      const qs = params ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)).toString() : '';
      return this.request(p + qs);
    },
    post(p, data) { return this.request(p, { method: 'POST', body: JSON.stringify(data || {}) }); },
    clearAuth() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); },
    setAuth(token, user) { localStorage.setItem(TOKEN_KEY, token); localStorage.setItem(USER_KEY, JSON.stringify(user)); },
    getUser() { try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; } },
    isLogin() { return !!localStorage.getItem(TOKEN_KEY); }
  };

  if (!localStorage.getItem(DEVICE_KEY)) {
    localStorage.setItem(DEVICE_KEY, 'd' + Math.random().toString(36).slice(2, 14));
  }

  function toast(msg, type) {
    let host = document.getElementById('toast-host');
    if (!host) { host = document.createElement('div'); host.id = 'toast-host'; document.body.appendChild(host); }
    const el = document.createElement('div');
    el.className = 'toast' + (type ? ' toast-' + type : '');
    el.textContent = msg;
    el.setAttribute('role', 'status');
    host.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 2600);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function reviewBadge(status) {
    if (status === 'expert_confirmed') return '<span class="badge badge-confirmed">顾问已确认</span>';
    if (status === 'rejected') return '<span class="badge badge-error">已驳回</span>';
    return '<span class="badge badge-pending">待顾问确认</span>';
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /* 线性图标：统一 21×21、stroke 1.6、圆头，避免用汉字当图标造成语义错位 */
  const S = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"';
  const ICON = {
    home: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="4.2"/><path d="M12 3.8v3M12 17.2v3M3.8 12h3M17.2 12h3"/></svg>`,
    needle: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><path d="M4 20 17.5 6.5"/><path d="M15 4.8 19.2 9"/><circle cx="5.6" cy="18.4" r="1.5"/><path d="M20 5c1.6 2.2.6 4.4-1.6 5.6"/></svg>`,
    stitch: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><path d="M3 8.5h6M10.5 8.5h10"/><path d="M3 15.5h10M14.5 15.5h6"/><path d="M9.2 5.6v5.8M13.8 12.6v5.8"/></svg>`,
    book: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><path d="M4 5.2c2.6-1 5.4-1 8 .6v13c-2.6-1.6-5.4-1.6-8-.6z" /><path d="M20 5.2c-2.6-1-5.4-1-8 .6v13c2.6-1.6 5.4-1.6 8-.6z"/></svg>`,
    chat: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><path d="M20 12.4c0 3.6-3.6 6.4-8 6.4-1 0-2-.14-2.9-.4L4.6 20l1.1-3.2C4.4 15.6 4 14 4 12.4 4 8.8 7.6 6 12 6s8 2.8 8 6.4z"/><path d="M9 12h.01M12 12h.01M15 12h.01"/></svg>`,
    user: `<svg viewBox="0 0 24 24" ${S} aria-hidden="true"><circle cx="12" cy="8.6" r="3.6"/><path d="M4.8 20c.6-3.6 3.6-5.6 7.2-5.6s6.6 2 7.2 5.6"/></svg>`
  };

  /* 一份导航数据，两种呈现：
     - 桌面（≥768px）：顶部文字导航，全 6 项
     - 移动（<768px）：底部 Tab 5 项 + 顶部头像进入「我的」
     两者由 CSS 互斥控制，不会同时出现 */
  const NAV = [
    { href: '/', label: '首页', key: 'home', tab: true },
    { href: '/generate', label: '生成', key: 'needle', tab: true },
    { href: '/stitches', label: '针法', key: 'stitch', tab: true },
    { href: '/knowledge', label: '知识', key: 'book', tab: true },
    { href: '/community', label: '社区', key: 'chat', tab: true },
    { href: '/me', label: '我的', key: 'user', tab: false }
  ];

  function renderChrome(active) {
    const nav = NAV.map(n =>
      `<a href="${n.href}"${n.href === active ? ' class="active" aria-current="page"' : ''}>${esc(n.label)}</a>`
    ).join('');

    const tabs = NAV.filter(n => n.tab).map(n =>
      `<a href="${n.href}"${n.href === active ? ' class="active" aria-current="page"' : ''}>
         ${ICON[n.key]}<span class="label">${esc(n.label)}</span>
       </a>`
    ).join('');

    document.body.insertAdjacentHTML('afterbegin', `
      <header class="site-header">
        <div class="wrap inner">
          <a class="brand" href="/"><span class="brand-mark" aria-hidden="true">绣</span>针语</a>
          <nav class="nav-links" aria-label="主导航">${nav}</nav>
          <div class="user-slot" id="user-slot"></div>
        </div>
      </header>`);

    document.body.insertAdjacentHTML('beforeend', `
      <footer class="site-footer">
        <div class="wrap">
          <div class="foot-top">
            <div><strong style="font-family:var(--f-serif)">针语</strong> · 湘绣灵感与针法学习</div>
            <div><a href="/about">关于与内容来源</a></div>
          </div>
          <p class="foot-note">专业内容会标注审核状态；AI 生成图片均为效果示意，不是实物绣品。</p>
        </div>
      </footer>
      <nav class="tabbar" aria-label="底部导航"><div class="tabbar-inner">${tabs}</div></nav>`);

    const slot = document.getElementById('user-slot');
    const u = api.getUser();
    if (u) {
      const initial = esc((u.nickname || '绣').trim().slice(0, 1));
      slot.innerHTML = `<a href="/me" class="avatar" title="${esc(u.nickname)}，进入个人中心">${initial}</a>`;
    } else {
      slot.innerHTML = `<a href="/me" class="btn btn-outline btn-sm">登录</a>`;
    }
  }

  window.ZY = { api, toast, esc, reviewBadge, fmtTime, renderChrome, NAV, ICON };
})();
