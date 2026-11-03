function clean(x){return String(x==null?'':x).replace(/\s+/g,' ').trim();}
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


var cases=[
 ['日期+医院',"10月1日去了市第一人民医院，医生诊断为胃炎。"],
 ['无剂型药',"医生让每天吃两次阿莫西林 0.5g，共7天。"],
 ['同句多药',"今天在市人民医院就诊，医生诊断为高血压。开苯磺酸氨氯地平片5mg每天一次，阿托伐他汀钙片20mg每晚一次，吃30天。"],
 ['否定句',"不用吃阿莫西林胶囊，也没有对青霉素过敏，不用复查。"],
 ['常规',"今天下午去了市第一人民医院消化内科。医生说还是慢性胃炎伴糜烂。开四种药：奥美拉唑肠溶胶囊 20mg，每天一次，早饭前吃，吃14天；铝碳酸镁咀嚼片 1g，每天三次，饭后嚼服，吃14天；嘱咐少吃辣的，两周后来复诊。青霉素过敏。"],
];
cases.forEach(function(c){
 var r=extract(c[1]);
 console.log('==',c[0]);
 console.log('  hospital=',r.hospital,'| summary=',r.summary,'| hint=',r.visitHint?new Date(r.visitHint).toISOString():'-');
 console.log('  meds=',JSON.stringify(r.meds));
 console.log('  disc=',JSON.stringify(r.discontinued),'| denied=',r.allergyDenied,'| allergy=',r.allergy,'| cond=',JSON.stringify(r.conditions));
});
