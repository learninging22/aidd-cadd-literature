#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取各期刊的影响因子 (来源 bioxbio, 基于 JCR 历年数据)
输出 data/impact.json, 供看板显示
用法: python fetch_if.py [--retry]
"""
import json
import os
import re
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
META_FILE = os.path.join(BASE_DIR, "data", "journal_meta.json")
OUT_FILE = os.path.join(BASE_DIR, "data", "impact.json")

# bioxbio 的 URL slug 与 MEDLINE 缩写不一致时的手工覆盖
SLUG_OVERRIDE = {
    "PNAS": ["PNAS", "PROC-NATL-ACAD-SCI-U-S-A", "P-NAS"],
    "JACS": ["J-AM-CHEM-SOC"],
    "Angew Chem": ["ANGEW-CHEM-INT-EDIT", "ANGEW-CHEM-INT-ED", "ANGEW-CHEM-INT-ED-ENGL"],
    "J Med Chem": ["J-MED-CHEM"],
    "J Cheminform": ["J-CHEMINFORMATICS", "J-CHEMINFORM"],
    "Bioinformatics": ["BIOINFORMATICS"],
    "Nat Commun": ["NAT-COMMUN"],
    "Nat Biotechnol": ["NAT-BIOTECHNOL"],
    "Nat Methods": ["NAT-METHODS"],
    "Sci Adv": ["SCI-ADV", "SCI-ADVANCES"],
    "Chem Sci": ["CHEM-SCI"],
    "Patterns": ["PATTERNS", "PATTERNS-N-Y"],
    "J Phys Chem B": ["J-PHYS-CHEM-B"],
    "J Chem Phys": ["J-CHEM-PHYS"],
    "RSC Med Chem": ["RSC-MED-CHEM", "RSC-MEDICINAL-CHEMISTRY"],
    "Digital Discovery": ["DIGITAL-DISCOVERY", "DIGIT-DISCOV"],
    "Nat Mach Intell": ["NAT-MACH-INTELL", "NATURE-MACHINE-INTELLIGENCE"],
    "Nat Comput Sci": ["NAT-COMPUT-SCI", "NATURE-COMPUTATIONAL-SCIENCE"],
    "Mach Learn Sci Technol": ["MACH-LEARN-SCI-TECHNOL"],
    "J Comput Aided Mol Des": ["J-COMPUT-AIDED-MOL-DES", "J-COMPUT-AID-MOL-DES"],
    "Comput Struct Biotechnol J": ["COMPUT-STRUCT-BIOTECHNOL-J"],
    "Expert Opin Drug Discov": ["EXPERT-OPIN-DRUG-DISCOV"],
    "ACS Cent Sci": ["ACS-CENT-SCI", "ACS-CENTRAL-SCIENCE"],
    "WIREs Comput Mol Sci": ["WILEY-INTERDISCIP-REV-COMPUT-MOL-SCI"],
}


# bioxbio 未收录的期刊: 以下数值经人工核对 (多为出版社官网 / JCR 官方数据), 优先级高于自动抓取
# 格式: 显示名 -> (影响因子, 年度, 来源)
MANUAL = {
    "Nat Mach Intell":          (29.8, "2025", "Nature 官方期刊推荐 (科学网)"),
    "Nat Comput Sci":           (18.3, "2025", "JCR 2025 (journalsimpactfactors)"),
    "Sci Adv":                  (13.9, "2025", "2025 JCR (科学网/美捷登 + manusights)"),
    "PNAS":                     (9.5,  "2025", "2025 JCR (科学网/美捷登 + manusights)"),
    "ACS Cent Sci":             (11.1, "2025", "JCR 2025, 2026-06-17 发布 (journalmetrics)"),
    "WIREs Comput Mol Sci":     (10.9, "2025", "Wiley 官网 Journal Metrics"),
    "Patterns":                 (10.8, "2025", "Cell Press 官方"),
    "Mach Learn Sci Technol":   (4.6,  "2025", "JCR 2025 (journalsimpactfactors)"),
    "Comput Struct Biotechnol J": (4.8, "2025", "2025 JCR (Wikipedia 引 Clarivate)"),
    "Expert Opin Drug Discov":  (7.3,  "2025", "bioxbio 2026 update / ooir WoS 2025"),
    "RSC Med Chem":             (4.1,  "2025", "RSC 官网 pubs.rsc.org"),
    "Digital Discovery":        (5.6,  "2024", "RSC 官方 2024 JIF 表"),
}


def slugify(abbr):
    s = abbr.upper().replace(".", "")
    s = re.sub(r"[^A-Z0-9]+", "-", s).strip("-")
    return s


def fetch_page(slug):
    """bioxbio 有两套路径: /journal/<slug> 与 /if/html/<slug>, 逐个尝试"""
    for path in ("https://www.bioxbio.com/journal/%s",
                 "https://www.bioxbio.com/if/html/%s"):
        url = path % slug
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIDD-Lit/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "ignore")
                if parse_if(html):
                    return html, url
        except Exception:
            continue
        time.sleep(0.3)
    return "", ""


def parse_if(html):
    rows = re.findall(r"<tr>\s*<td[^>]*>\s*(20\d\d[^<]*)</td>\s*<td[^>]*>\s*([\d.]+)", html)
    out = []
    for y, v in rows:
        out.append((y.strip(), float(v)))
    return out


def main():
    only_missing = "--retry" in sys.argv
    meta = json.load(open(META_FILE, encoding="utf-8"))
    prev = {}
    if only_missing and os.path.exists(OUT_FILE):
        prev = json.load(open(OUT_FILE, encoding="utf-8"))

    result, failed = {}, []
    for disp, m in meta.items():
        if only_missing and prev.get(disp, {}).get("if_latest"):
            result[disp] = prev[disp]
            continue
        if disp in MANUAL:      # 人工核对值优先
            v, y, s = MANUAL[disp]
            result[disp] = {"if_latest": v, "if_year": y, "if_label": y,
                            "hist": {}, "src": "manual", "source": s}
            print("  %-28s IF %-5s (%s) [人工核对: %s]" % (disp, v, y, s))
            continue
        cands = SLUG_OVERRIDE.get(disp) or [slugify(m.get("abbr") or disp)]
        got = None
        for slug in cands:
            html, used = fetch_page(slug)
            if not html:
                continue
            rows = parse_if(html)
            if rows:
                year_label, val = rows[0]
                hist = {y.split()[0]: v for y, v in rows}
                got = {
                    "if_latest": val,
                    "if_year": year_label.split()[0],
                    "if_label": year_label,
                    "hist": hist,
                    "src": "bioxbio/JCR",
                    "source": used,
                }
                print("  %-28s IF %-5s (%s)" % (disp, val, year_label))
                break
        if got:
            result[disp] = got
        else:
            failed.append(disp)
            print("  %-28s 未获取" % disp)
        time.sleep(0.4)

    json.dump(result, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n成功 %d / %d，已保存 %s" % (len(result), len(meta), OUT_FILE))
    if failed:
        print("未获取:", ", ".join(failed))


if __name__ == "__main__":
    main()
