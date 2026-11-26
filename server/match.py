#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询容错与中文分词打分。

解决两类需求：
  1. 拼写容错：输入 "rufuse" 也要能搜到 "rufus"
  2. 中文拆词：输入 "疯狂动物城" 时，完全匹配的排最前，命中的子词（动物城/疯狂）排后面

只依赖标准库（difflib / re / unicodedata），不引入分词库。
"""

import difflib
import math
import re
import unicodedata

CJK_RE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff]+')
TOKEN_RE = re.compile(r'[a-z0-9]+')
# 分词时丢弃的无意义片段（中文常见修饰词/格式词）
CJK_STOP = {
    '高清', '蓝光', '中字', '字幕', '国语', '粤语', '双语', '完整', '未删减', '下载', '电影',
    '合集', '最新', '更新', '免费', '在线', '版', '的', '了', '之', '和', '与', '全集',
}
# 英文里常见但无区分度的词
EN_STOP = {'the', 'a', 'an', 'of', 'and', 'for', 'in', 'on', 'to', 'with', 'vs'}

MIN_SCORE = 0.62          # 英文：低于这个相似度视为无关（挡噪声）
CJK_PARTIAL_MIN = 0.40    # 中文：只命中"动物城"这类子词也放行，但排序靠后
FUZZY_OK = 0.72           # 拼写容错阈值：rufuse vs rufus ≈ 0.8


def norm(s):
    """归一化：全角转半角、小写、去掉标点噪声"""
    s = unicodedata.normalize('NFKC', s or '').casefold()
    s = re.sub(r'[\.\-_\[\]()（）【】「」《》:：,，!！?？+&/\\|~*#@$%^"\'`]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def has_cjk(s):
    return bool(CJK_RE.search(s or ''))


def en_tokens(s):
    return [t for t in TOKEN_RE.findall(norm(s)) if t not in EN_STOP and len(t) > 1]


def cjk_runs(s):
    return [r for r in CJK_RE.findall(s or '') if r]


def cjk_segments(q, min_len=2, max_parts=6, keep_grams=2):
    """把中文查询拆成候选子词：全串 + 前缀窗口 + 后缀窗口 + 2 字词。

    "疯狂动物城" -> ['疯狂动物城', '疯狂动物', '狂动物城', '动物城', '疯狂动', '疯狂', '动物']
    只保留长度 >= min_len、非停用词的片段。
    """
    runs = cjk_runs(norm(q))
    segs = []

    def add(seg):
        if seg and seg not in segs and seg not in CJK_STOP and len(seg) >= min_len:
            segs.append(seg)

    for run in runs:
        n = len(run)
        add(run)
        for size in range(n - 1, min_len - 1, -1):
            add(run[:size])                 # 前缀
            add(run[n - size:])             # 后缀
        grams = [run[i:i + 2] for i in range(n - 1)]
        for g in grams:                     # 二字词
            add(g)
    # 长度降序；同长度保持出现顺序
    segs.sort(key=len, reverse=True)
    picked = segs[:max_parts]
    # 至少给一个二字词，方便"动物城"这类只能靠短词命中的情况
    if len(picked) < keep_grams:
        for g in [x for x in segs if len(x) == 2]:
            if g not in picked:
                picked.append(g)
            if len(picked) >= keep_grams:
                break
    return picked


def en_variants(q, limit=3):
    """给英文查询生成拼写纠正候选（去掉多余尾字母、去重复字母、前缀截断）。"""
    toks = [t for t in TOKEN_RE.findall(norm(q)) if len(t) > 1 and t not in EN_STOP]
    if not toks:
        return []
    head, rest = toks[0], toks[1:]
    cands = []

    def add(w):
        if len(w) >= 3 and w != head and w not in cands:
            cands.append(w)

    if head.endswith('es'):
        add(head[:-2])
    if head.endswith('s'):
        add(head[:-1])
    if head.endswith('e'):
        add(head[:-1])
    if len(head) > 5:
        add(head[:-1])
    add(re.sub(r'(.)\1+', r'\1', head))            # rufusss -> rufus
    if len(head) >= 5:
        add(head[:max(4, len(head) - 1)])          # 前缀截断，交给源做前缀匹配
    return [' '.join([c] + rest) for c in cands][:limit]


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def en_similarity(q, title):
    """标题与查询的英文相似度：逐 token 取最佳模糊匹配后平均。"""
    qt = en_tokens(q)
    if not qt:
        return 0.0
    tt = set(en_tokens(title))
    if not tt:
        return 0.0
    total = 0.0
    for t in qt:
        best = 0.0
        for x in tt:
            if x == t:
                best = 1.0
                break
            if t in x or x in t:              # 前缀/包含关系也算高分
                best = max(best, 0.88)
                continue
            best = max(best, _ratio(t, x))
        total += best
    return total / len(qt)


def cjk_similarity(q, title):
    """标题与中文查询的相似度：最长命中片段占比 + 全串加成。"""
    qn, tn = norm(q), norm(title)
    if not qn or not tn:
        return 0.0
    runs = cjk_runs(qn)
    if not runs:
        return 0.0
    run = max(runs, key=len)
    if run in tn:
        return 1.0
    best = 0.0
    for seg in cjk_segments(run, min_len=2, max_parts=6):
        if seg in tn:
            best = max(best, len(seg) / float(len(run)))
    # 中文标题里常夹英文别名，顺便看一眼英文部分
    if best < FUZZY_OK:
        best = max(best, 0.9 * en_similarity(q, title)) if not has_cjk(q) else best
    return best


def similarity(q, title):
    return cjk_similarity(q, title) if has_cjk(q) else en_similarity(q, title)


def is_relevant(q, title):
    """比原来宽松：相似度够高就放行，保证拼写小错的条目不被过滤掉。"""
    s = similarity(q, title)
    if s >= MIN_SCORE:
        return True
    # 中文：只命中部分（"动物城"/"疯狂"）也算相关，排序时自然排在后面
    if has_cjk(q):
        return s >= CJK_PARTIAL_MIN
    return False


PROMO_RE = re.compile(r'【[^】]{0,60}】|\[[^\]]{0,60}\]|www\.[a-z0-9.\-]+', re.I)
ADULT_WORDS = ('偷拍', '自拍', '约炮', '裸', '色情', '成人片', '里番', '无码', '援交', '三级片',
               '情色', '做爱', '口交', '中出')


def clean_title(title):
    """去掉站点前缀/宣传尾巴，只留能代表内容的标题，用于打分"""
    t = PROMO_RE.sub(' ', title or '')
    t = re.sub(r'@[A-Za-z0-9_\-]{2,20}\b', ' ', t)
    return norm(t)


def spam_penalty(cleaned):
    low = cleaned.casefold()
    return 0.9 if any(w in low for w in ADULT_WORDS) else 0.0


def rank_value(q, item):
    """综合排序分：完全命中 3 分、相似度 2 分、出现位置 0.6 分、做种对数 0.25 分，垃圾内容扣 0.9。

    只是排序，不过滤 —— 用户仍能看到全部结果，只是把"正主"排到最前。
    """
    title = item.get('title') or ''
    cleaned = clean_title(title)
    qn = norm(q)
    exact = 1.0 if qn and qn in cleaned else 0.0
    sim = similarity(q, title)
    pos = 0.0
    if qn:
        idx = cleaned.find(qn)
        if idx >= 0:
            pos = 1.0 if idx <= 3 else (0.6 if idx <= 20 else 0.3)
    seed = math.log1p(int(item.get('seeders') or 0))
    return 3.0 * exact + 2.0 * sim + 0.6 * pos + 0.25 * seed - spam_penalty(cleaned)


def rank_key(q, item):
    """给 sorted(key=...) 用：分数相同时按做种数"""
    return (round(rank_value(q, item), 4), int(item.get('seeders') or 0))


def expand_queries(q):
    """需要扩大检索时给出的补充查询（中文子词 + 英文拼写纠正）。"""
    out = []
    if has_cjk(q):
        # 用"词尾 3 字 + 词头 3 字 + 词头/词尾 2 字"做补充检索：
        # 疯狂动物城 -> 动物城 / 疯狂动 / 疯狂 / 物城（真实用户会这么搜）
        for run in cjk_runs(norm(q)):
            n = len(run)
            for cand in (run[-3:], run[:3], run[-4:]):
                if 2 <= len(cand) < n and cand not in CJK_STOP and cand not in out:
                    out.append(cand)
            out = out[:3]
        # 中文里夹英文时（如"沙丘 Dune"）也把英文部分单独试一次
        en = ' '.join(en_tokens(q))
        if en and en not in out:
            out.append(en)
    else:
        out.extend(en_variants(q))
    return [x for x in out if norm(x) and norm(x) != norm(q)]
