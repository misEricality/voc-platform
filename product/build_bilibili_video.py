"""B 站单视频原型 HTML 组装（2026-08-21 · 简单原型 v0.1）

读 export_bilibili_data.py 导出的 JSON → 生成单文件 HTML 原型。

设计要点（"简单原型"，不堆细节）：
- 单文件自包含，数据 inline（不引外部 JSON）
- 不内嵌字体（用系统字体栈：OPPO Sans → PingFang → 微软雅黑）
- 不内嵌 logo（用纯文字 "灵听·Lynx"）
- ECharts 用 CDN 加载（<script src>）
- 区块4 重点做，其他区块基础版

区块（与设计稿对齐）：
1. 视频概览（封面 + 标题 + UP + tags + 互动统计 + 评论构成含男女比）
2. 评论情感总览（饼图 + 数字）
3. 主题 × 情感下钻（L1 堆叠条 + TOP 负面观点）
4. 弹幕时间轴热力图（B 站独有 · 密度曲线 + 模式散点云 + TOP 高亮时刻 + 词典粗匹配提示）
Footer: "打开分析看板 →"（跳 app.py）

输出：product/prototype/bilibili-video.html
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROTOTYPE_DIR = ROOT / "product" / "prototype"
OUT = PROTOTYPE_DIR / "bilibili-video.html"

ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵听 ·Lynx · B 站视频 · {title}</title>
<style>
:root {{
  --bg:#0e141b;--bg2:#101822;--panel:#1b2838;--panel2:#16202d;
  --line:#2a3f5f;--line2:#223349;
  --ink:#e7ecf1;--ink2:#c7d5e0;--muted:#8f98a0;--dim:#5c7285;
  --primary:#66c0f4;--green:#a1cd44;--red:#e05c5c;--yellow:#d9b54e;
  --warm:#f59e0b;--cool:#3b82f6;
}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:"OPPO Sans",Inter,"PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--ink);min-width:1080px;line-height:1.5}}
h1,h2,h3{{margin:0;font-weight:600}}
a{{color:var(--primary);text-decoration:none}}

.topbar{{height:60px;background:var(--panel);border-bottom:1px solid var(--line);
  display:flex;align-items:center;padding:0 28px;position:sticky;top:0;z-index:30}}
.brand{{display:flex;align-items:center;gap:12px}}
.brand h1{{font-size:18px;color:var(--ink);letter-spacing:1px}}
.brand .sub{{color:var(--muted);font-size:13px;margin-left:8px}}
.top-actions{{margin-left:auto;display:flex;align-items:center;gap:12px}}
.icon-btn{{width:36px;height:36px;border:1px solid var(--line);background:var(--panel2);
  border-radius:10px;color:var(--muted);display:grid;place-items:center;font-size:14px}}

.main{{max-width:1320px;margin:auto;padding:24px 28px 60px}}
.section{{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:22px 26px;margin-bottom:18px}}
.section h2{{font-size:17px;color:var(--ink);margin-bottom:14px;
  display:flex;align-items:center;gap:10px}}
.section h2 .pill{{font-size:11px;padding:2px 8px;border-radius:8px;
  background:var(--primary);color:#0e141b;font-weight:700}}

/* 区块 1：概览 */
.overview{{display:grid;grid-template-columns:240px 1fr;gap:24px}}
.cover{{aspect-ratio:16/9;background:linear-gradient(135deg,#1b2838,#0e141b);
  border-radius:10px;display:grid;place-items:center;font-size:60px;color:var(--dim);
  border:1px solid var(--line2)}}
.meta h3{{font-size:22px;margin-bottom:8px;line-height:1.3}}
.meta-line{{font-size:13px;color:var(--ink2);margin-bottom:12px}}
.meta-line .key{{color:var(--muted);margin-right:6px}}
.tags{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}}
.tag{{padding:3px 9px;border-radius:8px;background:var(--panel2);
  border:1px solid var(--line2);font-size:11.5px;color:var(--ink2)}}

.stat-row{{display:flex;flex-wrap:wrap;gap:18px;margin:14px 0 12px;font-size:13px;color:var(--ink2)}}
.stat-row .s{{display:flex;align-items:center;gap:6px}}
.stat-row .s strong{{color:#fff;font-weight:700;font-size:15px}}
.stat-row .ic{{color:var(--primary);font-size:15px}}
.three-rate{{font-size:12px;color:var(--muted);margin-bottom:14px}}
.three-rate b{{color:var(--green)}}

.compose{{font-size:13px;color:var(--ink2);padding:10px 14px;background:var(--panel2);
  border-radius:8px;border:1px solid var(--line2);display:flex;flex-wrap:wrap;gap:18px}}
.compose .v{{color:#fff;font-weight:700}}
.sex-bar{{display:inline-flex;height:8px;border-radius:4px;overflow:hidden;width:80px;
  margin-left:6px;vertical-align:middle}}
.sex-bar span{{display:block;height:100%}}

/* 区块 2：情感总览 */
.sent-grid{{display:grid;grid-template-columns:300px 1fr;gap:24px;align-items:center}}
.sent-chart{{height:240px}}
.sent-stats{{display:flex;flex-direction:column;gap:10px}}
.sent-stats .row{{display:flex;align-items:center;gap:10px;font-size:14px}}
.sent-stats .dot{{width:10px;height:10px;border-radius:50%}}
.sent-stats .pos{{background:var(--green)}}
.sent-stats .neu{{background:var(--yellow)}}
.sent-stats .neg{{background:var(--red)}}
.sent-stats .bar{{flex:1;height:8px;background:var(--panel2);border-radius:4px;overflow:hidden}}
.sent-stats .fill{{height:100%}}
.sent-stats .num{{font-weight:700;color:#fff;min-width:40px;text-align:right}}

/* 区块 3：主题 */
.topic-row{{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line2)}}
.topic-row:last-child{{border-bottom:none}}
.topic-row .name{{width:130px;font-size:13px;color:var(--ink2)}}
.topic-row .bar{{flex:1;height:18px;border-radius:4px;overflow:hidden;display:flex}}
.topic-row .seg{{height:100%}}
.topic-row .seg.pos{{background:var(--green)}}
.topic-row .seg.neu{{background:var(--yellow)}}
.topic-row .seg.neg{{background:var(--red)}}
.topic-row .pct{{width:60px;text-align:right;font-size:12px;color:var(--muted);font-weight:600}}
.topic-row .legend{{display:flex;gap:8px;font-size:11px;color:var(--muted);width:160px;justify-content:flex-end}}
.topic-row .legend span{{display:flex;align-items:center;gap:3px}}
.topic-row .legend .d{{width:7px;height:7px;border-radius:2px}}
.neg-list{{margin-top:14px;padding:14px 16px;background:var(--panel2);border-radius:10px;
  border:1px solid var(--line2)}}
.neg-list h4{{font-size:13px;color:var(--red);margin:0 0 8px;letter-spacing:.5px}}
.neg-list ul{{margin:0;padding:0;list-style:none;font-size:12.5px;color:var(--ink2)}}
.neg-list li{{padding:4px 0;border-bottom:1px solid var(--line2)}}
.neg-list li:last-child{{border-bottom:none}}
.neg-list .np{{color:#fff;font-weight:600;margin-right:6px}}

/* 区块 4：弹幕时间轴 */
.dm-legend{{display:flex;gap:18px;font-size:12px;color:var(--muted);margin-bottom:10px;
  flex-wrap:wrap}}
.dm-legend span{{display:flex;align-items:center;gap:5px}}
.dm-legend .d{{width:11px;height:11px;border-radius:2px}}
.dm-legend .warm{{background:var(--warm)}}
.dm-legend .cool{{background:var(--cool)}}
.dm-legend .neu{{background:var(--yellow)}}
.dm-legend .m1{{background:var(--primary)}}
.dm-legend .m4{{background:var(--green)}}
.dm-legend .m5{{background:var(--red)}}
.dm-timeline{{height:300px;margin-bottom:12px}}
.dm-scatter{{height:140px;margin-bottom:14px}}
.top-moments{{background:var(--panel2);border-radius:10px;border:1px solid var(--line2);
  padding:14px 16px;font-size:12.5px}}
.top-moments h4{{margin:0 0 10px;font-size:13px;color:var(--primary);letter-spacing:.5px}}
.top-moments ol{{margin:0;padding:0 0 0 22px;columns:2;column-gap:24px}}
.top-moments li{{margin-bottom:6px;break-inside:avoid;color:var(--ink2)}}
.top-moments li b{{color:var(--warm);font-family:monospace}}
.top-moments .sm{{color:var(--muted);font-size:11.5px;margin-left:6px}}
.dm-warn{{margin-top:14px;padding:8px 12px;font-size:11.5px;color:var(--yellow);
  background:rgba(217,181,78,.08);border:1px solid rgba(217,181,78,.3);border-radius:6px}}

/* Footer */
.footer{{margin-top:30px;padding:20px;text-align:center;color:var(--dim);font-size:12px;
  border-top:1px solid var(--line2)}}
.footer a{{display:inline-flex;align-items:center;gap:6px;margin-top:8px;padding:8px 20px;
  background:var(--primary);color:#0e141b;border-radius:8px;font-weight:700;font-size:13px}}
.footer a:hover{{background:#8bd0f8}}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <span style="font-size:24px">🎙️</span>
    <h1>灵听·Lynx</h1>
    <span class="sub">B 站视频看板 · v0.1</span>
  </div>
  <div class="top-actions">
    <div class="icon-btn" title="对比模式（v0.2 规划）">⇄</div>
    <div class="icon-btn" title="数据导出">⤓</div>
  </div>
</div>

<div class="main">

  <!-- 区块 1 · 视频概览 -->
  <div class="section">
    <h2>① 视频概览 <span class="pill">{bvid}</span></h2>
    <div class="overview">
      <div class="cover">▶</div>
      <div class="meta">
        <h3>{title}</h3>
        <div class="meta-line">
          <span class="key">UP 主:</span><b>{owner_name}</b>
          <span class="key" style="margin-left:14px">发布:</span>{pubdate}
        </div>
        <div class="tags">{tags_html}</div>
        <div class="stat-row">
          <div class="s"><span class="ic">▶</span><strong>{view}</strong><span>播放</span></div>
          <div class="s"><span class="ic">💬</span><strong>{reply}</strong><span>评论</span></div>
          <div class="s"><span class="ic">▶</span><strong>{danmaku}</strong><span>弹幕</span></div>
          <div class="s"><span class="ic">❤</span><strong>{like}</strong><span>点赞</span></div>
          <div class="s"><span class="ic">⭐</span><strong>{fav}</strong><span>收藏</span></div>
          <div class="s"><span class="ic">🪙</span><strong>{coin}</strong><span>投币</span></div>
          <div class="s"><span class="ic">↗</span><strong>{share}</strong><span>分享</span></div>
        </div>
        <div class="three-rate">三连率 (like + coin + favorite) / view = <b>{three_rate}</b></div>
        <div class="compose">
          <span>评论构成：评论 <span class="v">{total_c}</span> 条 · 已分析 <span class="v">{analyzed_c}</span> ({analyzed_pct}%)</span>
          <span>男女比：
            <span class="sex-bar">
              <span style="background:#66c0f4;width:{sex_male}%"></span>
              <span style="background:#f59e0b;width:{sex_female}%"></span>
              <span style="background:#5c7285;width:{sex_unknown}%"></span>
            </span>
            <span class="v">{sex_male}%</span> 男 / <span class="v">{sex_female}%</span> 女 / <span class="v">{sex_unknown}%</span> 保密
          </span>
        </div>
      </div>
    </div>
  </div>

  <!-- 区块 2 · 评论情感总览 -->
  <div class="section">
    <h2>② 评论情感总览</h2>
    <div class="sent-grid">
      <div class="sent-chart" id="sentChart"></div>
      <div class="sent-stats">
        <div class="row"><span class="dot pos"></span><span style="width:60px">👍 正面</span><div class="bar"><div class="fill" style="background:var(--green);width:{pos_pct}%"></div></div><span class="num">{pos}</span><span style="color:var(--muted);width:50px;text-align:right">({pos_pct}%)</span></div>
        <div class="row"><span class="dot neu"></span><span style="width:60px">😐 中性</span><div class="bar"><div class="fill" style="background:var(--yellow);width:{neu_pct}%"></div></div><span class="num">{neu}</span><span style="color:var(--muted);width:50px;text-align:right">({neu_pct}%)</span></div>
        <div class="row"><span class="dot neg"></span><span style="width:60px">👎 负面</span><div class="bar"><div class="fill" style="background:var(--red);width:{neg_pct}%"></div></div><span class="num">{neg}</span><span style="color:var(--muted);width:50px;text-align:right">({neg_pct}%)</span></div>
        <div style="margin-top:8px;padding-top:12px;border-top:1px solid var(--line2);font-size:13px;color:var(--ink2)">
          情感均分 <b style="color:#fff">{avg_score}</b> （核心观点 · -1~+1） · 平均置信度 <b style="color:#fff">{avg_conf}</b>
        </div>
      </div>
    </div>
  </div>

  <!-- 区块 3 · 主题 × 情感下钻 -->
  <div class="section">
    <h2>③ L1 主题 × 情感下钻</h2>
    {topics_html}
    <div class="neg-list">
      <h4>🔍 TOP 负面观点（具体维度，排除综合与元表达兜底）</h4>
      <ul>{neg_opinions_html}</ul>
    </div>
  </div>

  <!-- 区块 4 · 弹幕时间轴热力图（B 站独有） -->
  <div class="section">
    <h2>④ 弹幕时间轴 <span class="pill" style="background:var(--warm)">B 站独有</span></h2>
    <div class="dm-legend">
      <span><span class="d warm"></span>暖（激动）</span>
      <span><span class="d neu"></span>中性</span>
      <span><span class="d cool"></span>冷（吐槽）</span>
      <span style="margin-left:24px"><span class="d m1"></span>① 滚动</span>
      <span><span class="d m4"></span>④ 底部</span>
      <span><span class="d m5"></span>⑤ 顶部</span>
      <span style="margin-left:auto;color:var(--dim)">共 {dm_total} 条弹幕 · 视频时长 {dm_duration}s</span>
    </div>
    <div class="dm-timeline" id="dmTimeline"></div>
    <div class="dm-scatter" id="dmScatter"></div>
    <div class="top-moments">
      <h4>🔥 TOP 弹幕高亮时刻（按桶内密度排序）</h4>
      <ol>{top_moments_html}</ol>
    </div>
    <div class="dm-warn">
      ⚠️ 弹幕情绪 = 词典匹配 + 颜色信号 · 仅作情绪分布参考（成本红线：弹幕不进 LLM 精标链路）
    </div>
  </div>

</div>

<div class="footer">
  数据截至 {fetched_at} · 简单原型 v0.1（区块 4 重点验证）<br>
  <a href="http://localhost:8501" target="_blank">打开分析看板 →</a>
</div>

<script src="{echarts_cdn}"></script>
<script>
const DATA = {data_json};

// ===== 区块 2 饼图 =====
(function() {{
  const d = DATA.comments.sentiment_dist;
  echarts.init(document.getElementById('sentChart')).setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'item',formatter:'{{b}}: {{c}} ({{d}}%)'}},
    legend:{{show:false}},
    series:[{{
      type:'pie',radius:['55%','78%'],center:['50%','55%'],
      avoidLabelOverlap:false,label:{{show:false}},labelLine:{{show:false}},
      itemStyle:{{borderColor:'#0e141b',borderWidth:2}},
      data:[
        {{name:'👍 正面',value:d.positive,itemStyle:{{color:'#a1cd44'}}}},
        {{name:'😐 中性',value:d.neutral,itemStyle:{{color:'#d9b54e'}}}},
        {{name:'👎 负面',value:d.negative,itemStyle:{{color:'#e05c5c'}}}}
      ]
    }}]
  }});
}})();

// ===== 区块 4 主图：密度曲线 + 情绪色分段 =====
(function() {{
  const buckets = DATA.danmaku.buckets;
  const warm = buckets.map(b => [b.start_s, b.warm]);
  const neutral = buckets.map(b => [b.start_s, b.neutral_emo]);
  const cool = buckets.map(b => [b.start_s, b.cool]);
  // TOP 5 时刻标点
  const top = DATA.danmaku.top_moments.slice(0, 5).map(m => ({{
    name: m.start_s + 's · ' + m.count + '条',
    coord: [m.start_s + (DATA.danmaku.bucket_size_s/2),
      (buckets.find(b => b.start_s === m.start_s) || {{}}).count || 0],
    symbolSize: 14, itemStyle:{{color:'#f59e0b'}}
  }}));
  echarts.init(document.getElementById('dmTimeline')).setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis',axisPointer:{{type:'cross'}}}},
    legend:{{textStyle:{{color:'#c7d5e0'}},top:0,right:10}},
    grid:{{left:50,right:24,top:36,bottom:36}},
    xAxis:{{type:'value',name:'progress (s)',nameTextStyle:{{color:'#8f98a0'}},
      axisLabel:{{color:'#8f98a0'}},axisLine:{{lineStyle:{{color:'#2a3f5f'}}}},
      splitLine:{{lineStyle:{{color:'#223349'}}}}}},
    yAxis:{{type:'value',name:'条数',nameTextStyle:{{color:'#8f98a0'}},
      axisLabel:{{color:'#8f98a0'}},axisLine:{{lineStyle:{{color:'#2a3f5f'}}}},
      splitLine:{{lineStyle:{{color:'#223349'}}}}}},
    series:[
      {{name:'暖(激动)',type:'line',smooth:true,stack:'emo',areaStyle:{{opacity:.7}},
        lineStyle:{{color:'#f59e0b'}},itemStyle:{{color:'#f59e0b'}},data:warm,
        markPoint:{{data:top,symbol:'pin',label:{{formatter:'\n{{b}}',color:'#fff',fontSize:10}}}}}},
      {{name:'中性',type:'line',smooth:true,stack:'emo',areaStyle:{{opacity:.6}},
        lineStyle:{{color:'#d9b54e'}},itemStyle:{{color:'#d9b54e'}},data:neutral}},
      {{name:'冷(吐槽)',type:'line',smooth:true,stack:'emo',areaStyle:{{opacity:.7}},
        lineStyle:{{color:'#3b82f6'}},itemStyle:{{color:'#3b82f6'}},data:cool}}
    ]
  }});
}})();

// ===== 区块 4 辅图：模式散点云（按 mode 分层）=====
(function() {{
  const sc = DATA.danmaku.scatter_sample;
  const byMode = {{}};
  sc.forEach(s => {{ const k = s.mode; (byMode[k] = byMode[k] || []).push(s); }});
  const seriesNames = {{1:'① 滚动',4:'④ 底部',5:'⑤ 顶部',7:'⑦ 高级'}};
  const colors = {{1:'#66c0f4',4:'#a1cd44',5:'#e05c5c',7:'#d9b54e'}};
  const series = Object.keys(byMode).sort().map(m => ({{
    name: seriesNames[m] || ('mode ' + m),
    type:'scatter',symbolSize:4,
    data: byMode[m].map(s => [s.progress_s, 0.5 + Math.random() * 0.4,
      s.content, s.color_int]),
    itemStyle:{{color:colors[m] || '#8f98a0',opacity:0.7}}
  }}));
  echarts.init(document.getElementById('dmScatter')).setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'item',formatter:p => `${{p.value[2]}} · ${{(p.value[0]/60).toFixed(1)}}min · #${{p.value[3]}}`}},
    legend:{{textStyle:{{color:'#c7d5e0'}},top:0}},
    grid:{{left:50,right:24,top:30,bottom:36}},
    xAxis:{{type:'value',name:'progress (s)',nameTextStyle:{{color:'#8f98a0'}},
      axisLabel:{{color:'#8f98a0'}},axisLine:{{lineStyle:{{color:'#2a3f5f'}}}},
      splitLine:{{lineStyle:{{color:'#223349'}}}}}},
    yAxis:{{type:'value',min:0,max:1,show:false}},
    series: series
  }});
}})();
</script>
</body>
</html>
"""


