/* 关于：简介、声明、开源声明（许可全文、照片署名）、友情链接、关注 AI政见（两个二维码），最下面一行小字。
   文字照维护者 2026-10-21 审定的文案稿，不要改字；“关注 AI政见”一节是维护者 10-22 加的。版本号和照片署名从 /api/about 取，许可全文从 /api/license 取。 */
(function () {
  'use strict';
  const ZZB = (window.ZZB = window.ZZB || {});
  const ui = ZZB.ui, api = ZZB.api, h = ui.h;

  const REPO_URL = 'https://github.com/yuelutech/zuzhibu';
  const INTRO = 'AI政见·组织部是 Yuelu Tech“AI政见”推出的开源桌面软件，按照干部简历的规范体例，整理美国白宫、商界、国会、国防、司法系统主要负责人的公开资料，收录人事任免动态。';
  const STATEMENTS = [
    '本软件为戏仿项目。“组织部”等名称和干部简历体例仅为借用。本软件不代表任何党政机关，与任何党政机关、政府部门、政党组织和新闻媒体均无隶属或合作关系。',
    '本软件所收资料均来自各机构官方网站、政府公报和其他公开渠道，力求准确，但不保证完整、及时，仅供参考，不构成任何决策依据。引用时请核对原始来源。',
    '人物照片和来源摘录的权利归原权利人所有，照片来源和许可见“照片署名”。',
    '本软件不收集任何个人信息。使用者填写的接口密钥只保存在本机，只用于向使用者指定的接口发起请求。'
  ];
  const OPEN_SOURCE = '本软件源代码以 Apache License 2.0 发布；人物资料库的编排和中文撰写部分以知识共享署名—相同方式共享 4.0 国际许可协议（CC BY-SA 4.0）发布；来源摘录和人物照片不在上述许可之内，按各自来源和许可使用。';
  const LINKS = [
    ['美国白宫', 'https://www.whitehouse.gov/'], ['美国国会', 'https://www.congress.gov/'], ['美国参议院', 'https://www.senate.gov/'],
    ['美国众议院', 'https://www.house.gov/'], ['美国最高法院', 'https://www.supremecourt.gov/'], ['美国国防部', 'https://www.defense.gov/'],
    ['美国国务院', 'https://www.state.gov/'], ['美国联邦储备委员会', 'https://www.federalreserve.gov/'], ['美国证券交易委员会', 'https://www.sec.gov/'],
    ['联邦公报', 'https://www.federalregister.gov/']
  ];
  /* 两个二维码是维护者给的图（img/qr-video.png 短视频账号、img/qr-article.png 图文账号）。短视频那张是圆形码，原图是从码卡上偏心裁的，
     重新裁成整圆：点阵照抄不动，右上角徽标的外圈补完整（维护者本人账号码的一部分，照留）；页面上用圆框。说明只写“扫码关注”，不写平台名称。 */
  const QR = [['img/qr-video.png', '短视频　扫码关注 AI政见', 'round'], ['img/qr-article.png', '图文　扫码关注 AI政见', '']];
  const FOOT = '本项目为戏仿项目，与任何党政机关无关。';

  function ext(text, url, cls) { return h('a', { class: cls || 'lk', href: url, target: '_blank', rel: 'noopener', data: { external: '1' } }, text); }

  function main(body, show) {
    const sec = ZZB.panels.section;
    const top = h('div', { class: 'ab-top' }, h('img', { class: 'ab-logo', src: 'favicon.svg', alt: '' }),
      h('div', { class: 'ab-mark' }, 'AI政见', h('i', null, '·'), '组织部'), h('div', { class: 'ab-ver' }, '版本'));
    api.about().then(function (a) { top.lastChild.textContent = '版本 ' + a.version; }).catch(function () {});
    const s1 = sec('简介'); s1.appendChild(h('p', { class: 'ab-p' }, INTRO));
    const s2 = sec('声明'); s2.appendChild(h('ol', { class: 'ab-ol' }, STATEMENTS.map(function (t) { return h('li', null, t); })));
    const s3 = sec('开源声明');
    s3.append(h('p', { class: 'ab-p' }, OPEN_SOURCE),
      h('p', { class: 'ab-repo' }, '项目地址：', ext('github.com/yuelutech/zuzhibu', REPO_URL),
        h('span', { class: 'ab-sub' },
          h('button', { class: 'lk', type: 'button', on: { click: function () { show('license'); } } }, '许可全文'),
          h('button', { class: 'lk', type: 'button', on: { click: function () { show('credits'); } } }, '照片署名'))));
    const s4 = sec('友情链接');
    s4.appendChild(h('div', { class: 'links' }, LINKS.map(function (l) { const a = ext(l[0], l[1], 'flink'); a.insertAdjacentHTML('beforeend', ui.ICONS.out); return a; })));
    const s5 = sec('关注 AI政见');
    s5.appendChild(h('div', { class: 'ab-qr' }, QR.map(function (q) {
      return h('figure', { class: q[2] || null }, h('img', { src: q[0], width: '150', height: '150', alt: q[1], draggable: 'false' }), h('figcaption', null, q[1]));
    })));
    body.append(top, h('div', { class: 'ab-in' }, s1, s2, s3, s4, s5));
  }

  function sub(body, title, back, fill) {
    const b = h('button', { class: 'back', type: 'button', on: { click: back } });
    b.insertAdjacentHTML('beforeend', ui.ICONS.back);
    b.appendChild(document.createTextNode('返回'));
    const box = h('div', { class: 'ab-in ab-subview' }, b, h('h3', { class: 'ab-subtitle' }, title));
    body.appendChild(box);
    fill(box);
  }

  ZZB.panels.register('about', function () {
    const body = h('div', { class: 'd-body ab-body' });
    const box = ZZB.panels.dialog('关于', 'about', body);
    box.appendChild(h('div', { class: 'ab-foot' }, FOOT));
    function show(view) {
      ui.clear(body); body.scrollTop = 0;
      if (view === 'license') {
        sub(body, '许可全文', function () { show(''); }, function (el) {
          api.license().then(function (l) {
            el.append(h('h4', null, '源代码：Apache License 2.0'), h('pre', { class: 'ab-pre sel' }, l.code || ''),
              h('h4', null, '人物资料库：CC BY-SA 4.0'), h('pre', { class: 'ab-pre sel' }, l.content || ''));
          }).catch(function (e) { el.appendChild(h('p', { class: 'status' }, e.message)); });
        });
      } else if (view === 'credits') {
        sub(body, '照片署名', function () { show(''); }, function (el) {
          api.about().then(function (a) {
            el.appendChild(h('ul', { class: 'ab-list' }, (a.credits || []).map(function (c) { return h('li', null, h('span', null, c.name), h('b', { class: 'sel' }, c.credit)); })));
          }).catch(function (e) { el.appendChild(h('p', { class: 'status' }, e.message)); });
        });
      } else main(body, show);
    }
    show('');
    return { el: box };
  });
})();
