/* 快商机 · 全国招投标商机控制台
   数据来自 collector 产出 data/tenders.json（中国政府采购网全国34省级分区采集）。
   纯静态运行，无外部依赖；所有统计与匹配在端上完成。 */
(function () {
  'use strict';
  var ALL = [], VIEW = [], TAB = 'all', PAGE = 40, SHOWN = 40;
  var SELCAT = {};   /* 行业分类多选：{分类名: true} —— 命中任一即保留（并集） */
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };

  var AMTS = [['0', '不限金额'], ['0-50', '50万以下'], ['50-200', '50-200万'],
              ['200-1000', '200-1000万'], ['1000-', '1000万以上']];
  var DAYS = [['0', '全部时间'], ['1', '今日'], ['3', '近3天'], ['7', '近7天'], ['30', '近30天']];

  /* ---------- 载入 ---------- */
  fetch('data/tenders.json', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (rows) {
      ALL = rows.map(norm);
      $('#srcSt').textContent = '实时数据';
      buildFilters();
      flipTab('all');
    })
    .catch(function (e) {
      $('#srcSt').textContent = '离线';
      $('#main').innerHTML = '<div class="empty"><b>尚未生成数据</b>' +
        '在项目根目录执行：<br><code>python3 -m collector.run</code><br>' +
        '<span style="font-size:11px">' + esc(e.message) + '</span></div>';
    });

  function norm(r) {
    var t = r.pub_time ? Date.parse(String(r.pub_time).replace(/-/g, '/')) : 0;
    return {
      id: r.id, title: r.title || '(无标题)', url: r.url, ts: t || 0,
      time: shortTime(t), buyer: r.buyer || '', agency: r.agency || '',
      region: r.region || '未标注', stage: r.stage || '其他', type: r.notice_type || '',
      cat: r.category_path || '', amount: r.amount || 0, winner: r.winner || '',
      deadline: r.deadline || '', detail: !!r.detail_flag, score: 0,
      src: r.source || '未知来源', hasdoc: !!r.cf,
      /* 行业分类：categories 是多标签数组，缺失时退回主分类 category */
      cats: (r.categories && r.categories.length ? r.categories
             : [r.category || '其他'].filter(Boolean))
    };
  }
  /* 卡片上的来源标签用短名，全名太长会把移动端标签行挤爆 */
  var SRC_SHORT = {
    '中国政府采购网': '央采',
    '广东省政府采购智慧云平台': '粤采'
  };
  function shortSrc(s) { return SRC_SHORT[s] || s.slice(0, 6); }

  function shortTime(t) {
    if (!t) return '';
    var d = new Date(t);
    return (d.getMonth() + 1) + '-' + String(d.getDate()).padStart(2, '0');
  }
  var fmtAmt = function (v) {
    if (!v) return '';
    return v >= 10000 ? (v / 10000).toFixed(2) + '亿' : (Math.round(v * 10) / 10) + '万';
  };

  /* ---------- 统计 ---------- */
  function buildFilters() {
    var regs = {};
    ALL.forEach(function (r) { regs[r.region] = (regs[r.region] || 0) + 1; });
    var list = Object.keys(regs).sort(function (a, b) { return regs[b] - regs[a]; });
    fill('#selRegion', [['', '全国 ' + list.length + ' 地区']].concat(
      list.map(function (k) { return [k, k + ' (' + regs[k] + ')']; })));
    fill('#selAmt', AMTS);
    fill('#selDay', DAYS);
    var srcs = {};
    ALL.forEach(function (r) { srcs[r.src] = (srcs[r.src] || 0) + 1; });
    var slist = Object.keys(srcs).sort(function (a, b) { return srcs[b] - srcs[a]; });
    fill('#selSource', [['', '全部数据源 ' + slist.length + ' 个']].concat(
      slist.map(function (k) { return [k, k + ' (' + srcs[k] + ')']; })));
    buildCatChips();
    buildChips();
    fillMain();
    $('#filter').style.display = '';
  }

  /* 行业分类多选：按当前数据量倒序铺开，标签右附命中条数 */
  function buildCatChips() {
    var cnt = {};
    ALL.forEach(function (r) { r.cats.forEach(function (c) { cnt[c] = (cnt[c] || 0) + 1; }); });
    var list = Object.keys(cnt).sort(function (a, b) {
      return cnt[b] - cnt[a] || (a < b ? -1 : 1);
    });
    $('#cats').innerHTML = list.map(function (k) {
      return '<span class="chip" data-cat="' + esc(k) + '">' + esc(k) +
        '<i>' + cnt[k] + '</i></span>';
    }).join('');
  }
  function selCatCount() {
    return Object.keys(SELCAT).filter(function (k) { return SELCAT[k]; }).length;
  }
  function hitCat(r) {
    var n = selCatCount(); if (!n) return true;
    for (var i = 0; i < r.cats.length; i++) { if (SELCAT[r.cats[i]]) return true; }
    return false;
  }

  function buildChips() {
    var presets = [
      { t: '全部', d: '0', a: '0', on: true },
      { t: '今日新增', d: '1' },
      { t: '近 7 天', d: '7' },
      { t: '百万级项目', a: '200-1000' },
      { t: '千万级项目', a: '1000-' }
    ];
    $('#stages').innerHTML = presets.map(function (p) {
      return '<span class="chip' + (p.on ? ' on' : '') + '" data-d="' + (p.d || '0') +
        '" data-a="' + (p.a || '0') + '">' + p.t + '</span>';
    }).join('');
  }
  function fill(sel, pairs) {
    var el = $(sel); if (!el) return;
    el.innerHTML = pairs.map(function (p) {
      return '<option value="' + esc(p[0]) + '">' + esc(p[1]) + '</option>'; }).join('');
  }

  function fillMain() {
    var n = ALL.length, bid = 0, win = 0, pre = 0, sum = 0, regs = {};
    ALL.forEach(function (r) {
      if (r.stage === '招标公告') bid++;
      else if (r.stage === '中标结果') win++;
      if (r.stage === '前置预警' || r.stage === '变更公告' || r.stage === '异常公告') pre++;
      if (r.amount) sum += r.amount;
      regs[r.region] = 1;
    });
    var money = sum >= 10000 ? (sum / 10000).toFixed(1) + '亿' : Math.round(sum) + '万';
    $('#stats').innerHTML =
      st('a', n, '商机总数') + st('b', bid, '招标中') +
      st('c', win, '中标结果') + st('d', money, '披露金额');
    $('#cAll').textContent = n; $('#cBid').textContent = bid;
    $('#cPre').textContent = pre; $('#cWin').textContent = win;
  }
  function st(c, v, l) {
    return '<div class="st ' + c + '"><b>' + v + '</b><span>' + l + '</span></div>';
  }

  /* ---------- 筛选 ---------- */
  function applyFilter() {
    var kw = $('#kw').value.trim(), reg = $('#selRegion').value,
        amt = $('#selAmt').value, day = $('#selDay').value,
        src = $('#selSource') ? $('#selSource').value : '';
    $('#clr').hidden = !kw;
    var now = Date.now(), cut = 0;
    if (day !== '0') cut = now - parseInt(day, 10) * 86400000;
    VIEW = ALL.filter(function (r) {
      if (src && r.src !== src) return false;
      if (!hitCat(r)) return false;
      if (TAB === '招标公告' || TAB === '中标结果') { if (r.stage !== TAB) return false; }
      if (reg && r.region !== reg) return false;
      if (cut && r.ts < cut) return false;
      if (amt !== '0') {
        var p = amt.split('-');
        var lo = parseFloat(p[0]) || 0, hi = p[1] ? parseFloat(p[1]) : Infinity;
        if (!(r.amount >= lo && r.amount <= hi)) return false;
      }
      if (kw) {
        var words = kw.split(/[\s,，、]+/).filter(Boolean);
        var hay = r.title + ' ' + r.buyer + ' ' + r.agency + ' ' + r.cat + ' ' + r.type;
        return words.every(function (w) { return hay.indexOf(w) >= 0; });
      }
      return true;
    });
    if (TAB === 'ai') scoreAll();
    else VIEW.sort(function (a, b) { return b.ts - a.ts; });
    SHOWN = PAGE;
    var nc = selCatCount();
    $('#catHit').textContent = nc ? '已选 ' + nc + ' 类' : '可多选';
    $('#fcount').textContent = '命中 ' + VIEW.length + ' 条';
    render();
  }

  /* ---------- 渲染 ---------- */
  function render() {
    var slice = VIEW.slice(0, SHOWN);
    if (!slice.length) {
      $('#main').innerHTML = '<div class="empty"><b>没有匹配的商机</b>换个关键词或放宽筛选条件试试</div>';
      return;
    }
    var html = '<div class="list">' + slice.map(function (r) { return card(r, TAB === 'ai'); }).join('') + '</div>';
    if (SHOWN < VIEW.length) html += '<button class="more" id="more">加载更多（剩余 ' + (VIEW.length - SHOWN) + ' 条）</button>';
    $('#main').innerHTML = html;
    var m = $('#more');
    if (m) m.onclick = function () { SHOWN += PAGE; render(); };
    /* 标题点击 → 打开详情抽屉（原文链接仍在抽屉右上角） */
    Array.prototype.forEach.call($('#main').querySelectorAll('.cd-t a'), function (a) {
      a.onclick = function (e) {
        if (e.metaKey || e.ctrlKey) return;      // 保留「新标签打开原文」
        e.preventDefault();
        var id = a.getAttribute('data-id');
        for (var i = 0; i < VIEW.length; i++) {
          if (VIEW[i].id === id) { openDetail(VIEW[i]); return; }
        }
      };
    });
  }

  function card(r, withScore) {
    var cls = r.stage === '中标结果' ? 'win'
      : (r.stage === '前置预警' || r.stage === '变更公告' || r.stage === '异常公告') ? 'warn' : '';
    if (withScore) cls += ' has-score';
    var kw = $('#kw').value.trim();
    var title = esc(r.title);
    if (kw) kw.split(/[\s,，、]+/).filter(Boolean).forEach(function (w) {
      title = title.split(esc(w)).join('<mark>' + esc(w) + '</mark>');
    });
    var tags = '<span class="tag st-' + esc(r.stage) + '">' + esc(r.stage) + '</span>' +
      (r.cats.length ? r.cats.map(function (c, i) {
        return '<span class="tag ' + (i ? 'cat2' : 'cat') + '">' + esc(c) + '</span>';
      }).join('') : '') +
      '<span class="tag">' + esc(r.region) + '</span>';
    if (r.type && r.type.length < 12) tags += '<span class="tag">' + esc(r.type) + '</span>';
    if (r.cat) tags += '<span class="tag">' + esc(r.cat.slice(0, 22)) + '</span>';
    if (r.amount) tags += '<span class="tag amt">预算 ' + fmtAmt(r.amount) + '</span>';
    if (r.deadline) tags += '<span class="tag dl">截止 ' + esc(String(r.deadline).slice(5)) + '</span>';
    if (r.src) tags += '<span class="tag src">来自 ' + esc(shortSrc(r.src)) + '</span>';
    if (r.hasdoc) tags += '<span class="tag doc">正文</span>';

    var meta = [];
    if (r.buyer) meta.push('采购人：' + esc(r.buyer));
    if (r.winner) meta.push('中标：' + esc(r.winner));
    if (r.agency) meta.push('代理：' + esc(r.agency));
    if (r.time) meta.push('发布于 ' + esc(r.time));

    return '<article class="cd ' + cls + '">' +
      (withScore ? '<div class="score"><b>' + r.score + '</b><span>匹配度</span></div>' : '') +
      '<h3 class="cd-t"><a href="' + esc(r.url) + '" data-id="' + esc(r.id) +
        '" target="_blank" rel="noopener">' + title + '</a></h3>' +
      '<div class="tags">' + tags + '</div>' +
      '<div class="meta">' + meta.map(function (t) { return '<span>' + t + '</span>'; }).join('') + '</div>' +
      '</article>';
  }

  /* ---------- 机会预警 ----------
     立项/采购意向在国家级平台没有检索入口，因此预警改用数据里真实存在的
     三类信号：废标终止（会重新招标）、需求变更、投标窗口即将关闭。 */
  function daysLeft(r) {
    if (!r.deadline) return -1;
    var t = Date.parse(String(r.deadline).replace(/-/g, '/'));
    if (!t) return -1;
    return Math.ceil((t - Date.now()) / 86400000);
  }

  function warnView() {
    var abn = ALL.filter(function (r) { return r.stage === '异常公告'; });
    var chg = ALL.filter(function (r) { return r.stage === '变更公告'; });
    var pre = ALL.filter(function (r) { return r.stage === '前置预警'; });
    var soon = ALL.filter(function (r) {
      var d = daysLeft(r); return r.stage === '招标公告' && d >= 0 && d <= 14;
    }).sort(function (a, b) { return daysLeft(a) - daysLeft(b); });

    var byTime = function (a, b) { return b.ts - a.ts; };
    abn.sort(byTime); chg.sort(byTime); pre.sort(byTime);

    function group(title, tip, list, extra) {
      if (!list.length) return '';
      return '<section class="panel"><h3>' + title + ' <small style="font-size:11px;color:var(--hint)">' +
        list.length + ' 条</small></h3><p class="tip">' + tip + '</p>' +
        '<div class="list">' + list.slice(0, 8).map(function (r) {
          var ex = extra ? '<span class="tag dl">' + extra(r) + '</span>' : '';
          return cardWith(r, ex);
        }).join('') + '</div></section>';
    }

    $('#main').innerHTML =
      '<section class="panel"><h3>预警信号说明</h3><p class="tip">' +
        '国家级发布平台暂未开放采购意向检索，因此这里的预警基于三类已在数据中确证的商机信号：' +
        '<b>废标/终止</b>的项目会重新招标，可提前备标；' +
        '<b>更正/澄清</b>说明需求或门槛正在松动；' +
        '<b>临近截止</b>的项目是当下还能抢的窗口。' +
      '</p></section>' +
      group('废标 / 终止 — 二次招标机会',
            '项目流标或终止后通常会在短期内重新招标，提前联系采购人可抢占先机。', abn) +
      group('更正 / 澄清 — 需求调整信号',
            '公告被修改往往意味着资质或技术门槛发生变化，是重新评估可否参与的时机。', chg) +
      group('投标窗口将关闭', '距截止时间 14 天以内、仍可参与的项目。', soon,
            function (r) { var d = daysLeft(r); return d <= 0 ? '今日截止' : '剩 ' + d + ' 天'; }) +
      (pre.length ? group('采购意向 / 需求公示', '招标前最早释放的需求信号。', pre) : '');
  }

  /* 在卡片 tags 尾部追加一个自定义标签 */
  function cardWith(r, extraTag) {
    var html = card(r, false);
    if (extraTag) html = html.replace('</div><div class="meta">', extraTag + '</div><div class="meta">');
    return html;
  }

  /* ---------- 详情数据（按需加载，不进首屏） ----------
     details.json 里是正文 + 结构化信息 + 联系人，体积远大于列表，
     因此不在首屏拉取，等用户点开某条公告 / 打开通讯录时才加载一次。 */
  var DMAP = null, DLOAD = null;
  function loadDetails(cb) {
    if (DMAP) { cb(DMAP); return; }
    if (DLOAD) { DLOAD.then(cb); return; }
    DLOAD = fetch('data/details.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (j) { DMAP = j.items || {}; $('#cCt').textContent = j.count || 0; return DMAP; })
      .catch(function () { DMAP = {}; return DMAP; });
    DLOAD.then(cb);
  }

  function openDetail(rec) {
    loadDetails(function (map) {
      var d = map[rec.id];
      $('#sheet').hidden = false;
      document.body.classList.add('locked');
      $('#shLink').href = rec.url || '#';
      $('#shBody').innerHTML = sheetHtml(rec, d);
      $('#shBody').scrollTop = 0;
      var art = $('#art');
      var btn = $('#artMore');
      if (btn) btn.onclick = function () {
        art.classList.remove('fold'); btn.hidden = true;
      };
    });
  }
  function closeDetail() {
    $('#sheet').hidden = true;
    document.body.classList.remove('locked');
  }

  function sheetHtml(r, d) {
    d = d || {};
    var f = d.f || {}, cts = d.ct || [];
    var kv = '';
    Object.keys(f).forEach(function (k) {
      kv += '<dt>' + esc(k) + '</dt><dd>' + esc(f[k]) + '</dd>';
    });
    // 列表字段里没进结构化的，补在后面兜底展示
    var extra = '';
    if (r.amount) extra += '<dt>预算金额</dt><dd>' + fmtAmt(r.amount) + '</dd>';
    if (r.winner) extra += '<dt>中标单位</dt><dd>' + esc(r.winner) + '</dd>';
    if (r.deadline) extra += '<dt>投标截止</dt><dd>' + esc(String(r.deadline).slice(0, 16)) + '</dd>';

    var ctHtml = cts.length ? cts.map(function (c) {
      var cls = c.role === '代理机构' ? 'ag' : (c.role === '采购人' ? 'pr' : '');
      var tel = c.mobile || c.phone || '';
      var initial = (c.name || c.org || '?').slice(0, 1);
      return '<div class="ct ' + cls + '">' +
        '<div class="av">' + esc(initial) + '</div>' +
        '<div class="who"><span class="nm">' + esc(c.name || '（未署名）') +
        ' <small style="color:var(--hint);font-weight:400">' + esc(c.role || '') + '</small></span>' +
        '<span class="og">' + esc(c.org || '') + '</span></div>' +
        (tel ? '<a class="call" href="tel:' + esc(tel) + '">' + esc(tel) + '</a>'
             : '<span class="none">无电话</span>') + '</div>';
    }).join('') : '<p class="tip-empty">该公告正文里未解析出联系人。</p>';

    var body = d.c ? '<div class="article fold" id="art">' + esc(d.c) + '</div>' +
      '<button class="moreart" id="artMore">展开全文</button>'
      : '<p class="tip-empty">正文尚未采集。执行 <code>python3 -m collector.run ' +
        '--rebuild-content 500</code> 补抓正文，或点右上角查看原文。</p>';

    return '<div class="sh-card">' +
        '<h3>' + esc(r.title) + '</h3>' +
        '<div class="sh-sub"><span>' + esc(r.stage) + '</span><span>' + esc(r.region) + '</span>' +
        '<span>' + esc(r.time || (r.pub_time || '')) + '</span><span>' + esc(shortSrc(r.src)) + '</span></div>' +
        (kv || extra ? '<div class="sh-sec">结构化信息 <span>从公告正文解析</span></div>' +
          '<dl class="kv">' + kv + extra + '</dl>' : '') +
      '</div>' +
      '<div class="sh-card"><div class="sh-sec">联系人 <span>' + cts.length + ' 位</span></div>' +
        ctHtml + '</div>' +
      '<div class="sh-card"><div class="sh-sec">公告正文</div>' + body + '</div>';
  }

  /* ---------- 通讯录 ---------- */
  function contactsView() {
    $('#main').innerHTML = '<div class="empty">正在加载联系人…</div>';
    loadDetails(function (map) {
      var rows = [];
      Object.keys(map).forEach(function (id) {
        var d = map[id];
        (d.ct || []).forEach(function (c) {
          if (!(c.name || c.mobile || c.phone)) return;
          rows.push({ name: c.name || '', org: c.org || '', role: c.role || '',
                      tel: c.mobile || c.phone || '', title: d.t || '',
                      region: d.r || '', id: id });
        });
      });
      $('#main').innerHTML =
        '<section class="panel"><h3>项目联系人库 <small style="font-size:11px;color:var(--hint)">' +
          rows.length + ' 条</small></h3>' +
          '<p class="tip">从公告正文解析出的采购人 / 代理机构 / 项目联系人，' +
          '随详情采集持续累积 —— 补抓的正文越多，这里越全。</p>' +
          '<div class="ab-srch"><input id="abKw" placeholder="搜姓名 / 单位 / 电话 / 项目"></div>' +
          '<div class="chips" id="abRole"></div></section>' +
        '<div id="abList"></div>';
      var roles = {};
      rows.forEach(function (r) { roles[r.role] = (roles[r.role] || 0) + 1; });
      $('#abRole').innerHTML = '<span class="chip on" data-role="">全部 ' + rows.length + '</span>' +
        Object.keys(roles).map(function (k) {
          return '<span class="chip" data-role="' + esc(k) + '">' + esc(k) + ' ' + roles[k] + '</span>';
        }).join('');
      var curRole = '';
      function draw() {
        var kw = ($('#abKw').value || '').trim();
        var list = rows.filter(function (r) {
          if (curRole && r.role !== curRole) return false;
          if (!kw) return true;
          return (r.name + r.org + r.tel + r.title).indexOf(kw) >= 0;
        });
        if (!list.length) {
          $('#abList').innerHTML = '<div class="empty"><b>没有匹配的联系人</b>' +
            '换个关键词，或先补抓正文扩大样本</div>';
          return;
        }
        $('#abList').innerHTML = '<div class="list">' + list.slice(0, 200).map(function (r) {
          return '<div class="ab-item">' +
            '<div class="av" style="width:34px;height:34px;border-radius:9px;flex:0 0 auto;' +
              'background:var(--brand-s);color:var(--brand);display:flex;align-items:center;' +
              'justify-content:center;font-size:13px;font-weight:600">' +
              esc((r.name || r.org || '?').slice(0, 1)) + '</div>' +
            '<div class="who"><span class="nm">' + esc(r.name || '（未署名）') +
              ' <small style="color:var(--hint);font-weight:400">' + esc(r.role) + '</small></span>' +
              '<span class="og">' + esc(r.org || '') + (r.region ? ' · ' + esc(r.region) : '') + '</span>' +
              '<span class="pj" data-id="' + esc(r.id) + '">▸ ' + esc(r.title.slice(0, 30)) + '</span></div>' +
            (r.tel ? '<a class="call" href="tel:' + esc(r.tel) + '">' + esc(r.tel) + '</a>'
                   : '<span class="none">—</span>') + '</div>';
        }).join('') + '</div>' +
          (list.length > 200 ? '<p class="tip" style="text-align:center">仅显示前 200 条，' +
            '用搜索缩小范围</p>' : '');
        Array.prototype.forEach.call(document.querySelectorAll('#abList .pj'), function (el) {
          el.onclick = function () {
            var rec = null;
            for (var i = 0; i < ALL.length; i++) { if (ALL[i].id === el.dataset.id) { rec = ALL[i]; break; } }
            if (rec) openDetail(rec);
          };
        });
      }
      $('#abKw').addEventListener('input', draw);
      Array.prototype.forEach.call($('#abRole').children, function (c) {
        c.onclick = function () {
          Array.prototype.forEach.call($('#abRole').children, function (x) { x.classList.remove('on'); });
          c.classList.add('on');
          curRole = c.dataset.role || '';
          draw();
        };
      });
      draw();
    });
  }

  /* ---------- 对手分析 ---------- */
  /* 多标签计数：一条公告挂在多个赛道下，各赛道各计一次 */
  function catRank() {
    var m = {};
    ALL.forEach(function (r) { r.cats.forEach(function (c) { m[c] = (m[c] || 0) + 1; }); });
    return Object.keys(m).map(function (k) { return [k, m[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 10);
  }
  function rival() {
    var cnt = function (key, filter) {
      var m = {};
      ALL.forEach(function (r) {
        var v = r[key]; if (!v) return;
        if (filter && !filter(r)) return;
        v = String(v).slice(0, 24);
        m[v] = (m[v] || 0) + 1;
      });
      return Object.keys(m).map(function (k) { return [k, m[k]]; })
        .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8);
    };
    var bars = function (data, cls, unit) {
      var max = data.length ? data[0][1] : 1;
      return data.map(function (d) {
        return '<div class="bar ' + cls + '"><span class="nm" title="' + esc(d[0]) + '">' + esc(d[0]) + '</span>' +
          '<span class="tr"><span class="fl" style="width:' + (d[1] / max * 100) + '%"></span></span>' +
          '<span class="vl">' + d[1] + unit + '</span></div>';
      }).join('');
    };
    var winr = cnt('winner');
    $('#main').innerHTML =
      panel('行业分布 <small style="font-size:11px;color:var(--hint)">哪些赛道项目最多</small>',
            '按行业标签统计（一条公告可归属多个赛道）；点「商机」页的行业分类可多选筛选。',
            bars(catRank(), 'p', '条')) +
      panel('中标单位榜 <small style="font-size:11px;color:var(--hint)">谁在拿单</small>',
            '中标人需抓取公告正文才能提取，覆盖率取决于详情采集配额；样本越多结论越准。',
            winr.length ? bars(winr, 'w', '单')
              : '<p class="tip">当前尚无中标人样本。提高详情配额重跑：<code>python3 -m collector.run --detail 400</code></p>') +
      panel('代理机构榜 <small style="font-size:11px;color:var(--hint)">谁在操盘项目</small>',
            '代理机构是商机的前置管道 —— 绑定头部代理机构，比逐个盯采购人更高效。',
            bars(cnt('agency'), '', '项')) +
      panel('采购人榜 <small style="font-size:11px;color:var(--hint)">谁在持续买单</small>',
            '高频采购人往往有年度采购惯性，是长期跟踪对象。',
            bars(cnt('buyer'), 'p', '项')) +
      panel('地区分布', '各省商机密度，可用于判断资源投放重点。',
            bars(cnt('region'), 'w', '条'));
  }
  function panel(t, tip, body) {
    return '<section class="panel"><h3>' + t + '</h3><p class="tip">' + tip + '</p>' + body + '</section>';
  }

  /* ---------- AI 商机匹配 ---------- */
  function loadProfile() {
    try { return JSON.parse(localStorage.getItem('ksj_profile') || '{}'); } catch (e) { return {}; }
  }
  function saveProfile(p) { localStorage.setItem('ksj_profile', JSON.stringify(p)); }

  function aiPanel() {
    var p = loadProfile();
    $('#main').innerHTML =
      '<section class="prof">' +
        '<h3>企业画像配置</h3><p class="tip">按你的资质和业务范围设定，系统会据此给每条商机打分</p>' +
        '<input id="pKw" placeholder="能力关键词，逗号分隔：智能化,安防,系统集成" value="' + esc(p.kw || '') + '">' +
        '<div class="row">' +
          '<select id="pReg"><option value="">全国</option></select>' +
          '<select id="pAmt">' +
            '<option value="0">金额不限</option><option value="1">50万以内</option>' +
            '<option value="2">50-500万</option><option value="3">500万以上</option>' +
          '</select>' +
        '</div>' +
        '<button id="pRun">开始匹配</button>' +
      '</section>' +
      '<section class="panel"><h3>匹配逻辑</h3>' +
        '<p class="tip">评分在本地实时完成，规则可解释：</p>' +
        '<div class="explain">' +
          '<b>0-50 分</b> 能力词命中：标题与品目分类中命中的关键词数量加权，多词同时命中分数更高<br>' +
          '<b>0-15 分</b> 地域偏好：目标省份内项目<br>' +
          '<b>0-15 分</b> 金额适配：落在你设定的承接区间<br>' +
          '<b>0-12 分</b> 阶段价值：招标中最高，前置预警次之（可提前介入），中标结果最低<br>' +
          '<b>0-8 分</b> 时效窗口：仍在投标截止期内的项目' +
        '</div></section>' +
      '<div id="aiList"></div>';

    var regs = {};
    ALL.forEach(function (r) { regs[r.region] = 1; });
    var sel = $('#pReg'), html = '<option value="">全国</option>';
    Object.keys(regs).sort().forEach(function (k) { html += '<option value="' + esc(k) + '">' + esc(k) + '</option>'; });
    sel.innerHTML = html;
    sel.value = p.region || '';
    $('#pAmt').value = p.amt || '0';
    $('#pRun').onclick = function () {
      var np = { kw: $('#pKw').value.trim(), region: sel.value, amt: $('#pAmt').value };
      saveProfile(np);
      scoreAll();
      VIEW = ALL.slice(0).filter(function (r) { return r.score > 0; })
        .sort(function (a, b) { return (b.score - a.score) || (b.ts - a.ts); });
      SHOWN = PAGE;
      $('#fcount').textContent = '匹配 ' + VIEW.length + ' 条（按匹配度排序）';
      $('#aiList').innerHTML = '<div class="list">' +
        VIEW.slice(0, SHOWN).map(function (r) { return card(r, true); }).join('') + '</div>';
      $('#main').querySelector('.prof').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
  }

  function scoreAll() {
    var p = loadProfile();
    var words = (p.kw || '').split(/[\s,，、]+/).filter(Boolean);
    var now = Date.now();
    ALL.forEach(function (r) {
      var s = 0, hay = r.title + ' ' + r.cat + ' ' + r.type + ' ' + r.cats.join(' ');
      var hit = 0;
      words.forEach(function (w) { if (hay.indexOf(w) >= 0) hit++; });
      if (words.length) s += Math.min(50, Math.round(hit / words.length * 50));
      if (p.region && r.region === p.region) s += 15;
      if (p.amt && p.amt !== '0' && r.amount) {
        var ok = { '1': r.amount <= 50, '2': r.amount > 50 && r.amount <= 500, '3': r.amount > 500 }[p.amt];
        if (ok) s += 15;
      }
      s += { '招标公告': 12, '前置预警': 10, '变更公告': 8, '异常公告': 6, '中标结果': 3 }[r.stage] || 2;
      if (r.deadline && Date.parse(String(r.deadline).replace(/-/g, '/')) > now) s += 8;
      r.score = Math.min(100, s);
    });
  }

  /* ---------- Tab 切换 ---------- */
  function flipTab(t) {
    TAB = t;
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (el) {
      el.classList.toggle('on', el.dataset.t === t);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.nav div'), function (el) {
      el.classList.toggle('on', el.dataset.t === t);
    });
    var listLike = (t === 'all' || t === '招标公告' || t === '中标结果');
    $('#filter').style.display = listLike ? '' : 'none';
    $('.stats').style.display = (t === 'rival' || t === 'ai' || t === 'warn' || t === 'contacts') ? 'none' : '';
    if (t === 'rival') { rival(); return; }
    if (t === 'contacts') { contactsView(); return; }
    if (t === 'warn') { warnView(); return; }
    if (t === 'ai') { aiPanel(); $('#fcount').textContent = '配置画像后点击匹配'; return; }
    applyFilter();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  document.addEventListener('click', function (e) {
    var tab = e.target.closest ? e.target.closest('.tab,.nav div') : null;
    if (tab && tab.dataset.t) { flipTab(tab.dataset.t); return; }
    /* 行业分类 chip：多选，点一次切换一次，互不影响 */
    var cc = e.target.closest ? e.target.closest('.chip[data-cat]') : null;
    if (cc) {
      var key = cc.dataset.cat, on = !SELCAT[key];
      SELCAT[key] = on;
      cc.classList.toggle('on', on);
      applyFilter();
      return;
    }
    var chip = e.target.closest ? e.target.closest('.chip') : null;
    if (chip) {
      Array.prototype.forEach.call(chip.parentNode.children, function (c) { c.classList.remove('on'); });
      chip.classList.add('on');
      $('#selDay').value = chip.dataset.d || '0';
      $('#selAmt').value = chip.dataset.a || '0';
      applyFilter();
    }
  });
  ['#kw', '#selRegion', '#selAmt', '#selDay', '#selSource'].forEach(function (s) {
    var el = $(s);
    el.addEventListener(s === '#kw' ? 'input' : 'change', applyFilter);
  });
  $('#clr').addEventListener('click', function () { $('#kw').value = ''; applyFilter(); });
  $('#catClr').addEventListener('click', function () {
    SELCAT = {};
    Array.prototype.forEach.call(document.querySelectorAll('#cats .chip'),
      function (c) { c.classList.remove('on'); });
    applyFilter();
  });
  $('#catMore').addEventListener('click', function () {
    var box = $('#cats'), open = box.classList.toggle('open');
    this.textContent = open ? '收起' : '展开全部';
  });
  $('#shClose').addEventListener('click', closeDetail);
})();
