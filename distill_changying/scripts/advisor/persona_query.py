"""
E大智慧查询接口

提供通过自然语言问题查询E大"可能回答"的能力。
从四通道数据（观点库+操作时间线+近期判断库+品种观点库）检索相关知识，
由LLM综合生成模拟E大口吻的回答。

检索机制说明：
  - 使用 jieba 分词提取关键词（降级到标点分词）
  - 使用 BM25 算法进行文本相关性排序
  - 可选集成向量检索（sentence-transformers 语义搜索）
  - 混合排序：BM25*0.2 + 向量*0.5 + 品种匹配*0.3 + 时间衰减

Usage:
    python -m scripts.advisor.persona_query "当前市场怎么看"
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any

from scripts.shared.llm_utils import build_llm_client

logger = logging.getLogger(__name__)

# 查询 Prompt 模板
QUERY_PROMPT_TEMPLATE = """你是一位熟悉"ETF拯救世界（E大）"投资思想的助手。用户提出了一个投资相关问题，请基于E大已有的观点、操作和原则，模拟E大的口吻给出回答。

## 用户问题
{question}

## 当前市场条件（如果提供）
{market_condition}

## 可参考的E大已有知识

### 相关市场判断
{observations_text}

### 相关操作记录
{operations_text}

### 相关通用原则
{principles_text}

## 输出格式
请以 JSON 格式输出以下结构，不要包含其他文字：
{{
  "answer": "模拟E大口吻的回答（200-500字，口语化、有力、有数据支撑）",
  "sources": [
    {{
      "content": "引用的具体原文或观点",
      "source_type": "observation / operation / principle",
      "relevance": "为什么引用这条"
    }}
  ],
  "confidence": 0.0-1.0
}}

## 要求
1. answer 要用E大的口吻——口语化、犀利、经常使用比喻和反问
2. sources 必须来源于提供的参考知识，不要编造
3. confidence 表示对该回答依据充分程度的评估：
   - 0.8-1.0: E大直接说过相关内容
   - 0.5-0.7: 可以根据E大的原则推导出合理回答
   - 0.0-0.4: 缺乏直接依据，只能给出一般性建议
