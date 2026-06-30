// ═══════════════════════════════════════════════════════
//  common.js — Shared constants, utilities, and state
//  Used by index.html, step2.html, step3.html
// ═══════════════════════════════════════════════════════

const REF_STYLES = [
  { id:'new-chinese', name:'新中式', nameEn:'New Chinese Style', style:'中式·当代', tags:['莱姆石','青砖','竹格栅','铜构件','借景'], mats:['limestone','bluestone'], desc:'当下最受欢迎的中式演绎：保留青砖、竹格栅、漏窗等中国文化符号，以现代材料和简洁线条重组，有中国味但不复古沉重。' },
  { id:'song-dynasty', name:'宋式文人园', nameEn:'Song Dynasty Scholar Garden', style:'中式·宋', tags:['太湖石','青砖','白墙','修竹','梅'], mats:['limestone','bluestone'], desc:'取法宋人山水画意，以太湖石叠山，白墙黛瓦为底，修竹梅花为骨，追求清雅脱俗的文人气韵。' },
  { id:'chinese-courtyard', name:'明清合院', nameEn:'Ming-Qing Courtyard', style:'中式·明清', tags:['莱姆石','青砖','黄砂岩','影壁','天井'], mats:['limestone','sandstone'], desc:'方正严谨的合院布局，莱姆石铺地，青砖影壁，搭配天井与盆景，体现礼制秩序与自然情趣的平衡。' },
  { id:'zen-dry', name:'枯山水禅庭', nameEn:'Zen Dry Garden', style:'日式·禅', tags:['白碎石','青板岩','苔藓','耙纹','蹲踞'], mats:['bluestone','gravel'], desc:'以白碎石耙纹象征水波，配以青板岩踏石，苔藓点缀，营造静谧禅意空间。' },
  { id:'japanese-garden', name:'日式池泉庭', nameEn:'Japanese Pond Garden', style:'日式·池泉', tags:['鹅卵石','花岗岩','红枫','石灯笼','锦鲤池'], mats:['cobble','granite'], desc:'鹅卵石铺就曲径，花岗岩踏石引路，红枫与石灯笼点缀，四季皆有景致。' },
  { id:'moss-garden', name:'苔庭·雨露风', nameEn:'Moss & Stone Garden', style:'日式·苔庭', tags:['青苔','石灯笼','溪流','鹅卵石','蕨类'], mats:['cobble','bluestone'], desc:'以大面积青苔为地毯，鹅卵石溪流蜿蜒，石灯笼半掩苔痕，湿润阴翳中透出生机，尤适成都多雨气候。' },
  { id:'wabi-sabi', name:'侘寂自然风', nameEn:'Wabi-Sabi Natural', style:'日式·侘寂', tags:['自然石','枯木','野草','锈铁','青苔'], mats:['cobble','bluestone'], desc:'接受不完美与无常，以自然风化石材、野生苔藓、枯木残枝构建庭院，在岁月痕迹中寻见宁静与深邃。' },
  { id:'mixed-modern', name:'现代东方极简', nameEn:'Modern East-Asian Minimal', style:'融合·现代', tags:['花岗岩大板','线形水景','清水混凝土','留白','极简'], mats:['granite','limestone'], desc:'以现代简约手法重诠东方留白美学，花岗岩大板铺地，清水混凝土墙面，线形水景倒映天光，去繁归简。' },
  { id:'industrial-modern', name:'工业现代风', nameEn:'Industrial Modern', style:'现代·工业', tags:['耐候钢板','清水混凝土','砾石','黑色铁艺','观赏草'], mats:['granite','gravel'], desc:'耐候钢板自然锈蚀成暖棕色，清水混凝土墙配砾石地面，铁艺构架点缀观赏草，粗粝中透出克制的当代气质。' },
  { id:'minimalist', name:'极简白盒子', nameEn:'Minimalist White Box', style:'现代·极简', tags:['白色涂料','莱姆石大板','水景','线条','留白'], mats:['limestone','granite'], desc:'纯白墙面与浅色大板铺地，以比例和光影为设计语言，减去一切多余元素，让空间本身成为主角。' },
  { id:'english-cottage', name:'英式乡村花园', nameEn:'English Cottage Garden', style:'欧式·英', tags:['莱姆石','碎拼石','玫瑰','薰衣草','铸铁'], mats:['limestone','cobble'], desc:'自由生长的宿根花卉溢满边界，莱姆石小径蜿蜒其间，铸铁园艺装饰点缀，浪漫而不失野趣的英伦乡村气息。' },
  { id:'french-formal', name:'法式规则园', nameEn:'French Formal Garden', style:'欧式·法', tags:['花岗岩','黄砂岩','修剪绿篱','喷泉','对称轴线'], mats:['granite','sandstone'], desc:'笛卡尔式几何美学，严格对称轴线，花岗岩铺就放射形园路，修剪精整的黄杨绿篱，中央喷泉为视觉焦点。' },
  { id:'mediterranean', name:'地中海庭院', nameEn:'Mediterranean Courtyard', style:'欧式·地中海', tags:['石灰岩','赭石','橄榄树','陶罐','藤架'], mats:['limestone','sandstone'], desc:'粗犷石灰岩铺地，赭红陶罐盛满薰衣草，古老橄榄树撑起一片荫凉，藤架缠绕葡萄，尽是南欧慵懒诗意。' },
  { id:'american-farmhouse', name:'美式农场风', nameEn:'American Farmhouse', style:'欧式·美', tags:['碎石路','木栅栏','多年生花境','秋千','草坪'], mats:['gravel','cobble'], desc:'白色木栅栏围合大草坪，碎石小路串联花境与菜园，秋千与壁炉台点缀其间，实用、放松、充满生活气息。' },
  { id:'nanyang', name:'南洋热带园', nameEn:'Tropical Nanyang Garden', style:'热带·南洋', tags:['黄砂岩','碎石','芭蕉','棕榈','流水'], mats:['sandstone','cobble'], desc:'融合中式与东南亚热带风情，黄砂岩铺地，茂密热带植物，流水叮咚，呈现闽南华侨庭院的南洋情调。' },
  { id:'tropical-jungle', name:'热带雨林风', nameEn:'Tropical Jungle', style:'热带·雨林', tags:['岩石叠层','芭蕉','龟背竹','流瀑','蕨类'], mats:['sandstone','cobble'], desc:'浓密植物层层叠叠，芭蕉与龟背竹营造丛林感，岩石叠层引水成瀑，成都夏季的湿热气候是天然优势。' },
  { id:'naturalistic', name:'自然野趣风', nameEn:'Naturalistic Garden', style:'自然', tags:['自然石','野花草甸','砾石','枯木','昆虫旅馆'], mats:['cobble','gravel'], desc:'模仿自然生态群落，自播野花、观赏草与本土植物混种，自然石随意散落，减少人工干预，与自然共生。' },
  { id:'zen-tea', name:'茶禅一味', nameEn:'Tea Garden Zen', style:'中日·茶境', tags:['莱姆石','青板岩','竹篱','茶席','流水'], mats:['limestone','bluestone'], desc:'以茶事为核心动线，青板岩铺就的入茶之路，竹篱围合静谧茶席空间，流水声掩去城市喧嚣，专为品茗而造。' },
];

