"""
人物画像索引模块

提供结构化索引和向量索引两种索引方式，用于对三通道数据（操作时间线、近期判断库、观点库）
进行高效检索。

功能分区：
  - Task 2: 结构化索引 — 按品种(fund)、操作类型(action)、主题(topic)、年份(year)建立索引
  - Task 3: 向量索引 — 基于 sentence-transformers 的语义向量检索

Usage:
    # 命令行构建结构化索引
    python -m scripts.distill.persona_index

    # 代码中调用
    from scripts.distill.persona_index import build_structured_index, build_embeddings

    data = load_data("docs/distilled")
    index = build_structured_index(data)
    embeddings = build_embeddings(data["observations"] + data["principles"], model)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections import defaultdict
from typing import Any

import numpy as np

from scripts.distill.config import get_config

logger = logging.getLogger(__name__)


# ============================================================
# 常量定义
# ============================================================

# 向量索引的默认输出子目录
_VECTOR_INDEX_SUBDIR = "persona_index"

# 余弦相似度阈值（低于此值的记录不返回）
_COSINE_SIMILARITY_THRESHOLD = 0.0


# ============================================================
# Task 2: 结构化索引
# ============================================================


def _compute_fingerprint(record: dict, dedup_field: str = "opinion") -> str:
    """计算记录的唯一指纹，用于去重。

    基于 dedup_field 字段的内容计算 SHA256 哈希的前 32 位作为指纹。
    如果字段不存在或为空，退而使用整条记录的 JSON 序列化。

    Args:
        record: 单条记录
        dedup_field: 用于计算指纹的字段名

    Returns:
        str: 32 位十六进制指纹字符串
    """
    content = record.get(dedup_field, "")
    if not content or not isinstance(content, str):
        # 字段不存在或为空时，用整条记录的 JSON 表示作为指纹源
        content = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]


def _extract_year(record: dict) -> str:
    """从记录的 time 字段提取年份。

    Args:
        record: 单条记录

    Returns:
        str: 4 位年份字符串；无法提取时返回 "unknown"
    """
    time_str = record.get("time", "")
    if isinstance(time_str, str) and len(time_str) >= 4:
        return time_str[:4]
    return "unknown"


def _sort_by_time(records: list[dict], reverse: bool = True) -> list[dict]:
    """按 time 字段对记录排序。

    空 time 或 time 不可比的记录排在末尾。

    Args:
        records: 记录列表
        reverse: 是否倒排（最新的在前），默认 True

    Returns:
        list[dict]: 排序后的新列表（不修改原列表）
    """

    def _time_key(r: dict) -> str:
        return r.get("time", "") or ""

    return sorted(records, key=_time_key, reverse=reverse)


def build_structured_index(data: dict) -> dict:
    """从三通道数据构建多维可筛选的知识索引。

    输入 data 结构:
    .. code-block:: python

        {
            "operations": [...],      # 操作时间线记录
            "observations": [...],    # 近期判断记录
            "principles": [...],      # 观点库中 source_type=principle 的记录
        }

    输出索引结构:
    .. code-block:: python

        {
            "by_fund": {              # 按品种索引（仅 operations）
                "中证500": [op1, op2, ...],
                "沪深300": [op1, op2, ...],
            },
            "by_action": {            # 按操作类型索引（仅 operations）
                "买入": [op1, op2, ...],
                "卖出": [op1, op2, ...],
            },
            "by_topic": {             # 按主题索引（所有类型）
                "估值": [rec1, rec2, ...],
                "仓位管理": [rec1, rec2, ...],
            },
            "by_year": {              # 按年份索引（所有类型）
                "2020": [rec1, rec2, ...],
                "2024": [rec1, rec2, ...],
            },
        }

    每个维度下的记录按 time 字段倒排（最新的在前）。

    Args:
        data: 三通道数据字典

    Returns:
        dict: 结构化索引字典
    """
    operations = data.get("operations", [])
    observations = data.get("observations", [])
    principles = data.get("principles", [])
    all_records = operations + observations + principles

    logger.info(
        "开始构建索引: operations=%d, observations=%d, principles=%d",
        len(operations), len(observations), len(principles),
    )

    # -- by_fund: 从 operations 的 fund 字段提取品种名 --------------------
    by_fund: dict[str, list[dict]] = {}
    for op in operations:
        fund = op.get("fund", "").strip()
        if not fund:
            continue
        by_fund.setdefault(fund, []).append(op)

    for fund in by_fund:
        by_fund[fund] = _sort_by_time(by_fund[fund])

    # -- by_action: 从 operations 的 action 字段提取操作类型 ---------------
    by_action: dict[str, list[dict]] = {}
    for op in operations:
        action = op.get("action", "").strip()
        if not action:
            continue
        # 空格分割取第一个词（兼容 "分批买入" 这类复合词）
        action_key = action.split(" ", maxsplit=1)[0]
        by_action.setdefault(action_key, []).append(op)

    for act in by_action:
        by_action[act] = _sort_by_time(by_action[act])

    # -- by_topic: 从所有记录的 topic 字段提取主题 -------------------------
    by_topic: dict[str, list[dict]] = {}
    for rec in all_records:
        topic = rec.get("topic", "").strip()
        if not topic:
            continue
        by_topic.setdefault(topic, []).append(rec)

    for topic in by_topic:
        by_topic[topic] = _sort_by_time(by_topic[topic])

    # -- by_year: 从所有记录的 time 字段提取年份 ---------------------------
    by_year: dict[str, list[dict]] = {}
    for rec in all_records:
        year = _extract_year(rec)
        by_year.setdefault(year, []).append(rec)

    for year in by_year:
        by_year[year] = _sort_by_time(by_year[year])

    index: dict[str, dict[str, list[dict]]] = {
        "by_fund": by_fund,
        "by_action": by_action,
        "by_topic": by_topic,
        "by_year": by_year,
    }

    logger.info(
        "索引构建完成: by_fund=%d 个品种, by_action=%d 种操作类型, "
        "by_topic=%d 个主题, by_year=%d 个年份",
        len(by_fund), len(by_action), len(by_topic), len(by_year),
    )

    return index


def save_index(index: dict, output_dir: str, subdir: str = "persona_index") -> str:
    """将索引保存为多文件到 persona_index 目录。

    保存的文件清单:
    - {subdir}/by_fund.json
    - {subdir}/by_action.json
    - {subdir}/by_topic.json
    - {subdir}/by_year.json
    - {subdir}/metadata.json（索引统计信息）

    Args:
        index: 索引字典
        output_dir: 输出根目录（绝对或相对路径）
        subdir: 索引子目录名

    Returns:
        str: persona_index 目录的绝对路径
    """
    index_dir = os.path.join(output_dir, subdir)
    os.makedirs(index_dir, exist_ok=True)

    dimensions = ["by_fund", "by_action", "by_topic", "by_year"]
    metadata: dict[str, Any] = {
        "dimensions": {},
        "total_records": 0,
        "index_version": "1.0",
    }

    for dim in dimensions:
        data = index.get(dim, {})
        file_path = os.path.join(index_dir, f"{dim}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        group_count = len(data)
        record_count = sum(len(v) for v in data.values())
        metadata["dimensions"][dim] = {
            "groups": group_count,
            "records": record_count,
        }
        metadata["total_records"] += record_count

        logger.info("已保存 %s: %d 个分组, %d 条记录", file_path, group_count, record_count)

    # 保存元数据
    meta_path = os.path.join(index_dir, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    logger.info(
        "索引元数据已保存: %s (共 %d 条记录, %d 个维度)",
        meta_path, metadata["total_records"], len(dimensions),
    )

    return os.path.abspath(index_dir)


def load_index(index_dir: str) -> dict:
    """从 persona_index 目录加载索引。

    Args:
        index_dir: persona_index 目录的路径（包含 by_fund.json 等文件的目录）

    Returns:
        dict: 索引字典，包含 by_fund / by_action / by_topic / by_year 四个维度。
              某个维度文件不存在时，该维度使用空字典。
    """
    dimensions = ["by_fund", "by_action", "by_topic", "by_year"]
    index: dict[str, dict[str, list[dict]]] = {}

    for dim in dimensions:
        file_path = os.path.join(index_dir, f"{dim}.json")
        if not os.path.isfile(file_path):
            logger.warning("索引文件不存在，使用空字典: %s", file_path)
            index[dim] = {}
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 确保加载的数据结构符合预期
        if not isinstance(data, dict):
            logger.warning("索引文件格式异常，使用空字典: %s", file_path)
            index[dim] = {}
            continue

        index[dim] = data
        record_count = sum(len(v) for v in data.values())
        logger.info("已加载 %s: %d 个分组, %d 条记录", file_path, len(data), record_count)

    return index


def update_index_incremental(
    index: dict,
    new_records: dict,
    dedup_field: str = "opinion",
) -> dict:
    """增量更新索引。

    将新记录追加到各个维度的对应分组中，基于 dedup_field 内容的哈希去重，
    然后对每个分组重新按时间排序。

    Args:
        index: 现有索引字典
        new_records: 新增数据字典，结构同 build_structured_index 的 data 参数
        dedup_field: 用于去重的字段名

    Returns:
        dict: 更新后的索引字典
    """
    # 收集现有索引中所有记录的指纹（用于去重）
    existing_fingerprints: set[str] = set()
    for dim_name in ["by_fund", "by_action", "by_topic", "by_year"]:
        for group_records in index.get(dim_name, {}).values():
            for rec in group_records:
                existing_fingerprints.add(_compute_fingerprint(rec, dedup_field))

    logger.info("现有索引去重指纹数: %d", len(existing_fingerprints))

    # 分离新增记录中的 operations 和其他类型
    new_ops = new_records.get("operations", [])
    new_observations = new_records.get("observations", [])
    new_principles = new_records.get("principles", [])

    # 筛选出真正新增的记录（指纹不存在于现有索引中）
    truly_new_ops: list[dict] = []
    truly_new_rest: list[dict] = []

    for op in new_ops:
        fp = _compute_fingerprint(op, dedup_field)
        if fp not in existing_fingerprints:
            truly_new_ops.append(op)
            existing_fingerprints.add(fp)

    for rec in new_observations + new_principles:
        fp = _compute_fingerprint(rec, dedup_field)
        if fp not in existing_fingerprints:
            truly_new_rest.append(rec)
            existing_fingerprints.add(fp)

    if not truly_new_ops and not truly_new_rest:
        logger.info("无新增记录，索引无需更新")
        return index

    logger.info(
        "新增记录: operations=%d, observations+principles=%d",
        len(truly_new_ops), len(truly_new_rest),
    )

    # -- 更新 by_fund 维度 ------------------------------------------------
    by_fund = index.get("by_fund", {})
    for op in truly_new_ops:
        fund = op.get("fund", "").strip()
        if not fund:
            continue
        by_fund.setdefault(fund, []).append(op)
    for fund in by_fund:
        by_fund[fund] = _sort_by_time(by_fund[fund])

    # -- 更新 by_action 维度 ----------------------------------------------
    by_action = index.get("by_action", {})
    for op in truly_new_ops:
        action = op.get("action", "").strip()
        if not action:
            continue
        action_key = action.split(" ", maxsplit=1)[0]
        by_action.setdefault(action_key, []).append(op)
    for act in by_action:
        by_action[act] = _sort_by_time(by_action[act])

    # -- 更新 by_topic 维度 ------------------------------------------------
    by_topic = index.get("by_topic", {})
    for rec in truly_new_ops + truly_new_rest:
        topic = rec.get("topic", "").strip()
        if not topic:
            continue
        by_topic.setdefault(topic, []).append(rec)
    for topic in by_topic:
        by_topic[topic] = _sort_by_time(by_topic[topic])

    # -- 更新 by_year 维度 ------------------------------------------------
    by_year = index.get("by_year", {})
    for rec in truly_new_ops + truly_new_rest:
        year = _extract_year(rec)
        by_year.setdefault(year, []).append(rec)
    for year in by_year:
        by_year[year] = _sort_by_time(by_year[year])

    index["by_fund"] = by_fund
    index["by_action"] = by_action
    index["by_topic"] = by_topic
    index["by_year"] = by_year

    logger.info("索引增量更新完成")

    return index


def load_data(distill_dir: str) -> dict:
    """从蒸馏产物目录加载三通道数据。

    加载以下文件:
    - {distill_dir}/操作时间线.jsonl   → operations
    - {distill_dir}/近期判断库.jsonl   → observations
    - {distill_dir}/观点库.jsonl       → principles（过滤 source_type=principle）

    使用 opinion_matcher.load_jsonl() 函数加载 JSONL 文件。

    Args:
        distill_dir: 蒸馏产物目录路径

    Returns:
        dict: 三通道数据字典
    """
    from scripts.advisor.opinion_matcher import load_jsonl

    ops_path = os.path.join(distill_dir, "操作时间线.jsonl")
    obs_path = os.path.join(distill_dir, "近期判断库.jsonl")
    principles_path = os.path.join(distill_dir, "观点库.jsonl")

    operations = load_jsonl(ops_path)
    observations = load_jsonl(obs_path)
    all_opinions = load_jsonl(principles_path)

    # 从观点库中过滤出 source_type=principle 的记录
    principles = [o for o in all_opinions if o.get("source_type") == "principle"]

    logger.info(
        "三通道数据加载完成: operations=%d, observations=%d, principles=%d",
        len(operations), len(observations), len(principles),
    )

    return {
        "operations": operations,
        "observations": observations,
        "principles": principles,
    }


# ============================================================
# Task 3: 向量索引
# ============================================================


def _get_embedding_model():
    """获取 Embedding 模型，安全降级。

    优先使用 sentence-transformers 的 paraphrase-multilingual-MiniLM-L12-v2 模型
    （轻量、支持中文）。加载模型时设置 30 秒超时，超时则降级。
    如果 sentence-transformers 不可用，则降级到 sklearn 的 TfidfVectorizer。

    Returns:
        object | None: 模型对象；全部失败返回 None
    """
    import threading

    # 尝试 sentence-transformers（带 30 秒超时）
    model_result: dict[str, Any] = {"model": None, "error": None}

    def _load_st_model() -> None:
        """在线程中加载 sentence-transformers 模型。"""
        try:
            from sentence_transformers import SentenceTransformer

            model_result["model"] = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        except Exception as e:
            model_result["error"] = str(e)

    load_thread = threading.Thread(target=_load_st_model, daemon=True)
    load_thread.start()
    load_thread.join(timeout=30)

    if model_result["model"] is not None:
        logger.info("使用 sentence-transformers 模型")
        return model_result["model"]

    # 分析降级原因并记录日志
    if load_thread.is_alive():
        logger.warning("sentence-transformers 模型下载超时（30秒），降级为 sklearn TF-IDF")
    else:
        logger.warning(
            "sentence-transformers 不可用（%s），降级为 sklearn TF-IDF",
            model_result.get("error", "未知错误"),
        )

    # 降级方案: sklearn TfidfVectorizer
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        logger.info("使用 sklearn TfidfVectorizer 降级")

        class _TfidfFallback:
            """TfidfVectorizer 的包装，提供与 SentenceTransformer 类似的 encode 接口。"""

            def __init__(self) -> None:
                self._vectorizer = TfidfVectorizer(max_features=512, analyzer="char", ngram_range=(2, 3))
                self._fitted = False

            def encode(self, sentences: str | list[str]) -> np.ndarray:
                """将文本向量化。

                Args:
                    sentences: 单个文本字符串或文本列表

                Returns:
                    np.ndarray: 向量数组
                """
                if isinstance(sentences, str):
                    texts = [sentences]
                else:
                    texts = sentences

                if not self._fitted:
                    embeddings = self._vectorizer.fit_transform(texts).toarray()
                    self._fitted = True
                else:
                    embeddings = self._vectorizer.transform(texts).toarray()
                return embeddings.astype(np.float32)

        return _TfidfFallback()
    except Exception:
        logger.error("sklearn 也不可用，所有向量化方案均失败")
        return None


def build_embeddings(records: list[dict], model) -> np.ndarray:
    """为记录列表批量生成 Embedding 向量。

    使用每条记录的 "opinion" 字段作为文本输入进行向量化。
    空文本或缺失文本用空字符串占位，保证输出向量数量与记录数一致。

    Args:
        records: 记录列表，每条记录应包含 "opinion" 字段
        model: embedding 模型对象（由 _get_embedding_model 返回）

    Returns:
        np.ndarray: shape=(n_records, embedding_dim) 的向量矩阵

    Raises:
        ValueError: records 为空或 model 为 None
    """
    if not records:
        raise ValueError("记录列表为空，无法构建向量")
    if model is None:
        raise ValueError("模型为 None，无法构建向量")

    texts = [record.get("opinion", "") or "" for record in records]
    embeddings = model.encode(texts)

    # 确保返回二维数组
    if embeddings.ndim == 1:
        embeddings = embeddings.reshape(1, -1)

    logger.info(
        "向量构建完成: %d 条记录, 向量维度 %d",
        len(records), embeddings.shape[1],
    )
    return embeddings.astype(np.float32)


def cosine_similarity(query_vec: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
    """计算查询向量与所有向量的余弦相似度。

    余弦相似度公式：cos(a, b) = (a . b) / (||a|| * ||b||)

    Args:
        query_vec: 查询向量，shape=(embedding_dim,) 或 (1, embedding_dim)
        embeddings: 待比较的向量矩阵，shape=(n_records, embedding_dim)

    Returns:
        np.ndarray: shape=(n_embeddings,) 的相似度数组，取值范围 [-1, 1]
    """
    # 确保查询向量为一维
    query_flat = query_vec.flatten()

    # 计算查询向量的范数
    query_norm = np.linalg.norm(query_flat)
    if query_norm == 0:
        logger.warning("查询向量为零向量，返回全零相似度")
        return np.zeros(embeddings.shape[0], dtype=np.float32)

    # 计算所有向量的范数
    emb_norms = np.linalg.norm(embeddings, axis=1)
    # 避免除零
    emb_norms = np.where(emb_norms == 0, 1e-10, emb_norms)

    # 计算点积和相似度
    dot_products = np.dot(embeddings, query_flat)
    similarities = dot_products / (emb_norms * query_norm)

    return similarities.astype(np.float32)


def search_vectors(
    query_text: str,
    records: list[dict],
    model,
    embeddings: np.ndarray,
    top_k: int = 20,
) -> list[tuple[float, dict]]:
    """文本查询入口：用自然语言查询语义最相似的记录。

    流程：
    1. 用 model.encode(query_text) 生成查询向量
    2. 计算查询向量与所有记录的余弦相似度
    3. 按相似度从高到低排序，返回 Top-K 结果

    Args:
        query_text: 自然语言查询文本
        records: 原始记录列表（与 embeddings 顺序一致）
        model: embedding 模型对象
        embeddings: 预计算的向量矩阵
        top_k: 最多返回的匹配条数

    Returns:
        list[tuple[float, dict]]: Top-K 结果列表，每个元素为 (相似度分数, 记录字典)，
            按相似度从高到低排列
    """
    if not query_text or not query_text.strip():
        logger.warning("查询文本为空，返回空结果")
        return []

    if model is None:
        logger.error("模型为 None，无法执行向量检索")
        return []

    # 1. 生成查询向量
    query_vec = model.encode(query_text)
    query_vec = query_vec.flatten().astype(np.float32)

    # 2. 计算相似度
    scores = cosine_similarity(query_vec, embeddings)

    # 3. 排序取 Top-K
    top_k = min(top_k, len(scores))
    top_indices = np.argsort(scores)[::-1][:top_k]

    results: list[tuple[float, dict]] = []
    for idx in top_indices:
        score = float(scores[idx])
        if score < _COSINE_SIMILARITY_THRESHOLD:
            break
        results.append((score, records[idx]))

    logger.info(
        "向量检索完成: 查询='%s', 返回 %d 条",
        query_text[:50], len(results),
    )
    return results


def save_embeddings(embeddings: np.ndarray, filepath: str) -> str:
    """保存向量到 .npy 文件。

    向量的保存目录为 filepath 所在目录，同时在该目录下保存对应的
    embedding_records.json 文件（记录向量与记录的对应关系索引）。

    Args:
        embeddings: 向量矩阵，shape=(n_records, embedding_dim)
        filepath: .npy 文件保存路径（通常为 {output_dir}/persona_index/embeddings.npy）

    Returns:
        str: 写入的 .npy 文件路径
    """
    save_dir = os.path.dirname(filepath)
    os.makedirs(save_dir, exist_ok=True)

    np.save(filepath, embeddings)
    logger.info("向量已保存: %s, shape=%s", filepath, embeddings.shape)
    return filepath


def save_embedding_records(records: list[dict], filepath: str) -> str:
    """保存与向量对应的记录索引信息到 JSON 文件。

    仅保存记录的元信息（opinion、topic、source_type、time），
    不保存完整记录内容，以控制文件大小。

    Args:
        records: 与向量矩阵按行对应的记录列表
        filepath: JSON 文件保存路径

    Returns:
        str: 写入的文件路径
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # 仅保存关键元信息
    meta_records = []
    for r in records:
        meta_records.append({
            "opinion": r.get("opinion", ""),
            "topic": r.get("topic", ""),
            "source_type": r.get("source_type", ""),
            "time": r.get("time", ""),
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(meta_records, f, ensure_ascii=False, indent=2)

    logger.info("记录索引已保存: %s (%d 条)", filepath, len(meta_records))
    return filepath


def load_embeddings(filepath: str) -> np.ndarray:
    """从 .npy 文件加载向量矩阵。

    Args:
        filepath: .npy 文件路径

    Returns:
        np.ndarray: 向量矩阵，加载失败时返回空数组 shape=(0, 0)
    """
    if not os.path.isfile(filepath):
        logger.warning("向量文件不存在: %s", filepath)
        return np.empty((0, 0), dtype=np.float32)
    try:
        embeddings = np.load(filepath, mmap_mode=None)
        logger.info("向量已加载: %s, shape=%s", filepath, embeddings.shape)
        return embeddings.astype(np.float32)
    except Exception as e:
        logger.error("加载向量文件失败: %s", e)
        return np.empty((0, 0), dtype=np.float32)


def load_embedding_records(filepath: str) -> list[dict]:
    """从 JSON 文件加载与向量对应的记录索引信息。

    Args:
        filepath: JSON 文件路径

    Returns:
        list[dict]: 记录元信息列表，加载失败时返回空列表
    """
    if not os.path.isfile(filepath):
        logger.warning("记录索引文件不存在: %s", filepath)
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info("记录索引已加载: %s (%d 条)", filepath, len(records))
        return records
    except (json.JSONDecodeError, IOError) as e:
        logger.error("加载记录索引失败: %s", e)
        return []


def load_large_embeddings(filepath: str) -> np.ndarray:
    """使用 mmap 模式加载大向量文件，避免内存溢出。

    适用于向量文件很大（数百万条记录）的场景。

    Args:
        filepath: .npy 文件路径

    Returns:
        np.ndarray: 向量矩阵（mmap 模式），加载失败时返回空数组
    """
    if not os.path.isfile(filepath):
        logger.warning("向量文件不存在: %s", filepath)
        return np.empty((0, 0), dtype=np.float32)
    try:
        embeddings = np.load(filepath, mmap_mode="r")
        logger.info("向量已加载(mmap): %s, shape=%s", filepath, embeddings.shape)
        return embeddings
    except Exception as e:
        logger.error("加载向量文件失败: %s", e)
        return np.empty((0, 0), dtype=np.float32)


def append_embeddings(
    existing: np.ndarray,
    new_vectors: np.ndarray,
) -> np.ndarray:
    """增量追加：将新向量追加到现有向量矩阵末尾。

    使用 np.concatenate 实现，要求新旧向量的维度一致。

    Args:
        existing: 现有向量矩阵，shape=(n, embedding_dim)
        new_vectors: 新向量矩阵，shape=(m, embedding_dim)

    Returns:
        np.ndarray: 合并后的向量矩阵，shape=(n+m, embedding_dim)

    Raises:
        ValueError: 新旧向量维度不一致
    """
    if existing.size == 0:
        return new_vectors.copy()
    if new_vectors.size == 0:
        return existing.copy()

    if existing.shape[1] != new_vectors.shape[1]:
        raise ValueError(
            f"向量维度不一致: existing={existing.shape[1]}, new={new_vectors.shape[1]}"
        )

    combined = np.concatenate([existing, new_vectors], axis=0)
    logger.info(
        "向量追加完成: %s -> %s",
        existing.shape, combined.shape,
    )
    return combined


def append_embedding_records(
    existing_filepath: str,
    new_records: list[dict],
) -> list[dict]:
    """增量追加记录索引到 embedding_records.json 文件。

    Args:
        existing_filepath: 现有 embedding_records.json 路径
        new_records: 新追加的记录列表

    Returns:
        list[dict]: 合并后的完整记录列表
    """
    existing_records = load_embedding_records(existing_filepath)
    combined = existing_records + new_records
    save_embedding_records(combined, existing_filepath)
    logger.info(
        "记录索引追加完成: %d -> %d 条",
        len(existing_records), len(combined),
    )
    return combined


# ============================================================
# 统一入口（合并结构化索引与向量索引的构建/加载）
# ============================================================


def build_all_indices(
    data: dict,
    output_dir: str,
    build_vector: bool = True,
) -> dict[str, Any]:
    """一站式构建结构化索引和向量索引。

    对同一批三通道数据同时构建两种索引，并保存到 output_dir/persona_index/ 目录下。

    保存的文件结构：
        {output_dir}/persona_index/
        ├── by_fund.json              # 结构化索引 - 按品种
        ├── by_action.json            # 结构化索引 - 按操作类型
        ├── by_topic.json             # 结构化索引 - 按主题
        ├── by_year.json              # 结构化索引 - 按年份
        ├── metadata.json             # 结构化索引元数据
        ├── embeddings.npy            # 向量矩阵（仅 build_vector=True 时）
        └── embedding_records.json    # 记录索引（仅 build_vector=True 时）

    Args:
        data: 三通道数据字典（同 build_structured_index 的 data 参数）
        output_dir: 输出目录绝对路径
        build_vector: 是否构建向量索引（如果为 False 则跳过向量构建）

    Returns:
        dict: {
            "structured_index": dict | None,
            "embeddings": np.ndarray | None,
            "embedding_model": object | None,
            "index_dir": str,
        }
    """
    index_dir = os.path.join(output_dir, _VECTOR_INDEX_SUBDIR)
    os.makedirs(index_dir, exist_ok=True)

    result: dict[str, Any] = {
        "structured_index": None,
        "embeddings": None,
        "embedding_model": None,
        "index_dir": index_dir,
    }

    # 1. 构建并保存结构化索引
    logger.info("开始构建结构化索引...")
    structured_index = build_structured_index(data)
    save_index(structured_index, output_dir)
    result["structured_index"] = structured_index

    # 2. 构建向量索引（对 observations + principles 做语义向量化）
    if build_vector:
        logger.info("开始构建向量索引...")
        model = _get_embedding_model()
        if model is not None:
            # 向量索引的语料来源：所有含 opinion 字段的记录
            all_opinions = data.get("observations", []) + data.get("principles", [])
            if not all_opinions:
                logger.warning("无可向量化的记录（observations 和 principles 均为空）")
            else:
                embeddings = build_embeddings(all_opinions, model)
                save_embeddings(embeddings, os.path.join(index_dir, "embeddings.npy"))
                save_embedding_records(all_opinions, os.path.join(index_dir, "embedding_records.json"))
                result["embeddings"] = embeddings
                result["embedding_model"] = model
        else:
            logger.error("无法获取 Embedding 模型，向量索引构建失败")

    logger.info("所有索引构建完成，保存至: %s", index_dir)
    return result


def load_all_indices(output_dir: str) -> dict[str, Any]:
    """一站式加载已保存的结构化索引和向量索引。

    Args:
        output_dir: 输出目录绝对路径

    Returns:
        dict: {
            "structured_index": dict | None,
            "embeddings": np.ndarray | None,
            "embedding_records": list[dict] | None,
            "embedding_model": object | None,
            "index_dir": str,
        }
    """
    index_dir = os.path.join(output_dir, _VECTOR_INDEX_SUBDIR)

    result: dict[str, Any] = {
        "structured_index": None,
        "embeddings": None,
        "embedding_records": None,
        "embedding_model": None,
        "index_dir": index_dir,
    }

    # 1. 加载结构化索引
    result["structured_index"] = load_index(index_dir)

    # 2. 加载向量索引
    emb_path = os.path.join(index_dir, "embeddings.npy")
    rec_path = os.path.join(index_dir, "embedding_records.json")

    result["embeddings"] = load_embeddings(emb_path)
    result["embedding_records"] = load_embedding_records(rec_path)

    if result["embeddings"].size > 0:
        result["embedding_model"] = _get_embedding_model()
        if result["embedding_model"] is None:
            logger.warning("加载了向量文件，但无法获取 Embedding 模型（检索将不可用）")

    logger.info("所有索引加载完成，来源: %s", index_dir)
    return result


# ============================================================
# CLI 入口（扩展：支持向量索引构建）
# ============================================================


def main() -> None:
    """CLI 入口：构建并保存人物画像索引，然后加载验证。"""
    parser = argparse.ArgumentParser(description="人物画像索引构建工具")
    parser.add_argument(
        "--distill-dir", type=str, default=None,
        help="蒸馏产物目录（默认从 config 读取 paths.output_dir）",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="索引输出根目录（默认从 config 读取 paths.output_dir）",
    )
    parser.add_argument(
        "--skip-vector", action="store_true",
        help="跳过向量索引构建（仅构建结构化索引）",
    )
    args = parser.parse_args()

    config = get_config()

    # 设置日志 —— 复用 distill 的日志配置
    from scripts.distill.main import setup_logging
    setup_logging(config)

    # 路径解析
    distill_dir = args.distill_dir or config["paths"]["output_dir"]
    output_dir = args.output_dir or config["paths"]["output_dir"]
    index_subdir = config.get("persona_index", {}).get("output_subdir", "persona_index")
    dedup_field = config.get("persona_index", {}).get("dedup_field", "opinion")

    logger.info("蒸馏产物目录: %s", distill_dir)
    logger.info("索引输出根目录: %s", output_dir)
    logger.info("索引子目录: %s", index_subdir)

    # 1. 加载数据
    data = load_data(distill_dir)

    # 2. 构建结构化索引
    index = build_structured_index(data)

    # 3. 保存结构化索引
    index_dir = save_index(index, output_dir, subdir=index_subdir)

    # 4. 加载验证（幂等性检查）
    loaded = load_index(index_dir)

    # 5. 对比原始与加载后的记录数
    mismatches = []
    for dim in ["by_fund", "by_action", "by_topic", "by_year"]:
        orig_count = sum(len(v) for v in index.get(dim, {}).values())
        loaded_count = sum(len(v) for v in loaded.get(dim, {}).values())
        if orig_count != loaded_count:
            mismatches.append(f"{dim}: {orig_count} vs {loaded_count}")

    if mismatches:
        logger.warning("索引验证发现不一致: %s", "; ".join(mismatches))
    else:
        logger.info("索引验证通过: 保存与加载的记录数完全一致")

    # 6. 增量更新验证（幂等：第二次更新相同数据不应增加记录）
    updated = update_index_incremental(index, data, dedup_field=dedup_field)
    after_update_count = sum(
        sum(len(v) for v in updated.get(dim, {}).values())
        for dim in ["by_fund", "by_action", "by_topic", "by_year"]
    )
    before_update_count = sum(
        sum(len(v) for v in index.get(dim, {}).values())
        for dim in ["by_fund", "by_action", "by_topic", "by_year"]
    )
    if after_update_count == before_update_count:
        logger.info("增量更新幂等验证通过: 相同数据不重复添加")
    else:
        logger.warning(
            "增量更新幂等验证异常: 更新前 %d -> 更新后 %d",
            before_update_count, after_update_count,
        )

    # 打印摘要
    print(f"结构化索引已生成: {index_dir}/")
    for dim in ["by_fund", "by_action", "by_topic", "by_year"]:
        data_dim = index.get(dim, {})
        print(f"  {dim}: {len(data_dim)} 个分组, "
              f"{sum(len(v) for v in data_dim.values())} 条记录")

    # 7. 可选：构建向量索引
    if not args.skip_vector:
        print("\n开始构建向量索引...")
        all_opinions = data.get("observations", []) + data.get("principles", [])
        if not all_opinions:
            print("  无可向量化的记录（跳过）")
        else:
            model = _get_embedding_model()
            if model is not None:
                embeddings = build_embeddings(all_opinions, model)
                save_embeddings(embeddings, os.path.join(index_dir, "embeddings.npy"))
                save_embedding_records(all_opinions, os.path.join(index_dir, "embedding_records.json"))
                print(f"  向量索引已生成: {index_dir}/")
                print(f"  向量维度: {embeddings.shape}")
            else:
                print("  无法获取 Embedding 模型，向量索引构建失败")
    else:
        print("\n跳过向量索引构建（--skip-vector）")


if __name__ == "__main__":
    main()