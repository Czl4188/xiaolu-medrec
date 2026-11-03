
(function () {
  'use strict';

  /* ============================================================
     0. 小工具
     ============================================================ */
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmtDate(ts) {
    var d = new Date(ts);
    return d.getFullYear() + '.' + pad(d.getMonth() + 1) + '.' + pad(d.getDate());
  }
  function fmtTime(ts) {
    var d = new Date(ts);
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function fmtClock(ts) {
    var d = new Date(ts);
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  var WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  function fmtFull(ts) {
    var d = new Date(ts);
    return d.getFullYear() + ' 年 ' + (d.getMonth() + 1) + ' 月 ' + d.getDate() + ' 日 ' +
      WEEK[d.getDay()] + ' ' + fmtClock(ts);
  }
  function daysBetween(a, b) {
    var d1 = new Date(a); d1.setHours(0, 0, 0, 0);
    var d2 = new Date(b); d2.setHours(0, 0, 0, 0);
    return Math.round((d2 - d1) / 86400000);
  }
  function clean(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }

  /* ============================================================
     1. 本地存储：账号 / 会话 / 资料 / 记录
     ============================================================ */
  var LS = {
    accounts: 'lx_accounts_v1',
    session: 'lx_session_v1',
    user: function (u) { return 'lx_user_v1_' + u; },
    records: function (u) { return 'lx_records_v1_' + u; },
    theme: function (u) { return 'lx_theme_v1_' + u; },   // 账号隔离的主题
    ai: function (u) { return 'lx_ai_v1_' + u; },         // 账号隔离的千问配置
    scan: function (u) { return 'lx_scan_v1_' + u; }       // 账号隔离的识药记录
  };

  function read(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function write(key, val) {
    try {
      localStorage.setItem(key, JSON.stringify(val));
      return true;
    } catch (e) {
      toast('本地存储写入失败（' + (e && e.name ? e.name : '未知') + '），可能是空间已满或隐私模式。');
      return false;
    }
  }
  /* 说明：这是前端本地校验，用于区分本机不同使用者，不等同于服务器端鉴权。 */
  function hash(u, p) {
    var s = 'lx|' + u + '|' + p, h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0).toString(16);
  }


  /* ============================================================
     1.5 服务端 API
     有后端时以服务端为准：账号、档案、抽取、作答全部走 /api。
     探测不到后端则退回本机模式，页面仍可用，但会明确标注。
     ============================================================ */
  var API = {
    base: (function () {
      var q = new URLSearchParams(location.search).get('api');
      return q ? q.replace(/\/$/, '') : '';
    })(),
    token: '',
    on: false,
    key: 'lx_token_v1'
  };

  function apiFetch(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    /* 用自定义头而不是 Authorization：某些网关会改写 Authorization */
    if (API.token) headers['X-Xiaolu-Token'] = API.token;
    if (opts.body && typeof opts.body !== 'string') {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    opts.headers = headers;
    opts.cache = 'no-store';

    return fetch(API.base + path, opts).then(function (r) {
      if (r.status === 204) return null;
      return r.text().then(function (text) {
        var body = {};
        try { body = text ? JSON.parse(text) : {}; } catch (e) { body = {}; }
        if (!r.ok) {
          var err = new Error(body.detail || ('请求失败（' + r.status + '）'));
          err.status = r.status;
          throw err;
        }
        return body;
      });
    });
  }

  function loadToken() { try { return localStorage.getItem(API.key) || ''; } catch (e) { return ''; } }
  function saveToken(t) {
    try { t ? localStorage.setItem(API.key, t) : localStorage.removeItem(API.key); } catch (e) {}
  }

  /* 服务端记录 ↔ 界面记录 */
  function mapRecord(r) {
    return {
      id: r.id,
      clientId: r.client_id || r.id,
      at: new Date(r.visit_at).getTime(),
      hospital: r.hospital || '',
      summary: r.summary || '',
      transcript: r.transcript || '',
      meds: (r.meds || []).map(function (m) {
        return { name: m.name || '', dose: m.dose || '', freq: m.freq || '',
                 timing: m.timing || '', days: m.days || '' };
      }),
      followUp: r.follow_up || '',
      allergy: r.allergy || '',
      allergyDenied: !!r.allergy_denied,
      conditions: r.conditions || [],
      discontinued: r.discontinued || [],
      notes: r.notes || [],
      mode: r.mode || 'manual'
    };
  }

  function toApiRecord(d) {
    return {
      client_id: d.clientId || d.id || '',
      visit_at: new Date(d.at).toISOString(),
      hospital: d.hospital || '',
      summary: d.summary || '',
      transcript: d.transcript || '',
      meds: namedMeds(d.meds),
      follow_up: d.followUp || '',
      allergy: d.allergy || '',
      notes: d.notes || [],
      conditions: d.conditions || [],
      discontinued: d.discontinued || [],
      allergy_denied: !!d.allergyDenied,
      mode: d.mode || 'manual'
    };
  }

  function detectCloud() {
    if (location.protocol === 'file:') return Promise.resolve(false);

    /* 优先问 /api/ping：部署平台可能把 /healthz 占成自己的探针，
       那时返回的 JSON 里没有 status，会把「有后端」误判成「没后端」。 */
    function probe(url) {
      return fetch(API.base + url, { cache: 'no-store', headers: { 'Accept': 'application/json' } })
        .then(function (r) {
          if (!r.ok) return null;
          return r.json().catch(function () { return null; });
        })
        .catch(function () { return null; });
    }

    return probe('/api/ping').then(function (j) {
      if (j && j.status === 'ok') { API.on = true; return true; }
      return probe('/healthz').then(function (j2) {
        // 兼容自建部署：只有确实带 status/app 字段才算
        API.on = !!(j2 && j2.status === 'ok' && (j2.app || j2.env));
        return API.on;
      });
    });
  }

  /* ============================================================
     2. 状态
     ============================================================ */
  var state = {
    user: '',            // 登录用户名（也是存储分区的键）
    profile: null,       // {name, age, gender, hospital}
    records: [],         // 该用户的就诊记录
    view: 'home',
    speaking: false,     // 朗读默认关闭：公共场所外放病情是真实风险
    volume: 0.9,
    recording: false,
    recFailed: false,    // 麦克风被拒 / 无设备：别再补一句「没听到内容」
    lines: [],           // 本次转写 [{t, text}]
    interim: '',
    draft: null,         // 本次还未归档的记录
    chatReady: false,
    busy: false,
    cloud: false,        // 是否已连上服务端
    saving: false,       // 归档 / 修改请求进行中
    editId: '',          // 正在修改的已归档记录
    editDraft: null,
    theme: 'warm',
    ai: null,            // 千问配置（账号隔离）
    scan: { image: '', meds: [], source: '', at: 0 },
    scanHist: []
  };

  /* ============================================================
     3. 启动
     ============================================================ */
  document.addEventListener('DOMContentLoaded', init);

  function init() {
    startClock();
    bindGlobal();
    bindLogin();
    bindProfile();
    bindWorkbench();
    bindVoice();
    bindTheme();
    bindScan();
    applyTheme(read('lx_theme_guest_v1', 'warm'), false);

    detectCloud().then(function (on) {
      state.cloud = on;
      var badge = $('#cloudBadge');
      if (badge) {
        badge.hidden = false;
        badge.textContent = on ? '服务端模式' : '本机模式';
        badge.classList.toggle('is-off', !on);
        badge.title = on
          ? '已连接服务端：账号、档案、抽取与作答都走 /api'
          : '未连接服务端：数据只存在这台设备的浏览器里';
        var fa = $('#gateFootA'), fb = $('#gateFootB');
        if (fa) fa.textContent = '首次使用请勾选创建';
        if (fb) fb.textContent = on ? '服务端保存 · 跨设备可见' : '本机存储 · 不上传';
      }

      if (on) {
        API.token = loadToken();
        if (API.token) {
          apiFetch('/api/me')
            .then(function (me) { enterApp(me.username); })
            .catch(function () { saveToken(''); $('#loginUser').focus(); });
          return;
        }
      } else {
        var sess = read(LS.session, null);
        if (sess && sess.u) {
          var acc = read(LS.accounts, []).filter(function (a) { return a.u === sess.u; })[0];
          if (acc) { enterApp(sess.u); return; }
        }
      }
      $('#loginUser').focus();
    });
  }

  function startClock() {
    function tick() {
      var now = Date.now();
      var bar = $('#clockBar');
      if (bar) bar.textContent = fmtClock(now);
      var home = $('#homeClock');
      if (home) home.textContent = fmtFull(now);
    }
    tick();
    setInterval(tick, 1000);
  }


  /* ---------- 导出 / 导入（整改 P1-05）---------- */
  function archiveRecord(r) {
    return {
      client_id: r.clientId || r.id,
      visit_at: new Date(r.at).toISOString(),
      hospital: r.hospital, summary: r.summary, transcript: r.transcript,
      meds: r.meds, follow_up: r.followUp, allergy: r.allergy,
      allergy_denied: !!r.allergyDenied,
      conditions: r.conditions || [], discontinued: r.discontinued || [],
      notes: r.notes, mode: r.mode
    };
  }

  function downloadArchive() {
    var payload = {
      format: 'xiaolu-archive',
      version: 2,
      exported_at: new Date().toISOString(),
      user: state.user,
      mode: API.on ? 'server' : 'local',
      profile: state.profile || {},
      records: state.records.map(archiveRecord)
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'xiaolu-archive-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    toast('已导出 ' + payload.records.length + ' 份记录。');
  }

  function exportArchive() {
    if (state.saving) { toast('还在保存，等写完再导出，免得漏掉刚存的那份。'); return; }
    var btn = $('#exportBtn');
    var hint = $('#exportHint');

    if (!API.on) { downloadArchive(); return; }

    /* 服务端模式以服务端最新数据为准，不只信内存里的列表 */
    if (btn) btn.disabled = true;
    if (hint) hint.textContent = '正在取服务端最新档案…';
    apiFetch('/api/records')
      .then(function (list) {
        state.records = (list || []).map(mapRecord);
        syncIdentity();
        renderHome();
        renderMine();
        renderWork();
        downloadArchive();
        if (hint) hint.textContent = '已按服务端最新数据导出 · ' + fmtClock(Date.now());
      })
      .catch(function (e) {
        toast('导出失败：' + e.message);
        if (hint) hint.textContent = '导出失败，未生成文件。';
      })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function importArchive(e) {
    var file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast('文件超过 5MB，先确认是不是小鹿导出的档案。'); return; }

    var reader = new FileReader();
    reader.onload = function () {
      var data;
      try { data = JSON.parse(reader.result); } catch (err) { toast('不是合法的 JSON 文件。'); return; }
      var list = (data && data.records) || [];
      if (!Array.isArray(list) || !list.length) { toast('文件里没有记录。'); return; }
      if (list.length > 500) { toast('一次最多导入 500 份记录。'); return; }

      var bad = 0;
      var cleanList = list.filter(function (r) {
        if (!r || typeof r !== 'object') { bad++; return false; }
        if (!r.visit_at || isNaN(new Date(r.visit_at).getTime())) { bad++; return false; }
        if (typeof r.transcript !== 'string' || !r.transcript.trim()) { bad++; return false; }
        if (r.meds != null && !Array.isArray(r.meds)) { bad++; return false; }
        if (r.client_id) r.client_id = String(r.client_id).slice(0, 80);
        else r.client_id = newClientId();
        return true;
      });

      if (!cleanList.length) { toast('这些记录字段不完整，没有可导入的。'); return; }

      var existing = {};
      state.records.forEach(function (r) { existing[r.clientId || r.id] = 1; });
      var fresh = cleanList.filter(function (r) { return !existing[r.client_id]; });
      if (!fresh.length) { toast('这些记录都已在档案里，没有新增。'); return; }

      var msg = '导入 ' + fresh.length + ' 份记录？';
      if (bad) msg += '（另有 ' + bad + ' 条字段不完整，会跳过）';
      if (!window.confirm(msg)) return;

      if (!API.on) {
        fresh.forEach(function (r) {
          state.records.push(mapRecord({
            id: r.client_id, client_id: r.client_id, visit_at: r.visit_at,
            hospital: r.hospital, summary: r.summary, transcript: r.transcript,
            meds: r.meds, follow_up: r.follow_up, allergy: r.allergy,
            allergy_denied: r.allergy_denied, conditions: r.conditions,
            discontinued: r.discontinued, notes: r.notes, mode: r.mode
          }));
        });
        persistRecords();
        syncIdentity(); renderMine(); renderHome(); renderWork();
        toast('已导入 ' + fresh.length + ' 份（本机）。');
        $('#exportHint').textContent = '导入完成 · ' + fmtClock(Date.now());
        return;
      }

      var ok = 0, fail = 0;
      var queue = fresh.slice();
      $('#exportHint').textContent = '正在导入 0/' + fresh.length + '…';
      (function next() {
        if (!queue.length) {
          syncIdentity(); renderMine(); renderHome(); renderWork();
          toast('导入完成：成功 ' + ok + ' 份' + (fail ? '，失败 ' + fail + ' 份' : '') + '。');
          $('#exportHint').textContent = '导入完成 · ' + fmtClock(Date.now());
          return;
        }
        var r = queue.shift();
        apiFetch('/api/records', { method: 'POST', body: r })
          .then(function (saved) {
            var m = mapRecord(saved);
            state.records = state.records.filter(function (x) { return x.id !== m.id; });
            state.records.push(m);
            ok++;
          })
          .catch(function () { fail++; })
          .then(function () {
            $('#exportHint').textContent = '正在导入 ' + (ok + fail) + '/' + fresh.length + '…';
            next();
          });
      })();
    };
    reader.readAsText(file);
  }

  /* ============================================================
     4. 登录 / 退出
     ============================================================ */
  function bindLogin() {
    $('#loginForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var u = $('#loginUser').value.trim();
      var p = $('#loginPass').value;
      var err = $('#gateErr');

      if (u.length < 2) { err.textContent = '用户名至少 2 个字。'; return; }
      if (p.length < (API.on ? 8 : 4)) {
        err.textContent = API.on ? '密码至少 8 位。' : '密码至少 4 位。';
        return;
      }

      var isNew = $('#gateNew').checked;

      if (API.on) { cloudLogin(u, p, err, isNew); return; }

      var list = read(LS.accounts, []);
      var hit = list.filter(function (a) { return a.u === u; })[0];

      if (hit && hit.h !== hash(u, p)) {
        err.textContent = '这个用户名的密码不对，再试一次。';
        return;
      }
      if (!hit) {
        if (!isNew) {
          err.textContent = '本机没有这个账号。首次使用请先勾选「创建新账号」。';
          return;
        }
        list.push({ u: u, h: hash(u, p), at: Date.now() });
        write(LS.accounts, list);
      }
      write(LS.session, { u: u, at: Date.now() });
      err.textContent = '';
      enterApp(u);
    });

    $('#exportBtn').addEventListener('click', exportArchive);
    $('#importBtn').addEventListener('click', function () { $('#importFile').click(); });
    $('#importFile').addEventListener('change', importArchive);

    $('#logoutBtn').addEventListener('click', function () {
      try { localStorage.removeItem(LS.session); } catch (e) {}
      saveToken('');
      API.token = '';
      stopSpeech();
      state.user = '';
      state.profile = null;
      state.records = [];
      state.draft = null;
      state.lines = [];
      state.chatReady = false;
      state.ai = null;
      state.scan = { image: '', meds: [], source: '', at: 0 };
      state.scanHist = [];
      applyTheme(read('lx_theme_guest_v1', 'warm'), false);
      $('#thread').innerHTML = '';
      $('#app').hidden = true;
      $('#gate').hidden = false;
      $('#loginPass').value = '';
      $('#loginUser').value = '';
      document.body.removeAttribute('data-view');
      $('#loginUser').focus();
    });
  }


  /* 云端登录：先验密码；用户不存在则视为首次使用，直接建档。
     注册要求 8 位以上，且在服务端做 pbkdf2 散列 —— 不是前端比对。 */
  function cloudLogin(u, p, err, isNew) {
    err.textContent = '';
    var btn = $('#loginForm button[type=submit]');
    var old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = isNew ? '创建中…' : '进入中…'; }

    function done(msg) {
      if (btn) { btn.disabled = false; btn.textContent = old || '进入'; }
      if (msg) err.textContent = msg;
    }
    function ok(r) {
      saveToken(r.access_token);
      API.token = r.access_token;
      done('');
      enterApp(r.user.username);
    }

    var path = isNew ? '/api/auth/register' : '/api/auth/login';
    apiFetch(path, { method: 'POST', body: { username: u, password: p } })
      .then(ok)
      .catch(function (e) {
        if (isNew && e.status === 409) {
          done('这个账号已经存在，取消勾选后再登录。');
        } else if (!isNew && e.status === 401) {
          done('账号或密码不对。首次使用，请先勾选「创建新账号」。');
        } else if (isNew && e.status === 400 && /密码/.test(e.message)) {
          done(e.message);
        } else {
          done(e.message);
        }
      });
  }

  function enterApp(name) {
    state.user = name;
    loadLocalAccount(name);

    if (API.on) {
      /* 头像与身份证明明只存本机，读服务端资料时不能把它们覆盖掉 */
      var localProf = read(LS.user(name), null) || {};
      state.profile = {
        name: name, age: '', gender: '', hospital: '',
        avatar: localProf.avatar || '', idcard: localProf.idcard || ''
      };
      state.records = [];
      $('#gate').hidden = true;
      $('#app').hidden = false;
      syncIdentity();
      go('home');

      apiFetch('/api/me/profile')
        .then(function (p) {
          state.profile = {
            name: p.name || name, age: p.age || '',
            gender: p.gender || '', hospital: p.hospital || '',
            avatar: (state.profile && state.profile.avatar) || localProf.avatar || '',
            idcard: localProf.idcard || ''
          };
          syncIdentity();
          if (state.view === 'mine') renderMine();
          if (state.view === 'home') renderHome();
        })
        .catch(function () {});

      apiFetch('/api/records')
        .then(function (list) {
          state.records = (list || []).map(mapRecord);
          syncIdentity();
          renderHome();
          renderWork();
          if (state.view === 'mine') renderMine();
        })
        .catch(function (e) { toast('档案加载失败：' + e.message); });
      return;
    }

    var existed = read(LS.user(name), null);
    state.profile = existed || { name: name, age: '', gender: '', hospital: '' };
    if (!state.profile.name) state.profile.name = name;
    if (!existed) write(LS.user(name), state.profile);
    state.records = read(LS.records(name), []);
    if (!Array.isArray(state.records)) state.records = [];

    $('#gate').hidden = true;
    $('#app').hidden = false;

    syncIdentity();
    go('home');
  }

  /* ============================================================
     5. 视图切换
     ============================================================ */
  function go(view) {
    state.view = view;
    document.body.setAttribute('data-view', view);
    $$('.navbtn').forEach(function (b) {
      b.classList.toggle('is-on', b.getAttribute('data-go') === view);
    });
    if (view === 'home') renderHome();
    if (view === 'mine') renderMine();
    if (view === 'work') renderWork();
    if (view === 'scan') renderScan();
    window.scrollTo(0, 0);
  }

  function bindGlobal() {
    document.addEventListener('click', function (e) {
      var goBtn = e.target.closest ? e.target.closest('[data-go]') : null;
      if (goBtn) {
        e.preventDefault();
        go(goBtn.getAttribute('data-go'));
        return;
      }
      var src = e.target.closest ? e.target.closest('[data-src]') : null;
      if (src) {
        e.preventDefault();
        openRecord(src.getAttribute('data-src'));
        return;
      }
      var act = e.target.closest ? e.target.closest('[data-act]') : null;
      if (!act) return;
      var a = act.getAttribute('data-act');
      if (a === 'archive') { saveDraft(); }
      if (a === 'del') { deleteRecord(act.getAttribute('data-id')); }
      if (a === 'edit') { openEditDialog(act.getAttribute('data-id')); }
      if (a === 'reuse') {
        if (!state.draft) return;
        go('work');
        var input = $('#askInput');
        input.value = state.draft.transcript;
        autoGrow();
        input.focus();
      }
    });
  }

  /* ============================================================
     5.5 主题（三套，账号隔离）
     ============================================================ */
  var THEMES = ['warm', 'dark', 'light'];

  function applyTheme(id, remember) {
    if (THEMES.indexOf(id) === -1) id = 'warm';
    document.documentElement.setAttribute('data-theme', id);
    state.theme = id;
    var sel = $('#themeSel');
    if (sel) sel.value = id;
    if (remember) {
      /* 必须和 read() 的 JSON.parse 对齐：裸字符串会让下次启动解析失败、悄悄退回默认 */
      try { localStorage.setItem('lx_theme_guest_v1', JSON.stringify(id)); } catch (e) {}
      if (state.user) {
        try { localStorage.setItem(LS.theme(state.user), JSON.stringify(id)); } catch (e) {}
      }
    }
  }

  function bindTheme() {
    var sel = $('#themeSel');
    if (!sel) return;
    sel.addEventListener('change', function () {
      applyTheme(sel.value, true);
      toast('主题：' + sel.options[sel.selectedIndex].text + '（按账号记住）');
    });
  }

  /* ============================================================
     5.6 拍照识药
     图片只在前端选择与预览；识别交给千问 VL（OpenAI 兼容接口）。
     没配 Key 时不给假结果，直接说清楚，并给一张可手填的卡片。
     ============================================================ */
  var AI_DEFAULT = {
    base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    textModel: 'qwen-plus',
    visionModel: 'qwen3-vl-plus'
  };

  function loadLocalAccount(name) {
    state.ai = read(LS.ai(name), null) || {};
    state.scanHist = read(LS.scan(name), []);
    if (!Array.isArray(state.scanHist)) state.scanHist = [];
    state.scan = { image: '', meds: [], source: '', at: 0 };
    /* 新账号不继承上一个账号的主题：没有自己的主题就用默认暖纸 */
    applyTheme(read(LS.theme(name), 'warm'), false);
    renderEngine();
  }

  function aiCfg() {
    var c = state.ai || {};
    return {
      base: String(c.base || AI_DEFAULT.base).replace(/\/+$/, ''),
      key: c.key || '',
      textModel: c.textModel || AI_DEFAULT.textModel,
      visionModel: c.visionModel || AI_DEFAULT.visionModel
    };
  }
  function aiTextReady() { var c = aiCfg(); return !!(c.key && c.textModel); }
  function aiVisionReady() { var c = aiCfg(); return !!(c.key && c.visionModel); }

  function aiEndpoint(base) {
    if (/\/chat\/completions$/.test(base)) return base;
    return base + '/chat/completions';
  }

  function aiCall(messages, model, opts) {
    opts = opts || {};
    var c = aiCfg();
    if (!c.key) return Promise.reject(new Error('还没有填千问 API Key'));

    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, opts.timeout || 45000);

    return fetch(aiEndpoint(c.base), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + c.key
      },
      body: JSON.stringify({
        model: model,
        messages: messages,
        temperature: typeof opts.temperature === 'number' ? opts.temperature : 0.3,
        max_tokens: opts.maxTokens || 800
      }),
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (r) {
      return r.text().then(function (text) {
        clearTimeout(timer);
        var body = null;
        try { body = JSON.parse(text); } catch (e) {}
        if (!r.ok) {
          var detail = (body && body.error && (body.error.message || body.error.code)) || text.slice(0, 160) || ('HTTP ' + r.status);
          throw new Error('HTTP ' + r.status + '：' + detail);
        }
        if (!body) throw new Error('返回不是 JSON');
        var choice = (body.choices || [])[0] || {};
        var content = choice.message && choice.message.content;
        if (Array.isArray(content)) {
          content = content.map(function (x) { return x.text || ''; }).join('');
        }
        if (!content) throw new Error('返回里没有内容');
        return String(content);
      });
    }).catch(function (e) {
      clearTimeout(timer);
      if (e && e.name === 'AbortError') throw new Error('超时（45 秒）');
      throw e;
    });
  }

  function normTiming(v) {
    var t = clean(v);
    if (!t) return '';
    if (/饭前|餐前/.test(t)) return '饭前';
    if (/饭后|餐后/.test(t)) return '饭后';
    if (/睡前|临睡|晚上/.test(t)) return '睡前';
    if (/空腹/.test(t)) return '空腹';
    if (/随餐|餐中/.test(t)) return '随餐';
    return t.slice(0, 6);
  }

  function normMed(m) {
    m = m || {};
    var days = clean(m.days || m.duration || m['疗程'] || '').replace(/[^\d]/g, '');
    return {
      name: clean(m.name || m.drug || m['药品名称'] || m['药品'] || ''),
      dose: clean(m.dose || m.dosage || m['剂量'] || m['用量'] || ''),
      freq: clean(m.freq || m.frequency || m['服用频次'] || m['频次'] || ''),
      timing: normTiming(m.timing || m.time || m['用药时间'] || m['服用时间'] || ''),
      days: days
    };
  }

  function emptyMed() {
    return { name: '', dose: '', freq: '', timing: '', days: '' };
  }

  function parseMedJSON(text) {
    var t = String(text || '').trim().replace(/^```(?:json)?/i, '').replace(/```$/, '').trim();
    var arr = null;
    try { arr = JSON.parse(t); } catch (e) {
      var m = t.match(/\[[\s\S]*\]/);
      if (m) { try { arr = JSON.parse(m[0]); } catch (e2) {} }
      if (!arr) {
        var o = t.match(/\{[\s\S]*\}/);
        if (o) { try { arr = [JSON.parse(o[0])]; } catch (e3) {} }
      }
    }
    if (arr && !Array.isArray(arr)) arr = arr.meds || arr.medicines || arr.items || [arr];
    if (!Array.isArray(arr)) return [];
    return arr.map(normMed).filter(function (m) { return !!m.name; });
  }

  var VISION_SYS = '你是药品包装与处方的识别器。只输出 JSON，不要解释，不要 Markdown 代码块。';
  var VISION_USER = '识别图片里出现的所有药品，输出 JSON 数组，每项四个字段：\n'
    + '[{"name":"药品名称","dose":"单次剂量，如 20mg","freq":"频次，如 每天三次","timing":"饭前/饭后/睡前/空腹/随餐"}]\n'
    + '规则：看不清、或图片里没有的字段填空字符串；不要猜；不要输出诊断或用药建议。';

  function visionParse(dataUrl) {
    return aiCall([
      { role: 'system', content: VISION_SYS },
      { role: 'user', content: [
        { type: 'text', text: VISION_USER },
        { type: 'image_url', image_url: { url: dataUrl } }
      ] }
    ], aiCfg().visionModel, { temperature: 0.1, maxTokens: 900, timeout: 60000 });
  }

  function setScanStatus(msg, isErr) {
    var el = $('#scanStatus');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('is-err', !!isErr);
  }

  function showPreview(uri) {
    var img = $('#scanPreview');
    var empty = $('#shotEmpty');
    if (!img) return;
    if (uri) {
      img.src = uri;
      img.hidden = false;
      if (empty) empty.hidden = true;
    } else {
      img.removeAttribute('src');
      img.hidden = true;
      if (empty) empty.hidden = false;
    }
  }

  function shrinkImage(dataUrl, max, quality, cb) {
    var img = new Image();
    img.onload = function () {
      var w = img.naturalWidth || img.width;
      var h = img.naturalHeight || img.height;
      if (!w || !h) { cb(dataUrl); return; }
      var scale = Math.min(1, max / Math.max(w, h));
      var cw = Math.round(w * scale);
      var ch = Math.round(h * scale);
      var cv = document.createElement('canvas');
      cv.width = cw; cv.height = ch;
      var ctx = cv.getContext('2d');
      if (!ctx) { cb(dataUrl); return; }
      ctx.drawImage(img, 0, 0, cw, ch);
      try { cb(cv.toDataURL('image/jpeg', quality)); }
      catch (e) { cb(dataUrl); }
    };
    img.onerror = function () { cb(dataUrl); };
    img.src = dataUrl;
  }

  function toAvatar(file, cb) {
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        var side = Math.min(img.width, img.height) || 1;
        var cv = document.createElement('canvas');
        cv.width = 256; cv.height = 256;
        var ctx = cv.getContext('2d');
        if (!ctx) { cb(String(reader.result || '')); return; }
        ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 256, 256);
        try { cb(cv.toDataURL('image/jpeg', 0.82)); }
        catch (e) { cb(String(reader.result || '')); }
      };
      img.onerror = function () { cb(String(reader.result || '')); };
      img.src = String(reader.result || '');
    };
    reader.onerror = function () { toast('这张图读不出来。'); };
    reader.readAsDataURL(file);
  }

  function handleScanFile(file) {
    if (!/^image\//.test(file.type || '')) { toast('请选择图片文件（JPG / PNG）。'); return; }
    if (file.size > 12 * 1024 * 1024) { toast('图片超过 12MB，先压缩或换一张。'); return; }

    var reader = new FileReader();
    reader.onload = function () {
      showPreview(String(reader.result || ''));
      setScanStatus('图片已载入，正在压缩…', false);
      shrinkImage(String(reader.result || ''), 1400, 0.86, function (uri) {
        runScan(uri);
      });
    };
    reader.onerror = function () { toast('这张图读不出来，换一张试试。'); };
    reader.readAsDataURL(file);
  }

  function runScan(dataUrl) {
    state.scan.image = dataUrl;
    state.scan.meds = [];
    state.scan.source = '';
    renderScanResults();

    if (!aiVisionReady()) {
      setScanStatus('还没有接入千问，本机没有 OCR 能力，图片读不出字。点「千问设置」填 Key，或先手动填一条。', true);
      state.scan.meds = [emptyMed()];
      state.scan.source = 'manual';
      renderScanResults();
      return;
    }

    setScanStatus('正在交给 ' + aiCfg().visionModel + ' 识图…', false);
    var t0 = Date.now();
    visionParse(dataUrl).then(function (text) {
      var meds = parseMedJSON(text);
      state.scan.raw = text;
      if (!meds.length) {
        setScanStatus('千问没从这张图里认出药品。换一张更清楚的，或手动填一条。', true);
        state.scan.meds = [emptyMed()];
        state.scan.source = 'manual';
      } else {
        state.scan.meds = meds;
        state.scan.source = 'qwen';
        setScanStatus('识别完成：' + meds.length + ' 种药 · 用时 ' + ((Date.now() - t0) / 1000).toFixed(1) + ' 秒。核对后再加入档案。', false);
        saveScanHistory(meds, dataUrl);
      }
      renderScanResults();
    }).catch(function (e) {
      setScanStatus('识别失败：' + e.message + '（若是跨域被浏览器拦下，需要走支持 CORS 的网关或本机代理）', true);
      state.scan.meds = [emptyMed()];
      state.scan.source = 'manual';
      renderScanResults();
    });
  }

  function renderScan() {
    renderEngine();
    renderScanHist();
    renderScanResults();
    showPreview(state.scan.image || '');
    if (!state.scan.image) setScanStatus('选一张图，识别药名、剂量、频次与饭前饭后。', false);
  }

  function renderEngine() {
    var ready = aiVisionReady();
    var tag = $('#scanEngine');
    if (tag) tag.textContent = ready ? '千问 VL' : '本机识别';
    var hint = $('#aiHint');
    if (hint) {
      hint.innerHTML = ready
        ? '已接入 <b>' + esc(aiCfg().visionModel) + '</b>：识图交给千问，字段仍可手改。'
        : '未接入千问：本机没有 OCR，只能手动录入。点右边「千问设置」填 Key 后自动识图。';
    }
  }

  function renderScanResults() {
    var box = $('#scanResults');
    if (!box) return;
    var meds = state.scan.meds || [];
    var addAll = $('#scanAddAll');

    if (!meds.length) {
      box.innerHTML = '<div class="blank"><b>还没有识别结果</b>从左边拍一张药盒或处方，小鹿会认出药名、剂量、频次和用药时间。识别有误可以直接改，再决定要不要加进病历。</div>';
      if (addAll) addAll.disabled = true;
      return;
    }

    var tagText = state.scan.source === 'qwen' ? '千问识图 · 可改'
      : (state.scan.source === 'history' ? '历史记录 · 可改' : '手动录入 · 待核对');

    box.innerHTML = meds.map(function (m, i) {
      var opt = function (v) {
        return '<option value="' + v + '"' + (m.timing === v ? ' selected' : '') + '>' + v + '</option>';
      };
      return '<article class="med-card" data-med="' + i + '">' +
        '<div class="med-card__hd">' +
          '<span class="med-card__no">' + (i < 9 ? '0' : '') + (i + 1) + '</span>' +
          '<span class="med-card__tag">' + tagText + '</span>' +
        '</div>' +
        '<div class="med-card__bd">' +
          '<label class="med-field"><span>药品名称</span>' +
            '<input class="med-in" data-k="name" value="' + esc(m.name) + '" placeholder="例如 奥美拉唑肠溶胶囊"></label>' +
          '<div class="med-grid">' +
            '<label class="med-field"><span>剂量</span>' +
              '<input class="med-in" data-k="dose" value="' + esc(m.dose) + '" placeholder="20mg"></label>' +
            '<label class="med-field"><span>频次</span>' +
              '<input class="med-in" data-k="freq" value="' + esc(m.freq) + '" placeholder="每天一次"></label>' +
            '<label class="med-field"><span>时间</span>' +
              '<select class="med-in" data-k="timing"><option value="">未注明</option>' +
              opt('饭前') + opt('饭后') + opt('睡前') + opt('空腹') + opt('随餐') + '</select></label>' +
          '</div>' +
        '</div>' +
        '<div class="med-card__ft">' +
          '<button class="btn btn--primary" type="button" data-add="' + i + '">添加到本次病历用药</button>' +
        '</div>' +
      '</article>';
    }).join('');

    if (addAll) addAll.disabled = !meds.some(function (m) { return clean(m.name); });
  }

  function renderScanHist() {
    var box = $('#scanHist');
    var cnt = $('#scanHistCount');
    var all = state.scanHist || [];
    if (cnt) cnt.textContent = all.length + ' 条';
    if (!box) return;

    if (!all.length) {
      box.innerHTML = '<p class="scan__hint">还没有记录。识别成功的照片会留在这里，方便回头核对。</p>';
      return;
    }

    box.innerHTML = all.map(function (h, i) {
      var names = (h.meds || []).map(function (m) { return m.name; }).filter(Boolean).join('、');
      return '<button class="hist" type="button" data-hist="' + i + '">' +
        '<span class="hist__d">' + fmtDate(h.at) + ' ' + fmtTime(h.at) + '</span>' +
        '<span class="hist__t">' + esc(names || '未识别到药名') + '</span>' +
        '<span class="hist__x">' + ((h.meds || []).length) + ' 种 · 点一下重新载入</span>' +
        '</button>';
    }).join('');
  }

  function saveScanHistory(meds, image) {
    if (!state.user) return;
    shrinkImage(image, 240, 0.55, function (thumb) {
      var entry = { id: 's_' + Date.now().toString(36), at: Date.now(), meds: meds, thumb: thumb };
      var all = read(LS.scan(state.user), []);
      if (!Array.isArray(all)) all = [];
      all.unshift(entry);
      all = all.slice(0, 20);
      if (write(LS.scan(state.user), all)) {
        state.scanHist = all;
        renderScanHist();
      }
    });
  }

  function loadScanHist(i) {
    var h = (state.scanHist || [])[i];
    if (!h) return;
    state.scan.meds = (h.meds || []).map(normMed);
    state.scan.source = 'history';
    state.scan.image = h.thumb || '';
    showPreview(state.scan.image);
    renderScanResults();
    setScanStatus('已载入 ' + fmtDate(h.at) + ' 的识别记录，改完再决定是否加入。', false);
  }

  function clearScan() {
    state.scan = { image: '', meds: [], source: '', at: 0 };
    showPreview('');
    renderScanResults();
    setScanStatus('已清空。选一张图重新开始。', false);
  }

  function sameMed(list, m) {
    return (list || []).some(function (x) { return clean(x.name) === m.name; });
  }

  function addScanMed(med, quiet) {
    med = normMed(med);
    if (!med.name) { toast('先填药名，再加入档案。'); return; }

    /* 1) 有未归档草稿：加到草稿，用户确认归档时才落库 */
    if (state.draft && !state.draft.archived) {
      state.draft.meds = state.draft.meds || [];
      if (sameMed(state.draft.meds, med)) { toast('这份草稿里已经有「' + med.name + '」。'); return; }
      state.draft.meds.push(med);
      renderRecordPanel();
      renderRx();
      toast('已加入本次记录（还没归档）。');
      go('work');
      return;
    }

    /* 2) 最近一次就诊：确认后写进那一条 */
    var last = sortedDesc()[0];
    if (last) {
      var when = fmtDate(last.at) + ' ' + (last.hospital || '未记录医院');
      if (!quiet && !window.confirm('把「' + med.name + '」加入最近一次就诊（' + when + '）的用药？')) return;
      last.meds = last.meds || [];
      if (sameMed(last.meds, med)) { toast('那次记录里已经有「' + med.name + '」。'); return; }
      last.meds.push(med);
      persistRecords();

      if (API.on) {
        apiFetch('/api/records/' + encodeURIComponent(last.id), { method: 'PATCH', body: { meds: last.meds } })
          .catch(function (e) { toast('本地已加，服务端同步失败：' + e.message); });
      }

      syncIdentity();
      renderHome();
      renderMine();
      renderWork();
      toast('已加入 ' + fmtDate(last.at) + ' 那次记录的用药。');
      return;
    }

    /* 3) 一份记录都没有：建一份草稿，让用户补医院与诊断 */
    var now = Date.now();
    var d = new Date(now);
    state.draft = {
      id: 'v_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
        '_' + pad(d.getHours()) + pad(d.getMinutes()) + '_' + Math.random().toString(36).slice(2, 6),
      clientId: newClientId(),
      at: now,
      hospital: '',
      summary: '',
      meds: [med],
      followUp: '',
      allergy: '',
      allergyDenied: false,
      conditions: [],
      discontinued: [],
      notes: [],
      transcript: '',
      mode: 'scan',
      archived: false,
      extractState: 'ready',
      atFromText: false,
      edited: true,
      editedFields: { meds: 1 }
    };
    renderRecordPanel();
    go('work');
    toast('还没有就诊记录，已建一份草稿：补上医院和诊断再保存。');
  }

  function renderAiState() {
    var el = $('#aiState');
    if (!el) return;
    var c = aiCfg();
    el.textContent = aiTextReady()
      ? '已保存：' + c.textModel + ' / ' + c.visionModel + '。Key 只在本机这个账号下。'
      : '还没填 Key：识图与千问回答都不会启用，其余功能不受影响。';
  }

  function openAiDialog() {
    var c = aiCfg();
    $('#aiBase').value = (state.ai && state.ai.base) || '';
    $('#aiBase').placeholder = AI_DEFAULT.base;
    $('#aiKey').value = (state.ai && state.ai.key) || '';
    $('#aiTextModel').value = (state.ai && state.ai.textModel) || '';
    $('#aiVisionModel').value = (state.ai && state.ai.visionModel) || '';
    renderAiState();
    openDialog($('#aiDialog'));
  }

  function saveAiConfig() {
    var cfg = {
      base: clean($('#aiBase').value) || AI_DEFAULT.base,
      key: clean($('#aiKey').value),
      textModel: clean($('#aiTextModel').value) || AI_DEFAULT.textModel,
      visionModel: clean($('#aiVisionModel').value) || AI_DEFAULT.visionModel
    };
    state.ai = cfg;
    if (write(LS.ai(state.user), cfg)) {
      renderEngine();
      renderAiState();
    }
  }

  function testAi() {
    saveAiConfig();
    var el = $('#aiState');
    if (!aiTextReady()) { el.textContent = '先填 API Key。'; return; }
    el.textContent = '正在测试 ' + aiCfg().textModel + '…';
    aiCall([{ role: 'user', content: '只回复两个字：在的' }], aiCfg().textModel, { maxTokens: 16, timeout: 25000 })
      .then(function (text) {
        el.textContent = '连接成功：' + String(text).replace(/\s+/g, ' ').slice(0, 40);
      })
      .catch(function (e) {
        el.textContent = '连接失败：' + e.message;
      });
  }

  function bindScan() {
    var camFile = $('#camFile');
    var pickFile = $('#pickFile');
    if (!camFile || !pickFile) return;

    $('#camBtn').addEventListener('click', function () { camFile.click(); });
    $('#pickBtn').addEventListener('click', function () { pickFile.click(); });

    [camFile, pickFile].forEach(function (inp) {
      inp.addEventListener('change', function (e) {
        var f = e.target.files && e.target.files[0];
        e.target.value = '';
        if (f) handleScanFile(f);
      });
    });

    $('#scanClear').addEventListener('click', clearScan);
    $('#scanAddAll').addEventListener('click', function () {
      var meds = (state.scan.meds || []).filter(function (m) { return clean(m.name); });
      if (!meds.length) { toast('还没有可加入的药。'); return; }
      if (!window.confirm('把 ' + meds.length + ' 种药加入本次病历用药？')) return;
      meds.forEach(function (m, i) { addScanMed(m, true); });
    });

    $('#scanResults').addEventListener('input', onScanEdit);
    $('#scanResults').addEventListener('change', onScanEdit);
    $('#scanResults').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-add]');
      if (!btn) return;
      addScanMed(state.scan.meds[+btn.getAttribute('data-add')], false);
    });

    $('#scanHist').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-hist]');
      if (!btn) return;
      loadScanHist(+btn.getAttribute('data-hist'));
    });

    $('#aiOpen').addEventListener('click', openAiDialog);
    $('#aiTest').addEventListener('click', testAi);

    var dlg = $('#aiDialog');
    if (dlg) {
      dlg.addEventListener('close', function () {
        if (dlg.returnValue === 'save') {
          saveAiConfig();
          toast('千问配置已保存。');
        }
      });
    }
  }

  function onScanEdit(e) {
    var t = e.target;
    var k = t.getAttribute && t.getAttribute('data-k');
    if (!k) return;
    var card = t.closest('[data-med]');
    if (!card) return;
    var i = +card.getAttribute('data-med');
    if (!state.scan.meds[i]) return;
    state.scan.meds[i][k] = t.value;

    var addAll = $('#scanAddAll');
    if (addAll) addAll.disabled = !(state.scan.meds || []).some(function (m) { return clean(m.name); });
  }

  /* 头像：三处（顶栏 / 首页 / 我的）同一个绘制函数，改一处就三处同步 */
  function paintAvatar(el, p, name) {
    if (!el) return;
    var src = p && p.avatar;
    if (src) {
      el.classList.add('avatar--img');
      el.style.backgroundImage = 'url("' + src + '")';
      el.textContent = '';
    } else {
      el.classList.remove('avatar--img');
      el.style.backgroundImage = '';
      el.textContent = firstChar(name);
    }
  }

  /* ============================================================
     5.7 千问：把检索结论说得像人话
     事实仍由本机档案决定；模型只负责组织语言，越界即回退。
     ============================================================ */
  var SMALLTALK = /你叫什么|你是谁|你的名字|你叫啥|自我介绍|你是什么|你能做什么|你会什么|你可以做什么|在吗|你好|谢谢|今天几号|现在几点|星期几/;
  var ADVICE_OUT = /(建议|可以|应该|推荐|不妨)(吃|喝|服用|用|停|加|减|换)|停药|加量|减量|换药|不必吃|不用吃|自行/;

  function plainOf(res) {
    return (res.paras || []).map(function (p) { return p.t; }).filter(Boolean).join('\n');
  }

  function aiContext() {
    var recs = sortedDesc().slice(0, 5);
    if (!recs.length) return '（这个账号还没有任何就诊记录）';
    return recs.map(function (r, i) {
      var meds = (r.meds || []).map(function (m) {
        return [m.name, m.dose, m.freq, m.timing, m.days ? m.days + ' 天' : ''].filter(Boolean).join(' ');
      }).join('；');
      return '第' + (i + 1) + '次 ' + fmtDate(r.at) + ' ' + (r.hospital || '未记录医院') +
        '｜诊断：' + (r.summary || '未写明') +
        '｜用药：' + (meds || '未提到') +
        '｜复诊：' + (r.followUp || '未提到') +
        '｜过敏：' + (r.allergyDenied ? '明确否认' : (r.allergy || '未提到')) +
        '｜疾病/事件：' + ((r.conditions || []).join('、') || '未记录') +
        '｜注意：' + ((r.notes || []).join('；') || '未提到');
    }).join('\n');
  }

  function aiAnswerText(q, local) {
    var grounded = !!(local.sources && local.sources.length);

    var sys = grounded
      ? '你是「小鹿」，一个病历记录助手。说话简短、具体，像人一样，不用套话。\n'
        + '硬性规则：\n'
        + '1) 只能使用【档案】与【已检索结论】里的事实，不得补充档案里没有的信息。\n'
        + '2) 不做诊断、不给治疗或用药建议、不建议停药或改剂量。\n'
        + '3) 档案里没有的，直接说「病历档案未查询到该内容」，不要猜。\n'
        + '4) 不要重复免责声明，界面会统一显示。\n'
        + '5) 只回答用户问到的内容，不要额外补一句「其他未查询到」。\n'
        + '6) 中文，最多 3 段，总字数不超过 180 字。'
      : '你是「小鹿」，一个病历记录助手，正在和用户做普通对话。\n'
        + '用一两句话自然回答，可以自我介绍或说明你能做什么，不要端着。\n'
        + '绝不提供诊断、用药或治疗建议；对方若问病情，提醒他直接问「上次看病」「今天吃什么药」这类档案问题。\n'
        + '中文，不超过 60 字。';

    var user = grounded
      ? '【档案】\n' + aiContext() + '\n\n【已检索结论】\n' + plainOf(local) + '\n\n【用户问题】\n' + q
      : '【普通对话】\n' + q;
    return aiCall([{ role: 'system', content: sys }, { role: 'user', content: user }],
      aiCfg().textModel, { temperature: 0.35, maxTokens: 500, timeout: 30000 });
  }

  function shouldAskAi(local, q) {
    if (!aiTextReady()) return false;
    if (local.sources && local.sources.length) return true;
    if (/胸痛|呼吸困难|喘不上|昏|晕倒|大出血|抽搐|自杀|急救|急诊/.test(q)) return false;
    return SMALLTALK.test(q);
  }

  /* ============================================================
     6. 身份同步（顶栏 / 首页）
     ============================================================ */
  function syncIdentity() {
    var p = state.profile || {};
    var label = p.name || state.user || '—';

    $('#barUser').textContent = label;
    paintAvatar($('#barAvatar'), p, label);

    var bits = [];
    if (p.age) bits.push(p.age + ' 岁');
    if (p.gender) bits.push(p.gender);
    bits.push(state.records.length + ' 份记录');
    $('#barMeta').textContent = bits.join(' · ');
  }

  function renderHome() {
    var p = state.profile || {};
    var name = p.name || state.user || '—';
    $('#homeName').textContent = name;
    paintAvatar($('#homeAvatar'), p, name);

    var facts = [];
    if (p.age) facts.push(p.age + ' 岁');
    if (p.gender) facts.push(p.gender);
    if (p.hospital) facts.push('常在 ' + p.hospital);
    if (p.idcard) facts.push('身份证 ' + maskId(p.idcard));
    facts.push(state.records.length + ' 份记录');

    var html = facts.map(function (f, i) {
      return (i ? '<span class="dot">·</span>' : '') + esc(f);
    }).join('');
    if (!p.age && !p.gender) {
      html += '<span class="dot">·</span><a href="#" data-go="mine" style="color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent)">去「我的」补两行</a>';
    }
    $('#homeFacts').innerHTML = html;
    $('#homeRecCount').textContent = state.records.length + ' 份记录';
  }

  /* 身份证只显示后四位，首页/我的都能看到"已保存"，又不会到处暴露全号 */
  function maskId(v) {
    var t = clean(v).toUpperCase();
    if (!t) return '';
    if (t.length <= 4) return t;
    return '••••' + t.slice(-4);
  }

  function firstChar(s) {
    s = clean(s);
    return s ? s.slice(0, 1).toUpperCase() : '鹿';
  }

  /* ============================================================
     7. 我的：资料 + 记录
     ============================================================ */
  function paintIdHint(v) {
    var el = $('#idHint');
    if (!el) return;
    var t = clean(v).toUpperCase();
    if (!t) {
      el.textContent = '身份证仅保存在本机浏览器，不会上传任何网络。';
      el.classList.remove('is-warn');
      return;
    }
    var ok = /^\d{15}$/.test(t) || /^\d{17}[\dX]$/.test(t);
    el.textContent = ok
      ? '格式看起来对：' + t.length + ' 位。身份证仅保存在本机浏览器，不会上传任何网络。'
      : '身份证一般是 18 位（末位可能是 X）。这里只做格式提示，不做强校验；号码仅保存在本机浏览器。';
    el.classList.toggle('is-warn', !ok);
  }

  function bindProfile() {
    $('#fIdcard').addEventListener('input', function () {
      paintIdHint($('#fIdcard').value);
    });

    $('#avaBtn').addEventListener('click', function () { $('#avaFile').click(); });
    $('#profAvatar').addEventListener('click', function () { $('#avaFile').click(); });
    $('#avaFile').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      e.target.value = '';
      if (!f) return;
      if (!/^image\//.test(f.type || '')) { toast('请选择图片文件。'); return; }

      toAvatar(f, function (dataUrl) {
        if (!dataUrl || dataUrl.length < 32) { toast('这张图读不出来，换一张。'); return; }
        if (dataUrl.length > 900 * 1024) { toast('头像压缩后仍偏大，换一张更小的图。'); return; }
        var p = state.profile || { name: state.user, age: '', gender: '', hospital: '' };
        p.avatar = dataUrl;
        state.profile = p;
        if (write(LS.user(state.user), p)) {
          syncIdentity();
          renderHome();
          renderMine();
          toast('头像已更新，首页、顶栏都跟着变了。');
        }
      });
    });

    $('#profForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var p = state.profile || {};
      var name = clean($('#fName').value) || state.user;
      p.name = name;
      p.age = clean($('#fAge').value);
      p.gender = $('#fGender').value;
      p.hospital = clean($('#fHospital').value);
      p.idcard = clean($('#fIdcard').value).toUpperCase();
      state.profile = p;

      var btn = e.target.querySelector('button[type="submit"]');
      var oldText = btn ? btn.textContent : '';

      function apply(server) {
        var keepAvatar = (state.profile && state.profile.avatar) || '';
        var keepId = p.idcard || '';
        if (server) {
          state.profile = {
            name: server.name || name,
            age: server.age || '',
            gender: server.gender || '',
            hospital: server.hospital || '',
            avatar: keepAvatar,
            idcard: keepId
          };
        }
        write(LS.user(state.user), state.profile);
        syncIdentity();
        renderHome();
        renderMine();
        renderWork();
        paintIdHint(state.profile.idcard || '');
      }

      if (!API.on) {
        apply(null);
        toast('资料已保存到本机。');
        $('#saveHint').textContent = '已保存 · ' + fmtClock(Date.now());
        return;
      }

      if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
      $('#saveHint').textContent = '正在同步到服务端…';
      apiFetch('/api/me/profile', {
        method: 'PUT',
        body: { name: name, age: p.age, gender: p.gender, hospital: p.hospital }
      }).then(function (server) {
        apply(server);
        toast('资料已同步到服务端，换设备登录也能看到。');
        $('#saveHint').textContent = '已同步 · ' + fmtClock(Date.now());
      }).catch(function (err) {
        /* 失败时保留本机修改，但不能显示“已同步” */
        apply(null);
        toast('同步失败，已先存本机：' + err.message);
        $('#saveHint').textContent = '只存到本机，未同步';
      }).then(function () {
        if (btn) { btn.disabled = false; btn.textContent = oldText || '保存资料'; }
      });
    });
  }

  function renderMine() {
    var p = state.profile || {};
    var name = p.name || state.user || '—';
    $('#profName').textContent = name;
    paintAvatar($('#profAvatar'), p, name);

    var bits = [];
    if (p.age) bits.push(p.age + ' 岁');
    if (p.gender) bits.push(p.gender);
    bits.push(state.records.length + ' 份记录');
    /* 身份证在下方表单里完整显示，这里不再挤进一行小字 */
    $('#profMeta').textContent = bits.join(' · ');

    /* 表单只在未聚焦时回填，避免打断输入 */
    if (document.activeElement !== $('#fName')) $('#fName').value = p.name || '';
    if (document.activeElement !== $('#fAge')) $('#fAge').value = p.age || '';
    if (document.activeElement !== $('#fHospital')) $('#fHospital').value = p.hospital || '';
    if (document.activeElement !== $('#fIdcard')) $('#fIdcard').value = p.idcard || '';
    paintIdHint(p.idcard || '');
    $('#fGender').value = p.gender || '';

    $('#recCount').textContent = state.records.length + ' 份';
    renderRecords();
  }

  function sortedDesc() {
    return state.records.slice().sort(function (a, b) { return b.at - a.at; });
  }
  function sortedAsc() {
    return state.records.slice().sort(function (a, b) { return a.at - b.at; });
  }
  function seqOf(rec) {
    return sortedAsc().indexOf(rec) + 1;
  }

  function renderRecords() {
    var list = $('#timeline');
    var blank = $('#emptyRecs');
    if (!list) return;

    if (!state.records.length) {
      list.innerHTML = '';
      if (blank) blank.style.display = '';
      return;
    }
    if (blank) blank.style.display = 'none';

    list.innerHTML = sortedDesc().map(function (r) {
      var n = seqOf(r);
      var medCount = (r.meds || []).length;
      return '' +
        '<li data-id="' + esc(r.id) + '">' +
          '<button class="tl" type="button" aria-expanded="false">' +
            '<span class="tl__date"><span>' + fmtDate(r.at) + ' ' + fmtTime(r.at) + '</span>' +
              '<span class="tl__no">第 ' + n + ' 次</span></span>' +
            '<span class="tl__ttl">' + esc(r.hospital || '未记录医院') + '</span>' +
            '<span class="tl__sub">' + esc(r.summary || '未写明诊断') +
              (medCount ? ' · ' + medCount + ' 种药' : '') + '</span>' +
          '</button>' +
          '<div class="tl__panel" hidden>' + detailHtml(r, n) + '</div>' +
        '</li>';
    }).join('');

    $$('#timeline .tl').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var li = btn.parentNode;
        var panel = li.querySelector('.tl__panel');
        var open = li.classList.contains('is-open');
        /* 单开：点另一条时先收起别的 */
        $$('#timeline li.is-open').forEach(function (other) {
          if (other === li) return;
          other.classList.remove('is-open');
          other.querySelector('.tl__panel').hidden = true;
          other.querySelector('.tl').classList.remove('is-active');
          other.querySelector('.tl').setAttribute('aria-expanded', 'false');
        });
        li.classList.toggle('is-open', !open);
        panel.hidden = open;
        btn.classList.toggle('is-active', !open);
        btn.setAttribute('aria-expanded', String(!open));
      });
    });
  }

  function detailHtml(r, n) {
    var meds = r.meds || [];
    var notes = r.notes || [];

    var medHtml = meds.length
      ? '<ul class="ans-list">' + meds.map(function (m) {
          var bits = [];
          if (m.dose) bits.push(m.dose);
          if (m.freq) bits.push(m.freq);
          if (m.timing) bits.push(m.timing);
          if (m.days) bits.push(m.days + ' 天');
          return '<li><b>' + esc(m.name) + '</b>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</li>';
        }).join('') + '</ul>'
      : '<p class="empty" style="margin:0 0 10px">这次没有提到用药。</p>';

    return '' +
      '<dl class="kv">' +
        '<dt>就诊时间</dt><dd>' + fmtDate(r.at) + ' ' + fmtTime(r.at) + '（' + WEEK[new Date(r.at).getDay()] + '）</dd>' +
        '<dt>医院</dt><dd>' + esc(r.hospital || '未记录') + '</dd>' +
        '<dt>诊断</dt><dd>' + esc(r.summary || '未写明') + '</dd>' +
        '<dt>复诊</dt><dd>' + esc(r.followUp || '未提到') + '</dd>' +
        '<dt>过敏</dt><dd>' + esc(r.allergy || (r.allergyDenied ? '记录写「未发现过敏」' : '未记录（不等于没有）')) + '</dd>' +
        ((r.conditions || []).length
          ? '<dt>疾病/事件</dt><dd>' + esc(r.conditions.join('、')) + '</dd>' : '') +
        ((r.discontinued || []).length
          ? '<dt>停用药</dt><dd>' + esc(r.discontinued.join('、')) + '</dd>' : '') +
      '</dl>' +
      '<p class="tl__label">用药</p>' + medHtml +
      (notes.length ? '<p class="tl__label">注意事项</p><ul class="ans-list">' +
        notes.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '') +
      '<p class="tl__label">完整转写 · ' + r.transcript.length + ' 字</p>' +
      '<p class="tr-full">' + esc(r.transcript) + '</p>' +
      '<div class="tl__acts">' +
        '<span class="meta">第 ' + n + ' 次 · <span class="mono">' + esc(r.id) + '</span></span>' +
        '<span class="tl__acts-r">' +
          '<button class="del" type="button" data-act="edit" data-id="' + esc(r.id) + '">修改</button>' +
          '<button class="del" type="button" data-act="del" data-id="' + esc(r.id) + '">删除</button>' +
        '</span>' +
      '</div>';
  }

  function deleteRecord(id) {
    var rec = state.records.filter(function (r) { return r.id === id; })[0];
    if (!rec) return;
    if (!window.confirm('删除 ' + fmtDate(rec.at) + ' ' + (rec.hospital || '') + ' 这份记录？删除后无法恢复。')) return;

    if (API.on) {
      apiFetch('/api/records/' + encodeURIComponent(id), { method: 'DELETE' })
        .then(function () {
          state.records = state.records.filter(function (r) { return r.id !== id; });
          syncIdentity();
          renderMine();
          renderHome();
          renderWork();
          toast('已删除，剩余 ' + state.records.length + ' 份。');
        })
        .catch(function (e) { toast('删除失败：' + e.message); });
      return;
    }

    state.records = state.records.filter(function (r) { return r.id !== id; });
    persistRecords();
    syncIdentity();
    renderMine();
    renderWork();
    toast('已删除，剩余 ' + state.records.length + ' 份。');
  }

  function persistRecords() {
    // 云端模式下每条变更都已单独提交，这里不再覆盖整份列表（避免回写旧数据）
    if (API.on) return;
    write(LS.records(state.user), state.records);
  }

  /* ============================================================
     8. 工作台渲染
     ============================================================ */
  function renderWork() {
    syncIdentity();
    var mode = $('#ovMode');
    if (mode) mode.textContent = API.on ? '服务端' : '本机';
    renderOverview();
    renderRx();
    renderRecordPanel();
    if (!state.chatReady) { state.chatReady = true; greet(); }
  }

  function renderOverview() {
    var box = $('#overview');
    var cnt = $('#ovCount');
    if (!box) return;
    cnt.textContent = state.records.length + ' 份';

    if (!state.records.length) {
      box.innerHTML = '<p class="empty">还没有记录。这里只统计你自己的档案，别人的看不到。</p>';
      return;
    }
    var last = sortedDesc()[0];
    box.innerHTML = '<div class="ov">' +
      '<div class="ov__row"><span class="ov__k">最近</span><span class="ov__v"><b>' + fmtDate(last.at) + '</b> · ' + esc(last.hospital || '未记录') + '</span></div>' +
      '<div class="ov__row"><span class="ov__k">诊断</span><span class="ov__v">' + esc(last.summary || '未写明') + '</span></div>' +
      '<div class="ov__row"><span class="ov__k">复诊</span><span class="ov__v">' + esc(last.followUp || '未提到') + '</span></div>' +
      '<div class="ov__row"><span class="ov__k">过敏</span><span class="ov__v">' + esc(allergySummary().text) + '</span></div>' +
      '</div>';
  }

  function allergySummary() {
    var recs = sortedDesc();
    var positive = recs.filter(function (r) { return clean(r.allergy); });
    var denied = recs.filter(function (r) { return r.allergyDenied; });
    if (positive.length) {
      return {
        text: positive[0].allergy + (positive.length > 1 ? '（共 ' + positive.length + ' 次记录）' : ''),
        rec: positive[0],
        warn: true,
        count: positive.length
      };
    }
    if (denied.length) {
      return { text: '记录写「未发现过敏」', rec: denied[0], warn: false, count: 0 };
    }
    return { text: '所有记录均未记录过敏（不等于没有）', rec: null, warn: false, count: 0 };
  }

  function renderRx() {
    var box = $('#todayRx');
    var tag = $('#courseTag');
    var allergy = $('#rxAllergy');
    var src = $('#rxSource');
    if (!box) return;

    if (!state.records.length) {
      box.innerHTML = '<p class="empty">还没有用药记录。录一次会诊，医生说的药会按「名称 · 剂量 · 服法 · 天数」列在这里。</p>';
      tag.textContent = '—';
      allergy.textContent = '未记录过敏（不等于没有）';
      allergy.className = 'tag';
      src.textContent = '—';
      return;
    }

    var recs = sortedDesc();
    var last = recs[0];
    var withMeds = recs.filter(function (r) { return (r.meds || []).length; })[0];
    var source = (last.meds || []).length ? last : withMeds;

    if (!source) {
      box.innerHTML = '<p class="empty">你的记录里还没有开过药。</p>';
      tag.textContent = fmtDate(last.at);
    } else {
      var maxDays = 0;
      (source.meds || []).forEach(function (m) { if (m.days && +m.days > maxDays) maxDays = +m.days; });
      var day = daysBetween(source.at, Date.now()) + 1;

      if (maxDays) {
        tag.textContent = day > maxDays ? '已过 ' + maxDays + ' 天疗程' : '第 ' + day + ' 天 / 共 ' + maxDays + ' 天';
      } else {
        tag.textContent = fmtDate(source.at);
      }

      var note = source.id !== last.id
        ? '<p class="rx-note">最近一次（' + fmtDate(last.at) + '）没有记录用药；下面是 ' +
          fmtDate(source.at) + ' 那次有用药的医嘱。</p>'
        : '';

      box.innerHTML = note + (source.meds || []).map(function (m) {
        var bits = [];
        if (m.dose) bits.push(m.dose);
        if (m.freq) bits.push(m.freq);
        if (m.timing) bits.push(m.timing);
        if (m.days) bits.push(m.days + ' 天');
        return '<div class="rx-item">' +
          '<div><div class="rx-name">' + esc(m.name) + '</div>' +
          '<div class="rx-meta">' + bits.map(function (b) { return '<span>' + esc(b) + '</span>'; }).join('') + '</div></div>' +
          '</div>';
      }).join('');
    }

    var sum = allergySummary();
    var short = sum.text.length > 18 ? sum.text.slice(0, 18) + '…' : sum.text;
    allergy.textContent = short + (sum.rec ? ' · ' + fmtDate(sum.rec.at) : '');
    allergy.className = 'tag' + (sum.warn ? ' tag--warn' : '');
    allergy.title = sum.text + (sum.rec ? '（' + fmtDate(sum.rec.at) + '）' : '');
    src.textContent = source ? fmtDate(source.at) + ' 处方' : '—';
  }

  function renderRecordPanel() {
    var body = $('#recordBody');
    var st = $('#recordState');
    var d = state.draft;

    if (!d) {
      st.textContent = '未开始';
      body.innerHTML = '<p class="empty" id="recordEmpty">录完或贴上文字后，这里会出现抽好的字段。你确认保存之前，它不会进档案。</p>';
      return;
    }

    st.textContent = d.archived ? '已归档'
      : (d.extractState === 'extracting' ? '整理中'
        : (d.extractState === 'failed' ? '待手工填写' : '待归档'));

    var holes = [];
    if (!d.hospital) holes.push('医院');
    if (!d.summary) holes.push('诊断');
    if (!namedMeds(d.meds).length) holes.push('用药');
    if (!d.followUp) holes.push('复诊');

    var panelMeds = namedMeds(d.meds);
    var medHtml = panelMeds.length
      ? '<ul class="ans-list">' + panelMeds.map(function (m) {
          var bits = [];
          if (m.dose) bits.push(m.dose);
          if (m.freq) bits.push(m.freq);
          if (m.timing) bits.push(m.timing);
          if (m.days) bits.push(m.days + ' 天');
          return '<li><b>' + esc(m.name) + '</b>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</li>';
        }).join('') + '</ul>'
      : '<p class="rec-card__sub" style="margin:0 0 6px">这次没有提到用药。</p>';

    var notesHtml = d.notes.length
      ? '<p class="tl__label">注意事项</p><ul class="ans-list">' +
        d.notes.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>'
      : '';

    body.innerHTML =
      '<div class="rec-card">' +
        '<div class="rec-card__hd">' +
          '<span class="rec-card__date">' + fmtDate(d.at) + ' ' + fmtTime(d.at) +
            '（' + WEEK[new Date(d.at).getDay()] + '）</span>' +
          '<b class="rec-card__ttl">' + esc(d.hospital || '未记录医院') + '</b>' +
          '<span class="rec-card__sub">' + esc(d.summary || '未写明诊断') +
            (panelMeds.length ? ' · ' + panelMeds.length + ' 种药' : '') + '</span>' +
        '</div>' +
        '<div class="rec-card__bd">' +
          /* 卡头已经写了时间/医院/诊断，这里只补那三项之外的，不重复 */
          '<dl class="kv">' +
            '<dt>复诊</dt><dd>' + esc(d.followUp || '未提到') + '</dd>' +
            '<dt>过敏</dt><dd>' + esc(d.allergy || '未提到') + '</dd>' +
          '</dl>' +
          '<p class="tl__label">用药</p>' + medHtml +
          notesHtml +
        '</div>' +
        '<div class="rec-card__ft"><span>' + esc(d.id) + '</span><span>' +
          d.transcript.length + ' 字</span></div>' +
      '</div>' +
      (d.extractState === 'extracting'
        ? '<p class="holes">正在服务端整理，结果出来前不能保存。</p>'
        : (d.extractState === 'failed'
          ? '<p class="holes">自动整理失败：请手工填写，不会用本机规则猜药名和剂量。</p>'
          : (holes.length ? '<p class="holes">未抽到：' + esc(holes.join('、')) + '（留空，不猜）</p>' : ''))) +
      '<div class="draft__acts">' +
        (d.archived
          ? '<button class="btn btn--ghost btn--mini" type="button" id="goMineBtn">去「我的」看这份</button>'
          : (d.extractState === 'extracting'
            ? '<button class="btn btn--primary btn--mini" type="button" disabled>正在整理…</button>'
            : '<button class="btn btn--primary btn--mini" type="button" data-act="archive">保存到档案</button>') +
            '<button class="btn btn--ghost btn--mini" type="button" id="dropDraft">丢弃</button>') +
        (d.transcript ? '<button class="btn btn--ghost btn--mini" type="button" data-act="reuse">用这段问小鹿</button>' : '') +
      '</div>';

    var goMine = $('#goMineBtn');
    if (goMine) goMine.addEventListener('click', function () { go('mine'); });
    var drop = $('#dropDraft');
    if (drop) drop.addEventListener('click', function () {
      state.draft = null;
      renderRecordPanel();
      toast('已丢弃本次草稿，档案没有变化。');
    });
  }

  /* ============================================================
     9. 录音 + Web Speech 识别
     ============================================================ */
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var recognition = null;
  var restartTimer = null;

  function bindWorkbench() {
    $('#startBtn').addEventListener('click', function () {
      /* 只进工作台，不自动开麦：先让用户看到「征得在场人同意」的提示 */
      go('work');
      toast('开始录音前，请先征得医生与在场其他人的同意。');
    });

    $('#recBtn').addEventListener('click', function () {
      if (state.recording) stopRec();
      else startRec();
    });

    $('#manualGo').addEventListener('click', function () {
      var txt = clean($('#manualText').value);
      if (txt.length < 8) { toast('至少写一两句，比如「医生说……」'); return; }
      buildDraft(txt, 'manual', 0);
    });

    $('#manualFill').addEventListener('click', function () {
      $('#manualText').value =
        '今天下午去了市第一人民医院消化内科。医生说还是慢性胃炎伴糜烂，比上次好一些。' +
        '开四种药：奥美拉唑肠溶胶囊 20mg，每天一次，早饭前吃，吃14天；' +
        '铝碳酸镁咀嚼片 1g，每天三次，饭后嚼服，吃14天；' +
        '莫沙必利片 5mg，每天三次，饭前吃；' +
        '胶体果胶铋胶囊 100mg，每天两次，早晚空腹吃。' +
        '嘱咐少吃辣的、别喝酒，两周后来复诊。他对青霉素过敏，这个要记上。';
      toast('示例已填入，点「整理成记录」试试。');
    });

    $('#askForm').addEventListener('submit', function (e) {
      e.preventDefault();
      send();
    });

    var input = $('#askInput');
    input.addEventListener('input', autoGrow);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });

    $('#chips').addEventListener('click', function (e) {
      var chip = e.target.closest('.chip');
      if (!chip) return;
      $('#askInput').value = chip.textContent.trim();
      autoGrow();
      send();
    });

    $('#saveDialog').addEventListener('close', function () {
      var v = $('#saveDialog').returnValue;
      if (v === 'yes') {
        if (state.editId) saveEdit();
        else saveDraft();
      } else if (v === 'no') {
        if (state.editId) {
          state.editId = '';
          state.editDraft = null;
          toast('没有改动这份记录。');
        } else {
          toast('没有归档。草稿还在左栏，可以继续核对或丢弃。');
        }
      }
    });

    /* 表单里的任何手工输入都标成「已改」，迟到的服务端结果不得覆盖 */
    var dlgForm = $('#dlgForm');
    if (dlgForm) {
      function markEdited(e) {
        var d = formTarget();
        if (!d || !formBelongs()) return;
        d.edited = true;
        d.editedFields = d.editedFields || {};
        var t = e.target;
        var id = t.id;
        var map = {
          dAt: 'at', dHosp: 'hospital', dSum: 'summary', dCond: 'conditions',
          dFollow: 'follow', dAllergy: 'allergy', dNotes: 'notes'
        };
        if (map[id]) {
          d.editedFields[map[id]] = 1;
          if (id === 'dHosp') d.hospital = clean(t.value);
          if (id === 'dSum') d.summary = clean(t.value);
          if (id === 'dFollow') d.followUp = clean(t.value);
          if (id === 'dAllergy') d.allergy = clean(t.value);
          if (id === 'dCond') d.conditions = t.value.split(/[、,，;；]/).map(clean).filter(Boolean);
          if (id === 'dNotes') d.notes = t.value.split(/\n/).map(clean).filter(Boolean);
          if (id === 'dAt' && t.value) {
            var ts = new Date(t.value).getTime();
            if (!isNaN(ts)) d.at = ts;
          }
          return;
        }
        var card = t.closest('[data-med]');
        if (card) {
          var i = +card.getAttribute('data-med');
          var k = t.getAttribute('data-k');
          if (d.meds && d.meds[i] && k) d.meds[i][k] = t.value;
          d.editedFields.meds = 1;
        }
      }
      dlgForm.addEventListener('input', markEdited);
      dlgForm.addEventListener('change', markEdited);
      dlgForm.addEventListener('click', function (e) {
        var d = formTarget();
        if (!d || !formBelongs()) return;

        if (e.target.id === 'dlgAddMed') {
          readSaveForm();
          d.meds = d.meds || [];
          d.meds.push(emptyDraftMed());
          d.edited = true;
          d.editedFields = d.editedFields || {};
          d.editedFields.meds = 1;
          fillSaveForm();
          var names = $$('#dlgForm [data-k="name"]');
          if (names.length) names[names.length - 1].focus();
          return;
        }

        var del = e.target.closest('[data-del-med]');
        if (del) {
          readSaveForm();
          var idx = +del.getAttribute('data-del-med');
          if (d.meds && idx >= 0 && idx < d.meds.length) d.meds.splice(idx, 1);
          d.edited = true;
          d.editedFields = d.editedFields || {};
          d.editedFields.meds = 1;
          fillSaveForm();
        }
      });
    }

    /* 兼容性提示 */
    var notes = [];
    if (!SR) notes.push('这个浏览器不支持语音识别（Web Speech），录音键不可用；下方「手动整理」仍可贴文字整理成记录。');
    if (!window.speechSynthesis) notes.push('这个浏览器不支持语音合成，回答只显示文字，不会朗读。');
    if (notes.length) {
      var c = $('#compat');
      c.hidden = false;
      c.textContent = notes.join(' ');
    }
    if (!SR) {
      $('#recBtn').disabled = true;
      $('#recLabel').textContent = '不可用';
    }
  }

  function autoGrow() {
    var el = $('#askInput');
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 132) + 'px';
  }

  function setLive(mode) {
    var wrap = $('#liveState');
    var txt = $('#liveTxt');
    wrap.classList.remove('is-live', 'is-ok');
    if (mode === 'live') { wrap.classList.add('is-live'); txt.textContent = '录音中'; }
    else if (mode === 'ok') { wrap.classList.add('is-ok'); txt.textContent = '已转写'; }
    else { txt.textContent = '待机'; }

    var btn = $('#recBtn');
    btn.classList.toggle('is-live', mode === 'live');
    /* 没有 Web Speech 时按钮是禁用的，标签必须一直说「不可用」，不能被这里改回去 */
    $('#recLabel').textContent = !SR ? '不可用' : (mode === 'live' ? '停止录音' : '开始录音');
  }

  function startRec() {
    if (!SR) {
      toast('这个浏览器不支持语音识别。用「手动整理」贴文字，一样能存成记录。');
      $('#manualText').focus();
      return;
    }
    if (state.recording) return;
    state.recFailed = false;

    /* 新一轮录音：清掉上一轮，避免串档 */
    state.lines = [];
    state.interim = '';
    renderTranscript();
    stopSpeech();

    try {
      recognition = new SR();
    } catch (e) {
      toast('语音识别初始化失败：' + e.message);
      return;
    }

    recognition.lang = 'zh-CN';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = function () {
      state.recording = true;
      setLive('live');
      $('#trMeta').textContent = '录音中 · 中文识别 · 由浏览器服务完成';
    };

    recognition.onresult = function (e) {
      var finalText = '', interim = '';
      for (var i = e.resultIndex; i < e.results.length; i++) {
        var r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (clean(finalText)) {
        state.lines.push({ t: Date.now(), text: clean(finalText) });
      }
      state.interim = clean(interim);
      renderTranscript();
    };

    recognition.onerror = function (e) {
      var map = {
        'not-allowed': '麦克风权限被拒绝。允许后重试，或用「手动整理」贴文字。',
        'service-not-allowed': '浏览器禁止了语音识别服务，可能需要在 https 或 localhost 下打开。',
        'audio-capture': '没有找到麦克风。检查设备后重试。',
        'network': '语音识别需要联网，当前网络不可用。',
        'no-speech': '这一会儿没听到声音。'
      };
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      toast(map[e.error] || ('识别出错：' + e.error));
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed' || e.error === 'audio-capture') {
        state.recording = false;
        state.recFailed = true;
        setLive('idle');
      }
    };

    recognition.onend = function () {
      /* Chrome 静音几秒会自动断，录音未停就接上 */
      if (state.recording) {
        clearTimeout(restartTimer);
        restartTimer = setTimeout(function () {
          if (!state.recording) return;
          try { recognition.start(); } catch (err) { /* 已在运行，忽略 */ }
        }, 260);
        return;
      }
      finishRec();
    };

    try {
      recognition.start();
    } catch (e) {
      toast('无法开始录音：' + e.message);
      state.recording = false;
      setLive('idle');
    }
  }

  function stopRec() {
    state.recording = false;
    setLive('idle');
    clearTimeout(restartTimer);
    try { recognition && recognition.stop(); } catch (e) {}
    /* onend 会调用 finishRec；万一没触发，兜一层 */
    setTimeout(function () {
      if (!state.recording) finishRec();
    }, 700);
  }

  var finishLock = false;
  function finishRec() {
    if (finishLock) return;
    finishLock = true;
    setTimeout(function () { finishLock = false; }, 900);

    var text = joinLines(state.lines);
    state.interim = '';
    renderTranscript();

    if (!text) {
      setLive('idle');
      $('#trMeta').textContent = state.recFailed ? '麦克风不可用' : '没有识别到内容';
      if (!state.recFailed) toast('这轮没听到有效内容。再试一次，或手动贴文字。');
      else $('#manualText').focus();
      return;
    }
    setLive('ok');
    $('#trMeta').textContent = '已转写 ' + state.lines.length + ' 段 · ' + text.length + ' 字';
    buildDraft(text, 'voice', state.lines.length);
  }

  function joinLines(lines) {
    var out = '';
    lines.forEach(function (l) {
      var t = clean(l.text);
      if (!t) return;
      if (out && !/[。！？；，、,.!?;]$/.test(out)) out += '。';
      out += t;
    });
    return out;
  }

  function renderTranscript() {
    var box = $('#transcript');
    if (!box) return;
    if (!state.lines.length && !state.interim) {
      box.innerHTML = '<p class="empty">点右上「开始录音」，你说的话会一段段落在这里。识别由浏览器语音服务完成，可能经其云端。</p>';
      return;
    }
    var html = state.lines.map(function (l) {
      return '<p class="tr-line"><span class="tr-ts">' + fmtClock(l.t) + '</span>' +
        '<span class="tr-tx">' + esc(l.text) + '</span></p>';
    }).join('');
    if (state.interim) {
      html += '<p class="tr-line tr-line--interim"><span class="tr-ts">…</span>' +
        '<span class="tr-tx">' + esc(state.interim) + '</span></p>';
    }
    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
  }

  /* ============================================================
     10. 从文字抽字段
     ============================================================ */
  var MED_SUFFIX = '(?:片|胶囊|颗粒|口服液|糖浆|软膏|乳膏|滴眼液|喷雾剂|注射液|散|丸|凝胶|贴)';

  /* 从抽取结果里剥掉口语引导词：「今天下午去了」「医生说是」不该进字段 */
  var LEAD = /^(?:今天|昨天|前天|那天|当天|上午|下午|中午|晚上|早上|这次|上次|当时|后来|然后|前几天|上个月|这个月|本月|上周|上上周|本周|周末|又|就|还|我们|我|他|她|家属)?(?:去|到|在|从|回|来)?(?:了|过)?/;
  var MED_LEAD = /^(?:医生|大夫|主任|护士|说|讲|表示|让我|叫我|嘱咐我|叮嘱我|开了|开|给了|给|用了|用|吃了|吃|服用|服|加上|加|还有|和|及|与|配|先|再|同时|另外|不用吃|不用|不要吃|不要|不必吃|不必|不能吃|不能|别再吃|别再|停了|停用|已经停了|不需要吃|不需要)+/;
  /* 以服法/频次开头的不是药名（「饭后服用一片」是嘱咐，不是药） */
  var NOT_DRUG = /^(?:饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每天|每日|一次|两次|三次|一片|两片|半片|服用|吃|喝|没有|不用)/;
  var NEG_MED = /(?:不用|不要|不必|不能|别再|停了|停用|已经停|不需要)(?:再)?(?:吃|用|服用|服)?/;
  var DENY_ALLERGY = /(?:没有|没|无|否认|不|未)[^。；;]{0,6}(?:过敏|不耐受)/;
  var COMMON_DRUGS = ['阿莫西林克拉维酸钾','复方甘草片','单硝酸异山梨酯','厄贝沙坦氢氯噻嗪',
    '阿莫西林','头孢克肟','头孢呋辛','头孢地尼','阿奇霉素','克拉霉素','左氧氟沙星','莫西沙星',
    '甲硝唑','奥硝唑','布洛芬','对乙酰氨基酚','双氯芬酸','塞来昔布','奥美拉唑','雷贝拉唑',
    '埃索美拉唑','兰索拉唑','铝碳酸镁','莫沙必利','多潘立酮','蒙脱石','果胶铋','硫糖铝',
    '二甲双胍','格列美脲','阿卡波糖','胰岛素','氨氯地平','硝苯地平','厄贝沙坦','缬沙坦',
    '美托洛尔','比索洛尔','阿托伐他汀','瑞舒伐他汀','阿司匹林','氯吡格雷','华法林','利伐沙班',
    '氯雷他定','西替利嗪','孟鲁司特','沙丁胺醇','布地奈德','泼尼松','地塞米松','甲泼尼龙',
    '左甲状腺素','碳酸钙','叶酸','别嘌醇','非布司他','坦索罗辛'];
  var MED_RE = new RegExp('([\\u4e00-\\u9fa5A-Za-z]{2,14}' + MED_SUFFIX + '|' +
    COMMON_DRUGS.slice().sort(function (a, b) { return b.length - a.length; }).join('|') + ')', 'g');
  var DISEASE = /(?:慢性?|急性)?[\u4e00-\u9fa5]{0,6}(?:炎|病|症|溃疡|结石|感染|综合征|骨折|增生|囊肿|息肉)|高血压|糖尿病|冠心病|高血脂|高尿酸|痛风|贫血|甲亢|甲减|抑郁|焦虑|哮喘|慢阻肺|幽门螺杆菌阳性|乙肝|丙肝|肝硬化|脂肪肝/;
  var EVENT = /胃镜|肠镜|气管镜|喉镜|CT|核磁|磁共振|B超|彩超|心电图|活检|穿刺|手术|住院|出院|化疗|放疗|透析|支架|搭桥|输血|输液|疫苗|接种|孕检|体检/g;
  var LEAD2 = /^(?:医生|大夫|主任|护士|专家|检查结果|结果|报告)?(?:说|讲|表示|认为|提示|诊断|考虑)?(?:还是|是|为|有|有点|可能|大概|应该|就是|：|:)?/;

  var CN = { '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 };
  function cnNum(v) {
    if (!v) return '';
    if (/^\d+$/.test(v)) return v;
    if (v === '十') return 10;
    if (v.length === 2 && v[0] === '十') return 10 + (CN[v[1]] || 0);
    if (v.length === 2 && v[1] === '十') return (CN[v[0]] || 0) * 10;
    if (v.length === 3 && v[1] === '十') return (CN[v[0]] || 0) * 10 + (CN[v[2]] || 0);
    return CN[v] || '';
  }

  function stripMed(s) {
    var prev;
    var guard = 0;
    do {
      prev = s;
      s = s.replace(MED_LEAD, '');
      guard++;
    } while (s !== prev && guard < 4 && s.length > 3);
    return s.trim();
  }

  function stripLead(s) {
    s = clean(s);
    var prev;
    var guard = 0;
    do {
      prev = s;
      s = s.replace(LEAD, '');
      s = s.replace(LEAD2, '');
      s = s.replace(/^[的了着过和与及、，,。；;：:\s]+/, '');
      guard++;
    } while (s !== prev && guard < 6 && s.length > 3);
    return s.trim();
  }

  function guessVisitDate(text) {
    var now = new Date();
    var m = String(text || '').match(/(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?/);
    var y, mo, d;
    if (m) {
      y = +m[1]; mo = +m[2]; d = +m[3];
    } else {
      var m2 = String(text || '').match(/(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/);
      if (!m2) return null;
      mo = +m2[1]; d = +m2[2]; y = now.getFullYear();
      var cand = new Date(y, mo - 1, d, 9, 0, 0);
      if (cand.getTime() - now.getTime() > 14 * 86400000) y -= 1;
    }
    if (!(mo >= 1 && mo <= 12 && d >= 1 && d <= 31)) return null;
    var dt = new Date(y, mo - 1, d, 9, 0, 0);
    if (dt.getMonth() !== mo - 1) return null;
    return dt.getTime();
  }

  /* 只在这一味药自己的片段里找剂量/频次/服法/疗程；
     药名前的「每天吃两次」允许回看同一分句，不跨句借别的药。 */
  function parseMedWindow(window, before) {
    function first(re, text) {
      var m = text ? text.match(re) : null;
      return m ? clean(m[1] || m[0]) : '';
    }
    var dose = first(/(\d+(?:\.\d+)?\s*(?:mg|毫克|g|克|μg|ug|ml|毫升|单位|iu|片|粒))/i, window);
    var timing = first(/(饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每晚|早晚)/, window) ||
      first(/(饭前|饭后|餐前|餐后|睡前|空腹|早晨|早上|晚上|每晚|早晚)/, before);
    var freq = first(/((?:每日|每天|一日)\s*(?:吃|服)?\s*(?:[一二两三四1-4]\s*次)?|每晚|每早|一日三次|每日三次)/, window) ||
      first(/((?:每日|每天|一日)\s*(?:吃|服)?\s*(?:[一二两三四1-4]\s*次)?|每晚|每早|一日三次|每日三次)/, before);
    var dm = window.match(/(?:吃|服|连服|共|吃满|用)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)/) ||
      window.match(/(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)/);
    return {
      dose: dose,
      freq: freq,
      timing: timing.replace('每晚', '睡前'),
      days: dm ? String(cnNum(dm[1])) : ''
    };
  }

  function extract(text) {
    var raw = clean(text);
    var hint = guessVisitDate(raw);
    var sents = raw.split(/[。；;!?！？\n]/).map(clean).filter(Boolean);

    /* 医院：先去掉日期与时段，避免「日去了市第一人民医院」 */
    var hospSrc = raw.replace(
      /(?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|20\d{2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}|今天|昨天|前天|上午|下午|中午|晚上|早上|上周|上上周|本周|周末/g,
      ' '
    );
    var hospital = '';
    var hm = hospSrc.match(/([\u4e00-\u9fa5]{2,18}(?:医院|卫生院|诊所|门诊部|卫生服务中心))/);
    if (hm) {
      hospital = stripLead(hm[1]);
      if (/^(?:没|未|不|别|无|哪)/.test(hospital) || hospital === '医院') hospital = '';
    }

    /* 诊断 */
    var summary = '';
    var dm = raw.match(/(?:诊断|考虑|判断|结论|说是|应该是|确诊)(?:为|是|：|:)?\s*([^。；;，,\n]{2,24})/);
    if (!dm) dm = raw.match(/说(?:还是|是|可能|大概|有点)([^。；;，,\n]{2,24})/);
    if (dm) summary = stripLead(clean(dm[1]));
    if (!summary) {
      var sm = DISEASE.exec(raw);
      if (sm) summary = stripLead(sm[0]);
    }
    summary = summary.replace(/[，,。；;].*$/, '').slice(0, 24);
    if (/^(?:什么|啥|哪种|这种)/.test(summary)) summary = '';

    /* 用药：以药名为锚点，一味药一个窗口 */
    var meds = [];
    var seen = {};
    var discontinued = [];
    sents.forEach(function (sentence) {
      var hits = [];
      var m;
      MED_RE.lastIndex = 0;
      while ((m = MED_RE.exec(sentence))) {
        hits.push({ start: m.index, end: m.index + m[0].length, raw: m[1] });
      }
      if (!hits.length) return;

      hits.forEach(function (hit, idx) {
        var rawName = hit.raw;
        var name = stripMed(rawName);
        if (!name || name.length < 2 || seen[name] || NOT_DRUG.test(name)) return;
        var end = idx + 1 < hits.length ? hits[idx + 1].start : sentence.length;
        var window = sentence.slice(hit.end, end);
        var before = sentence.slice(Math.max(0, hit.start - 10), hit.start).split('，').pop().split(',').pop();
        seen[name] = 1;
        if (NEG_MED.test(rawName) || NEG_MED.test(before + window.slice(0, 12))) {
          discontinued.push(name);
          return;
        }
        var fields = parseMedWindow(window, before);
        meds.push({ name: name, dose: fields.dose, freq: fields.freq, timing: fields.timing, days: fields.days });
      });

      /* 句尾统一的疗程补给这一句里没写疗程的药 */
      var tail = sentence.slice(hits[hits.length - 1].end);
      var tailDays = tail.match(/(?:吃|服|连服|共|吃满|用)\s*(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)/) ||
        tail.match(/(\d+|[一二三四五六七八九十两]+)\s*(?:天|日)/);
      if (tailDays) {
        var val = String(cnNum(tailDays[1]));
        meds.forEach(function (med) { if (!med.days) med.days = val; });
      }
    });

    /* 复诊 */
    var followUp = '';
    var fm = raw.match(/((?:[一二三四五六两\d]+)\s*(?:周|个星期|个月|月|天)\s*(?:后|以后|之后)[^。；;]{0,6}(?:复诊|复查|再来|随访))/);
    if (!fm) {
      var fb = raw.match(/((?:复诊|复查|随访)[^。；;]{0,12})/);
      if (fb) {
        var pb = raw.slice(Math.max(0, fb.index - 4), fb.index);
        if (!/[没未不别无]/.test(pb)) fm = fb;
      }
    }
    if (fm) {
      var fv = clean(fm[1]);
      var tailv = fv.replace(/^.*?(?:复诊|复查|随访)[：:，,\s]*/, '');
      if (tailv.length >= 2 || /[一二三四五六两\d]+\s*(?:周|天|个月|月)/.test(fv)) followUp = fv;
    }

    /* 过敏 / 否认 */
    var allergy = '';
    var allergyDenied = DENY_ALLERGY.test(raw);
    if (!allergyDenied) {
      var am = raw.match(/((?:对)?[\u4e00-\u9fa5A-Za-z]{1,10}(?:过敏|不耐受))/);
      if (am) {
        allergy = stripLead(clean(am[1]));
        for (var k = 0; k < 4; k++) {
          var prev = allergy;
          allergy = allergy.replace(/^(?:患者|病人|本人|家属|孩子|老人|对|有|他|她|我|也|还|说)+/, '');
          if (allergy === prev) break;
        }
      }
    }

    var notes = sents.filter(function (x) {
      return /注意|忌口|避免|少吃|少喝|多喝|休息|不要|别吃|别喝|定期|戒烟|戒酒|低盐|低脂|清淡|按时/.test(x);
    }).map(function (x) {
      return x.replace(/^(?:医生|大夫|主任)?(?:嘱咐|交代|叮嘱|建议|提醒|说|让|要求|记得)+/, '').trim();
    }).filter(Boolean).slice(0, 4);

    var conditions = [];
    (raw.match(DISEASE) || []).forEach(function (x) {
      var v = stripLead(x);
      if (v && v.length >= 2 && !/^(?:什么|啥|哪种|这种)/.test(v) && conditions.indexOf(v) < 0) conditions.push(v);
    });
    var ev;
    EVENT.lastIndex = 0;
    while ((ev = EVENT.exec(raw))) {
      var item = stripLead(ev[0]);
      if (item && conditions.indexOf(item) < 0) conditions.push(item);
    }
    if (summary && conditions.indexOf(summary) < 0) conditions.unshift(summary);
    conditions = conditions.filter(function (x) {
      return !conditions.some(function (y) { return x !== y && x.indexOf(y) >= 0; });
    }).slice(0, 12);

    return {
      hospital: hospital,
      summary: summary,
      meds: meds,
      followUp: followUp,
      allergy: allergy,
      allergyDenied: allergyDenied,
      conditions: conditions,
      discontinued: discontinued,
      notes: notes,
      visitHint: hint
    };
  }

  /* 全局唯一 ID：展示编号可以带时间，但删除/对账用的是随机串，
     否则同一分钟建两条会撞 id，删一条把两条一起删掉。 */
  function newClientId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function emptyDraftMed() {
    return { name: '', dose: '', freq: '', timing: '', days: '' };
  }

  function namedMeds(list) {
    return (list || []).map(cloneMed).filter(function (m) { return clean(m.name); });
  }

  function cloneMed(m) {
    return {
      name: (m && m.name) || '', dose: (m && m.dose) || '',
      freq: (m && m.freq) || '', timing: (m && m.timing) || '',
      days: (m && m.days) || ''
    };
  }

  function makeDraft(text, mode) {
    var now = Date.now();
    var hint = guessVisitDate(text);
    var d = new Date(now);
    var id = 'v_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
      '_' + pad(d.getHours()) + pad(d.getMinutes()) +
      '_' + Math.random().toString(36).slice(2, 6);

    return {
      id: id,
      clientId: newClientId(),
      at: hint || now,
      atFromText: !!hint,
      hospital: '',
      summary: '',
      meds: [],
      followUp: '',
      allergy: '',
      allergyDenied: false,
      conditions: [],
      discontinued: [],
      notes: [],
      transcript: text,
      mode: mode,
      archived: false,
      /* 服务端整理完成前不允许保存；本机模式没有这个等待 */
      extractState: API.on ? 'extracting' : 'ready',
      extractError: '',
      extractIgnored: false,
      edited: false,
      editedFields: {}
    };
  }

  function applyLocalExtract(d, x) {
    if (!d || !x) return;
    d.hospital = x.hospital || '';
    d.summary = x.summary || '';
    d.meds = (x.meds || []).map(cloneMed);
    d.followUp = x.followUp || '';
    d.allergy = x.allergy || '';
    d.allergyDenied = !!x.allergyDenied;
    d.conditions = x.conditions || [];
    d.discontinued = x.discontinued || [];
    d.notes = x.notes || [];
    if (x.visitHint) { d.at = x.visitHint; d.atFromText = true; }
    d.extractState = 'ready';
  }

  /* 服务端结果逐字段合并：只补用户没手工改过的字段 */
  function applyExtract(x, d) {
    if (!d) return;
    var touched = d.editedFields || {};
    var skipped = false;

    function setIf(field, apply) {
      if (touched[field]) { skipped = true; return; }
      apply();
    }

    setIf('hospital', function () { d.hospital = x.hospital || ''; });
    setIf('summary', function () { d.summary = x.summary || ''; });
    setIf('meds', function () { d.meds = (x.meds || []).map(cloneMed); });
    setIf('follow', function () { d.followUp = x.follow_up || ''; });
    setIf('allergy', function () {
      d.allergy = x.allergy || '';
      d.allergyDenied = !!x.allergy_denied;
    });
    setIf('conditions', function () { d.conditions = x.conditions || []; });
    setIf('discontinued', function () { d.discontinued = x.discontinued || []; });
    setIf('notes', function () { d.notes = x.notes || []; });

    var t = x.visit_hint || x.visit_at;
    if (t) {
      var ts = new Date(t).getTime();
      if (!isNaN(ts)) {
        setIf('at', function () {
          d.at = ts;
          d.atFromText = !!x.visit_hint;
        });
      }
    }

    d.holes = x.holes || [];
    d.extractState = 'ready';
    d.extractIgnored = skipped;
  }

  var extractSeq = 0;

  function buildDraft(text, mode, segCount) {
    /* 从别处触发时先回工作台，弹窗背后必须是这一摊东西 */
    if (state.view !== 'work') go('work');

    var seq = ++extractSeq;
    var draft = makeDraft(text, mode);
    state.draft = draft;

    if (!API.on) applyLocalExtract(draft, extract(text));

    renderRecordPanel();

    var segs = segCount || text.split(/[。；;!?！？\n]/).filter(function (x) { return clean(x); }).length;
    openSaveDialog(segs);

    /* 不再把整段原文塞进聊天输入框；需要时在记录卡上点「用这段问小鹿」 */
    if (API.on) {
      apiFetch('/api/records/extract', { method: 'POST', body: { text: text } })
        .then(function (x) {
          /* 迟到的响应不得覆盖新草稿 */
          if (state.draft !== draft || seq !== extractSeq) return;
          applyExtract(x, draft);
          if ($('#saveDialog').open) fillSaveForm();
          renderRecordPanel();
        })
        .catch(function (e) {
          if (state.draft !== draft || seq !== extractSeq) return;
          draft.extractState = 'failed';
          draft.extractError = e.message;
          if ($('#saveDialog').open) fillSaveForm();
          renderRecordPanel();
          toast('自动整理失败：已切换为全手工录入，不会用本机规则猜字段。');
        });
    }
  }

  function countFilled(d) {
    var n = 0;
    if (!d) return 0;
    if (d.hospital) n++;
    if (d.summary) n++;
    if (namedMeds(d.meds).length) n++;
    if (d.followUp) n++;
    if (d.allergy) n++;
    if (d.notes && d.notes.length) n++;
    return n;
  }
  function countHoles(d) {
    var n = 0;
    if (!d) return 0;
    if (!d.hospital) n++;
    if (!d.summary) n++;
    if (!namedMeds(d.meds).length) n++;
    if (!d.followUp) n++;
    return n;
  }

  function formTarget() {
    return state.editId ? state.editDraft : state.draft;
  }

  function formKey() {
    var d = formTarget();
    if (!d) return '';
    return state.editId ? 'edit:' + state.editId : d.id;
  }

  /* 表单可能是上一份草稿留下的；对不上就不能回读，否则会覆盖新草稿 */
  function formBelongs() {
    var box = $('#dlgForm');
    return !!box && box.getAttribute('data-draft') === formKey();
  }

  function openSaveDialog(segCount) {
    var dlg = $('#saveDialog');
    state.editId = '';
    state.editDraft = null;
    var d = state.draft;
    if (!d) return;

    $('#dlgId').textContent = d.id;
    $('#dlgSeg').textContent = (segCount || state.lines.length || 0) + ' 段';
    $('#dlgFields').textContent = countFilled(d) + ' 项';
    $('#dlgHoles').textContent = countHoles(d) + ' 项';
    $('#saveTitle').textContent = '这次记录对吗？存进档案前可以直接改';
    fillSaveForm();
    openDialog(dlg);
  }

  function openEditDialog(id) {
    var rec = state.records.filter(function (r) { return r.id === id; })[0];
    if (!rec) { toast('这份记录不在了。'); return; }

    state.editId = id;
    state.editDraft = {
      id: rec.id,
      clientId: rec.clientId || '',
      at: rec.at,
      atFromText: false,
      hospital: rec.hospital || '',
      summary: rec.summary || '',
      meds: (rec.meds || []).map(cloneMed),
      followUp: rec.followUp || '',
      allergy: rec.allergy || '',
      allergyDenied: !!rec.allergyDenied,
      conditions: (rec.conditions || []).slice(),
      discontinued: (rec.discontinued || []).slice(),
      notes: (rec.notes || []).slice(),
      transcript: rec.transcript || '',
      mode: rec.mode || 'manual',
      archived: true,
      extractState: 'ready',
      edited: false,
      editedFields: {},
      holes: []
    };

    $('#dlgId').textContent = rec.id;
    $('#dlgSeg').textContent = (rec.transcript || '').length + ' 字';
    $('#dlgFields').textContent = countFilled(state.editDraft) + ' 项';
    $('#dlgHoles').textContent = '—';
    $('#saveTitle').textContent = '修改这份记录';
    fillSaveForm();
    openDialog($('#saveDialog'));
  }

  /* ---------- 归档前可编辑：抽取结果不是最终结果，用户说了算 ---------- */
  function toLocalInput(ms) {
    var d = new Date(ms);
    var p2 = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()) +
      'T' + p2(d.getHours()) + ':' + p2(d.getMinutes());
  }

  function dlgStatusText(d) {
    if (!d) return '';
    if (state.editId) {
      return '修改后保存会覆盖这份记录；原始转写仍保留。';
    }
    if (d.extractState === 'extracting') {
      return '正在服务端整理，请稍候。结果出来前不能保存，避免把旧规则的结果写进档案。';
    }
    if (d.extractState === 'failed') {
      return '自动整理失败：' + (d.extractError || '服务端不可用') +
        '。已切换为全手工录入，不会用本机规则猜药名和剂量；请逐项填写后再保存。';
    }
    if (d.extractIgnored) {
      return '服务端结果已返回：你手工改过的字段保留，其余字段已补全。请核对后保存。';
    }
    if (API.on) {
      return '服务端整理完成，请核对后再保存。日期若来自原文，年份请确认。';
    }
    return '本机规则整理完成。药名、剂量和频次请逐项核对；抽不到的字段留空，不会替你猜。';
  }

  function setDlgStatus() {
    var el = $('#dlgStatus');
    if (el) el.textContent = dlgStatusText(formTarget());
  }

  function updateSaveButton() {
    var btn = $('#saveYes');
    if (!btn) return;
    var d = formTarget();
    var extracting = !!(d && d.extractState === 'extracting');
    btn.disabled = extracting || state.saving;
    btn.textContent = extracting ? '正在整理…'
      : (state.saving ? '保存中…' : (state.editId ? '保存修改' : '保存到档案'));
  }

  function refreshDialog() {
    var d = formTarget();
    if (!d) return;
    setDlgStatus();
    $('#dlgFields').textContent = countFilled(d) + ' 项';
    $('#dlgHoles').textContent = state.editId ? '—' : countHoles(d) + ' 项';
    updateSaveButton();
  }

  function fillSaveForm() {
    var d = formTarget();
    var box = $('#dlgForm');
    if (!d || !box) return;

    var meds = d.meds && d.meds.length ? d.meds : [];
    var rows = meds.map(function (m, i) {
      var field = function (key, label, ph, type) {
        return '<label class="dlgmed__f"><i>' + label + '</i>' +
          '<input class="dlgin" data-k="' + key + '" value="' + esc(m[key] || '') +
          '" placeholder="' + ph + '"' + (type ? ' inputmode="' + type + '"' : '') + '></label>';
      };
      return '<div class="dlgmed" data-med="' + i + '">' +
        '<button class="dlgmed__del" type="button" data-del-med="' + i + '" title="删除这味药">×</button>' +
        field('name', '药名', '药品名称') +
        field('dose', '剂量', '20mg') +
        field('freq', '频次', '每天一次') +
        field('timing', '服法', '饭前/饭后') +
        field('days', '天数', '14') +
        '</div>';
    }).join('');

    box.setAttribute('data-draft', formKey());
    box.innerHTML =
      '<p class="dlgstatus' + (d.extractState === 'failed' ? ' is-err' : '') + '" id="dlgStatus">' +
        esc(dlgStatusText(d)) + '</p>' +
      '<label class="dlgrow"><span>就诊时间</span>' +
        '<input class="dlgin" id="dAt" type="datetime-local" value="' + toLocalInput(d.at) + '"></label>' +
      (d.atFromText ? '<p class="dlghint">日期是从原文里认出来的，年份请确认一次。</p>' : '') +
      '<label class="dlgrow"><span>医院</span>' +
        '<input class="dlgin" id="dHosp" value="' + esc(d.hospital || '') + '" placeholder="没提到就留空"></label>' +
      '<label class="dlgrow"><span>诊断</span>' +
        '<input class="dlgin" id="dSum" value="' + esc(d.summary || '') + '" placeholder="没提到就留空"></label>' +
      '<label class="dlgrow"><span>疾病/事件</span>' +
        '<input class="dlgin" id="dCond" value="' + esc((d.conditions || []).join('、')) +
          '" placeholder="如：高血压、胃镜、胆囊结石手术（用、分隔）"></label>' +
      '<label class="dlgrow"><span>复诊</span>' +
        '<input class="dlgin" id="dFollow" value="' + esc(d.followUp || '') + '" placeholder="没提到就留空"></label>' +
      '<label class="dlgrow"><span>过敏</span>' +
        '<input class="dlgin" id="dAllergy" value="' + esc(d.allergy || '') + '" placeholder="没有就留空"></label>' +
      '<div class="dlgmeds">' +
        (meds.length ? rows : '<p class="dlghint">这次没有整理出用药。可以点下面「添加药物」自己补，也可以留空。</p>') +
      '</div>' +
      '<div class="dlgmeds__acts"><button class="btn btn--ghost btn--mini" id="dlgAddMed" type="button">＋ 添加药物</button></div>' +
      (d.discontinued && d.discontinued.length
        ? '<p class="dlghint">记录中提到停用：' + esc(d.discontinued.join('、')) + '（不会计入当前用药）</p>'
        : '') +
      '<label class="dlgrow" style="align-items:start"><span style="padding-top:8px">注意事项</span>' +
        '<textarea class="dlgnote" id="dNotes">' + esc((d.notes || []).join('\n')) + '</textarea></label>' +
      '<p class="dlghint">抽不到的字段留空即可，不会替你猜。' +
        (state.editId ? '原始转写不会被改动。' : '') + '</p>';

    updateSaveButton();
  }

  function readSaveForm() {
    var d = formTarget();
    var box = $('#dlgForm');
    if (!d || !box || !formBelongs()) return;

    var at = $('#dAt');
    if (at && at.value) {
      var t = new Date(at.value).getTime();
      if (!isNaN(t)) d.at = t;
    }
    var hosp = $('#dHosp'); if (hosp) d.hospital = clean(hosp.value);
    var sum = $('#dSum'); if (sum) d.summary = clean(sum.value);
    var follow = $('#dFollow'); if (follow) d.followUp = clean(follow.value);
    var alg = $('#dAllergy'); if (alg) d.allergy = clean(alg.value);
    var cond = $('#dCond');
    if (cond) {
      d.conditions = cond.value.split(/[、,，;；]/).map(clean).filter(Boolean);
    }
    var notes = $('#dNotes');
    if (notes) {
      d.notes = notes.value.split(/\n/).map(clean).filter(Boolean);
    }

    var medBoxes = box.querySelectorAll('[data-med]');
    var meds = [];
    for (var i = 0; i < medBoxes.length; i++) {
      var inputs = medBoxes[i].querySelectorAll('input');
      var m = {};
      for (var j = 0; j < inputs.length; j++) {
        m[inputs[j].getAttribute('data-k')] = clean(inputs[j].value);
      }
      meds.push(m);
    }
    if (medBoxes.length) d.meds = meds;
  }

  function openDialog(dlg) {
    if (dlg.showModal) {
      try { dlg.showModal(); return; } catch (e) {}
    }
    dlg.setAttribute('open', '');
  }
  function closeDialog(dlg) {
    if (dlg.close) { try { dlg.close(); return; } catch (e) {} }
    dlg.removeAttribute('open');
  }

  /* ============================================================
     11. 归档
     ============================================================ */
  function setSaveBadge(text, isErr, autoHide) {
    var el = $('#saveState');
    if (!el) return;
    if (!text) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle('is-err', !!isErr);
    clearTimeout(el._t);
    if (autoHide) {
      el._t = setTimeout(function () { el.hidden = true; }, 2200);
    }
  }

  function saveDraft() {
    var d = state.draft;
    if (!d || d.archived) return;

    if (d.extractState === 'extracting') {
      toast('正在整理，等结果出来再保存——现在存会把旧结果写进档案。');
      return;
    }
    if (state.saving) return;

    // 归档前用户可能改过字段，以表单里的值为准
    readSaveForm();

    /* 服务端整理失败时是全手工模式：空白记录不允许存 */
    if (API.on && d.extractState === 'failed' &&
        !d.hospital && !d.summary && !namedMeds(d.meds).length) {
      toast('自动整理失败，请至少填写医院、诊断或一味药再保存。');
      return;
    }

    var rec = {
      id: d.id,
      clientId: d.clientId || newClientId(),
      at: d.at,
      hospital: d.hospital || '未记录医院',
      summary: d.summary || '未写明诊断',
      transcript: d.transcript,
      meds: namedMeds(d.meds),
      followUp: d.followUp,
      allergy: d.allergy,
      allergyDenied: !!d.allergyDenied,
      conditions: d.conditions || [],
      discontinued: d.discontinued || [],
      notes: d.notes,
      mode: d.mode
    };

    if (API.on) {
      state.saving = true;
      setSaveBadge('保存中…');
      updateSaveButton();
      apiFetch('/api/records', { method: 'POST', body: toApiRecord(rec) })
        .then(function (saved) {
          var mapped = mapRecord(saved);
          // 用服务端返回的 id 替换本地临时 id，之后删除/查询都按它来
          state.records = state.records.filter(function (r) {
            return r.id !== mapped.id && r.clientId !== mapped.clientId;
          });
          state.records.push(mapped);
          d.archived = true;
          state.saving = false;
          finishArchive(mapped, d);
        })
        .catch(function (e) {
          state.saving = false;
          updateSaveButton();
          setSaveBadge('保存失败', true);
          toast('归档失败：' + e.message);
        });
      return;
    }

    state.records.push(rec);
    persistRecords();
    d.archived = true;
    finishArchive(rec, d);
  }

  /* 归档后的修改：PATCH 同一条记录，不新建、不丢原始转写 */
  function saveEdit() {
    var d = state.editDraft;
    var id = state.editId;
    if (!d || !id || state.saving) return;

    readSaveForm();
    var rec = state.records.filter(function (r) { return r.id === id; })[0];
    if (!rec) { toast('这份记录不在了。'); state.editId = ''; state.editDraft = null; return; }

    var patch = {
      visit_at: new Date(d.at).toISOString(),
      hospital: d.hospital || '',
      summary: d.summary || '',
      transcript: d.transcript || '',
      meds: namedMeds(d.meds),
      follow_up: d.followUp || '',
      allergy: d.allergy || '',
      notes: d.notes || [],
      conditions: d.conditions || [],
      discontinued: d.discontinued || [],
      allergy_denied: !!d.allergyDenied
    };

    function done(saved) {
      state.saving = false;
      state.editId = '';
      state.editDraft = null;
      syncIdentity();
      renderMine();
      renderHome();
      renderWork();
      updateSaveButton();
      setSaveBadge('已保存', false, true);
      toast('改动已保存。');
    }

    if (API.on) {
      state.saving = true;
      setSaveBadge('保存中…');
      updateSaveButton();
      apiFetch('/api/records/' + encodeURIComponent(id), { method: 'PATCH', body: patch })
        .then(function (saved) {
          var mapped = mapRecord(saved);
          state.records = state.records.map(function (r) { return r.id === id ? mapped : r; });
          done(mapped);
        })
        .catch(function (e) {
          state.saving = false;
          updateSaveButton();
          setSaveBadge('保存失败', true);
          toast('修改失败：' + e.message);
        });
      return;
    }

    rec.at = d.at;
    rec.hospital = d.hospital || '未记录医院';
    rec.summary = d.summary || '未写明诊断';
    rec.transcript = d.transcript || rec.transcript;
    rec.meds = namedMeds(d.meds);
    rec.followUp = d.followUp || '';
    rec.allergy = d.allergy || '';
    rec.allergyDenied = !!d.allergyDenied;
    rec.conditions = d.conditions || [];
    rec.discontinued = d.discontinued || [];
    rec.notes = d.notes || [];
    persistRecords();
    done(rec);
  }

  function finishArchive(rec, d) {
    state.saving = false;
    updateSaveButton();
    setSaveBadge('已保存', false, true);

    syncIdentity();
    renderMine();
    renderHome();
    renderRx();
    renderOverview();
    renderRecordPanel();

    if (d && d.mode === 'manual') $('#manualText').value = '';

    var n = seqOf(rec);
    toast('已存为第 ' + n + ' 份记录。');

    var medLine = rec.meds.length
      ? '开了 ' + rec.meds.length + ' 种药，' + rec.meds.slice(0, 3).map(function (m) { return m.name; }).join('、') +
        (rec.meds.length > 3 ? ' 等' : '') + '。'
      : '这次没提到用药。';

    addBot([
      { h: '记下了：<span class="hl">' + fmtDate(rec.at) + '</span> · ' + esc(rec.hospital) + ' · ' + esc(rec.summary) + '。',
        t: '记下了：' + fmtDate(rec.at) + '，' + rec.hospital + '，' + rec.summary + '。' },
      { h: medLine + (rec.followUp ? esc(rec.followUp) + '。' : ''),
        t: medLine + (rec.followUp ? rec.followUp + '。' : '') },
      { h: '这是第 ' + n + ' 份记录。想问什么直接说，比如「今天要吃什么药」。',
        t: '这是第 ' + n + ' 份记录。想问什么直接说。' }
    ], [{ label: cite(rec) }], true);
  }

  /* ============================================================
     12. 对话
     ============================================================ */
  function greet() {
    addBot([
      { h: '我在。我是小鹿——只做一件事：把你看病时医生说的话，整理成一页能翻回去的记录。',
        t: '我在。我是小鹿，只做一件事：把你看病时医生说的话，整理成一页能翻回去的记录。' },
      { h: '想开始，就点右上「开始录音」；也可以按「手动整理」贴一段问诊内容。看不清药盒，就去首页「拍照识药」拍一张。想先试试我，问我「你叫什么名字」。',
        t: '想开始，就点右上开始录音；也可以按手动整理贴一段问诊内容。看不清药盒，就去首页拍照识药拍一张。' }
    ], [], false);
  }

  function send() {
    var input = $('#askInput');
    var q = input.value.trim();
    if (!q) { input.focus(); return; }
    if (state.busy) { toast('上一条还在答，稍等一下再发。'); return; }

    addUser(q);
    input.value = '';
    autoGrow();
    state.busy = true;

    var holder = document.createElement('div');
    holder.className = 'msg msg--bot';
    holder.innerHTML = '<div class="msg__who"><span class="dot"></span>小鹿</div>' +
      '<div class="msg__body"><span class="typing"><i></i><i></i><i></i></span></div>';
    $('#thread').appendChild(holder);
    scrollThread();

    /* 有服务端就以服务端结论为准：急症兜底、拒答规则、临床实体守卫都在那边 */
    if (API.on) {
      apiFetch('/api/chat/sync', { method: 'POST', body: { question: q } })
        .then(function (res) {
          state.busy = false;
          holder.remove();
          addBot(buildCloudParas(res), buildCloudSources(res), !res.refused || /120|急诊/.test(res.answer));
        })
        .catch(function (e) {
          state.busy = false;
          holder.remove();
          /* 医疗事实不能靠本机旧规则兜底：宁可这条不答，也不返回可能错误的药单 */
          addBot([
            P('<span class="hl">服务端暂时不可用，这条我没有作答。</span>'),
            P('为了不给你可能错误的药名或剂量，我不会用本机规则猜。请稍后再问；急着核对的话，去「我的」直接翻那次的完整转写。'),
            P('（' + esc(e.message) + '）')
          ], [], false);
        });
      return;
    }

    var local = answer(q);

    if (!shouldAskAi(local, q)) {
      setTimeout(function () {
        state.busy = false;
        holder.remove();
        addBot(local.paras, local.sources, local.medical);
      }, 420);
      return;
    }

    var who = holder.querySelector('.msg__who');
    if (who) who.innerHTML = '小鹿 · 千问<span class="dot"></span>';

    aiAnswerText(q, local).then(function (text) {
      var answerText = String(text || '').trim();
      /* 模型越界（给出吃药/停药建议）而本机结论里没有 —— 一律丢弃，用本机文案 */
      var unsafe = !answerText || (ADVICE_OUT.test(answerText) && !ADVICE_OUT.test(plainOf(local)));
      state.busy = false;
      holder.remove();
      if (unsafe) {
        addBot(local.paras, local.sources, local.medical);
        return;
      }
      var paras = answerText.split(/\n{2,}/).filter(function (x) { return x.trim(); }).map(function (block) {
        return P(esc(block).replace(/\n/g, '<br>'), block);
      });
      addBot(paras, local.sources, local.medical, '千问');
    }).catch(function (e) {
      state.busy = false;
      holder.remove();
      var r = answer(q);
      r.paras.push(P('（千问暂时没接通，这条用本机规则答：' + esc(e.message) + '）'));
      addBot(r.paras, r.sources, r.medical);
    });
  }

  /* 服务端回答（纯文本 + 来源）→ 现有气泡结构 */
  function buildCloudParas(res) {
    var text = String(res.answer || '').trim();
    var blocks = text.split(/\n{2,}/).filter(function (x) { return x.trim(); });
    return blocks.map(function (block) {
      var lines = block.split('\n');
      var isList = lines.length > 1 && lines.every(function (l) { return /^[-\d]/.test(l.trim()) || !l.trim(); });
      if (isList) {
        return P('<ul class="ans-list">' + lines.filter(function (l) { return l.trim(); }).map(function (l) {
          return '<li>' + esc(l.replace(/^[-\d.]+\s*/, '')) + '</li>';
        }).join('') + '</ul>');
      }
      return P(esc(block).replace(/\n/g, '<br>'));
    });
  }

  function buildCloudSources(res) {
    return (res.citations || []).map(function (c) {
      var d = new Date(c.visit_at);
      var rec = state.records.filter(function (r) {
        return r.id === c.record_id || (c.client_id && r.clientId === c.client_id);
      })[0];
      var label = '来源：' + fmtDate(d.getTime()) + ' · ' + (c.hospital || '未记录医院') +
        (rec ? ' · 第 ' + seqOf(rec) + ' 次' : '');
      return { label: label, recId: rec ? rec.id : '' };
    });
  }

  /* 点来源跳到「我的」并展开那一条，答案和原文能对上 */
  function openRecord(id) {
    go('mine');
    setTimeout(function () {
      var lis = $$('#timeline li');
      var li = lis.filter(function (x) { return x.getAttribute('data-id') === id; })[0];
      if (!li) { toast('这条记录不在当前列表里。'); return; }
      var btn = li.querySelector('.tl');
      if (btn && !li.classList.contains('is-open')) btn.click();
      li.scrollIntoView({ block: 'center', behavior: 'smooth' });
      li.classList.add('is-flash');
      setTimeout(function () { li.classList.remove('is-flash'); }, 1400);
    }, 160);
  }

  function cite(rec) {
    return '来源：' + fmtDate(rec.at) + ' ' + fmtTime(rec.at) + ' · ' + (rec.hospital || '未记录医院') + ' · 第 ' + seqOf(rec) + ' 次';
  }

  function P(h, t) {
    return { h: h, t: t || String(h).replace(/<[^>]+>/g, '') };
  }
  function out(paras, sources, medical) {
    return { paras: paras, sources: sources || [], medical: !!medical };
  }

  var ASK_ADVICE = /能不能(吃|喝|用|做|去|运动)|可不可以(吃|喝|用|做)|能吃吗|能喝吗|可以吃吗|能不能停药|要不要(停药|继续|停|做|手术|复查|住院)|该不该|需不需要|严重吗|要紧吗|会好吗|怎么治|如何治疗|吃什么药好|可以停|能停|停药|减量|加量|会不会(癌变|恶化)|是不是(癌|肿瘤|很严重)/;

  /* —— 本机模式的结构化查询：日期 / 医院 / 第几次，条件取交集 —— */
  function localDateConds(text) {
    var out = [];
    var now = new Date();
    var m = text.match(/(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?/);
    if (m) out.push({ y: +m[1], m: +m[2], d: +m[3] });
    var m2 = text.match(/(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/);
    if (m2) {
      var y = now.getFullYear();
      var cand = new Date(y, +m2[1] - 1, +m2[2], 9, 0, 0);
      if (cand.getTime() - now.getTime() > 14 * 86400000) y -= 1;
      out.push({ y: y, m: +m2[1], d: +m2[2] });
    }
    return out.filter(function (c, i) {
      return c.m >= 1 && c.m <= 12 && c.d >= 1 && c.d <= 31 &&
        out.findIndex(function (x) { return x.y === c.y && x.m === c.m && x.d === c.d; }) === i;
    });
  }

  function localHospitalConds(text, recs) {
    var out = [];
    recs.forEach(function (r) {
      if (r.hospital && r.hospital !== '未记录医院' && text.indexOf(r.hospital) >= 0 && out.indexOf(r.hospital) < 0) {
        out.push(r.hospital);
      }
    });
    var m = text.match(/([\u4e00-\u9fa5]{2,14}(?:医院|卫生院|诊所|门诊部|卫生服务中心))/);
    if (m) {
      var token = m[1].replace(/^(?:为什么|什么|哪家|怎么|上次|最近|这次|那次|去|到|在|从|回|来|当地|附近)+/, '');
      token = token.replace(/^(?:去|到|在|从|回|来|了)+/, '');
      if (token.length >= 4 && token !== '医院' && token !== '人民医院' &&
          out.indexOf(token) < 0 && !/为什么|什么|哪家|怎么/.test(token)) {
        out.push(token);
      }
    }
    return out;
  }

  function localHospMatch(a, b) {
    function core(x) { return String(x || '').replace(/^(?:市|省|区|县|镇|街道)/, ''); }
    var x = core(a), y = core(b);
    return !!x && !!y && (x === y || x.indexOf(y) >= 0 || y.indexOf(x) >= 0);
  }

  function localPick(qn, recs, need) {
    var dates = localDateConds(qn);
    var hosps = localHospitalConds(qn, recs);
    var ordinal = null;
    var om = qn.match(/第\s*([一二两三四五六七八九十\d]+)\s*次/);
    if (om) {
      var v = om[1];
      ordinal = /^\d+$/.test(v) ? +v : (v === '十' ? 10 : (CN[v] || null));
    }
    var explicit = !!(dates.length || hosps.length || ordinal);

    var pool = recs.slice();
    if (dates.length) {
      pool = pool.filter(function (r) {
        var d = new Date(r.at);
        return dates.some(function (c) { return d.getFullYear() === c.y && d.getMonth() + 1 === c.m && d.getDate() === c.d; });
      });
    }
    if (hosps.length) {
      pool = pool.filter(function (r) {
        return hosps.some(function (h) { return localHospMatch(r.hospital || '', h); });
      });
    }
    if (ordinal !== null) {
      var asc = pool.slice().sort(function (a, b) { return a.at - b.at; });
      pool = (ordinal >= 1 && ordinal <= asc.length) ? [asc[ordinal - 1]] : [];
    }

    var labels = [];
    if (dates.length) labels.push('日期 ' + dates.map(function (c) { return c.y + '年' + c.m + '月' + c.d + '日'; }).join('、'));
    if (hosps.length) labels.push('医院 ' + hosps.join('、'));
    if (ordinal !== null) labels.push('第 ' + ordinal + ' 次');

    if (explicit) {
      return { rec: pool[0] || null, explicit: true, count: pool.length, pool: pool, label: labels.join('，') };
    }
    if (need === 'meds') {
      for (var i = 0; i < recs.length; i++) {
        if ((recs[i].meds || []).length) return { rec: recs[i], explicit: false, count: 0, pool: recs };
      }
    }
    return { rec: recs[0], explicit: false, count: 0, pool: recs };
  }

  function answer(q) {
    var qn = q.replace(/\s+/g, '');
    var recs = sortedDesc();
    var last = recs[0];

    /* —— 身份与闲聊：允许正常应答 —— */
    if (/你叫什么|你是谁|你的名字|你叫啥|自我介绍|你是什么/.test(qn)) {
      return out([
        P('我叫<span class="hl">小鹿</span>。'),
        P('一个只做记录的小工具：把问诊录音转成文字，抽出时间、医院、诊断、用药和复诊，存成你能翻回去的一页页。'),
        P('我不看病，也不替你判断该吃什么——那部分留给医生。')
      ], [], false);
    }
    if (/你能做什么|你会什么|能帮我做什么|怎么用|有什么功能|帮助我/.test(qn)) {
      return out([
        P('我能做三件事：'),
        P('<ul class="ans-list"><li>把录音转成文字，一字不改地留着</li><li>从这段文字里抽出：就诊时间、医院、诊断、用药（剂量/饭前饭后/天数）、复诊、注意事项</li><li>按你的提问去翻这些记录，并告诉你答案出自哪一次</li></ul>'),
        P('我不会做的：诊断、开药、判断严不严重。这些只问医生。')
      ], [], false);
    }
    if (/^(你好|您好|hi|hello|嗨|在吗)/i.test(qn)) {
      return out([
        P('在。要开始录一次问诊，还是先翻翻以前的记录？'),
        P('「开始录音」在右上角；旧记录在「我的」里。')
      ], [], false);
    }
    if (/谢谢|多谢|thanks|thx/i.test(qn)) {
      return out([P('不客气。记录都在「我的」里，随时能翻。')], [], false);
    }
    if (/几点|现在时间|今天几号|今天日期|现在是哪天/.test(qn)) {
      return out([
        P('我这边读到的系统时间是：<span class="hl">' + fmtFull(Date.now()) + '</span>。'),
        P('按这个时间算，你最近一次记录是 ' + (last ? fmtDate(last.at) + '，距今 ' + daysBetween(last.at, Date.now()) + ' 天' : '还没有') + '。')
      ], last ? [{ label: cite(last) }] : [], false);
    }

    /* —— 整段对话粘贴进来：当成「帮我整理这次」 —— */
    if (qn.length > 60 && /医生|大夫|诊断|开了|复诊|复查|检查/.test(qn) && !/[？?]$/.test(qn)) {
      var saved = state.records.filter(function (r) { return r.transcript === q; })[0];
      if (saved) {
        return out([
          P('这段已经是 <span class="hl">第 ' + seqOf(saved) + ' 份</span>记录（' + fmtDate(saved.at) + '，' + esc(saved.hospital) + '）。'),
          P('诊断 ' + esc(saved.summary) + '，' + (saved.meds.length ? saved.meds.length + ' 种药' : '没有提到用药') +
            (saved.followUp ? '，' + esc(saved.followUp) : '') + '。'),
          P('要问细节，直接说「上次开了什么药」「什么时候复诊」。')
        ], [{ label: cite(saved) }], true);
      }

      if (!state.draft || state.draft.transcript !== q) {
        state.draft = makeDraft(q, 'manual');
        renderRecordPanel();
        setLive('ok');
        $('#trMeta').textContent = '来自粘贴 · ' + qn.length + ' 字';
        $('#transcript').innerHTML = '<p class="tr-line"><span class="tr-ts">粘贴</span>' +
          '<span class="tr-tx">' + esc(q) + '</span></p>';
      }
      var dd = state.draft;
      /* 和录音走同一条路：先给看，再问「存不存」。不弹窗的话用户没有落档入口 */
      if (!dd.archived) {
        var segs0 = q.split(/[。；;!?！？\n]/).filter(function (x) { return clean(x); }).length;
        setTimeout(function () { openSaveDialog(segs0); }, 320);
      }
      return out([
        P('我把这段过了一遍：'),
        P('<ul class="ans-list">' +
          '<li>医院：' + esc(dd.hospital || '没听出是哪家') + '</li>' +
          '<li>诊断：' + esc(dd.summary || '没写明') + '</li>' +
          '<li>用药：' + (namedMeds(dd.meds).length ? esc(namedMeds(dd.meds).map(function (m) { return m.name; }).join('、')) : '没提到') + '</li>' +
          '<li>复诊：' + esc(dd.followUp || '没提到') + '</li>' +
          '</ul>'),
        P(dd.archived
          ? '这份<span class="hl">已经归档</span>，在「我的」里能翻到。'
          : '这段还<span class="hl">没有归档</span>，现在只算一次临时整理。我问一句：存进档案吗？')
      ], [{ label: '本次粘贴 · ' + qn.length + ' 字' }], true);
    }

    /* —— 求诊疗建议：一律不答 —— */
    if (ASK_ADVICE.test(qn)) {
      return out([
        P('<span class="hl">这条我不能答。</span>'),
        P('能不能吃、要不要停药、严不严重，这类判断属于诊疗，不在记录工具的范围内。'),
        P('我能告诉你的是：医生当时怎么交代的、开了哪些药、什么时候复诊。可以问「医生交代了什么注意事项」。')
      ], [{ label: '超出记录范围' }], true);
    }

    /* —— 档案为空 —— */
    if (!recs.length) {
      return out([
        P('你的档案里还没有记录。'),
        P('点右上「开始录音」，或者把一段问诊内容贴进「手动整理」的输入框。有了第一份，我就能回答「上次什么时候」「今天吃什么药」这类问题。')
      ], [{ label: '暂无记录' }], false);
    }

    /* —— 本次 vs 历史 —— */
    var aboutCurrent = /这次|刚才|刚刚|本次/.test(qn);
    var subject = aboutCurrent && state.draft ? state.draft : null;

    if (subject) {
      var d = subject;
      if (/药|吃几|饭前|饭后|怎么吃|剂量/.test(qn)) {
        if (!namedMeds(d.meds).length) {
          return out([P('这次对话里没有提到用药。')], [{ label: '本次转写' }], true);
        }
        return out([
          P('这次（' + fmtDate(d.at) + '）一共提到 ' + namedMeds(d.meds).length + ' 种：'),
          P('<ul class="ans-list">' + namedMeds(d.meds).map(function (m) {
            var bits = [m.dose, m.freq, m.timing, m.days ? m.days + ' 天' : ''].filter(Boolean);
            return '<li><b>' + esc(m.name) + '</b>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</li>';
          }).join('') + '</ul>'),
          P(d.archived ? '这份已经归档了。' : '这份还没归档。点「保存到档案」，或在左边记录卡上保存。')
        ], [{ label: '本次转写 · ' + fmtDate(d.at) }], true);
      }
      if (/复诊|复查|下次/.test(qn)) {
        return out([P(d.followUp ? '这次说的是：<span class="hl">' + esc(d.followUp) + '</span>。' : '这次没有提到复诊时间。')],
          [{ label: '本次转写' }], true);
      }
      return out([
        P('这次抽到的是：' + (d.summary ? '<span class="hl">' + esc(d.summary) + '</span>' : '诊断未写明') +
          (d.hospital ? '，' + esc(d.hospital) : '') + '。'),
        P(d.archived ? '已经归档。' : '还没归档——要留下的话，点「保存到档案」。')
      ], [{ label: '本次转写' }], true);
    }

    /* —— 指定的某一次：日期 / 医院 / 第几次 —— */
    var spec = localPick(qn, recs, /药|剂量|饭前|饭后/.test(qn) ? 'meds' : '');
    if (spec.explicit) {
      if (!spec.rec) {
        return out([
          P('<span class="hl">没有找到符合条件的记录，无法回答。</span>'),
          P('你说的「' + esc(spec.label || '这组条件') + '」在档案里对不上。我不会拿最近一次顶替。'),
          P('可以问「我一共看过几次」看完整时间线，或换一个日期 / 医院再问。')
        ], [{ label: '未命中 · 条件不匹配' }], true);
      }
      var sr = spec.rec;
      var more = spec.count > 1 ? P('（符合条件的有 ' + spec.count + ' 次，上面是最近的一次。）') : null;
      if (/过敏/.test(qn)) {
        return out([
          P('<span class="hl">' + fmtDate(sr.at) + '</span>，' + esc(sr.hospital) + '：' +
            (sr.allergy ? esc(sr.allergy) : (sr.allergyDenied ? '记录写「未发现药物过敏」' : '未记录过敏（不等于没有）')) + '。'),
          P('这是那一次的记录，新记录空白不会覆盖它。')
        ], [{ label: cite(sr) }], true);
      }
      if (/复诊|复查|随访/.test(qn)) {
        return out([
          P(sr.followUp ? '<span class="hl">' + esc(sr.followUp) + '</span>' : '那次没有记录复诊时间。'),
          P('出自 ' + fmtDate(sr.at) + ' ' + esc(sr.hospital) + '。')
        ], [{ label: cite(sr) }], true);
      }
      if (/注意|忌口|交代|医嘱|嘱咐/.test(qn)) {
        return out([
          P((sr.notes || []).length ? '那次医生交代的是：' : '那次没有记录注意事项。'),
          (sr.notes || []).length ? P('<ul class="ans-list">' + sr.notes.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>') : null,
          more
        ].filter(Boolean), [{ label: cite(sr) }], true);
      }
      if (/药|吃几|饭前|饭后|怎么吃|剂量|服用/.test(qn)) {
        if (!(sr.meds || []).length) {
          return out([P('<span class="hl">' + fmtDate(sr.at) + ' ' + esc(sr.hospital) + '</span> 那次没有记录用药。')],
            [{ label: cite(sr) }], true);
        }
        return out([
          P('按 <span class="hl">' + fmtDate(sr.at) + ' ' + esc(sr.hospital) + '</span> 那次记录，共 ' + sr.meds.length + ' 种：'),
          P('<ul class="ans-list">' + sr.meds.map(function (m) {
            var bits = [m.dose, m.freq, m.timing, m.days ? m.days + ' 天' : ''].filter(Boolean);
            return '<li><b>' + esc(m.name) + '</b>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</li>';
          }).join('') + '</ul>'),
          more
        ].filter(Boolean), [{ label: cite(sr) }], true);
      }
      return out([
        P('<span class="hl">' + fmtDate(sr.at) + '</span>，' + esc(sr.hospital) + '，诊断 ' + esc(sr.summary || '未写明') + '。'),
        P(((sr.meds || []).length ? '开了 ' + sr.meds.length + ' 种药。' : '没有提到用药。') +
          (sr.followUp ? '复诊：' + esc(sr.followUp) + '。' : '') +
          (sr.allergy ? '过敏：' + esc(sr.allergy) + '。' : '')),
        more,
        P('完整转写在「我的」里那一份记录下面。')
      ].filter(Boolean), [{ label: cite(sr) }], true);
    }

    /* —— 上次 / 最近 —— */
    if (/上次|上一次|上回|最近一次|最近看|什么时候看|哪天看|多久以前/.test(qn)) {
      var gap = daysBetween(last.at, Date.now());
      return out([
        P('最近一次是 <span class="hl">' + fmtDate(last.at) + '</span>（' + fmtTime(last.at) + '），' + esc(last.hospital) + '，诊断是 ' + esc(last.summary) + '。'),
        P(gap === 0 ? '就在今天。' : gap > 0 ? '距今 ' + gap + ' 天。' : '这条记录的时间在今天之后，检查一下设备时间。')
      ], [{ label: cite(last) }], true);
    }

    /* —— 全部记录 —— */
    if (/一共|几次|多少次|所有记录|全部记录|时间线|历史记录|看过几/.test(qn)) {
      var lines = recs.map(function (r) {
        return '<li><b>' + fmtDate(r.at) + '</b> · ' + esc(r.hospital) + ' · ' + esc(r.summary) + '</li>';
      }).join('');
      return out([
        P('一共 <span class="hl">' + recs.length + '</span> 份记录，最近的在最上面：'),
        P('<ul class="ans-list">' + lines + '</ul>'),
        P('要看某一次的全部内容，去「我的」点开那一条。')
      ], [{ label: '全档 ' + recs.length + ' 份' }], true);
    }

    /* —— 找特定一次（医院 / 日期） —— */
    var found = null;
    recs.forEach(function (r) {
      if (found) return;
      if (r.hospital && r.hospital !== '未记录医院' && qn.indexOf(r.hospital) >= 0) found = r;
      if (found) return;
      var d0 = new Date(r.at);
      var md = (d0.getMonth() + 1) + '月' + d0.getDate() + '日';
      if (qn.indexOf(md) >= 0 || qn.indexOf(fmtDate(r.at)) >= 0) found = r;
    });
    if (found) {
      var f = found;
      return out([
        P('<span class="hl">' + fmtDate(f.at) + '</span>，' + esc(f.hospital) + '，诊断 ' + esc(f.summary) + '。'),
        P((f.meds.length ? '开了 ' + f.meds.length + ' 种药。' : '没有提到用药。') +
          (f.followUp ? '复诊：' + esc(f.followUp) + '。' : '') +
          (f.allergy ? '过敏：' + esc(f.allergy) + '。' : '')),
        P('完整转写在「我的」里那一份记录下面。')
      ], [{ label: cite(f) }], true);
    }

    /* —— 用药 —— */
    if (/药|吃几|饭前|饭后|怎么吃|剂量|服用/.test(qn)) {
      if (!last.meds.length) {
        return out([
          P('最近一次（' + fmtDate(last.at) + '）的记录里没有提到用药。'),
          P('我一律按记录说，记录里没有就不编。要确认，去「我的」翻那次的完整转写。')
        ], [{ label: cite(last) }], true);
      }
      return out([
        P('按 <span class="hl">' + fmtDate(last.at) + '</span> 那次记录，一共 ' + last.meds.length + ' 种：'),
        P('<ul class="ans-list">' + last.meds.map(function (m) {
          var bits = [m.dose, m.freq, m.timing, m.days ? m.days + ' 天' : ''].filter(Boolean);
          return '<li><b>' + esc(m.name) + '</b>' + (bits.length ? ' · ' + esc(bits.join(' · ')) : '') + '</li>';
        }).join('') + '</ul>'),
        P('这是记录里医生说的，不是我的建议。剂量和天数以处方为准。')
      ], [{ label: cite(last) }], true);
    }

    /* —— 复诊 —— */
    if (/复诊|复查|随访|下次来|还要来/.test(qn)) {
      if (!last.followUp) {
        return out([P('最近一次的记录里没有提到复诊时间。')], [{ label: cite(last) }], true);
      }
      return out([
        P('上一次记的是：<span class="hl">' + esc(last.followUp) + '</span>。'),
        P('出自 ' + fmtDate(last.at) + ' ' + esc(last.hospital) + ' 那次。')
      ], [{ label: cite(last) }], true);
    }

    /* —— 过敏（跨记录汇总） —— */
    if (/过敏/.test(qn)) {
      var pos = recs.filter(function (r) { return clean(r.allergy); });
      var denied = recs.filter(function (r) { return r.allergyDenied; });
      if (pos.length) {
        return out([
          P('档案里记过 <span class="hl">' + pos.length + '</span> 次过敏：'),
          P('<ul class="ans-list">' + pos.slice(0, 4).map(function (r) {
            return '<li>' + esc(r.allergy) + '（' + fmtDate(r.at) + ' ' + esc(r.hospital) + '）</li>';
          }).join('') + '</ul>'),
          P(denied.length ? '另有记录写过「没有过敏」，但不能覆盖上面的阳性记录。' : '新记录空着，不会把旧记录里的过敏抹掉。')
        ], pos.slice(0, 3).map(function (r) { return { label: cite(r) }; }), true);
      }
      if (denied.length) {
        return out([P('档案里写的是「未发现药物过敏」，出自 ' + fmtDate(denied[0].at) + ' ' + esc(denied[0].hospital) + '。'),
          P('这只代表当时那次，之后有变化要重新确认。')], [{ label: cite(denied[0]) }], true);
      }
      return out([P('所有记录都没有写过过敏史。这栏空着，不代表你没有过敏——需要你自己补记，或问医生。')],
        [{ label: '全档 · 未记录过敏' }], true);
    }

    /* —— 注意事项 —— */
    if (/注意|忌口|交代|医嘱|饮食|忌什么|嘱咐/.test(qn)) {
      if (!last.notes.length) {
        return out([P('最近一次记录里没有单独记下注意事项。完整转写在「我的」里，可以自己再核一遍。')],
          [{ label: cite(last) }], true);
      }
      return out([
        P('上一次医生交代的：'),
        P('<ul class="ans-list">' + last.notes.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>')
      ], [{ label: cite(last) }], true);
    }

    /* —— 兜底 —— */
    return out([
      P('<span class="hl">病历档案未查询到该内容，无法回答。</span>'),
      P('你的 ' + recs.length + ' 份记录里都没有这一条。我能答的是：某次就诊的时间与医院、当时的诊断、开过哪些药和怎么吃、复诊时间、注意事项。'),
      P('也可以问「我上次看病是什么时候」。')
    ], [{ label: '未命中记录' }], true);
  }

  function addUser(text) {
    var el = document.createElement('div');
    el.className = 'msg msg--user';
    el.innerHTML = '<div class="msg__who">' + esc(state.profile && state.profile.name || state.user) + '</div>' +
      '<div class="msg__body">' + esc(text) + '</div>';
    $('#thread').appendChild(el);
    scrollThread();
  }

  function addBot(paras, sources, medical, engine) {
    var el = document.createElement('div');
    el.className = 'msg msg--bot';

    var body = paras.map(function (p) { return '<p>' + p.h + '</p>'; }).join('');
    if (medical) {
      body += '<p class="disc">本内容仅为病历档案整理，不构成诊疗建议。</p>';
    }

    var srcHtml = '';
    if (sources && sources.length) {
      var tags = sources.map(function (s) {
        var label = typeof s === 'string' ? s : (s && s.label);
        var rid = s && s.recId;
        if (!label) return '';
        return '<button class="tag tag--src' + (rid ? ' is-link' : '') + '" type="button"' +
          (rid ? ' data-src="' + esc(rid) + '"' : '') + '>' + esc(label) + '</button>';
      }).filter(Boolean).join('');
      if (tags) {
        srcHtml = '<div class="sources"><span class="sources__label">依据</span>' + tags + '</div>';
      }
    }

    el.innerHTML = '<div class="msg__who">小鹿' + (engine ? ' · ' + esc(engine) : '') +
      '</div><div class="msg__body">' + body + '</div>' + srcHtml;
    $('#thread').appendChild(el);
    scrollThread();

    var speak = paras.map(function (p) { return p.t; });
    if (medical) speak.push('本内容仅为病历档案整理，不构成诊疗建议。');
    speakQueue(speak);
  }

  function scrollThread() {
    var t = $('#thread');
    if (!t) return;
    var prev = t.style.scrollBehavior;
    t.style.scrollBehavior = 'auto';   /* 覆盖 CSS 的 smooth，否则连发时滚不到底 */
    t.scrollTop = t.scrollHeight;
    t.style.scrollBehavior = prev;
  }

  /* ============================================================
     13. 语音合成：默认朗读 + 音量
     ============================================================ */
  var synth = window.speechSynthesis;
  var voice = null;
  var speakToken = 0;

  function pickVoice() {
    if (!synth) return null;
    if (voice) return voice;
    var vs = synth.getVoices() || [];
    if (!vs.length) return null;
    var pref = ['Xiaoxiao', 'Yaoyao', 'Huihui', 'Ting-Ting', 'Mei-Jia', 'Google 普通话'];
    for (var i = 0; i < pref.length; i++) {
      var v = vs.filter(function (x) { return x.name.indexOf(pref[i]) >= 0; })[0];
      if (v) { voice = v; return v; }
    }
    voice = vs.filter(function (x) { return /zh[-_]?CN|Chinese|中文|普通话/i.test(x.lang + ' ' + x.name); })[0] || null;
    return voice;
  }

  if (synth) {
    synth.addEventListener && synth.addEventListener('voiceschanged', function () { pickVoice(); });
    pickVoice();
  }

  function speakQueue(list) {
    if (!state.speaking || !synth) return;
    var token = ++speakToken;
    try { synth.cancel(); } catch (e) {}

    var i = 0;
    var btn = $('#spkBtn');

    function next() {
      if (token !== speakToken) return;
      if (i >= list.length) {
        if (btn) btn.classList.remove('speaking');
        return;
      }
      var text = clean(list[i++]);
      if (!text) { next(); return; }

      var u = new SpeechSynthesisUtterance(text);
      u.lang = 'zh-CN';
      u.volume = state.volume;
      u.rate = 1.02;
      u.pitch = 1;
      var v = pickVoice();
      if (v) u.voice = v;

      if (btn) btn.classList.add('speaking');
      u.onend = next;
      u.onerror = next;

      try { synth.speak(u); } catch (e) { if (btn) btn.classList.remove('speaking'); }
    }
    next();
  }

  function stopSpeech() {
    speakToken++;
    if (synth) { try { synth.cancel(); } catch (e) {} }
    var btn = $('#spkBtn');
    if (btn) btn.classList.remove('speaking');
  }

  function bindVoice() {
    var btn = $('#spkBtn');
    var range = $('#volRange');

    btn.addEventListener('click', function () {
      state.speaking = !state.speaking;
      btn.classList.toggle('is-on', state.speaking);
      btn.setAttribute('aria-pressed', String(state.speaking));
      $('#spkTxt').textContent = state.speaking ? '朗读' : '静音';
      if (!state.speaking) stopSpeech();
      else toast('朗读已开启，音量 ' + Math.round(state.volume * 100) + '%。');
    });

    range.addEventListener('input', function () {
      state.volume = Math.max(0, Math.min(1, Number(range.value) / 100));
      range.title = '音量 ' + Math.round(state.volume * 100) + '%';
    });

    if (!synth) {
      btn.classList.remove('is-on');
      btn.setAttribute('aria-pressed', 'false');
      btn.title = '这个浏览器不支持语音合成';
      btn.disabled = true;
      range.disabled = true;
    }
  }

  /* ============================================================
     14. 杂项
     ============================================================ */
  var toastTimer = null;
  function toast(msg) {
    var el = $('#toast');
    el.textContent = msg;
    el.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('is-on'); }, 3200);
  }

})();