const DEFAULT_MATERIALS = [
  { id:'limestone', name:'莱姆石', nameEn:'Limestone', cat:'石材', desc:'质地细腻，色泽温润，适用于庭院地铺、景观墙面及水景边缘。' },
  { id:'bluestone', name:'青板岩', nameEn:'Bluestone Slate', cat:'石材', desc:'层理清晰，色调沉稳，适合铺设步道、踏石及景观台阶，极具禅意。' },
  { id:'sandstone', name:'黄砂岩', nameEn:'Yellow Sandstone', cat:'石材', desc:'色调温暖，纹理自然，适合中式庭院景墙、花坛边框及园林小品。' },
  { id:'granite', name:'芝麻灰花岗岩', nameEn:'Granite', cat:'石材', desc:'质地坚硬，颗粒均匀，防滑耐磨，适合高流量庭院地面与台阶。' },
  { id:'cobble', name:'鹅卵石', nameEn:'River Pebble', cat:'地铺', desc:'圆润自然，色彩丰富，可铺设日式枯山水、中式园路或装饰性水景。' },
  { id:'gravel', name:'白碎石', nameEn:'White Gravel', cat:'地铺', desc:'洁净明亮，是日式枯山水的经典铺材，与苔藓、黑松搭配极具禅境。' },
];