4. 如果提供的参考知识不足以回答问题，请如实说明"""


class BM25Retriever:
    """简化的 BM25 检索器。

    使用 BM25 算法对文档集合进行相关性评分排序。
    支持对给定查询词列表计算每篇文档的 BM25 得分。

    Args:
        corpus: 文档文本列表
        k1: BM25 参数，控制词频饱和度（默认 1.5）
        b: BM25 参数，控制文档长度归一化（默认 0.75）
    """

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.n = len(corpus)
        self.avgdl = sum(len(doc.split()) for doc in corpus) / max(self.n, 1)
        self.k1 = k1
        self.b = b

        # 倒排索引：term -> {doc_idx -> term_frequency}
        self.idf: dict[str, float] = {}
        # 每篇文档的词频统计
        self.doc_term_freqs: list[Counter] = []

        self._build_index()

    def _build_index(self) -> None:
        """构建倒排索引，计算每个词的 IDF 值和每篇文档的词频。"""
        # 统计文档频率（DF）：包含该词的文档数
        doc_freq: Counter[str] = Counter()

        for doc_text in self.corpus:
            terms = doc_text.split()
            # 文档内去重后统计 DF
            unique_terms = set(terms)
            doc_freq.update(unique_terms)
            # 记录文档内词频（用于 TF 计算）
            self.doc_term_freqs.append(Counter(terms))

        # 计算 IDF：idf = ln((N - df + 0.5) / (df + 0.5) + 1)
        for term, df in doc_freq.items():
            self.idf[term] = math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)

        logger.debug(
            "BM25 索引构建完成: %d 篇文档, %d 个词项",
            self.n, len(self.idf),
        )

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        """计算单篇文档对查询词的 BM25 得分。

        Args:
            query_terms: 查询词列表
            doc_idx: 文档索引

        Returns:
            float: BM25 得分
        """
        doc_len = len(self.corpus[doc_idx].split())
        doc_tf = self.doc_term_freqs[doc_idx]
        score = 0.0

        for term in query_terms:
            if term not in self.idf:
                continue
            tf = doc_tf.get(term, 0)
            if tf == 0:
                continue

            idf = self.idf[term]
            # BM25 公式：idf * (tf * (k1+1)) / (tf + k1 * (1 - b + b * doc_len / avgdl))
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * doc_len / self.avgdl)
            score += idf * numerator / denominator

        return score

    def search(self, query_terms: list[str], top_k: int = 20) -> list[tuple[float, int]]:
        """对查询词进行 BM25 检索，返回 Top-K 结果。

        Args:
            query_terms: 查询词列表
            top_k: 最多返回条数

        Returns:
            list[tuple[float, int]]: 按得分降序排列的 (分数, 文档索引) 列表
        """
        if not query_terms or self.n == 0:
            return []

        scored: list[tuple[float, int]] = []
        for i in range(self.n):
            s = self.score(query_terms, i)
            if s > 0:
                scored.append((s, i))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]


# 默认停用词表（不含投资领域关键词）
_DEFAULT_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "为什么", "能否", "是否", "可以", "应该", "需要",
    "这个", "那个", "这些", "那些", "之", "的", "与", "及", "以及",
    "当前", "现在", "目前", "最近", "近期", "今天", "明天", "昨天",
}


class PersonaQuery:
    """E大人物智慧查询引擎"""

    def __init__(self, config: dict[str, Any], distill_dir: str | None = None):
        """初始化查询引擎。

        Args:
            config: advisor 配置字典
            distill_dir: 蒸馏产物目录（可选）
        """
        self.config = config

        # 确定蒸馏产物目录
        if distill_dir is None:
            veins_path = config.get("knowledge_base", {}).get("core_veins", "")
            quotes_path = config.get("knowledge_base", {}).get("golden_quotes", "")
            distill_dir = os.path.dirname(veins_path or quotes_path)

        self.distill_dir = distill_dir
        self._all_opinions: list[dict] = []
        self._operations: list[dict] = []
        self._observations: list[dict] = []
        self._principles: list[dict] = []
        self._product_opinions: list[dict] = []

        # BM25 检索器（延迟初始化）
        self._bm25_opinions: BM25Retriever | None = None
        self._bm25_observations: BM25Retriever | None = None
        self._bm25_principles: BM25Retriever | None = None
        self._bm25_product_opinions: BM25Retriever | None = None

        # 向量检索相关（延迟加载）
        self._vec_model: Any = None
        self._vec_embeddings: Any = None
        self._vec_records: list[dict] = []

        self._load_data()

        # 加载结构化索引的 by_topic 维度（用于主题过滤）
        self._topic_index: dict[str, list[str]] = {}
        try:
            from scripts.distill.persona_index import load_index
            index_dir = os.path.join(self.distill_dir, "persona_index")
            if os.path.isdir(index_dir):
                index = load_index(index_dir)
                # 提取 topic -> [opinion_text, ...] 的映射
                for topic, records in index.get("by_topic", {}).items():
                    self._topic_index[topic] = [r.get("opinion", "") for r in records]
                logger.info("已加载主题索引: %d 个主题", len(self._topic_index))
        except Exception as e:
            logger.warning("主题索引加载失败（不影响检索）: %s", e)

    def _load_data(self) -> None:
        """加载所有数据到内存。"""
        distill_dir = self.distill_dir
        if not distill_dir or not os.path.isdir(distill_dir):
            logger.warning("蒸馏产物目录不存在: %s", distill_dir)
            return

        # 加载观点库
        opinions_path = os.path.join(distill_dir, "观点库.jsonl")
        self._all_opinions = self._load_jsonl(opinions_path)

        # 按 source_type 分组
        for op in self._all_opinions:
            st = op.get("source_type", "")
            if st == "operation":
                self._operations.append(op)
            elif st == "observation":
                self._observations.append(op)
            elif st == "principle":
                self._principles.append(op)
            elif st == "product_opinion":
                self._product_opinions.append(op)

        # 也直接加载操作时间线和判断库（更完整）
        ops_path = os.path.join(distill_dir, "操作时间线.jsonl")
        obs_path = os.path.join(distill_dir, "近期判断库.jsonl")

        ops_from_file = self._load_jsonl(ops_path)
        obs_from_file = self._load_jsonl(obs_path)

        # 为操作时间线记录自动生成 opinion 字段（如果缺失）
        opinion_generated = 0
        for op in ops_from_file:
            if not op.get("opinion"):
                plan = op.get("plan", "")
                action = op.get("action", "")
                fund = op.get("fund", "")
                code = op.get("code", "")
                parts = []
                if plan:
                    parts.append(f"{plan}计划")
                if action or fund:
                    action_fund = f"{action}{fund}"
                    if code:
                        action_fund += f"({code})"
                    parts.append(action_fund)
                op["opinion"] = " ".join(parts) if parts else "未知操作"
                opinion_generated += 1

        # 合并但不重复
        existing_op_keys = {json.dumps(o, ensure_ascii=False) for o in self._operations}
        for op in ops_from_file:
            key = json.dumps(op, ensure_ascii=False)
            if key not in existing_op_keys:
                self._operations.append(op)
                existing_op_keys.add(key)

        existing_obs_keys = {json.dumps(o, ensure_ascii=False) for o in self._observations}
        for obs in obs_from_file:
            key = json.dumps(obs, ensure_ascii=False)
            if key not in existing_obs_keys:
                self._observations.append(obs)
                existing_obs_keys.add(key)

        # 对合并后的 self._operations 中已有记录也补充 opinion 字段
        for op in self._operations:
            if not op.get("opinion"):
                plan = op.get("plan", "")
                action = op.get("action", "")
                fund = op.get("fund", "")
                code = op.get("code", "")
                parts = []
                if plan:
                    parts.append(f"{plan}计划")
                if action or fund:
                    action_fund = f"{action}{fund}"
                    if code:
                        action_fund += f"({code})"
                    parts.append(action_fund)
                op["opinion"] = " ".join(parts) if parts else "未知操作"
                opinion_generated += 1

        if opinion_generated > 0:
            logger.info("自动生成了 %d 条 opinion 字段", opinion_generated)

        logger.info("数据加载完成: %d operations, %d observations, %d principles, %d product_opinions",
                    len(self._operations), len(self._observations), len(self._principles),
                    len(self._product_opinions))

    @staticmethod
    def _load_jsonl(filepath: str) -> list[dict]:
        """加载 JSONL 文件。"""
        if not os.path.isfile(filepath):
            return []
        records: list[dict] = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.error("加载 %s 失败: %s", filepath, e)
        return records

    # ---------------------------------------------------------------
    # 关键词提取
    # ---------------------------------------------------------------

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从文本中提取关键词。

        使用 jieba 分词作为首选方案，过滤停用词和单字词。
        当 jieba 不可用时，自动降级到标点/空格分词。

        Args:
            text: 输入文本

        Returns:
            list[str]: 关键词列表
        """
        try:
            # 首选方案：jieba 分词
            import jieba
            words = jieba.lcut(text)
            # 过滤停用词（使用不含投资领域词的停用词表）
            return [
                w.strip() for w in words
                if len(w.strip()) > 1 and w.strip() not in _DEFAULT_STOP_WORDS
            ]
        except ImportError:
            # 降级方案：标点/空格分词
            logger.debug("jieba 不可用，使用标点分词降级")
            words = re.split(r'[\s,，。.！？、；：""''【】（）()/\\\-]+', text)
            return [
                w.strip() for w in words
                if len(w.strip()) > 1 and w.strip() not in _DEFAULT_STOP_WORDS
            ]

    # ---------------------------------------------------------------
    # BM25 检索器初始化
    # ---------------------------------------------------------------

    def _ensure_bm25(self) -> None:
        """确保 BM25 检索器已初始化。

        在首次查询时延迟构建 BM25 索引，避免 __init__ 阶段开销。
        """
        if self._bm25_opinions is not None:
            return

        # 构建 BM25 检索器：观点库 -> 按 opinion 字段检索
        if self._all_opinions:
            opinion_corpus = [
                (op.get("opinion", "") or "") + " " + " ".join(op.get("keywords", []))
                for op in self._all_opinions
            ]
            self._bm25_opinions = BM25Retriever(opinion_corpus)

        # 观察（近期判断库）-> 按 opinion 字段检索
        if self._observations:
            obs_corpus = [obs.get("opinion", "") or "" for obs in self._observations]
            self._bm25_observations = BM25Retriever(obs_corpus)

        # 原则（观点库中 source_type=principle）-> 按 opinion 字段检索
        if self._principles:
            principle_corpus = [p.get("opinion", "") or "" for p in self._principles]
            self._bm25_principles = BM25Retriever(principle_corpus)

        # 品种观点（观点库中 source_type=product_opinion）-> 按 opinion 字段检索
        if self._product_opinions:
            prod_corpus = [
                (po.get("opinion", "") or "") + " " + " ".join(po.get("keywords", []))
                for po in self._product_opinions
            ]
            self._bm25_product_opinions = BM25Retriever(prod_corpus)

        logger.info("BM25 检索器初始化完成")

    # ---------------------------------------------------------------
    # 检索方法
    # ---------------------------------------------------------------

    def _search_opinions(self, question: str, source_type: str = "", topic_filter: str = "", top_k: int = 20) -> list[dict]:
        """使用 BM25 算法检索观点库，支持按 source_type 分层和按 topic 主题过滤。

        Args:
            question: 用户问题
            source_type: 按来源类型过滤（operation/observation/principle，空字符串表示不过滤）
            topic_filter: 主题过滤（可选，已弃用，建议使用自动主题匹配）
            top_k: 最多返回条数

        Returns:
            list[dict]: 匹配的观点列表
        """
        # 从问题中提取关键词
        keywords = self._extract_keywords(question)

        if source_type:
            # ---- 分层检索：按 source_type 过滤记录集，优先使用缓存的 BM25 索引 ----
            if source_type == "operation":
                records = self._operations
                cached_bm25 = self._bm25_opinions  # operations 混在全量索引中，走全量路径
            elif source_type == "observation":
                records = self._observations
                cached_bm25 = self._bm25_observations
            elif source_type == "principle":
                records = self._principles
                cached_bm25 = self._bm25_principles
            elif source_type == "product_opinion":
                records = self._product_opinions
                cached_bm25 = self._bm25_product_opinions
            else:
                records = self._all_opinions
                cached_bm25 = None

            if not keywords:
                return records[:top_k]

            # 确保 BM25 缓存索引已初始化
            self._ensure_bm25()
            # 重新获取缓存引用（_ensure_bm25 可能已更新）
            if source_type == "observation":
                cached_bm25 = self._bm25_observations
            elif source_type == "principle":
                cached_bm25 = self._bm25_principles
            elif source_type == "product_opinion":
                cached_bm25 = self._bm25_product_opinions

            # 优先使用缓存的 BM25 索引（observation/principle/product_opinion 有独立缓存）
            if cached_bm25 is not None:
                scored_indices = cached_bm25.search(keywords, top_k=top_k * 2)
                results: list[dict] = []
                for score, idx in scored_indices:
                    if idx >= len(records):
                        continue
                    op = records[idx]
                    if topic_filter and topic_filter != op.get("topic", ""):
                        continue
                    results.append(op)
                return results[:top_k]

            # 回退：构建临时 BM25 索引（operation 或全量检索时走此路径）
            corpus = []
            try:
                import jieba
                for rec in records:
                    opinion = rec.get("opinion", "") or ""
                    tokens = jieba.lcut(opinion)
                    text = " ".join(tokens) + " " + " ".join(rec.get("keywords", []))
                    corpus.append(text)
            except ImportError:
                corpus = [
                    (rec.get("opinion", "") or "") + " " + " ".join(rec.get("keywords", []))
                    for rec in records
                ]
            if not corpus:
                return []

            bm25 = BM25Retriever(corpus)
            scored_indices = bm25.search(keywords, top_k=top_k * 2)

            results: list[dict] = []
            for score, idx in scored_indices:
                if idx >= len(records):
                    continue
                op = records[idx]
                if topic_filter and topic_filter != op.get("topic", ""):
                    continue
                results.append(op)

            return results[:top_k]

        # ---- 全量检索：使用缓存的 BM25 索引 ----
        if not keywords:
            return self._all_opinions[:top_k]

        self._ensure_bm25()

        if self._bm25_opinions is None:
            return self._all_opinions[:top_k]

        # 执行 BM25 检索，取 top_k*2 以支持主题置顶
        scored_indices = self._bm25_opinions.search(keywords, top_k=top_k * 2)

        # 将 BM25 结果映射为记录列表
        results: list[dict] = []
        for score, idx in scored_indices:
            if idx >= len(self._all_opinions):
                continue
            op = self._all_opinions[idx]
            topic = op.get("topic", "")
            if topic_filter and topic_filter != topic:
                continue
            results.append(op)

        # 主题匹配：将匹配主题的记录置顶
        matched_topics = self._match_topic(question)
        if matched_topics:
            topic_matched = [r for r in results if r.get("topic", "") in matched_topics]
            non_topic = [r for r in results if r.get("topic", "") not in matched_topics]
            results = topic_matched + non_topic

        return results[:top_k]

    def _search_all_layered(self, question: str, top_k: int = 10) -> dict[str, list[dict]]:
        """分层检索：分别从四种类型检索并返回分组结果。

        Args:
            question: 用户问题
            top_k: 每种类型最多返回条数

        Returns:
            dict: {"operations": [...], "observations": [...], "principles": [...], "product_opinions": [...]}
        """
        return {
            "operations": self._search_opinions(question, source_type="operation", top_k=top_k),
            "observations": self._search_opinions(question, source_type="observation", top_k=top_k),
            "principles": self._search_opinions(question, source_type="principle", top_k=top_k),
            "product_opinions": self._search_opinions(question, source_type="product_opinion", top_k=top_k),
        }

    def _match_topic(self, question: str) -> list[str]:
        """从查询中匹配已知主题。

        使用关键词交集匹配：提取查询关键词，与主题名做交集匹配。
        同时检查主题名是否包含查询中的品种名。

        Args:
            question: 用户问题

        Returns:
            list[str]: 匹配的主题列表，按匹配度降序排列
        """
        keywords = self._extract_keywords(question)
        if not keywords:
            return []

        matched = []
        for topic in self._topic_index:
            # 策略1：主题名直接出现在查询中
            if topic in question:
                matched.append((len(topic), topic))
                continue
            # 策略2：查询关键词与主题名关键词有交集
            topic_words = set(topic)
            kw_set = set(keywords)
            overlap = sum(1 for kw in kw_set if kw in topic)
            if overlap > 0:
                matched.append((overlap, topic))
        matched.sort(reverse=True)
        return [t for _, t in matched]

    def _search_operations(self, fund_name: str = "", top_k: int = 15) -> list[dict]:
        """按品种检索操作记录。

        Args:
            fund_name: 基金名称关键词（可选）
            top_k: 最多返回条数

        Returns:
            list[dict]: 匹配的操作记录
        """
        if not fund_name:
            # 没有指定品种，返回最近的
            sorted_ops = sorted(self._operations, key=lambda x: x.get("time", ""), reverse=True)
            return sorted_ops[:top_k]

        fund_name_lower = fund_name.lower()
        matched = [op for op in self._operations if fund_name_lower in op.get("fund", "").lower()]
        matched.sort(key=lambda x: x.get("time", ""), reverse=True)
        return matched[:top_k] if matched else self._operations[:top_k]

    def _search_observations(self, keyword: str = "", top_k: int = 20) -> list[dict]:
        """使用 BM25 算法检索近期判断库。

        如果提供了具体关键词，使用 BM25 进行相关性排序；
        否则按时间倒序返回最近的记录。

        Args:
            keyword: 搜索关键词（可选）
            top_k: 最多返回条数

        Returns:
            list[dict]: 匹配的判断记录
        """
        if not keyword:
            sorted_obs = sorted(self._observations, key=lambda x: x.get("time", ""), reverse=True)
            return sorted_obs[:top_k]

        # 从关键词提取有效词
        keywords = self._extract_keywords(keyword)
        if not keywords:
            sorted_obs = sorted(self._observations, key=lambda x: x.get("time", ""), reverse=True)
            return sorted_obs[:top_k]

        # 确保 BM25 检索器已初始化
        self._ensure_bm25()

        if self._bm25_observations is None:
            sorted_obs = sorted(self._observations, key=lambda x: x.get("time", ""), reverse=True)
            return sorted_obs[:top_k]

        # 执行 BM25 检索
        scored_indices = self._bm25_observations.search(keywords, top_k=top_k)

        results: list[dict] = []
        for score, idx in scored_indices:
            if idx < len(self._observations):
                results.append(self._observations[idx])

        return results if results else self._observations[:top_k]

    def _search_principles(self, question: str, top_k: int = 20) -> list[dict]:
        """使用 BM25 算法检索通用原则。

        Args:
            question: 用户问题
            top_k: 最多返回条数

        Returns:
            list[dict]: 匹配的原则列表
        """
        keywords = self._extract_keywords(question)
        if not keywords:
            return self._principles[:top_k]

        self._ensure_bm25()

        if self._bm25_principles is None:
            return self._principles[:top_k]

        scored_indices = self._bm25_principles.search(keywords, top_k=top_k)

        results: list[dict] = []
        for score, idx in scored_indices:
            if idx < len(self._principles):
                results.append(self._principles[idx])

        return results if results else self._principles[:top_k]

    # ---------------------------------------------------------------
    # 向量检索集成
    # ---------------------------------------------------------------

    def _load_vector_index(self) -> bool:
        """尝试加载向量索引。

        从 `{distill_dir}/persona_index/` 目录加载预构建的向量索引。
        如果向量文件不存在或加载失败，静默降级。

        Returns:
            bool: 向量索引是否加载成功
        """
        if self._vec_model is not None:
            return True

        if not self.distill_dir:
            return False

        index_dir = os.path.join(self.distill_dir, "persona_index")
        embeddings_path = os.path.join(index_dir, "embeddings.npy")
        records_path = os.path.join(index_dir, "embedding_records.json")

        if not os.path.isfile(embeddings_path) or not os.path.isfile(records_path):
            logger.info("向量索引文件不存在，跳过向量检索: %s", index_dir)
            return False

        try:
            from scripts.distill.persona_index import (
                _get_embedding_model,
                load_embeddings,
                load_embedding_records,
                search_vectors,
            )

            model = _get_embedding_model()
            if model is None:
                logger.warning("无法获取 Embedding 模型，跳过向量检索")
                return False

            embeddings = load_embeddings(embeddings_path)
            if embeddings.size == 0:
                logger.warning("向量为空，跳过向量检索")
                return False

            records = load_embedding_records(records_path)
            if not records:
                logger.warning("向量记录为空，跳过向量检索")
                return False

            self._vec_model = model
            self._vec_embeddings = embeddings
            self._vec_records = records

            logger.info(
                "向量索引加载成功: %d 条记录, 维度 %d",
                len(records), embeddings.shape[1] if embeddings.ndim > 1 else 0,
            )
            return True

        except ImportError:
            logger.warning("向量索引模块不可用（persona_index），跳过向量检索")
            return False
        except Exception as e:
            logger.warning("加载向量索引失败: %s", e)
            return False

    def _vector_search(self, question: str, top_k: int = 20) -> list[tuple[float, dict]]:
        """执行向量语义检索。

        Args:
            question: 自然语言查询
            top_k: 最多返回条数

        Returns:
            list[tuple[float, dict]]: 按相似度降序的 (分数, 记录) 列表
        """
        if not self._load_vector_index():
            return []

        from scripts.distill.persona_index import search_vectors

        try:
            results = search_vectors(
                query_text=question,
                records=self._vec_records,
                model=self._vec_model,
                embeddings=self._vec_embeddings,
                top_k=top_k,
            )
            logger.info("向量检索完成: 返回 %d 条", len(results))
            return results
        except Exception as e:
            logger.warning("向量检索执行失败: %s", e)
            return []

    # ---------------------------------------------------------------
    # 混合排序
    # ---------------------------------------------------------------

    def _extract_fund_names(self, query_keywords: list[str]) -> list[str]:
        """从查询关键词中提取可能的品种名。

        通过以下两种方式提取品种名：
        1. 从 self._topic_index 的键中匹配，看哪些主题名出现在查询关键词中
        2. 使用 jieba 分词从查询关键词中提取更细粒度的品种名

        Args:
            query_keywords: 查询关键词列表

        Returns:
            list[str]: 提取到的品种名列表
        """
        # 将关键词列表拼接为查询字符串，方便匹配
        query_text = " ".join(query_keywords) if query_keywords else ""

        fund_names: list[str] = []

        # 1. 从 self._topic_index 的键中匹配
        for topic in self._topic_index:
            if topic in query_text and topic not in fund_names:
                fund_names.append(topic)

        # 2. 从 jieba 分词结果中提取（补充 topic_index 未覆盖的品种名）
        try:
            import jieba
            words = jieba.lcut(query_text)
            for w in words:
                w = w.strip()
                if len(w) > 1 and w not in _DEFAULT_STOP_WORDS and w not in fund_names:
                    fund_names.append(w)
        except ImportError:
            logger.debug("jieba 不可用，跳过品种名分词提取")

        return fund_names

    def _hybrid_sort(
        self,
        bm25_results: list[dict],
        vec_results: list[tuple[float, dict]],
        query_keywords: list[str] | None = None,
        time_weight: float = 0.1,
    ) -> list[dict]:
        """混合排序：BM25 + 向量检索 + 品种匹配 + 时间衰减。

        BM25 权重 0.2，向量权重 0.5，品种匹配权重 0.3，时间衰减因子（最新记录额外加分）。

        Args:
            bm25_results: BM25 检索结果（按相关性降序）
            vec_results: 向量检索结果，每个元素为 (余弦相似度, 记录字典)
            query_keywords: 查询关键词列表，用于提取品种名做品种匹配
            time_weight: 时间衰减权重（默认 0.1）

        Returns:
            list[dict]: 按混合得分降序排列的结果
        """
        # 以记录的 opinion 文本作为唯一标识，合并两种检索结果
        final_scores: dict[str, dict[str, Any]] = {}

        # ---- BM25 得分（归一化到 0-1） ----
        total_bm25 = len(bm25_results)
        for i, rec in enumerate(bm25_results):
            key = rec.get("opinion", "") or json.dumps(rec, ensure_ascii=False)
            final_scores[key] = {
                "record": rec,
                "bm25_score": 1.0 - i / max(total_bm25, 1),
            }

        # ---- 向量得分（余弦相似度已在 0-1 范围） ----
        for score, rec in vec_results:
            key = rec.get("opinion", "") or json.dumps(rec, ensure_ascii=False)
            if key in final_scores:
                final_scores[key]["vec_score"] = score
            else:
                final_scores[key] = {
                    "record": rec,
                    "bm25_score": 0.0,
                    "vec_score": score,
                }

        # ---- 从查询关键词中提取品种名 ----
        fund_names = self._extract_fund_names(query_keywords or [])

        # ---- 计算最终混合得分 ----
        now = int(datetime.now().strftime("%Y%m%d"))
        bm25_weight = 0.2
        vec_weight = 0.5
        fund_match_weight = 0.3

        sorted_items: list[tuple[float, dict]] = []
        for key, item in final_scores.items():
            rec = item["record"]

            # BM25 得分
            bm25_s = item.get("bm25_score", 0.0)

            # 向量得分（默认 0）
            vec_s = item.get("vec_score", 0.0)

            # 品种匹配度：检查记录的 keywords 和 opinion 字段是否包含品种名
            fund_match = 0.0
            if fund_names:
                rec_text = (rec.get("opinion", "") or "") + " " + " ".join(rec.get("keywords", []))
                matched_count = sum(1 for fn in fund_names if fn in rec_text)
                fund_match = matched_count / len(fund_names)

            # 时间衰减：time 字段不为空时，越新的记录加分越高
            time_decay = 0.0
            time_str = rec.get("time", "")
            if time_str and len(time_str) >= 8:
                try:
                    time_num = int(time_str[:8].replace("-", "").replace("/", ""))
                    # 越接近当前，time_decay 越高（范围 0-0.1）
                    days_diff = (now - time_num) / 10000
                    time_decay = max(0, time_weight - time_weight * min(days_diff / 365, 1.0))
                except (ValueError, IndexError):
                    pass

            # 混合得分 = bm25*0.2 + vec*0.5 + time_decay + fund_match*0.3
            hybrid = bm25_s * bm25_weight + vec_s * vec_weight + time_decay + fund_match * fund_match_weight

            sorted_items.append((hybrid, rec))

        # 按混合得分降序排列
        sorted_items.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in sorted_items]

    # ---------------------------------------------------------------
    # 格式化方法
    # ---------------------------------------------------------------

    def _format_observations(self, observations: list[dict]) -> str:
        """格式化市场判断为 prompt 文本。"""
        if not observations:
            return "（暂无相关市场判断）"
        lines = [f"- {o.get('opinion', '')}" for o in observations if o.get('opinion')]
        return "\n".join(lines[:20])

    def _format_operations(self, operations: list[dict]) -> str:
        """格式化操作记录为 prompt 文本。"""
        if not operations:
            return "（暂无相关操作记录）"
        lines = []
        for op in operations[:10]:
            plan = op.get("plan", "")
            action = op.get("action", "")
            fund = op.get("fund", "")
            code = op.get("code", "")
            yield_pct = op.get("yield_pct", 0)
            time_str = op.get("time", "")[:10] if op.get("time") else ""
            parts = [f"[{time_str}]" if time_str else ""]
            if plan:
                parts.append(f"{plan}计划")
            parts.append(f"{action}{fund}({code})" if code else f"{action}{fund}")
            if yield_pct:
                parts.append(f"收益率{yield_pct}%")
            lines.append("- " + " ".join(parts))
        return "\n".join(lines)

    def _format_principles(self, principles: list[dict], max_chars: int = 2000) -> str:
        """格式化通用原则为 prompt 文本。"""
        if not principles:
            return "（暂无相关通用原则）"
        lines = []
        chars = 0
        for p in principles:
            opinion = p.get("opinion", "")
            if not opinion:
                continue
            if chars + len(opinion) > max_chars:
                lines.append("（原则库过长，已截断）")
                break
            lines.append(f"- {opinion}")
            chars += len(opinion)
        return "\n".join(lines)

    # ---------------------------------------------------------------
    # 核心查询方法
    # ---------------------------------------------------------------

    def query(self, question: str, market_condition: str = "") -> dict[str, Any]:
        """查询E大对某个问题的可能回答。

        检索流程：
        1. 从问题中提取关键词
        2. 对四通道数据分别执行 BM25 检索（操作/判断/原则/品种观点）
        3. 尝试加载并执行向量语义检索
        4. 使用混合排序（BM25*0.2 + 向量*0.5 + 品种匹配*0.3 + 时间衰减）合并结果
        5. 组装 Prompt 调用 LLM 生成回答

        Args:
            question: 自然语言问题
            market_condition: 当前市场条件描述（可选）

        Returns:
            dict: {
                "answer": "模拟E大口吻的回答",
                "sources": [{"content": "...", "source_type": "...", "relevance": "..."}],
                "confidence": 0.0-1.0
            }
        """
        # 1. 从问题提取关键词
        keywords = self._extract_keywords(question)
        search_term = " ".join(keywords[:5]) if keywords else ""

        # 2. BM25 检索三通道数据
        matched_observations = self._search_observations(search_term)
        matched_operations = self._search_operations(search_term)
        matched_principles = self._search_principles(question)

        # 3. 向量检索（仅对 observations + principles 做语义搜索）
        vec_results = self._vector_search(question, top_k=20)

        # 4. 混合排序合并向量检索结果
        if vec_results:
            # 对 observations 和 principles 分别做混合排序，传入查询关键词用于品种匹配
            matched_observations = self._hybrid_sort(
                matched_observations,
                [(s, r) for s, r in vec_results if r.get("source_type") == "observation"],
                query_keywords=keywords,
            )
            matched_principles = self._hybrid_sort(
                matched_principles,
                [(s, r) for s, r in vec_results if r.get("source_type") == "principle"],
                query_keywords=keywords,
            )

        # 如果没有匹配到任何内容，返回低置信度
        if not matched_observations and not matched_operations and not matched_principles:
            return {
                "answer": "抱歉，关于这个问题，E大没有直接发表过相关观点。从E大的投资原则来看，建议关注估值而非预测，做好资产配置比猜测走势更重要。",
                "sources": [],
                "confidence": 0.1,
            }

        # 5. 格式化各通道数据
        observations_text = self._format_observations(matched_observations)
        operations_text = self._format_operations(matched_operations)
        principles_text = self._format_principles(matched_principles)

        # 6. 组装 prompt
        prompt = QUERY_PROMPT_TEMPLATE.format(
            question=question,
            market_condition=market_condition or "（未提供）",
            observations_text=observations_text,
            operations_text=operations_text,
            principles_text=principles_text,
        )

        # 7. 调用 LLM
        client = build_llm_client(self.config)
        api_config = self.config["api"]
        query_config = self.config.get("persona_query", {})

        logger.info("查询E大智慧: %s", question[:50])
        logger.info("匹配: %d observations, %d operations, %d principles (向量检索 %s)",
                    len(matched_observations), len(matched_operations),
                    len(matched_principles), "启用" if self._vec_model is not None else "未启用")

        try:
            response = client.chat.completions.create(
                model=api_config.get("model", "deepseek-chat"),
                messages=[
                    {"role": "system", "content": "你是一位熟悉E大投资思想的助手。请只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=query_config.get("temperature", 0.4),
                max_tokens=query_config.get("max_tokens", 4096),
            )

            content = response.choices[0].message.content or ""

            from scripts.shared.llm_utils import parse_json_response
            result = parse_json_response(content)

            if isinstance(result, dict) and "answer" in result:
                logger.info("查询成功，置信度: %.2f", result.get("confidence", 0))
                return result

        except Exception as e:
            logger.error("查询失败: %s", e)

        return {
            "answer": f"关于「{question}」，E大没有直接说过，但可以参考以下原则：{principles_text[:200]}",
            "sources": [],
            "confidence": 0.0,
        }


def query_persona(
    question: str,
    config: dict[str, Any] | None = None,
    market_condition: str = "",
) -> dict[str, Any]:
    """便捷函数：一行调用查询E大智慧。

    Args:
        question: 自然语言问题
        config: 配置字典（可选，不提供则从默认配置加载）
        market_condition: 当前市场条件描述

    Returns:
        dict: 查询结果
    """
    if config is None:
        from scripts.distill.config import get_config
        config = get_config()

    engine = PersonaQuery(config)
    return engine.query(question, market_condition)


def main() -> None:
    """CLI 入口"""
    import argparse
    import sys

    from scripts.distill.config import get_config

    parser = argparse.ArgumentParser(description="查询E大智慧")
    parser.add_argument("question", type=str, nargs="?", default="", help="自然语言问题")
    parser.add_argument("--market", type=str, default="", help="当前市场条件描述")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    args = parser.parse_args()

    config = get_config()

    # 设置日志
    from scripts.distill.main import setup_logging
    setup_logging(config)

    engine = PersonaQuery(config)

    if args.interactive:
        print("进入交互查询模式（输入 'exit' 退出）")
        while True:
            try:
                q = input("\n问题: ").strip()
                if q.lower() in ("exit", "quit", "q"):
                    break
                if not q:
                    continue
                result = engine.query(q, args.market)
                print(f"\nE大说: {result['answer']}")
                print(f"置信度: {result['confidence']:.2f}")
                if result.get("sources"):
                    print("\n参考来源:")
                    for s in result["sources"]:
                        print(f"  [{s.get('source_type','')}] {s.get('content','')[:80]}...")
            except (EOFError, KeyboardInterrupt):
                break
        return

    if not args.question:
        parser.print_help()
        sys.exit(1)

    result = engine.query(args.question, args.market)
    print(f"\n问题: {args.question}")
    print(f"E大说: {result['answer']}")
    print(f"置信度: {result['confidence']:.2f}")
    if result.get("sources"):
        print("\n参考来源:")
        for s in result["sources"]:
            print(f"  [{s.get('source_type','')}] {s.get('content','')[:100]}...")


if __name__ == "__main__":
    main()