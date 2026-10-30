/* ---------- 快商机 · 云服务接入层 ----------
 *
 * 只做一件事：把「登录 / 收藏 / 画像」三个能力接到 WorkBuddy 云服务上。
 * 配置来自 index.html 里的 KSJ_CLOUD_CONFIG（publicConfig，前端可见是设计如此，
 * publishableKey 本身不带权限，服务端按 Origin 精确匹配）。
 *
 * 约定：
 *   - 每个方法都返回 { data, error }，绝不抛异常，调用方（store.js）据此决定提示文案；
 *   - 未登录时这些方法会返回 error，由上层展示登录入口，不做匿名兜底、不伪造 session；
 *   - endpoint / publishableKey 一律从配置读，不写死、不从 location 推断。
 */
(function () {
  var CFG = window.KSJ_CLOUD_CONFIG || null;
  var cloud = null;
  var pending = null;          // 短信验证码中间态：{ phone, verificationId, isExistingUser }

  function client() {
    if (cloud) return cloud;
    if (!CFG) return null;
    if (!window.WorkBuddyCloud || !window.WorkBuddyCloud.createWorkBuddyCloud) return null;
    cloud = window.WorkBuddyCloud.createWorkBuddyCloud({
      endpoint: CFG.endpoint,
      oauthRelayBaseUrl: CFG.oauthRelayBaseUrl,
      publishableKey: CFG.publishableKey
    });
    return cloud;
  }

  function fail(msg, code) { return { data: null, error: { message: msg, code: code || '' } }; }
  function ok(d) { return { data: d, error: null }; }

  /* ---------- 会话 ---------- */
  async function session() {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.auth.getSession();
      if (r.error) return fail(r.error.message || '获取登录状态失败', r.error.kind);
      return ok(r.data);
    } catch (e) {
      return fail(e && e.message ? e.message : '云服务连接失败');
    }
  }

  /* ---------- 手机号验证码登录 ---------- */
  async function sendCode(phone) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    if (!/^1\d{10}$/.test(phone)) return fail('请输入正确的 11 位手机号');
    try {
      var r = await c.auth.sendOtp({ phone: phone });
      if (r.error) return fail(r.error.message || '验证码发送失败', r.error.kind);
      pending = {
        phone: phone,
        verificationId: r.data.verificationId,
        isExistingUser: !!r.data.isExistingUser
      };
      return ok({ sent: true });
    } catch (e) {
      return fail(e && e.message ? e.message : '验证码发送失败');
    }
  }

  async function verifyCode(phone, token) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    if (!pending || pending.phone !== phone) return fail('请先获取当前手机号的验证码');
    try {
      var r = await c.auth.verifyOtp({
        phone: pending.phone,
        verificationId: pending.verificationId,
        isExistingUser: pending.isExistingUser,
        token: String(token || '').trim()
      });
      if (r.error) return fail(r.error.message || '验证码校验失败', r.error.kind);
      pending = null;
      return ok(r.data);
    } catch (e) {
      return fail(e && e.message ? e.message : '验证码校验失败');
    }
  }

  async function signOut() {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.auth.signOut();
      return r.error ? fail(r.error.message || '退出失败') : ok(true);
    } catch (e) {
      return fail(e && e.message ? e.message : '退出失败');
    }
  }

  function onAuth(cb) {
    var c = client();
    if (!c || typeof c.auth.onAuthStateChange !== 'function') return;
    c.auth.onAuthStateChange(cb);
  }

  /* ---------- 收藏：云数据库 favorites ---------- */
  function rowToRec(row) {
    // 换设备时列表里没有活数据，靠 snapshot 把整张卡片还原出来
    var s = row.snapshot || {};
    return {
      id: row.tender_id, title: row.title || s.title || '',
      url: row.url || s.url || '', region: row.region || s.region || '',
      stage: s.stage || '', src: s.src || '', amount: Number(row.amount || s.amount || 0),
      time: row.pub_time || s.time || '', ts: s.ts || 0,
      buyer: row.buyer || s.buyer || '', cats: s.cats || [], at: s.at || 0
    };
  }
  function recToRow(rec) {
    return {
      tender_id: String(rec.id),
      title: rec.title || '',
      buyer: rec.buyer || '',
      region: rec.region || '',
      amount: rec.amount ? Number(rec.amount) : null,
      pub_time: rec.time || '',
      url: rec.url || '',
      snapshot: rec
    };
  }

  async function favList() {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.database.from('favorites')
        .select('id, tender_id, title, buyer, region, amount, pub_time, url, snapshot, created_at')
        .order('created_at', { ascending: false })
        .limit(500);
      if (r.error) return fail(r.error.message || '读取云端收藏失败', r.error.code);
      return ok((r.data || []).map(rowToRec));
    } catch (e) {
      return fail(e && e.message ? e.message : '读取云端收藏失败');
    }
  }

  async function favAdd(rec) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.database.from('favorites').insert(recToRow(rec)).select();
      if (r.error) return fail(r.error.message || '同步收藏失败', r.error.code);
      // 空数组 = RLS 拦下了（多半是未登录），不是写成功
      if (!r.data || !r.data.length) return fail('同步失败：云端未接受这条收藏（请确认已登录）');
      return ok(r.data[0]);
    } catch (e) {
      return fail(e && e.message ? e.message : '同步收藏失败');
    }
  }

  /* 登录后把本机已有收藏一次性补传云端：
     用 UNIQUE(owner_id, tender_id) 做 upsert + ignoreDuplicates，重复收藏不会报错也不会覆盖 */
  async function favSyncAll(recs) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    if (!recs || !recs.length) return ok(0);
    try {
      var rows = recs.map(recToRow);
      var r = await c.database.from('favorites')
        .upsert(rows, { onConflict: 'owner_id,tender_id', ignoreDuplicates: true }).select();
      if (r.error) return fail(r.error.message || '同步收藏失败', r.error.code);
      return ok((r.data || []).length);
    } catch (e) {
      return fail(e && e.message ? e.message : '同步收藏失败');
    }
  }

  async function favRemove(tenderId) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.database.from('favorites')
        .delete().eq('tender_id', String(tenderId)).select();
      if (r.error) return fail(r.error.message || '取消收藏失败', r.error.code);
      if (!r.data || !r.data.length) return fail('云端没有这条收藏，或你不是它的主人');
      return ok(true);
    } catch (e) {
      return fail(e && e.message ? e.message : '取消收藏失败');
    }
  }

  /* ---------- 画像：云数据库 profiles ---------- */
  async function profileGet() {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.database.from('profiles').select('data, updated_at').limit(1);
      if (r.error) return fail(r.error.message || '读取画像失败', r.error.code);
      if (!r.data || !r.data.length) return ok(null);
      return ok(r.data[0].data || null);
    } catch (e) {
      return fail(e && e.message ? e.message : '读取画像失败');
    }
  }

  async function profileSet(obj) {
    var c = client();
    if (!c) return fail('云服务未初始化');
    try {
      var r = await c.database.from('profiles').upsert({ data: obj }, { onConflict: 'owner_id' }).select();
      if (r.error) return fail(r.error.message || '保存画像失败', r.error.code);
      if (!r.data || !r.data.length) return fail('保存失败：云端未接受这份画像（请确认已登录）');
      return ok(r.data[0].data);
    } catch (e) {
      return fail(e && e.message ? e.message : '保存画像失败');
    }
  }

  window.KSJCloud = {
    available: function () { return !!client(); },
    initError: function () {
      if (!CFG) return '缺少云服务配置';
      if (!window.WorkBuddyCloud) return '云服务 SDK 未加载';
      return '';
    },
    session: session,
    sendCode: sendCode,
    verifyCode: verifyCode,
    signOut: signOut,
    onAuth: onAuth,
    favList: favList,
    favAdd: favAdd,
    favSyncAll: favSyncAll,
    favRemove: favRemove,
    profileGet: profileGet,
    profileSet: profileSet
  };
})();