const STYLE_PROMPTS = {
  'zen-dry': { style:'Japanese Zen dry garden (karesansui)', ground:'meticulously raked white gravel with concentric wave patterns', features:'precisely placed large boulders, minimalist moss patches between stepping stones, a weathered stone lantern, low bamboo fence', plants:'black pine bonsai, moss clumps, ornamental grasses', atmosphere:'serene, meditative, monochromatic, quiet morning mist' },
  'japanese-garden': { style:'traditional Japanese stroll garden with pond', ground:'river pebble paths, granite stepping stones, moss lawn', features:'koi pond with arched stone bridge, stone lantern, bamboo water spout (tsukubai), wooden pergola', plants:'Japanese maple (momiji), black pine, cherry blossom, irises by water', atmosphere:'lush, four-season beauty, dappled light through maple canopy' },
  'song-dynasty': { style:'Song Dynasty Chinese scholar garden', ground:'aged grey brick paving in herringbone pattern, smooth flagstone paths', features:'Taihu rock sculptural stones, white plastered walls with moon gate openings, bamboo grove, ink-wash painting aesthetic', plants:'slender bamboo, winter plum blossom, chrysanthemum, orchid, pine', atmosphere:'scholarly, poetic, ink-wash painting aesthetic, misty and refined' },
  'chinese-courtyard': { style:'Ming-Qing dynasty Chinese courtyard', ground:'traditional grey brick courtyard floor in grid pattern, limestone threshold stones', features:'decorative screen wall, carved stone flower pots, wooden lattice windows, central water jar', plants:'potted osmanthus, pomegranate tree, banana palm, rock garden', atmosphere:'dignified, ordered symmetry, warm afternoon sunlight on grey brick' },
  'nanyang': { style:'tropical Nanyang garden', ground:'warm yellow sandstone tiles, terracotta paving, river pebble borders', features:'ornate ceramic pots, carved stone railings with peranakan motifs, small water feature with lotus, wooden pavilion', plants:'banana palm, bird of paradise, frangipani, torch ginger, bougainvillea', atmosphere:'lush tropical, vibrant color, humid warmth, Straits Chinese heritage' },
  'english-cottage': { style:'English cottage garden', ground:'weathered limestone path winding through garden beds, old brick edging', features:'rustic wooden picket fence, cast iron garden bench, terracotta pots, climbing rose trellis, sundial', plants:'roses, lavender, foxglove, hollyhock, delphinium, wisteria on walls', atmosphere:'romantic, abundant, soft pastels, bees and butterflies, golden afternoon' },
  'french-formal': { style:'French formal garden (jardin à la française)', ground:'perfectly symmetrical granite pathways in geometric grid, light gravel parterres', features:'precisely clipped boxwood hedges in geometric shapes, central stone fountain, classical urns on pedestals, wrought iron gates', plants:'topiary yew and boxwood, standard rose trees, pleached lime allée', atmosphere:'grand, perfectly symmetrical, Baroque elegance, crisp shadow lines' },
  'mediterranean': { style:'Mediterranean courtyard garden', ground:'terracotta tiles, rough limestone slabs, mosaic accent panels', features:'whitewashed or stone walls draped in bougainvillea, large terracotta urns, pergola with grape vine, stone well or fountain', plants:'olive tree, bougainvillea, rosemary, lavender, cypress, citrus trees', atmosphere:'warm sun-drenched, vibrant color against white walls, relaxed Provencal charm' },
  'mixed-modern': { style:'modern East-Asian minimalist garden', ground:'large-format granite or limestone slabs with thin dark joints, clean linear lines', features:'slim linear water channel, concrete or corten steel planter walls, recessed lighting, minimalist sculpture', plants:'ornamental grasses, single specimen tree, low spreading juniper, bamboo screen', atmosphere:'clean, contemporary, strong geometry, evening mood lighting' },
  'wabi-sabi': { style:'wabi-sabi natural garden', ground:'irregular mossy stepping stones, bare earth paths, weathered pebbles', features:'naturally weathered driftwood, lichen-covered boulders, rusty metal accents, imperfect handmade ceramic pots', plants:'wild moss, ferns, seed heads left standing, gnarled old tree, bamboo grass', atmosphere:'imperfect beauty, quiet decay, soft overcast light, profound stillness' },
};

const MAT_COLORS = { limestone:'#d4c9b5', bluestone:'#5a6270', sandstone:'#c8a96e', granite:'#9a9890', cobble:'#a89880', gravel:'#dddbd5' };

// ═══════════════════════════════════════════════════════
//  SUPABASE
// ═══════════════════════════════════════════════════════
const SUPABASE_URL = 'https://zehfyibwmxyedswbdyjw.supabase.co';
const SUPABASE_KEY = 'sb_publishable_wGGWRcVnkzfIWK4Pp42J8w_vhoTBsKg';