def build(bvid: str, json_path: Path, out_path: Path = OUT) -> int:
    """组装单文件 HTML 原型

    Returns:
        输出文件字节数
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    v = data["video"]
    c = data["comments"]
    p = data["profile"]
    dm = data["danmaku"]
    stat = v.get("stat", {})

    # 互动统计千分位格式化
    def fmt(n):
        if n is None:
            return "—"
        return f"{n:,}"

    # 三连率
    like, coin, fav = stat.get("like", 0), stat.get("coin", 0), stat.get("favorite", 0)
    view = stat.get("view", 0)
    three_rate = f"{(like + coin + fav) / view * 100:.1f}%" if view else "—"

    # 性别分布
    sex = p.get("sex_dist_pct", {})
    sex_male = sex.get("男", 0)
    sex_female = sex.get("女", 0)
    sex_unknown = sex.get("保密", 0)

    # tags HTML
    tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in (v.get("tags") or []))

    # L1 主题堆叠条 HTML
    topics_html = ""
    for t in c.get("topics_l1", []):
        total = t["total"]
        if total == 0:
            continue
        p_pos = t["positive"] / total * 100
        p_neu = t["neutral"] / total * 100
        p_neg = t["negative"] / total * 100
        topics_html += (
            f'<div class="topic-row">'
            f'<span class="name">{t["name"]}</span>'
            f'<div class="bar">'
            f'<div class="seg pos" style="width:{p_pos:.1f}%"></div>'
            f'<div class="seg neu" style="width:{p_neu:.1f}%"></div>'
            f'<div class="seg neg" style="width:{p_neg:.1f}%"></div>'
            f'</div>'
            f'<span class="legend">'
            f'<span><span class="d" style="background:var(--green)"></span>{t["positive"]}</span>'
            f'<span><span class="d" style="background:var(--yellow)"></span>{t["neutral"]}</span>'
            f'<span><span class="d" style="background:var(--red)"></span>{t["negative"]}</span>'
            f'</span>'
            f'<span class="pct">{t["pct"]}%</span>'
            f'</div>'
        )

    # 负面观点列表 HTML
    neg_html = ""
    for op in c.get("top_negative_opinions", []):
        neg_html += (
            f'<li>'
            f'<span class="np">{op["l2"]}</span>'
            f'<i style="color:var(--muted);font-style:normal">×{op["count"]}</i> · '
            f'"{op["phrase"]}"'
            f'</li>'
        )
    if not neg_html:
        neg_html = '<li style="color:var(--muted)">无具体负面观点（评论偏整体褒贬）</li>'

    # 弹幕 TOP 高亮时刻 HTML
    tm_html = ""
    for m in dm.get("top_moments", [])[:10]:
        s = m["start_s"]
        samples = " · ".join(f'"{x}"' for x in m["samples"][:3])
        tm_html += (
            f'<li>'
            f'<b>▶{s}s</b>'
            f'<span class="sm">({m["count"]}条)</span>'
            f' {samples}'
            f'</li>'
        )

    html = HTML_TEMPLATE.format(
        # 视频
        title=v.get("title", "?"),
        bvid=bvid,
        owner_name=v.get("owner_name", "?"),
        pubdate=(v.get("pubdate_iso") or "—")[:10],
        tags_html=tags_html or '<span class="tag" style="color:var(--dim)">(无标签)</span>',
        view=fmt(stat.get("view")),
        reply=fmt(stat.get("reply")),
        danmaku=fmt(stat.get("danmaku")),
        like=fmt(stat.get("like")),
        fav=fmt(stat.get("favorite")),
        coin=fmt(stat.get("coin")),
        share=fmt(stat.get("share")),
        three_rate=three_rate,
        # 评论构成
        total_c=c["total"],
        analyzed_c=c["analyzed"],
        analyzed_pct=c["analyzed_rate"],
        sex_male=sex_male, sex_female=sex_female, sex_unknown=sex_unknown,
        # 情感
        pos=c["sentiment_dist"]["positive"],
        neu=c["sentiment_dist"]["neutral"],
        neg=c["sentiment_dist"]["negative"],
        pos_pct=c["sentiment_dist_pct"]["positive"],
        neu_pct=c["sentiment_dist_pct"]["neutral"],
        neg_pct=c["sentiment_dist_pct"]["negative"],
        avg_score=c.get("avg_score") or "—",
        avg_conf=c.get("avg_confidence") or "—",
        # 主题 / 负面观点
        topics_html=topics_html,
        neg_opinions_html=neg_html,
        # 弹幕
        dm_total=fmt(dm["total"]),
        dm_duration=fmt(dm["duration_s"]),
        top_moments_html=tm_html,
        # ECharts CDN
        echarts_cdn=ECHARTS_CDN,
        # 数据 inline
        data_json=json.dumps(data, ensure_ascii=False),
        fetched_at="2026-08-21",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    size = out_path.stat().st_size
    print(f"已生成 {out_path} ({size:,} bytes / {size/1024:.1f} KB)")
    return size


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--bvid", default="BV1kS8H6VERt")
    parser.add_argument("--json", default=str(ROOT / "data" / "_bili_video.json"))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    build(args.bvid, Path(args.json), Path(args.out))