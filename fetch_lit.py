#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIDD / CADD 文献订阅抓取器
数据源: Europe PMC (覆盖 PubMed + 预印本, 含摘要/DOI/引用数)
输出:   dashboard/index.html (自包含网页看板) + reports/YYYY-MM-DD.md + outbox/email_digest.md
用法:   python fetch_lit.py [--days 3] [--verify] [--no-html]
"""
import json
import os
import re
import sys
import time
import html
import argparse
import datetime
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
DASH_DIR = os.path.join(BASE_DIR, "dashboard")
OUTBOX_DIR = os.path.join(BASE_DIR, "outbox")
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")
DB_FILE = os.path.join(DATA_DIR, "papers.json")
HTML_FILE = os.path.join(DASH_DIR, "index.html")

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# 看板注入的摘要预览长度; 超出部分在用户点"展开全文"时按需在线拉取
ABS_PREVIEW = 700

# ---------------------------------------------------------------- 期刊刊单
# (显示简称, [Europe PMC JOURNAL 候选名], [ISSN 候选], 是否核心刊, 保底窗口天数)
# 核心刊: 全量收录不过滤; 综合刊: 必须命中 >=2 个 AIDD/CADD 关键词才收录
# 保底窗口: 该刊出刊频率低, 若按常规窗口检索为 0 篇, 自动扩大到该天数重试。
#           这是"保证每刊都能取到文献"的关键——季刊/月刊在 3 天窗口内必然为 0。
#
# 自适应扩窗阶梯: 保底窗口仍未命中时, 按此阶梯逐级放大直到命中。
# 存在必要性——保底值是按"出刊频率"估的, 但 Europe PMC 的入库节奏与出刊频率
# 并不一致: 有的刊按批入库(实测 JMC 约 15 天一批、单次数十篇), 3 天窗口恒为 0,
# 7 天保底同样够不着, 会被整个漏掉。阶梯兜底可保证任何入库节奏的刊都不落空。
FALLBACK_LADDER = (7, 14, 30, 60, 90, 180, 365)
JOURNALS = [
    # ===== 药物化学 & 分子模拟 核心刊 =====
    # JMC 在 Europe PMC 按批入库(实测约 16-18 天一批, 单批数十篇), 保底需覆盖该周期,
    # 否则 3 天窗口恒为 0、7 天保底又够不着, 会被整个漏掉。
    ("J Med Chem", ["Journal of medicinal chemistry"], ["0022-2623"], True, 21),
    ("J Chem Inf Model", ["Journal of chemical information and modeling"], ["1549-9596"], True, 7),
    ("Eur J Med Chem", ["European journal of medicinal chemistry"], ["0223-5234"], True, 7),
    ("J Chem Theory Comput", ["Journal of chemical theory and computation"], ["1549-9618"], True, 7),
    ("J Comput Aided Mol Des", ["Journal of computer-aided molecular design"], ["1573-4951"], True, 30),
    ("J Cheminform", ["Journal of cheminformatics", "J Cheminform"], ["1758-2946"], True, 60),
    ("Mol Inform", ["Molecular informatics"], ["1868-1751"], True, 60),
    ("ChemMedChem", ["ChemMedChem"], ["1860-7187"], True, 14),
    ("RSC Med Chem", ["RSC medicinal chemistry"], ["2632-8682"], True, 21),
    ("Digital Discovery", ["Digital discovery"], ["2635-098X"], True, 120),
    # ===== AI / 计算方法 =====
    ("Nat Mach Intell", ["Nature machine intelligence"], ["2522-5839"], True, 150),
    ("Nat Comput Sci", ["Nature computational science"], ["2662-8457"], True, 30),
    ("Mach Learn Sci Technol", ["Machine learning: science and technology"], ["2632-2153"], True, 180),
    ("Patterns", ["Patterns (New York, N.Y.)", "Patterns"], ["2666-3899"], True, 45),
    ("Chem Sci", ["Chemical science"], ["2041-6539"], False, 7),
    # ===== 生物信息 / 药物发现 =====
    ("Bioinformatics", ["Bioinformatics"], ["1367-4811"], True, 7),
    ("Brief Bioinform", ["Briefings in bioinformatics"], ["1477-4054"], True, 14),
    ("BMC Bioinformatics", ["BMC bioinformatics"], ["1471-2105"], True, 60),
    ("Bioinform Adv", ["Bioinformatics advances"], ["2635-0041"], True, 21),
    ("Comput Struct Biotechnol J", ["Computational and structural biotechnology journal"], ["2001-0370"], True, 14),
    ("Drug Discov Today", ["Drug discovery today"], ["1878-5832"], True, 14),
    ("Expert Opin Drug Discov", ["Expert opinion on drug discovery"], ["1746-045X"], True, 45),
    ("Future Med Chem", ["Future medicinal chemistry"], ["1756-8927"], True, 30),
    ("Comput Biol Chem", ["Computational biology and chemistry"], ["1476-9271"], True, 14),
    # ===== 分子模拟 / 药物设计工具刊 =====
    ("J Mol Graph Model", ["Journal of molecular graphics & modelling"], ["1093-3263"], True, 14),
    ("Chem Biol Drug Des", ["Chemical biology & drug design"], ["1747-0277"], True, 30),
    ("SAR QSAR Environ Res", ["SAR and QSAR in environmental research"], ["1062-936X"], True, 180),
    # ===== 综合刊 (严格关键词过滤) =====
    ("Nat Commun", ["Nature communications"], ["2041-1723"], False, 7),
    ("Nat Biotechnol", ["Nature biotechnology"], ["1546-1696"], False, 14),
    ("Nat Methods", ["Nature methods"], ["1548-7105"], False, 14),
    ("Sci Adv", ["Science advances"], ["2375-2548"], False, 7),
    ("PNAS", ["Proceedings of the National Academy of Sciences of the United States of America"],
     ["0027-8424"], False, 7),
    ("JACS", ["Journal of the American Chemical Society"], ["1520-5126"], False, 7),
    ("Angew Chem", ["Angewandte Chemie (International ed. in English)"], ["1521-3773"], False, 7),
    ("ACS Cent Sci", ["ACS central science"], ["2374-7951"], False, 21),
    ("J Phys Chem B", ["The journal of physical chemistry. B"], ["1520-5207"], False, 7),
    # WIREs 是综述刊, Europe PMC 收录极少(近半年仅 1 篇), 只能用超长窗口兜底
    ("WIREs Comput Mol Sci", ["Wiley interdisciplinary reviews. Computational molecular science"],
     ["1759-0884", "1759-0876"], False, 365),
    ("J Chem Phys", ["The Journal of chemical physics"], ["1089-7690"], False, 7),
]

# ---------------------------------------------------------------- 主题关键词
TOPICS = {
    "生成式与从头设计": [
        "de novo design", "generative model", "generative chemistry", "molecular generation",
        "diffusion model", "scaffold hopping", "linker design", "fragment growing",
        "variational autoencoder", "reinforcement learning", "molecular optimization",
        "lead optimization", "hit-to-lead", "structure-based drug design", "ligand-based drug design",
    ],
    "结构与蛋白设计": [
        "alphafold", "protein structure prediction", "protein design", "antibody design",
        "peptide design", "co-folding", "esmfold", "rosettafold", "binding site prediction",
        "protein-ligand complex", "protac", "molecular glue", "degrader",
    ],
    "虚拟筛选与对接": [
        "docking", "virtual screening", "pharmacophore", "scoring function", "pose prediction",
        "high-throughput screening", "hit identification", "dna-encoded library", "del ",
        "ultra-large library", "screening cascade",
    ],
    "自由能与动力学": [
        "free energy perturbation", "fep", "mm/pbsa", "mm-gbsa", "binding free energy",
        "molecular dynamics", "enhanced sampling", "metadynamics", "umbrella sampling",
        "alchemical", "coarse-grained", "markov state model", "neural network potential",
        "machine learning force field", "ml potential", "md simulation",
    ],
    "性质预测与ADMET": [
        "qsar", "qspr", "admet", "adme", "property prediction", "solubility prediction",
        "toxicity prediction", "binding affinity prediction", "drug-target interaction",
        "bioactivity prediction", "permeability", "metabolic stability", "pka prediction",
        "logp", "selectivity prediction",
    ],
    "机器学习方法": [
        "deep learning", "machine learning", "graph neural network", "message passing",
        "transformer", "large language model", "llm", "foundation model", "attention mechanism",
        "equivariant", "self-supervised", "contrastive learning", "active learning",
        "transfer learning", "few-shot", "bayesian optimization", "molecular representation",
        "artificial intelligence", "neural network", "benchmark", "interpretability",
        "federated learning", "multimodal",
    ],
    "合成规划与逆合成": [
        "retrosynthesis", "retrosynthetic", "synthesis planning", "reaction prediction",
        "reaction yield", "synthetic accessibility", "automated synthesis", "condition recommendation",
    ],
    "数据与工具": [
        "database", "web server", "software", "toolkit", "benchmark dataset", "open source",
        "python package", "pipeline", "workflow", "platform", "cheminformatics",
        "chemical space", "molecular descriptor", "fingerprint",
    ],
}

# 综合刊的准入门槛: 命中任一关键词即收录
FILTER_TERMS = sorted({t for v in TOPICS.values() for t in v} | {
    "computer-aided drug design", "cadd", "aidd", "in silico drug", "drug discovery",
    "cheminformatics", "chemoinformatics", "molecular dynamics simulation",
})


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def epmc_query(terms, date_from, date_to, cursor="*"):
    """terms: ['JOURNAL:"x"', 'ISSN:"y"', ...] 以 OR 组合"""
    q = '(%s) AND (FIRST_IDATE:[%s TO %s])' % (" OR ".join(terms), date_from, date_to)
    params = {
        "query": q,
        "format": "json",
        "resultType": "core",
        "pageSize": "1000",
        "cursorMark": cursor,
        "sort": "P_PDATE_D desc",
    }
    url = EPMC + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "AIDD-Lit-Subscription/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log("  请求失败(%d/3): %s" % (attempt + 1, e))
            time.sleep(3)
    return None


def clean(txt):
    if not txt:
        return ""
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt).replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", txt).strip()


def journal_of(rec, fallback):
    for key in ("journalInfo", "journal"):
        ji = rec.get(key) or {}
        if isinstance(ji, dict):
            j = ji.get("journal") or ji.get("title") or ji.get("medlineAbbreviation")
            if j:
                return j
    return rec.get("journalTitle") or fallback


def match_topics(text):
    """按命中数排序, 只保留最相关的 3 个主题, 避免标签过于发散"""
    hits, scored = [], []
    for topic, terms in TOPICS.items():
        n = sum(1 for t in terms if t in text)
        if n:
            scored.append((n, topic))
            hits.extend([t for t in terms if t in text])
    scored.sort(key=lambda x: (-x[0], x[1]))
    tags = [t for _, t in scored[:3]]
    return tags, hits


def norm_record(rec, fallback_journal, is_core):
    title = clean(rec.get("title", ""))
    abstract = clean(rec.get("abstractText", ""))
    text = (title + " " + abstract).lower()
    tags, hits = match_topics(text)

    # 综合刊门槛: 至少命中 2 个不同关键词, 避免被无关文章淹没
    if not is_core and len(set(hits)) < 2:
        return None

    doi = (rec.get("doi") or "").strip()
    pmid = (rec.get("pmid") or "").strip()
    pmcid = (rec.get("pmcid") or "").strip()
    key = doi.lower() or (pmid and ("pmid:" + pmid)) or title[:120].lower()
    if not key:
        return None

    if doi:
        link = "https://doi.org/" + doi
    elif pmid:
        link = "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid
    else:
        link = rec.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "") or \
            "https://europepmc.org/search?query=%s" % urllib.parse.quote(title[:80])

    authors = rec.get("authorString") or ""
    authors = authors[:220]
    pub_types = rec.get("pubTypeList", {}).get("pubType", []) or []
    if isinstance(pub_types, str):
        pub_types = [pub_types]

    jname = journal_of(rec, fallback_journal)
    score = len(hits) + (3 if is_core else 0)
    if any(p.lower() in ("review", "journal article") for p in pub_types):
        score += 1 if "review" in " ".join(pub_types).lower() else 0

    return {
        "key": key,
        "title": title,
        "abstract": abstract,
        "journal_disp": fallback_journal,
        "journal_raw": jname,
        "authors": authors,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "link": link,
        "date": rec.get("firstPublicationDate") or rec.get("firstIndexDate") or "",
        "index_date": rec.get("firstIndexDate") or "",
        "tags": tags,
        "hits": sorted(set(hits))[:8],
        "is_review": "review" in " ".join(pub_types).lower(),
        "is_preprint": (rec.get("source") == "PPR") or bool(rec.get("preprintServer")),
        "cited": rec.get("citedByCount", 0) or 0,
        "score": score,
        "core": is_core,
        "added_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def epmc_raw(query, page_size=1):
    """不带日期限制的原始查询, 用于探测期刊元数据"""
    params = {"query": query, "format": "json", "resultType": "core", "pageSize": str(page_size)}
    url = EPMC + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "AIDD-Lit-Subscription/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log("  探测失败: %s" % e)
        return None


def probe_journals():
    """探测每种期刊在 Europe PMC 中的真实 title / issn / medlineAbbreviation,
    存为 data/journal_meta.json, 供浏览器端在线同步时精确归刊"""
    meta = {}
    for d, nm, issns, core, _min in JOURNALS:
        terms = ['JOURNAL:"%s"' % n for n in nm] + ['ISSN:"%s"' % i for i in issns]
        data = epmc_raw(" OR ".join(terms), 1)
        recs = ((data or {}).get("resultList") or {}).get("result", []) or []
        ji = ((recs[0].get("journalInfo") if recs else None) or {}).get("journal") or {}
        meta[d] = {
            "title": ji.get("title", ""),
            "issn": ji.get("issn", ""),
            "abbr": ji.get("medlineAbbreviation", ""),
            "core": core,
            "min_days": _min,          # 浏览器端在线同步用的保底窗口
            "terms": terms,
        }
        log("  %-28s title=%r issn=%r abbr=%r (保底 %d 天)"
            % (d, ji.get("title"), ji.get("issn"), ji.get("medlineAbbreviation"), _min))
        time.sleep(0.2)
    path = os.path.join(DATA_DIR, "journal_meta.json")
    json.dump(meta, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("期刊元数据已保存: %s" % path)
    return meta


def fetch_journal(disp, names, issns, date_from, date_to, min_days=0):
    """按刊单独检索(带分页), 返回该刊全部记录; 失败返回 None

    自适应阶梯扩窗: 常规窗口为 0 篇时, 逐级放大窗口重试直到命中。

    这是"保证每刊不落空"的关键。各刊在 Europe PMC 的入库节奏差异极大:
    有的每天入库(如 EJMC), 有的按批入库(如 JMC 约 15 天一批, 单次入库数十篇)。
    单一固定保底窗口无法同时适配——保底设小了, 批量入库的刊会长期为 0;
    保底设大了, 又会一次性拖入过多旧文。故改为阶梯: 先试该刊的经验保底天数
    (min_days), 未命中再逐级放大, 首次命中即停, 兼顾覆盖与时效。"""
    terms = ['JOURNAL:"%s"' % n for n in names] + ['ISSN:"%s"' % i for i in issns]

    def _run(d_from):
        out, cursor = [], "*"
        for _ in range(4):  # 最多 4 页 x 1000 条
            data = epmc_query(terms, d_from, date_to, cursor)
            if not data:
                return None if not out else out
            recs = (data.get("resultList") or {}).get("result", []) or []
            out.extend(recs)
            nxt = data.get("nextCursorMark")
            if len(recs) < 1000 or not nxt or nxt == cursor:
                break
            cursor = nxt
            time.sleep(0.3)
        return out

    recs = _run(date_from)
    if recs:
        return recs

    if not (date_to and date_from):
        return recs

    try:
        d0 = datetime.date.fromisoformat(date_to)
        span0 = max((d0 - datetime.date.fromisoformat(date_from)).days, 0)
    except Exception:
        return recs

    # 经验保底优先(命中率最高), 其后按阶梯逐级放大
    cands = []
    if min_days and min_days > span0:
        cands.append(min_days)
    cands.extend(s for s in FALLBACK_LADDER if s > span0 and s != min_days)

    for span in cands:
        log("  %-28s 常规窗口 0 篇 -> 扩窗到 %d 天重试" % (disp, span))
        recs = _run((d0 - datetime.timedelta(days=span)).isoformat())
        if recs:
            break
        time.sleep(0.2)

    if not recs:
        # 阶梯走完仍为空: 多半是检索式失效(JOURNAL/ISSN 名变更), 而非该刊真的没发文
        log("  %-28s !! 扩窗至 %d 天仍 0 篇, 请核对检索式: %s"
            % (disp, cands[-1] if cands else span0, " | ".join(names + issns)))
    return recs


def fetch(days, verify_only=False):
    today = datetime.date.today()
    d_from = today - datetime.timedelta(days=days)
    s_from, s_to = d_from.isoformat(), today.isoformat()
    log("检索窗口: %s ~ %s (近 %d 天)" % (s_from, s_to, days))

    if verify_only:
        log("逐刊校验 %d 种期刊近 %d 天命中(0 篇自动按保底窗口扩窗):" % (len(JOURNALS), days))
        hits = []
        for d, nm, issns, core, mind in JOURNALS:
            recs = fetch_journal(d, nm, issns, s_from, s_to, mind)
            n = len(recs) if recs is not None else -1
            hits.append((d, n, core))
            log("  [%s] %-28s %-5s (保底 %d 天)" % ("核心" if core else "过滤", d,
                                                n if n >= 0 else "失败", mind))
            time.sleep(0.25)
        bad = [h for h in hits if h[1] <= 0]
        if bad:
            log("!! 扩窗后仍无命中(该刊确无更新或检索式失效): %s" % ", ".join(b[0] for b in bad))
        else:
            log("全部 %d 种期刊均有文献命中" % len(hits))
        return hits

    results, seen_keys = [], set()
    for d, nm, issns, core, mind in JOURNALS:
        recs = fetch_journal(d, nm, issns, s_from, s_to, mind)
        if recs is None:
            log("  %-28s 请求失败" % d)
            continue
        got = 0
        for rec in recs:
            item = norm_record(rec, d, core)
            if item and item["key"] not in seen_keys:
                seen_keys.add(item["key"])
                results.append(item)
                got += 1
        log("  %-28s 原始 %d -> 收录 %d" % (d, len(recs), got))
        time.sleep(0.25)
    return results


# ---------------------------------------------------------------- 输出
def refresh_tags(db):
    """按当前关键词表重算全部条目的主题标签(最多 3 个)"""
    for p in db:
        tags, hits = match_topics(((p.get("title") or "") + " " + (p.get("abstract") or "")).lower())
        p["tags"], p["hits"] = tags, sorted(set(hits))[:8]
    return db


def build_html(db, new_keys):
    refresh_tags(db)  # 保证改了关键词后 --rebuild 也能刷新标签
    # 看板默认按发表日期倒序(最新在前); 少数记录缺 firstPublicationDate, 退回入库日期
    db_sorted = sorted(db, key=lambda x: (x.get("date") or x.get("index_date") or "", x.get("score", 0)), reverse=True)
    # 看板只注入摘要预览(前 ABS_PREVIEW 字符), 完整摘要在用户点"展开全文"时
    # 按需从 Europe PMC 拉取。3641 篇全量摘要约 5MB, 会让网页加载过慢。
    slim = []
    for p in db_sorted:
        q = dict(p)
        ab = q.get("abstract") or ""
        if len(ab) > ABS_PREVIEW:
            q["abstract"] = ab[:ABS_PREVIEW].rsplit(" ", 1)[0] + " …"
            q["abs_cut"] = 1
        slim.append(q)
    payload = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(db_sorted),
        "new_keys": list(new_keys),
        "papers": slim,
    }
    data_js = json.dumps(payload, ensure_ascii=False)
    all_topics = list(TOPICS.keys())
    journal_list = sorted({p["journal_disp"] for p in db_sorted})

    tpl = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIDD / CADD 文献订阅看板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:#f5f6f8;color:#1c2024;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
header{background:linear-gradient(120deg,#1e3a5f,#2d6a8f);color:#fff;border-radius:14px;padding:26px 28px;margin-bottom:20px}
header h1{font-size:22px;font-weight:650;letter-spacing:.3px}
header p{opacity:.85;font-size:13px;margin-top:6px}
.syncbar{margin-top:14px;display:flex;flex-wrap:wrap;gap:11px;align-items:center;font-size:12.5px;opacity:.96}
#syncbtn{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.42);color:#fff;padding:6px 15px;border-radius:8px;cursor:pointer;font-size:12.5px;font-family:inherit}
#syncbtn:hover{background:rgba(255,255,255,.3)}
#syncbtn:disabled{opacity:.55;cursor:default}
.syncbar label{opacity:.88;cursor:pointer;user-select:none}
.syncbar input[type=checkbox]{margin-right:4px;vertical-align:-1px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 22px}
.stat{background:#fff;border:1px solid #e3e6ea;border-radius:11px;padding:14px 16px}
.stat b{display:block;font-size:24px;color:#1e3a5f;font-weight:650}
.stat span{font-size:12px;color:#6b7280}
.controls{background:#fff;border:1px solid #e3e6ea;border-radius:11px;padding:14px 16px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
input[type=text],select{border:1px solid #d5d9df;border-radius:8px;padding:8px 11px;font-size:13px;outline:none;background:#fff;color:#1c2024;font-family:inherit}
input[type=text]{flex:1;min-width:200px}
input[type=text]:focus,select:focus{border-color:#2d6a8f}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:18px}
.chip{background:#fff;border:1px solid #dfe3e8;border-radius:16px;padding:5px 13px;font-size:12.5px;cursor:pointer;color:#4b5563;transition:.15s;user-select:none}
.chip:hover{border-color:#2d6a8f;color:#2d6a8f}
.chip.on{background:#2d6a8f;border-color:#2d6a8f;color:#fff}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:11px;padding:16px 18px;margin-bottom:11px;transition:.15s}
.card:hover{border-color:#b9c6d4;box-shadow:0 2px 10px rgba(30,58,95,.07)}
.card.new{border-left:3px solid #d9534f}
.badge-new{background:#fdecea;color:#c0392b;font-size:11px;padding:1px 7px;border-radius:5px;margin-left:8px;font-weight:600}
.tt{font-size:15.5px;font-weight:600;color:#1a3a5c;text-decoration:none;line-height:1.45;display:block}
.tt:hover{color:#2d6a8f;text-decoration:underline}
.meta{font-size:12.5px;color:#7a828c;margin-top:7px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.jn{background:#eef3f7;color:#2d6a8f;padding:2px 8px;border-radius:5px;font-weight:600}
.ifx{font-size:11px;padding:2px 7px;border-radius:5px;font-weight:600;cursor:help}
.if-a{background:#fde8e8;color:#c0392b}
.if-b{background:#fdf0e0;color:#b8720f}
.if-c{background:#e8f0fb;color:#2d6a8f}
.if-d{background:#eef1f4;color:#6b7280}
.if-na{background:#f5f6f8;color:#a4abb3}
.rev{background:#fdf3e3;color:#a06b1a;padding:2px 7px;border-radius:5px}
.pre{background:#eaf5ec;color:#2b7a3d;padding:2px 7px;border-radius:5px}
.tags{margin-top:9px;display:flex;flex-wrap:wrap;gap:6px}
.tag{font-size:11.5px;background:#f0f4f8;color:#41556b;padding:2px 9px;border-radius:5px;border:1px solid #e1e8ef}
.hits{margin-top:8px;font-size:11.5px;color:#98a1ac}
.abs{margin-top:10px;font-size:13px;color:#4a525c;background:#fafbfc;border-left:2px solid #dde3e9;padding:9px 12px;border-radius:0 6px 6px 0;display:block;max-height:4.7em;overflow:hidden;position:relative;white-space:pre-wrap;line-height:1.62}
.abs.show{max-height:none}
.abs:not(.show):after{content:"";position:absolute;left:0;right:0;bottom:0;height:1.7em;background:linear-gradient(to bottom,rgba(250,251,252,0),#fafbfc)}
.abs.plain{max-height:none}
.toggle{font-size:12px;color:#2d6a8f;cursor:pointer;margin-top:7px;display:inline-block;user-select:none}
#more{text-align:center;margin:18px 0 6px}
#more button{background:#fff;border:1px solid #d3dae1;color:#2d6a8f;padding:9px 26px;border-radius:7px;
 cursor:pointer;font-size:13px;font-weight:600}
#more button:hover{background:#f2f7fa;border-color:#2d6a8f}
.toggle:hover{text-decoration:underline}
.dist{background:#fff;border:1px solid #e3e6ea;border-radius:11px;padding:16px 18px;margin-bottom:16px}
.dist h3{font-size:13.5px;color:#41556b;margin-bottom:11px;font-weight:600}
.bar{display:flex;align-items:center;gap:9px;margin-bottom:6px;font-size:12.5px}
.bar .nm{width:190px;color:#5a636e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .tr{flex:1;background:#eef1f4;border-radius:4px;height:15px;overflow:hidden}
.bar .fl{height:100%;background:linear-gradient(90deg,#2d6a8f,#5aa2c4);border-radius:4px}
.bar .ct{width:32px;text-align:right;color:#8b939d}
.empty{text-align:center;padding:50px;color:#98a1ac;font-size:14px}
footer{text-align:center;color:#98a1ac;font-size:12px;margin-top:26px}
</style></head><body><div class="wrap">
<header>
<h1>AIDD / CADD 文献订阅看板</h1>
<p>AI 驱动药物设计 &amp; 计算机辅助药物设计 · 覆盖 __JCOUNT__ 种期刊 · 数据源 Europe PMC</p>
<div class="syncbar">
  <span id="syncinfo">本地快照生成于 __GEN__</span>
  <button id="syncbtn" onclick="syncOnline(7)">在线同步最新</button>
  <label><input type="checkbox" id="autosync" checked>打开页面时自动同步</label>
</div>
</header>
<div class="stats" id="stats"></div>
<div class="dist" id="dist"></div>
<div class="controls">
<input type="text" id="q" placeholder="搜索标题 / 摘要 / 作者 / 关键词...">
<select id="jsel"></select>
<select id="tsel"><option value="">全部主题</option></select>
<select id="ssel">
<option value="pub" selected>排序: 发表时间（最新在前）</option><option value="date">排序: 入库时间</option>
<option value="if">排序: 影响因子</option>
<option value="score">排序: 相关度</option>
</select>
<!-- 默认 30 天: 该筛选按入库时间(index_date)过滤, 而各刊入库节奏差异极大。
     JMC 为批量入库(约 16-18 天一批), 若默认 3 天, 其上批文献会整个被筛掉,
     看板上表现为"该刊始终无文献"。30 天可覆盖绝大多数刊的入库周期。 -->
<select id="dsel">
<option value="1">最近 1 天</option><option value="3">最近 3 天</option>
<option value="7">最近 7 天</option><option value="30" selected>最近 30 天</option>
<option value="90">最近 90 天</option><option value="0">全部</option>
</select>
<label style="font-size:13px;color:#5a636e"><input type="checkbox" id="newonly" style="margin-right:5px">只看本次新增</label>
</div>
<div class="chips" id="chips"></div>
<div id="list"></div>
<div id="more"></div>
<footer id="foot"></footer>
</div>
<script>
const DATA = __PAYLOAD__;
const NEWSET = new Set(DATA.new_keys);
const TOPICS = __TOPICS__;
const JMETA = __JMETA__;      // 期刊元数据: 显示名 -> {title, issn, abbr, core, terms}
const IMPACT = __IMPACT__;    // 期刊影响因子: 显示名 -> {if_latest, if_year, src, source}
const TTERMS = __TTERMS__;    // 主题 -> 关键词
const EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search";
const $ = id => document.getElementById(id);
let fTopic = "", MAP = new Map();

function daysAgo(s){ if(!s) return 9999; const d = new Date(s); return isNaN(d) ? 9999 : (Date.now()-d.getTime())/86400000; }

// 排序用的发表日期: 少数记录缺 firstPublicationDate, 退回入库日期, 避免空值全堆到末尾
function pubKey(p){ return p.date || p.index_date || ""; }

function statsView(){
  const ps = DATA.papers || [];
  const nw = ps.filter(p=>NEWSET.has(p.key)).length;
  const jn = new Set(ps.map(p=>p.journal_disp)).size;
  $('stats').innerHTML = [
    ['累计文献', ps.length], ['本次新增', nw], ['覆盖期刊', jn],
    ['近7天', ps.filter(p=>daysAgo(p.index_date)<=7).length],
    ['更新时间', DATA.generated]
  ].map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
}

// 影响因子徽章 (按分值分色)
function ifBadge(disp){
  const e = IMPACT[disp];
  if(!e || e.if_latest == null) return '<span class="ifx if-na" title="暂无影响因子数据">IF n/a</span>';
  const v = e.if_latest;
  const cls = v >= 15 ? "if-a" : (v >= 10 ? "if-b" : (v >= 5 ? "if-c" : "if-d"));
  const tip = (e.if_year || "") + " JIF " + v + " · 来源: " + (e.source || e.src || "-");
  return '<span class="ifx ' + cls + '" title="' + tip.replace(/"/g, "") + '">IF ' + v + '</span>';
}
function ifVal(disp){ const e = IMPACT[disp]; return (e && e.if_latest) || 0; }

function distView(){
  const m = {}; (DATA.papers||[]).forEach(p=>m[p.journal_disp]=(m[p.journal_disp]||0)+1);
  const arr = Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const mx = arr.length ? arr[0][1] : 1;
  $('dist').innerHTML = '<h3>期刊分布 Top 10（按近窗内篇数）</h3>' + arr.map(([k,v])=>
    `<div class="bar"><div class="nm" title="${k}">${k} ${ifBadge(k)}</div><div class="tr"><div class="fl" style="width:${v/mx*100}%"></div></div><div class="ct">${v}</div></div>`).join('');
}

function cardHTML(p){
  return `
   <div class="card ${NEWSET.has(p.key)?'new':''}">
     <a class="tt" href="${p.link}" target="_blank" rel="noopener">${p.title}${NEWSET.has(p.key)?'<span class="badge-new">NEW</span>':''}</a>
     <div class="meta"><span class="jn">${p.journal_disp}</span>${ifBadge(p.journal_disp)}
       ${p.date?`<span>发表 ${p.date}</span>`:''}
       ${p.index_date&&p.index_date!==p.date?`<span>入库 ${p.index_date}</span>`:''}
       ${p.is_review?'<span class="rev">Review</span>':''}
       ${p.is_preprint?'<span class="pre">Preprint</span>':''}
       ${p.doi?'<span>DOI '+p.doi.slice(0,32)+'</span>':''}
       ${p.pmid?'<span>PMID '+p.pmid+'</span>':''}
     </div>
     <div class="meta" style="color:#8b939d">${p.authors||''}</div>
     <div class="tags">${(p.tags||[]).map(t=>`<span class="tag">${t}</span>`).join('')}</div>
     ${p.hits&&p.hits.length?`<div class="hits">命中: ${p.hits.join(' · ')}</div>`:''}
     ${p.abstract?`<div class="abs${p.abstract.length<200?' plain':''}">${p.abstract}</div>`+
       (p.abstract.length>=200?`<span class="toggle" onclick="toggleAbs(this,'${String(p.key).replace(/'/g,"\\'")}')">展开全文 ▼</span>`:''):''}
   </div>`;
}

// 看板只内嵌摘要预览, 点"展开全文"时按需向 Europe PMC 拉全文并缓存
const ABS_CACHE = new Map();
async function fullAbstract(p){
  if(!p.abs_cut) return p.abstract;
  if(ABS_CACHE.has(p.key)) return ABS_CACHE.get(p.key);
  const tq = p.pmid ? ("EXT_ID:" + p.pmid) : ('DOI:"' + p.doi + '"');
  const url = EPMC + "?" + new URLSearchParams(
    {query: tq, format: "json", resultType: "core", pageSize: "1"});
  const r = await fetch(url);
  if(!r.ok) throw new Error("HTTP " + r.status);
  const d = await r.json();
  const rec = ((d.resultList || {}).result || [])[0] || {};
  const ab = cleanText(rec.abstractText || "") || p.abstract;
  ABS_CACHE.set(p.key, ab);
  p.abstract = ab; p.abs_cut = 0;
  return ab;
}

function toggleAbs(el, key){
  const box = el.previousElementSibling, p = MAP.get(key);
  if(box.classList.contains("show")){
    box.classList.remove("show"); el.textContent = "展开全文 ▼"; return;
  }
  box.classList.add("show");
  if(p && p.abs_cut){
    el.textContent = "加载全文…";
    fullAbstract(p).then(ab => { box.textContent = ab; el.textContent = "收起 ▲"; })
                   .catch(() => { el.textContent = "展开全文 ▼"; });
  }else{
    el.textContent = "收起 ▲";
  }
}

const PAGE_SIZE = 60;
let shown = PAGE_SIZE;

function render(){
  const q = $('q').value.toLowerCase().trim();
  const j = $('jsel').value, d = parseInt($('dsel').value,10);
  const onlyNew = $('newonly').checked;
  let ps = (DATA.papers||[]).filter(p=>{
    if(j && p.journal_disp!==j) return false;
    if(d && daysAgo(p.index_date)>d) return false;
    if(onlyNew && !NEWSET.has(p.key)) return false;
    if(fTopic && !(p.tags||[]).includes(fTopic)) return false;
    if(q){ const t=(p.title+' '+(p.abstract||'')+' '+p.authors+' '+(p.hits||[]).join(' ')+' '+p.journal_disp).toLowerCase(); if(!t.includes(q)) return false; }
    return true;
  });
  if(!ps.length){
    $('list').innerHTML='<div class="empty">没有匹配的文献，换个筛选条件试试</div>';
    $('more').innerHTML=''; $('foot').textContent='共 0 篇'; return;
  }
  const sort = $("ssel").value;
  if(sort === "if") ps.sort((a,b)=> ifVal(b.journal_disp) - ifVal(a.journal_disp) ||
                                    (b.index_date||"").localeCompare(a.index_date||""));
  else if(sort === "score") ps.sort((a,b)=> (b.score||0) - (a.score||0));
  else if(sort === "date") ps.sort((a,b)=> (b.index_date||"").localeCompare(a.index_date||"") ||
                                           pubKey(b).localeCompare(pubKey(a)));
  else ps.sort((a,b)=> pubKey(b).localeCompare(pubKey(a)) ||      // 默认: 发表时间最新在前
                                (b.index_date||"").localeCompare(a.index_date||"") ||
                                (b.score||0)-(a.score||0));
  // 分页渲染: 一次只挂 PAGE_SIZE 张卡片, 避免上千条 DOM 拖垮页面
  if(shown > ps.length) shown = ps.length;
  $('list').innerHTML = ps.slice(0, shown).map(cardHTML).join('');
  const rest = ps.length - shown;
  $('more').innerHTML = rest > 0
    ? `<button onclick="shown += PAGE_SIZE; render();">显示更多（还有 ${rest} 篇）</button>` : '';
  $('foot').textContent = `共 ${ps.length} 篇 · 已显示 ${Math.min(shown, ps.length)} 篇 · 数据生成于 ${DATA.generated}`;
}

// ================= 浏览器端在线同步 (直接请求 Europe PMC) =================
function cleanText(s){
  if(!s) return "";
  const d = document.createElement("div");
  d.innerHTML = s;
  return (d.textContent || "").replace(/\s+/g, " ").trim();
}

function jsTags(text){
  const scored = [], hits = [];
  for(const topic in TTERMS){
    let n = 0;
    for(const t of TTERMS[topic]){ if(text.includes(t)){ n++; hits.push(t); } }
    if(n) scored.push([n, topic]);
  }
  scored.sort((a,b)=> (b[0]-a[0]) || (a[1] < b[1] ? -1 : 1));
  return [scored.slice(0,3).map(x=>x[1]), [...new Set(hits)].slice(0,8)];
}

function jsNorm(rec, disp){
  const title = cleanText(rec.title || "");
  const abstract = cleanText(rec.abstractText || "");
  const text = (title + " " + abstract).toLowerCase();
  const [tags, hits] = jsTags(text);
  const core = !!(JMETA[disp] && JMETA[disp].core);
  if(!core && new Set(hits).size < 2) return null;      // 综合刊与本地一致的准入门槛
  const doi = (rec.doi || "").trim(), pmid = (rec.pmid || "").trim();
  const key = doi.toLowerCase() || (pmid ? "pmid:" + pmid : "") || title.slice(0,120).toLowerCase();
  if(!key) return null;
  const link = doi ? "https://doi.org/" + doi
             : (pmid ? "https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/" : "");
  const pt = (rec.pubTypeList && rec.pubTypeList.pubType) || [];
  const pts = (Array.isArray(pt) ? pt.join(" ") : String(pt)).toLowerCase();
  return {
    key:key, title:title, abstract:abstract, journal_disp:disp,
    authors:(rec.authorString || "").slice(0,220), doi:doi, pmid:pmid,
    pmcid:(rec.pmcid || ""), link:link,
    date:rec.firstPublicationDate || "", index_date:rec.firstIndexDate || "",
    tags:tags, hits:hits, is_review:pts.indexOf("review") >= 0,
    is_preprint:rec.source === "PPR", cited:rec.citedByCount || 0,
    score:hits.length + (core ? 3 : 0), core:core,
    added_at:new Date().toISOString().slice(0,19)
  };
}

function own(rec){
  const ji = (rec.journalInfo || {}).journal || {};
  const t = (ji.title || "").toLowerCase(),
        i = (ji.issn || "").toLowerCase(),
        a = (ji.medlineAbbreviation || "").toLowerCase();
  for(const disp in JMETA){
    const m = JMETA[disp];
    if((m.title && m.title.toLowerCase() === t) ||
       (m.issn && m.issn.toLowerCase() === i) ||
       (m.abbr && m.abbr.toLowerCase() === a)) return disp;
  }
  return null;
}

function iso(d){ return d.toISOString().slice(0,10); }

async function syncOnline(days){
  const btn = $("syncbtn"), info = $("syncinfo");
  btn.disabled = true; btn.textContent = "同步中...";
  const to = new Date();
  // 按"保底窗口"分桶: 低产刊(Monthly/季刊)用统一的短窗口必然 0 命中,
  // 必须各自拉更长的区间, 才能保证每刊都取到文献。
  const buckets = new Map();
  for(const d of Object.keys(JMETA)){
    const wd = Math.max(days, JMETA[d].min_days || 0);
    if(!buckets.has(wd)) buckets.set(wd, []);
    buckets.get(wd).push(d);
  }
  const jobs = [];
  for(const [wd, ds] of buckets)                       // 同窗口的刊再按 6 刊一组切分
    for(let i = 0; i < ds.length; i += 6) jobs.push({g: ds.slice(i, i + 6), wd: wd});
  const maxWin = Math.max.apply(null, jobs.map(j => j.wd));
  info.textContent = "正在向 Europe PMC 请求最新文献（最长回溯 " + maxWin + " 天）…";
  try{
    const results = await Promise.all(jobs.map(async ({g, wd}) => {
      const terms = [].concat.apply([], g.map(d => JMETA[d].terms || []));
      const from = new Date(Date.now() - wd * 86400000);
      const q = "(" + terms.join(" OR ") + ") AND (FIRST_IDATE:[" + iso(from) + " TO " + iso(to) + "])";
      const url = EPMC + "?" + new URLSearchParams({
        query:q, format:"json", resultType:"core", pageSize:"1000", sort:"P_PDATE_D desc"});
      const r = await fetch(url);
      if(!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    }));
    let added = 0;
    for(const data of results){
      const recs = ((data || {}).resultList || {}).result || [];
      for(const rec of recs){
        const disp = own(rec);
        if(!disp) continue;
        const it = jsNorm(rec, disp);
        if(!it || MAP.has(it.key)) continue;
        DATA.papers.push(it);
        if(!NEWSET.has(it.key)){ NEWSET.add(it.key); added++; }
        MAP.set(it.key, it);
      }
    }
    DATA.papers.sort((a,b)=> pubKey(b).localeCompare(pubKey(a)) ||      // 发表时间最新在前
                              (b.index_date||"").localeCompare(a.index_date||"") ||
                              (b.score||0)-(a.score||0));
    DATA.generated = new Date().toLocaleString("zh-CN", {hour12:false});
    // 渲染异常 ≠ 同步失败(数据此时已并入 DATA), 单独提示, 避免误导成网络问题
    try{ statsView(); distView(); render(); }
    catch(re){
      info.textContent = "已在线获取 " + added + " 篇，但列表刷新失败：" + re.message;
      console.error(re);
      btn.disabled = false; btn.textContent = "在线同步最新";
      return;
    }
    info.textContent = added > 0
      ? "已同步至最新 · 本次在线新增 " + added + " 篇 · " + DATA.generated
      : "已是最新，无新增文献 · 检查于 " + DATA.generated;
  }catch(e){
    info.textContent = "在线同步失败：" + e.message + "（当前显示本地快照，可点按钮重试）";
    console.error(e);
  }
  btn.disabled = false; btn.textContent = "在线同步最新";
}

function init(){
  const js = [...new Set((DATA.papers||[]).map(p=>p.journal_disp))].sort();
  $('jsel').innerHTML = '<option value="">全部期刊</option>' + js.map(j=>`<option>${j}</option>`).join('');
  $('tsel').innerHTML += TOPICS.map(t=>`<option>${t}</option>`).join('');
  $('chips').innerHTML = TOPICS.map(t=>`<span class="chip" data-t="${t}">${t}</span>`).join('');
  $('chips').addEventListener('click', e=>{
    const c = e.target.closest('.chip'); if(!c) return;
    const t = c.dataset.t;
    if(fTopic===t){ fTopic=""; c.classList.remove('on'); $('tsel').value=""; }
    else { document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on')); c.classList.add('on'); fTopic=t; $('tsel').value=t; }
    render();
  });
  $('tsel').addEventListener('change', e=>{
    fTopic = e.target.value;
    document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on', x.dataset.t===fTopic));
    shown = PAGE_SIZE; render();
  });
  ['q','jsel','dsel','newonly','ssel'].forEach(id=>$(id).addEventListener('input', ()=>{ shown = PAGE_SIZE; render(); }));
  MAP = new Map((DATA.papers||[]).map(p=>[p.key,p]));
  statsView(); distView(); render();
  if($("autosync").checked) setTimeout(()=>syncOnline(7), 700);   // 首屏渲染后再异步补最新数据
}
init();
</script></body></html>"""
    meta_path = os.path.join(DATA_DIR, "journal_meta.json")
    if os.path.exists(meta_path):
        jmeta = json.load(open(meta_path, encoding="utf-8"))
    else:  # 未探测过时用刊单直接兜底
        jmeta = {d: {"title": "", "issn": "", "abbr": "", "core": c,
                     "terms": ['JOURNAL:"%s"' % n for n in nm] + ['ISSN:"%s"' % i for i in issns]}
                 for d, nm, issns, c, _m in JOURNALS}

    ipath = os.path.join(DATA_DIR, "impact.json")
    impact = json.load(open(ipath, encoding="utf-8")) if os.path.exists(ipath) else {}

    def jsafe(obj):
        # 摘要中若含 </script> 会截断页面, 必须转义
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    out = (tpl.replace("__PAYLOAD__", jsafe(payload))
              .replace("__TOPICS__", jsafe(all_topics))
              .replace("__JMETA__", jsafe(jmeta))
              .replace("__IMPACT__", jsafe(impact))
              .replace("__TTERMS__", jsafe(TOPICS))
              .replace("__JCOUNT__", str(len(JOURNALS)))
              .replace("__GEN__", payload["generated"]))
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(out)
    log("看板已生成: %s" % HTML_FILE)


