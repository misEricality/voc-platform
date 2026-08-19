/* 游戏对比看板 v3 —— library_hero + 最近评测/所有评测双字段 + 词云重做 */
const DATA = /*__DATA__*/ null;
const GAMES = DATA.games;
const $ = s => document.querySelector(s);
function esc(s){return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function ratingClass(r){
  if(r === '好评如潮') return 'r-best';
  if(r === '特别好评') return 'r-great';
  if(r === '好评') return 'r-good';
  if(r === '褒贬不一') return 'r-mixed';
  return 'r-bad';
}
function fmtCount(n){
  if(n >= 10000) return (n/10000).toFixed(1).replace(/\.0$/,'') + ' 万';
  return n.toLocaleString('zh-CN');
}
function coverURL(appid){
  return `https://cdn.akamai.steamstatic.com/steam/apps/${appid}/library_hero.jpg`;
}

function cardHTML(g){
  const link = `voc-platform-prototype.html?game=${esc(g.appid)}`;
  const ratingCls = ratingClass(g.rating_zh);
  return `<div class="game-card">
    <div class="card-cover" style="background-image:url('${esc(coverURL(g.appid))}')">
      <div class="card-info">
        <h3 class="card-name">${esc(g.name)}</h3>
        <div class="card-line">
          <span class="lbl">最近评测</span>
          <a class="recent-link" href="${link}" title="跳转到该游戏原声看板">
            <span class="rl-rating ${ratingClass(g.local_rating_zh)}">${esc(g.local_rating_zh)}</span>
            <span class="rl-num">${fmtCount(g.local_count)}</span>
          </a>
        </div>
        <div class="card-line">
          <span class="lbl">所有评测</span>
          <span class="all-rating ${ratingCls}">${esc(g.rating_zh)}</span>
          <span class="all-num">${fmtCount(g.total_reviews)}</span>
        </div>
        <div class="card-line">
          <span class="lbl">发行日期</span>
          <span class="rd">${esc(g.release_text)}</span>
        </div>
      </div>
    </div>
    <div class="card-cloud">
      <div class="cloud-label">玩家高频词 · 词频差异已放大</div>
      <div class="cloud-canvas" data-cloud></div>
    </div>
  </div>`;
}

function render(){
  $('#gameGrid').innerHTML = GAMES.map(cardHTML).join('');
  document.querySelectorAll('[data-cloud]').forEach((c, i) => {
    if(GAMES[i] && GAMES[i].words) renderCloud(c, GAMES[i].words);
  });
}

$('#cloudToggle').addEventListener('click', () => {
  const grid = $('#gameGrid');
  grid.classList.toggle('cloud-open');
  const open = grid.classList.contains('cloud-open');
  const btn = $('#cloudToggle');
  btn.classList.toggle('on', open);
  btn.textContent = open ? '收起全部词云' : '展开全部词云';
});

/* 词云：矩形画布无规则排版（贪心探测） */
function renderCloud(canvas, words){
  if(!words || !words.length){
    canvas.innerHTML = '<span style="position:absolute;left:18px;top:14px;color:var(--dim);font-size:13px">暂无词云数据</span>';
    return;
  }
  const max = words[0].count, min = words[words.length-1].count;
  const range = (max - min) || 1;
  const fontMin = 12, fontMax = 36;
  const W = canvas.clientWidth, H = 200;
  const gridSize = 6;
  const cols = Math.ceil(W / gridSize);
  const rows = Math.ceil(H / gridSize);
  const occupied = new Uint8Array(cols * rows);
  function setGrid(x, y, w, h, v){
    for(let r=Math.max(0,Math.floor(y/gridSize)); r<Math.min(rows, Math.ceil((y+h)/gridSize)); r++){
      for(let c=Math.max(0,Math.floor(x/gridSize)); c<Math.min(cols, Math.ceil((x+w)/gridSize)); c++){
        occupied[r*cols + c] = v;
      }
    }
  }
  function collides(x, y, w, h){
    for(let r=Math.max(0,Math.floor(y/gridSize)); r<Math.min(rows, Math.ceil((y+h)/gridSize)); r++){
      for(let c=Math.max(0,Math.floor(x/gridSize)); c<Math.min(cols, Math.ceil((x+w)/gridSize)); c++){
        if(occupied[r*cols + c]) return true;
      }
    }
    return false;
  }
  let seed = 0;
  words.forEach(w => seed = (seed*31 + w.word.charCodeAt(0) + w.count) | 0);
  const placed = [];
  words.forEach((w, idx) => {
    const size = fontMin + (w.count - min) / range * (fontMax - fontMin);
    const tmp = document.createElement('span');
    tmp.className = 'cloud-word';
    tmp.style.fontSize = size + 'px';
    tmp.style.visibility = 'hidden';
    tmp.textContent = w.word;
    canvas.appendChild(tmp);
    const ww = tmp.getBoundingClientRect().width;
    const hh = size * 1.05;
    canvas.removeChild(tmp);
    const cx = W/2, cy = H/2;
    let placed2 = null;
    const maxR = Math.hypot(W, H) / 2;
    for(let r = 0; r <= maxR && !placed2; r += 4){
      const steps = Math.max(8, Math.floor(2*Math.PI*r/16));
      for(let s = 0; s < steps; s++){
        const a = s / steps * Math.PI * 2 + idx * 0.3;
        const x = cx + Math.cos(a)*r - ww/2;
        const y = cy + Math.sin(a)*r - hh/2;
        if(x < 4 || y < 4 || x + ww > W - 4 || y + hh > H - 4) continue;
        if(!collides(x, y, ww, hh)){
          setGrid(x, y, ww, hh, 1);
          placed2 = {x, y, size};
          break;
        }
      }
    }
    if(!placed2){
      placed2 = {x: 6 + (idx%5)*60, y: H - hh - 6, size};
      setGrid(placed2.x, placed2.y, ww, hh, 1);
    }
    placed.push({w, x: placed2.x, y: placed2.y, size});
  });
  placed.forEach(({w, x, y, size}) => {
    const el = document.createElement('span');
    el.className = 'cloud-word';
    el.textContent = w.word;
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    el.style.fontSize = size.toFixed(1) + 'px';
    const intensity = (w.count - min) / range;
    if(intensity > 0.7) el.style.color = '#fff';
    else if(intensity > 0.4) el.style.color = 'var(--primary)';
    else el.style.color = 'var(--muted)';
    el.style.opacity = (0.55 + intensity * 0.45).toFixed(2);
    el.title = `${w.word} · ${w.count} 次`;
    canvas.appendChild(el);
  });
}

render();
