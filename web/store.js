/* ---------- 快商机 · 数据层 ----------
 *
 * 这一层把「商机收藏 / 企业画像 / 浏览记录」的读写收敛到一处，
 * 上层只调 KSJStore.fav / KSJStore.profile / KSJStore.hist，不关心底下存在哪。
 *
 * 存储策略（云服务已开通）：
 *   - 未登录：照旧落 localStorage，只在本机有效，UI 会明说「仅存本机」；
 *   - 已登录：云端为准。启动时把本机收藏补传云端，再拉回云端全量做并集，
 *     之后每一次收藏/取消都即时改内存+本机（点下去立刻有反应），再异步写云端；
 *     云端写失败不会假装成功——会发一条 notice 让上层提示，数据仍留在本机。
 *   - 浏览记录只在本地，它只是「最近看过」，不值得占用云端配额。
 *
 * 对外 API 故意保持同步：收藏按钮要的是「点了马上有反应」，
 * 云端那一步在后台跑，成功失败通过 onNotice 通知。
 */
(function () {
  var NS = 'ksj_';

  function read(key, fallback) {
    try {
      var raw = localStorage.getItem(NS + key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;   // 存储被禁用（无痕模式）时降级成内存态，不能让页面崩
    }
  }
  function write(key, val) {
    try {
      localStorage.setItem(NS + key, JSON.stringify(val));
      return true;
    } catch (e) {
      return false;
    }
  }

  var CACHE = {
    fav: read('fav', []) || [],
    profile: read('profile', {}) || {}
  };
  if (!Array.isArray(CACHE.fav)) CACHE.fav = [];
  if (!CACHE.profile || typeof CACHE.profile !== 'object') CACHE.profile = {};

  var STATE = {
    ready: false,          // 云端初始化是否跑完
    cloudOk: false,        // SDK 是否可用
    signedIn: false,
    user: null,
    syncing: false,
    error: ''              // 云服务不可用时的原因，UI 直接展示
  };

  var listeners = { auth: [], notice: [] };
  function emit(k, payload) {
    (listeners[k] || []).forEach(function (fn) { try { fn(payload); } catch (e) { } });
  }
  function notice(msg, kind) { emit('notice', { msg: msg, kind: kind || 'info' }); }

  function persistFav() { write('fav', CACHE.fav); }
  function sortFav() {
    CACHE.fav.sort(function (a, b) { return (b.at || 0) - (a.at || 0); });
  }

  /* 云端收藏 + 本机收藏做并集：同一 id 以云端为准（字段更新），
     本机独有的一条不丢 —— 换设备登录时这些正是要补上去的 */
  function mergeFav(cloudList) {
    var byId = {};
    CACHE.fav.forEach(function (r) { byId[String(r.id)] = r; });
    cloudList.forEach(function (r) { byId[String(r.id)] = r; });
    CACHE.fav = Object.keys(byId).map(function (k) { return byId[k]; });
    sortFav();
    persistFav();
  }

  /* ---------- 启动：接云服务、恢复会话、合并收藏与画像 ---------- */
  function init() {
    var C = window.KSJCloud;
    if (!C) { STATE.ready = true; STATE.error = '云服务模块未加载'; return Promise.resolve(STATE); }
    var err = C.initError();
    if (err) { STATE.ready = true; STATE.error = err; return Promise.resolve(STATE); }
    STATE.cloudOk = true;

    return C.session().then(function (res) {
      STATE.ready = true;
      if (res.error || !res.data) {
        STATE.signedIn = false;
        STATE.user = null;
        return STATE;
      }
      return afterSignIn(res.data.user || null);
    });
  }

  function afterSignIn(user) {
    STATE.signedIn = true;
    STATE.user = user || null;
    STATE.syncing = true;
    var C = window.KSJCloud;
    // 先补传本机收藏，再拉云端全量并集 —— 顺序反了会把本机独有的丢掉
    return C.favSyncAll(CACHE.fav)
      .then(function () { return C.favList(); })
      .then(function (list) {
        if (list.error) {
          STATE.error = list.error.message;
          notice('云端收藏读取失败：' + list.error.message + '（已用本机数据）', 'warn');
        } else {
          mergeFav(list.data || []);
        }
        return C.profileGet();
      })
      .then(function (p) {
        if (!p.error && p.data) {
          CACHE.profile = p.data;
          write('profile', CACHE.profile);
        }
        STATE.syncing = false;
        emit('auth', STATE);
        return STATE;
      })
      .catch(function (e) {
        STATE.syncing = false;
        STATE.error = (e && e.message) || '云端同步异常';
        emit('auth', STATE);
        return STATE;
      });
  }

  /* ---------- 收藏 ----------
     存 id + 关键字段快照：只存 id 的话公告下线后找不到记录，有快照就永远渲染得出来 */
  var fav = {
    all: function () { return CACHE.fav.slice(); },
    ids: function () {
      var m = {};
      CACHE.fav.forEach(function (r) { m[r.id] = 1; });
      return m;
    },
    has: function (id) { return !!fav.ids()[id]; },

    toggle: function (rec) {
      var list = CACHE.fav, i = -1;
      for (var k = 0; k < list.length; k++) { if (list[k].id === rec.id) { i = k; break; } }
      if (i >= 0) {
        list.splice(i, 1);
        persistFav();
        syncRemove(rec.id);
        return false;
      }
      CACHE.fav.unshift({
        id: rec.id, title: rec.title || '', url: rec.url || '',
        region: rec.region || '', stage: rec.stage || '', src: rec.src || '',
        amount: rec.amount || 0, time: rec.time || '', ts: rec.ts || 0,
        buyer: rec.buyer || '', cats: rec.cats || [], at: Date.now()
      });
      persistFav();
      syncAdd(rec);
      return true;
    },

    remove: function (id) {
      CACHE.fav = CACHE.fav.filter(function (r) { return r.id !== id; });
      persistFav();
      syncRemove(id);
      return CACHE.fav.length;
    },
    count: function () { return CACHE.fav.length; }
  };

  /* 异步写云端：失败不回滚本机，但要说出来 */
  function syncAdd(rec) {
    if (!STATE.signedIn || !window.KSJCloud) return;
    window.KSJCloud.favAdd(rec).then(function (r) {
      if (r.error) notice('云端同步失败：' + r.error.message + '（已存本机）', 'warn');
    });
  }
  function syncRemove(id) {
    if (!STATE.signedIn || !window.KSJCloud) return;
    window.KSJCloud.favRemove(id).then(function (r) {
      // 云端本来就没有（本机独有的旧收藏）不算错误，静默即可
      if (r.error && !/没有这条收藏/.test(r.error.message)) {
        notice('云端同步失败：' + r.error.message, 'warn');
      }
    });
  }

  /* ---------- 企业画像（AI 匹配用） ---------- */
  var profile = {
    get: function () { return CACHE.profile || {}; },
    set: function (p) {
      CACHE.profile = p || {};
      write('profile', CACHE.profile);
      if (STATE.signedIn && window.KSJCloud) {
        window.KSJCloud.profileSet(CACHE.profile).then(function (r) {
          if (r.error) notice('画像云端保存失败：' + r.error.message + '（已存本机）', 'warn');
        });
      }
    }
  };

  /* ---------- 最近浏览（仅本机） ---------- */
  var hist = {
    all: function () {
      var v = read('hist', []);
      return Array.isArray(v) ? v : [];
    },
    push: function (rec) {
      var list = hist.all().filter(function (r) { return r.id !== rec.id; });
      list.unshift({ id: rec.id, title: rec.title || '', stage: rec.stage || '', at: Date.now() });
      write('hist', list.slice(0, 50));
    }
  };

  /* ---------- 登录 / 登出 ---------- */
  var auth = {
    sendCode: function (phone) {
      if (!window.KSJCloud) return Promise.resolve({ error: { message: '云服务未加载' } });
      return window.KSJCloud.sendCode(phone);
    },
    verify: function (phone, code) {
      if (!window.KSJCloud) return Promise.resolve({ error: { message: '云服务未加载' } });
      return window.KSJCloud.verifyCode(phone, code).then(function (r) {
        if (!r.error) return afterSignIn(STATE.user || (r.data && r.data.user) || null).then(function () { return r; });
        return r;
      });
    },
    signOut: function () {
      if (!window.KSJCloud) return Promise.resolve({ error: { message: '云服务未加载' } });
      return window.KSJCloud.signOut().then(function (r) {
        STATE.signedIn = false;
        STATE.user = null;
        emit('auth', STATE);
        return r;
      });
    },
    state: function () { return STATE; }
  };

  window.KSJStore = {
    fav: fav, profile: profile, hist: hist, auth: auth,
    init: init,
    cloudState: function () { return STATE; },
    /* 登录态变化 / 需要提示用户时回调；app.js 用它刷头部与收藏页文案 */
    onAuthChange: function (fn) { listeners.auth.push(fn); },
    onNotice: function (fn) { listeners.notice.push(fn); },
    /* 给 UI 用的身份展示：优先昵称，其次脱敏手机号，都没有就显示「已登录」 */
    userLabel: function () {
      var u = STATE.user;
      if (!u) return '';
      var name = u.name || u.nickname || '';
      if (name) return name;
      var p = u.phone || u.phoneNumber || '';
      if (p) return p.replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2');
      return '已登录';
    }
  };
})();
