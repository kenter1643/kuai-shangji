/* ---------- 快商机 · 数据层 ----------
 *
 * 这一层把「商机收藏 / 企业画像 / 浏览记录」的读写收敛到一处，
 * 上层只调 KSJStore.fav / KSJStore.profile / KSJStore.hist，不关心底下存在哪。
 *
 * 当前状态：云服务未开通（开通确认被取消，拿不到 publicConfig），
 * 因此底层是 localStorage —— 数据只留在当前这台设备的浏览器里，
 * 换设备、清缓存、无痕模式都会丢。
 *
 * 开通云服务后的迁移点只有下面三个函数：all() / save() / remove()，
 * 换成云数据库的查询与写入即可，UI 和调用方一行都不用动。
 * （云侧要用「用户维度」存：每条收藏带 userId，查询带身份约束，
 *   不能让一个用户读到另一个用户的数据。）
 */
(function () {
  var NS = 'ksj_';

  function read(key, fallback) {
    try {
      var raw = localStorage.getItem(NS + key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      // 存储被禁用（无痕模式 / 隐私设置）时不能让页面崩掉，降级成内存态
      return fallback;
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

  /* ---------- 收藏 ----------
   * 存 id + 关键字段快照：只存 id 的话，下次采集换了数据就找不到这条记录了，
   * 而公告下线后 id 也可能不再出现在列表里。有快照就永远渲染得出来。 */
  var fav = {
    all: function () {
      var v = read('fav', []);
      return Object.prototype.toString.call(v) === '[object Array]' ? v : [];
    },
    ids: function () {
      var m = {};
      fav.all().forEach(function (r) { m[r.id] = 1; });
      return m;
    },
    has: function (id) { return !!fav.ids()[id]; },
    /* rec 为列表里的记录对象；返回 true 表示收藏后状态（true=已收藏） */
    toggle: function (rec) {
      var list = fav.all();
      var i = -1;
      for (var k = 0; k < list.length; k++) { if (list[k].id === rec.id) { i = k; break; } }
      if (i >= 0) {
        list.splice(i, 1);
        write('fav', list);
        return false;
      }
      list.unshift({
        id: rec.id, title: rec.title || '', url: rec.url || '',
        region: rec.region || '', stage: rec.stage || '', src: rec.src || '',
        amount: rec.amount || 0, time: rec.time || '', ts: rec.ts || 0,
        buyer: rec.buyer || '', cats: rec.cats || [], at: Date.now()
      });
      write('fav', list);
      return true;
    },
    remove: function (id) {
      var list = fav.all().filter(function (r) { return r.id !== id; });
      write('fav', list);
      return list.length;
    },
    count: function () { return fav.all().length; }
  };

  /* ---------- 企业画像（AI 匹配用） ---------- */
  var profile = {
    get: function () {
      var v = read('profile', {});
      return (v && typeof v === 'object') ? v : {};
    },
    set: function (p) { write('profile', p || {}); }
  };

  /* ---------- 最近浏览 ---------- */
  var hist = {
    all: function () {
      var v = read('hist', []);
      return Object.prototype.toString.call(v) === '[object Array]' ? v : [];
    },
    push: function (rec) {
      var list = hist.all().filter(function (r) { return r.id !== rec.id; });
      list.unshift({ id: rec.id, title: rec.title || '', stage: rec.stage || '', at: Date.now() });
      write('hist', list.slice(0, 50));
    }
  };

  window.KSJStore = { fav: fav, profile: profile, hist: hist };
})();