def build_markdown(new_items, days, path):
    lines = ["# AIDD / CADD 文献日报 · %s" % datetime.date.today().isoformat(),
             "",
             "> 检索窗口: 近 %d 天 | 数据源: Europe PMC | 生成时间: %s" %
             (days, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
             "",
             "本次新增 **%d** 篇。" % len(new_items), ""]
    if not new_items:
        lines.append("本窗口内无新增文献。")
    else:
        by_j = {}
        for it in new_items:
            by_j.setdefault(it["journal_disp"], []).append(it)
        for j in sorted(by_j, key=lambda x: -len(by_j[x])):
            lines.append("## %s (%d)" % (j, len(by_j[j])))
            lines.append("")
            for it in by_j[j]:
                mark = "[Review] " if it["is_review"] else ""
                lines.append("### %s%s" % (mark, it["title"]))
                lines.append("")
                lines.append("- **作者**: %s" % (it["authors"] or "-"))
                lines.append("- **发表日期**: %s | **链接**: %s" % (it["date"] or "-", it["link"]))
                if it["tags"]:
                    lines.append("- **主题**: %s" % " / ".join(it["tags"]))
                if it["hits"]:
                    lines.append("- **命中词**: %s" % ", ".join(it["hits"]))
                if it["abstract"]:
                    ab = it["abstract"]
                    lines.append("- **摘要**: %s" % (ab[:420] + ("..." if len(ab) > 420 else "")))
                lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("Markdown 简报: %s" % path)


def build_email(new_items, days, path):
    top = sorted(new_items, key=lambda x: -x["score"])[:12]
    lines = ["AIDD / CADD 文献日报 %s" % datetime.date.today().isoformat(),
             "本次新增 %d 篇，以下为相关度最高的 %d 篇：" % (len(new_items), len(top)), ""]
    for i, it in enumerate(top, 1):
        lines.append("%d. %s" % (i, it["title"]))
        lines.append("   %s | %s" % (it["journal_disp"], it["date"] or ""))
        if it["tags"]:
            lines.append("   主题: %s" % "/".join(it["tags"]))
        if it["abstract"]:
            lines.append("   %s" % (it["abstract"][:180] + "..."))
        lines.append("   %s" % it["link"])
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("邮件摘要: %s" % path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="回溯天数")
    ap.add_argument("--verify", action="store_true", help="仅校验期刊刊单命中数")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="用本地文献库重建看板(不联网)")
    ap.add_argument("--probe-journals", action="store_true", help="探测并保存期刊元数据")
    args = ap.parse_args()

    for d in (DATA_DIR, REPORT_DIR, DASH_DIR, OUTBOX_DIR):
        os.makedirs(d, exist_ok=True)

    if args.probe_journals:
        probe_journals()
        return

    if args.rebuild:
        db = json.load(open(DB_FILE, encoding="utf-8")) if os.path.exists(DB_FILE) else []
        refresh_tags(db)
        json.dump(db, open(DB_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        build_html(db, set())
        log("已用本地库(%d 篇)重建看板" % len(db))
        return

    if args.verify:
        log("校验 %d 种期刊近 %d 天命中情况:" % (len(JOURNALS), args.days))
        fetch(args.days, verify_only=True)
        return

    items = fetch(args.days)
    log("窗口内共抓取 %d 篇" % len(items))

    seen = set()
    if os.path.exists(SEEN_FILE):
        seen = set(json.load(open(SEEN_FILE, encoding="utf-8")))
    db = []
    if os.path.exists(DB_FILE):
        db = json.load(open(DB_FILE, encoding="utf-8"))

    have = {p["key"] for p in db}
    fresh = [it for it in items if it["key"] not in have and it["key"] not in seen]
    for it in fresh:
        db.append(it)
        seen.add(it["key"])
    log("新增 %d 篇 (库内累计 %d 篇)" % (len(fresh), len(db)))

    db = [p for p in db if (datetime.datetime.now() -
          datetime.datetime.fromisoformat(p["added_at"])).days <= 120]
    json.dump(db, open(DB_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(sorted(seen), open(SEEN_FILE, "w", encoding="utf-8"), ensure_ascii=False)

    new_keys = {it["key"] for it in fresh}
    if not args.no_html:
        build_html(db, new_keys)
    stamp = datetime.date.today().isoformat()
    build_markdown(fresh, args.days, os.path.join(REPORT_DIR, "%s.md" % stamp))
    build_email(fresh, args.days, os.path.join(OUTBOX_DIR, "email_digest.md"))
    log("完成。")


if __name__ == "__main__":
    main()