async function loadCustomMaterials() {
  try {
    const res = await fetch(`${SUPABASE_URL}/rest/v1/materials?order=created_at.asc`, {
      headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` }
    });
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    return data.map(row => ({
      id: row.id, name: row.material, color: row.color, spec: row.spec,
      price: row.price, unit: row.unit, cat: row.cat, desc: row.description, img: row.img,
    }));
  } catch (e) {
    console.warn('Supabase loading failed, using localStorage fallback:', e);
    try { const l = localStorage.getItem('shijing-materials-v1'); return l ? JSON.parse(l) : []; }
    catch { return []; }
  }
}

// ═══════════════════════════════════════════════════════
//  SESSION STORAGE — cross-page state
// ═══════════════════════════════════════════════════════
const STORE = {
  set(key, val) {
    try { sessionStorage.setItem('shijing_' + key, typeof val === 'string' ? val : JSON.stringify(val)); }
    catch(e) { console.warn('sessionStorage set failed:', e); }
  },
  get(key) {
    try {
      const v = sessionStorage.getItem('shijing_' + key);
      if (v === null) return null;
      try { return JSON.parse(v); } catch { return v; }
    } catch(e) { return null; }
  },
  remove(key) { sessionStorage.removeItem('shijing_' + key); },
};

// ═══════════════════════════════════════════════════════
//  PROCEDURAL TEXTURES
// ═══════════════════════════════════════════════════════
function drawTexture(ctx, type, w, h) {
  const r = {
    limestone: () => {
      ctx.fillStyle = '#d4c9b5'; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 25; i++) {
        ctx.beginPath();
        ctx.strokeStyle = `rgba(150,135,110,${0.1 + Math.random() * 0.25})`;
        ctx.lineWidth = 0.3 + Math.random() * 1.2;
        const x = Math.random() * w, y = Math.random() * h;
        ctx.moveTo(x, y);
        ctx.bezierCurveTo(x + (Math.random() - .5) * 60, y + (Math.random() - .5) * 30, x + (Math.random() - .5) * 60, y + (Math.random() - .5) * 30, x + (Math.random() - .5) * 90, y + (Math.random() - .5) * 50);
        ctx.stroke();
      }
    },
    bluestone: () => {
      ctx.fillStyle = '#4e5a66'; ctx.fillRect(0, 0, w, h);
      for (let y = 0; y < h; y += 3 + Math.random() * 5) {
        ctx.beginPath();
        ctx.strokeStyle = `rgba(30,40,55,${0.12 + Math.random() * 0.22})`;
        ctx.lineWidth = 0.6;
        ctx.moveTo(0, y); ctx.lineTo(w, y + (Math.random() - .5) * 4);
        ctx.stroke();
      }
    },
    sandstone: () => {
      ctx.fillStyle = '#c8a96e'; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 20; i++) {
        ctx.beginPath();
        ctx.strokeStyle = `rgba(160,110,50,${0.08 + Math.random() * 0.28})`;
        ctx.lineWidth = 1 + Math.random() * 3;
        const y = Math.random() * h;
        ctx.moveTo(0, y); ctx.lineTo(w, y + (Math.random() - .5) * 10);
        ctx.stroke();
      }
    },
    granite: () => {
      ctx.fillStyle = '#9a9890'; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 280; i++) {
        const x = Math.random() * w, y = Math.random() * h, s = 1 + Math.random() * 2;
        ctx.fillStyle = `rgba(${50 + Math.random() * 100},${50 + Math.random() * 100},${50 + Math.random() * 100},0.4)`;
        ctx.fillRect(x, y, s, s);
      }
    },
    cobble: () => {
      ctx.fillStyle = '#a89880'; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 70; i++) {
        const x = Math.random() * w, y = Math.random() * h, rx = 5 + Math.random() * 12, ry = 4 + Math.random() * 9;
        const g = 100 + Math.floor(Math.random() * 80);
        ctx.beginPath();
        ctx.ellipse(x, y, rx, ry, Math.random() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${g},${g - 8},${g - 15},0.75)`;
        ctx.fill();
        ctx.strokeStyle = 'rgba(60,50,40,0.12)'; ctx.lineWidth = 0.5;
        ctx.stroke();
      }
    },
    gravel: () => {
      ctx.fillStyle = '#dddbd5'; ctx.fillRect(0, 0, w, h);
      for (let i = 0; i < 280; i++) {
        const x = Math.random() * w, y = Math.random() * h, s = 1 + Math.random() * 4;
        const v = 195 + Math.floor(Math.random() * 55);
        ctx.fillStyle = `rgba(${v},${v},${v},0.9)`;
        ctx.beginPath(); ctx.arc(x, y, s / 2, 0, Math.PI * 2); ctx.fill();
      }
    },
  };
  const fn = r[type];
  if (fn) fn(); else r.limestone();
  try {
    const id = ctx.getImageData(0, 0, w, h), d = id.data;
    for (let i = 0; i < d.length; i += 4) {
      const n = (Math.random() - .5) * 18;
      d[i] += n; d[i + 1] += n; d[i + 2] += n;
    }
    ctx.putImageData(id, 0, 0);
  } catch {}
}
