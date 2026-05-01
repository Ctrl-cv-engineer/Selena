import json
import logging
import os
import threading
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue, PointIdsList
from datetime import datetime
from project_config import get_project_config
import requests

try:
    from DialogueSystem.llm.CallingAPI import call_LLM, call_rerank, call_Embedding
except ImportError:
    from DialogueSystem.CallingAPI import call_LLM, call_rerank, call_Embedding

config = get_project_config()
logger = logging.getLogger(__name__)
db_request_logger = logging.getLogger("database.request")

class Qdrant():
    """
    Qdrant 本地向量库封装。

    设计目标：
    1) 统一集合初始化、增删改查与检索重排逻辑；
    2) 保证写入向量维度与集合配置严格一致；
    3) 在启动时执行一次 TTL 维护与温度升降级；
    4) 兼容 qdrant-client 的 search/query_points 两套查询接口。
    """
    _shared_clients = {}
    _shared_clients_lock = threading.Lock()

    def __init__(self, CollectionName, size: int=1024, session: requests.Session=None) -> None:
        """
        初始化集合级别配置与检索参数。

        Args:
            CollectionName: 目标集合名。
            size: 向量维度，必须与 Embedding 输出一致。
        """
        qdrant_setting = config.get("Qdrant_Setting", {})
        self.CollectionName = CollectionName
        self.DBpath = qdrant_setting.get("local_data_path", "./qdrant_data")
        self.qdrant_host = qdrant_setting.get("host", "127.0.0.1")
        self.qdrant_port = int(qdrant_setting.get("port", 6333))
        self.qdrant_grpc_port = int(qdrant_setting.get("grpc_port", 6334))
        self.prefer_grpc = bool(qdrant_setting.get("prefer_grpc", False))
        # client 为 qdrant 连接实例；session 复用 embedding 请求连接（沿用原命名）
        self.client = None
        self.session = session
        # 向量维度需与 Embedding 模型输出一致
        self.size = size
        # 从 config.json 读取阈值与增减系数，未配置时使用默认值

        # 重要性阈值超过此值则不会删除
        self.importance_threshold = config["VectorSetting"].get("Importance_Threshold", 0.8)
        self.decay_thread = None
        # SearchScore 上限，避免检索多次命中后无上限膨胀
        self.max_search_score = config["VectorSetting"].get("Max_SearchScore", 3.0)
        # 当searchscore处于升级和降级之间时，并且已经为cold时，SearchScore 增益倍数
        self.cold_search_score_multiplier = config["VectorSetting"].get("Cold_SearchScore_Multiplier", 0.3)
        # 当searchscore处于升级和降级之间时，并且不为cold时，SearchScore 增益倍数
        self.other_search_score_multiplier = config["VectorSetting"].get("Other_SearchScore_Multiplier", 0.7)
        # 检索命中后的 SearchScore 增益倍数
        self.boost_factor = config["VectorSetting"].get("Boost_Factor", 1.5)
        # 温度权重用于检索重排：hot > warm > cold
        self.temperature_weight = config["VectorSetting"].get(
            "Temperature_Weight",
            {"hot": 1.5, "warm": 1.2, "cold": 1.0}
        )
        # 重排公式的统一缩放系数
        self.rerank_scale = config["VectorSetting"].get("Rerank_Scale", 0.75)
        # 当TTL到0时，SearchScore超过此值则升级
        self.upgrade_score_threshold = config["VectorSetting"].get("Upgrade_Score_Threshold", 1.5)
        # 当TTL到0时，SearchScore低于此值则降级
        self.downgrade_score_threshold = config["VectorSetting"].get("Downgrade_Score_Threshold", 0.2)
        # 当TTL到0时，数据升级新的TTL的倍数
        self.upgrade_ttl_multiplier = config["VectorSetting"].get("Upgrade_TTL_Multiplier", 3)
        # 当TTL到0时，数据降级新的TTL的倍数
        self.downgrade_ttl_multiplier = config["VectorSetting"].get("Downgrade_TTL_Multiplier", 1)
        # 新增记忆默认生效天数
        self.default_ttl_days = config["VectorSetting"].get("Default_TTL_Days", 30)
        # 去重阈值：余弦相似度达到该阈值即视为“同向量”
        self.duplicate_vector_threshold = config["VectorSetting"].get("Duplicate_Vector_Threshold", 0.999999)
        self.temperature_order = ["cold", "warm", "hot"]

        self.rank_score = config["VectorSetting"].get("Rank_Score", 0.5)

    @staticmethod
    def _stringify_log_value(value):
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(item) for item in value)
        return value

    def _format_log_fields(self, **kwargs):
        parts = []
        for key, value in kwargs.items():
            if value is None:
                continue
            parts.append(f"{key}={self._stringify_log_value(value)}")
        return " | ".join(parts)

    def _log_db_request(self, action: str, **kwargs):
        detail = self._format_log_fields(collection=self.CollectionName, **kwargs)
        message = f"Qdrant request | action={action}"
        if detail:
            message = f"{message} | {detail}"
        db_request_logger.info(message)

    def _log_db_success(self, action: str, **kwargs):
        detail = self._format_log_fields(collection=self.CollectionName, **kwargs)
        message = f"Qdrant request succeeded | action={action}"
        if detail:
            message = f"{message} | {detail}"
        logger.info(message)

    def _get_client_key(self):
        """
        生成 Qdrant 客户端连接键。
        """
        return f"{self.qdrant_host}:{self.qdrant_port}:{self.qdrant_grpc_port}:{self.prefer_grpc}"

    def _acquire_client(self):
        """
        获取或创建 Qdrant 客户端连接，增加引用计数。
        """
        if self.client is not None:
            return self.client
        client_key = self._get_client_key()
        with self._shared_clients_lock:
            client_state = self._shared_clients.get(client_key)
            if client_state is None:
                try:
                    client = QdrantClient(
                        host=self.qdrant_host,
                        port=self.qdrant_port,
                        prefer_grpc=self.prefer_grpc,
                        grpc_port=self.qdrant_grpc_port
                    )
                except TypeError:
                    client = QdrantClient(
                        host=self.qdrant_host,
                        port=self.qdrant_port,
                        prefer_grpc=self.prefer_grpc
                    )
                client_state = {"client": client, "ref_count": 0}
                self._shared_clients[client_key] = client_state
            client_state["ref_count"] += 1
            self.client = client_state["client"]
        return self.client

    def _release_client(self):
        """
        释放当前 Qdrant 客户端连接，减少引用计数。
        """
        if self.client is None:
            return
        client_key = self._get_client_key()
        with self._shared_clients_lock:
            client_state = self._shared_clients.get(client_key)
            if client_state is None:
                self.client = None
                return
            client_state["ref_count"] -= 1
            if client_state["ref_count"] <= 0:
                client_state["client"].close()
                self._shared_clients.pop(client_key, None)
            self.client = None

    def createDB(self):
        """
        初始化 Qdrant 客户端并确保集合可用。

        行为说明：
        - 若集合不存在：按 self.size 创建。
        - 若集合已存在但维度不一致：删除后重建，避免写入时报维度错误。
        - 初始化完成后启动后台维护线程，执行 TTL 与温度策略。
        """
        if self.client is None:
            self._acquire_client()
        self._log_db_request("create_collection", vector_size=self.size)
        need_recreate = False
        created = False
        if self.client.collection_exists(self.CollectionName):
            collection_info = self.client.get_collection(self.CollectionName)
            vectors_config = collection_info.config.params.vectors
            current_size = self._extract_vector_size(vectors_config)
            if current_size != self.size:
                need_recreate = True
        if need_recreate:
            self.client.delete_collection(self.CollectionName)
        if not self.client.collection_exists(self.CollectionName):
            created = True
            self.client.create_collection(
                collection_name=self.CollectionName,
                vectors_config=VectorParams(
                    size=self.size,
                    distance=Distance.COSINE
                )
            )
        self._log_db_success(
            "create_collection",
            vector_size=self.size,
            created=created,
            recreated=need_recreate
        )
        # 启动时异步执行维护任务：TTL扣减、温度等级调整
        self.decay_thread = threading.Thread(target=self._run_startup_maintenance, daemon=True)
        self.decay_thread.start()

    def _extract_vector_size(self, vectors_config):
        """
        从集合配置中提取向量维度。

        兼容两类结构：
        - 单向量配置：VectorParams
        - 命名向量配置：dict[str, VectorParams]
        """
        if isinstance(vectors_config, dict):
            if not vectors_config:
                return None
            first_key = next(iter(vectors_config))
            vector_params = vectors_config[first_key]
            return getattr(vector_params, "size", None)
        return getattr(vectors_config, "size", None)

    def _normalize_vector(self, vector):
        """
        归一化输入向量格式并做维度校验。

        支持输入：
        - numpy.ndarray（通过 tolist 转为 list）
        - tuple（转 list）
        - [[...]] 形态的单条二维列表（自动展平为一维）

        Raises:
            ValueError: 非一维数组或维度不匹配时抛出。
        """
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if isinstance(vector, tuple):
            vector = list(vector)
        if isinstance(vector, list) and len(vector) == 1 and isinstance(vector[0], (list, tuple)):
            vector = list(vector[0])
        if not isinstance(vector, list):
            raise ValueError("vector 必须是一维数组")
        if len(vector) != self.size:
            raise ValueError(f"向量维度不匹配: 当前集合为 {self.size} 维，输入为 {len(vector)} 维")
        return vector

    def _parse_timestamp(self, value):
        """
        解析时间戳值为 datetime 对象。

        支持输入：
        - datetime 对象
        - Unix 秒级时间戳
        - ISO 时间字符串（支持 Z 格式）

        Returns:
            解析后的 datetime 对象或 None。
        """
        # 支持三类时间输入：
        # 1) datetime 对象；2) Unix 秒级时间戳；3) ISO 时间字符串
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value)
            except:
                return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except:
                return None
        return None

    def _extract_text_value(self, value):
        """
        从输入值中提取文本内容。

        支持输入：
        - 字典（包含 personalizedText 或 text 键）
        - 字符串（直接返回）
        - 其他类型（返回 None）


        Returns:
            提取的文本内容或 None。
        """
        if isinstance(value, dict):
            value = value.get("personalizedText") or value.get("text")
        if isinstance(value, str):
            return value
        return None

    def _run_startup_maintenance(self):
        self._log_db_request("startup_maintenance")
        """
        启动时全量扫描并维护记忆状态。

        处理步骤：
        1) 基于 UpdateTime（缺省回落 timestamp）计算过期天数，并用 initialTTL 反推当前 ttl；
        2) 当 ttl 降为 0 时，根据 SearchScore 做温度升降级；
        3) 升降级后刷新 ttl 与 initialTTL；
        4) 仅回写变化字段，避免覆盖其他业务字段。
        """
        # 启动维护策略：
        # 1) ttl = max(initialTTL - (now - timestamp).days, 0)
        # 2) ttl==0时按SearchScore调整temperature_type，并重置ttl/initialTTL
        now = datetime.now()
        now_iso = now.isoformat()
        offset = None
        updated_points = 0
        deleted_points = 0
        while True:
            records, offset = self.client.scroll(
                collection_name=self.CollectionName,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            if not records:
                break
            for record in records:
                payload = record.payload or {}
                UpdateTime = self._parse_timestamp(payload.get("UpdateTime"))
                if UpdateTime is None:
                    UpdateTime = self._parse_timestamp(payload.get("timestamp"))
                ttl = payload.get("ttl")
                initial_ttl = payload.get("initialTTL")
                search_score = payload.get("SearchScore")
                temperature_type = payload.get("temperature_type")
                importance = payload.get("importance")
                updates = {}
                try:
                    ttl = int(ttl)
                    initial_ttl = int(initial_ttl)
                    search_score = float(search_score)
                    importance = float(importance)
                except:
                    continue
                if UpdateTime is not None:
                    elapsed_days = max(0, (now - UpdateTime).days)
                    new_ttl = max(0, initial_ttl - elapsed_days)
                else:
                    new_ttl = max(0, ttl)
                updates["ttl"] = new_ttl
                if new_ttl == 0:
                    if temperature_type == "cold" and search_score <= 0.1 and importance < self.importance_threshold:
                        self.client.delete(
                            collection_name=self.CollectionName,
                            points_selector=PointIdsList(points=[record.id])
                        )
                        deleted_points += 1
                        continue
                    # 高活跃记忆升温：分高且已过期时，温度上调一级并延长TTL
                    if search_score > self.upgrade_score_threshold and temperature_type in self.temperature_order:
                        if temperature_type != "hot":
                            next_type = self.temperature_order[self.temperature_order.index(temperature_type) + 1]
                            refreshed_ttl = max(1, int(initial_ttl * self.upgrade_ttl_multiplier))
                            updates["temperature_type"] = next_type
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = search_score - self.upgrade_score_threshold
                            updates["UpdateTime"] = now_iso
                        else:
                            refreshed_ttl = max(1, int(initial_ttl * self.upgrade_ttl_multiplier))
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = search_score * self.other_search_score_multiplier
                            updates["UpdateTime"] = now_iso
                    # 低活跃记忆降温：分低且已过期时，温度下调一级并按规则刷新TTL
                    elif search_score < self.downgrade_score_threshold and temperature_type in self.temperature_order:
                        if temperature_type != "cold":
                            next_type = self.temperature_order[self.temperature_order.index(temperature_type) - 1]
                            refreshed_ttl = max(1, int(initial_ttl * self.downgrade_ttl_multiplier))
                            updates["temperature_type"] = next_type
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = 0.1
                            updates["UpdateTime"] = now_iso
                        else:
                            refreshed_ttl = max(1, int(initial_ttl * self.downgrade_ttl_multiplier))
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = search_score * self.cold_search_score_multiplier
                            updates["UpdateTime"] = now_iso
                    elif search_score > self.downgrade_score_threshold and search_score < self.upgrade_score_threshold and temperature_type in self.temperature_order:
                        if temperature_type == "cold":
                            refreshed_ttl = max(1, int(initial_ttl * self.downgrade_ttl_multiplier))
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = search_score * self.cold_search_score_multiplier
                            updates["UpdateTime"] = now_iso
                        else:
                            next_type = self.temperature_order[self.temperature_order.index(temperature_type) - 1]
                            refreshed_ttl = max(1, int(initial_ttl * self.downgrade_ttl_multiplier))
                            updates["temperature_type"] = next_type
                            updates["ttl"] = refreshed_ttl
                            updates["initialTTL"] = refreshed_ttl
                            updates["SearchScore"] = search_score * self.other_search_score_multiplier
                            updates["UpdateTime"] = now_iso
                # 只更新变化字段，避免覆盖未处理字段
                self.client.set_payload(
                    collection_name=self.CollectionName,
                    payload=updates,
                    points=[record.id]
                )
                updated_points += 1
            if offset is None:
                break
        self._log_db_success(
            "startup_maintenance",
            updated_points=updated_points,
            deleted_points=deleted_points
        )
    def _get_next_id(self):
        """
        生成下一个自增整型 ID。

        实现方式：
        - 分页扫描集合内所有点；
        - 找到最大 int 类型 id；
        - 返回 max_id + 1。
        """
        # 自增主键策略：扫描当前集合最大整数 id，下一条使用 max_id + 1
        max_id = -1
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.CollectionName,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False
            )
            if not records:
                break
            for record in records:
                if isinstance(record.id, int):
                    max_id = max(max_id, record.id)
            if offset is None:
                break
        return max_id + 1

    def _find_duplicate_id_by_vector(self, vector):
        # 向量去重策略：
        # 用当前向量在库内做一次近邻检索，若最高相似度达到阈值，则复用该记录 id 进行覆盖写入
        if hasattr(self.client, "search"):
            results = self.client.search(
                collection_name=self.CollectionName,
                query_vector=vector,
                limit=1,
                score_threshold=self.duplicate_vector_threshold,
                with_payload=False,
                with_vectors=False
            )
        else:
            results = self.client.query_points(
                collection_name=self.CollectionName,
                query=vector,
                limit=1,
                score_threshold=self.duplicate_vector_threshold,
                with_payload=False,
                with_vectors=False
            ).points
        if results:
            return results[0].id
        return None

    def _upsert_point(self, vector, id=None, deduplication: bool = True, **payload):
        self._log_db_request("upsert_point", deduplication=deduplication)
        if vector is None:
            raise ValueError("vector 不能为空")
        vector = self._normalize_vector(vector)
        duplicate_id = None
        if deduplication:
            duplicate_id = self._find_duplicate_id_by_vector(vector)
        if duplicate_id is not None:
            id = duplicate_id
        elif id is None:
            id = self._get_next_id()
        payload = {k: v for k, v in payload.items() if v is not None}
        if isinstance(payload.get("timestamp"), datetime):
            payload["timestamp"] = payload["timestamp"].isoformat()
        if isinstance(payload.get("UpdateTime"), datetime):
            payload["UpdateTime"] = payload["UpdateTime"].isoformat()
        if payload.get("initialTTL") is None and payload.get("ttl") is not None:
            payload["initialTTL"] = payload["ttl"]
        if payload.get("UpdateTime") is None and payload.get("timestamp") is not None:
            payload["UpdateTime"] = payload["timestamp"]
        points = [
            PointStruct(
                id=id,
                vector=vector,
                payload=payload
            )
        ]
        self.client.upsert(
            collection_name=self.CollectionName,
            points=points
        )
        self._log_db_success(
            "upsert_point",
            id=id,
            duplicate_reused=(duplicate_id is not None),
            payload_keys=sorted(payload.keys())
        )
        return payload.get("text"), id

    def add(self, text, personalizedText, vector, *, textType, importance, timestamp, ttl, id=None, deduplication: bool = True,
            temperature_type="warm", SearchScore: float = 0.1, initialTTL=None, UpdateTime=None, **kwargs):
        """
        新增一条带完整记忆字段的向量记录（支持按向量去重覆盖）。

        Args:
            text: 记忆文本（写入 payload["text"]）。
            personalizedText: 将text中的内容转换为人称代词。即站在用户角度说出的话。
            vector: 一维向量，长度必须等于 self.size。
            textType: 文本类型。
            importance: 重要度。
            timestamp: 创建时间。
            ttl: 当前有效期。
            id: 指定点 ID；若为空则自动分配。
            deduplication: 是否启用“同向量覆盖”逻辑。
            temperature_type: 温度类型，默认 warm。
            SearchScore: 检索分数，默认 0.1。
            initialTTL: 初始 TTL；为空时等于 ttl。
            UpdateTime: 更新时间；为空时等于 timestamp。
            **kwargs: 其余业务字段，透传到 payload。

        Returns:
            (text, id): 写入后的文本与点 ID。
        """
        if text is None:
            raise ValueError("text 不能为空")
        if personalizedText is None:
            raise ValueError("personalizedText 不能为空")
        if vector is None:
            raise ValueError("vector 不能为空")
        if textType is None:
            raise ValueError("textType 不能为空")
        if importance is None:
            raise ValueError("importance 不能为空")
        if timestamp is None:
            raise ValueError("timestamp 不能为空")
        if ttl is None:
            raise ValueError("ttl 不能为空")
        if initialTTL is None:
            initialTTL = ttl
        if UpdateTime is None:
            UpdateTime = timestamp
        return self._upsert_point(
            vector=vector,
            id=id,
            deduplication=deduplication,
            text=text,
            personalizedText=personalizedText,
            textType=textType,
            importance=importance,
            timestamp=timestamp,
            ttl=ttl,
            initialTTL=initialTTL,
            UpdateTime=UpdateTime,
            temperature_type=temperature_type,
            SearchScore=SearchScore,
            **kwargs
        )

    def addRaw(self, vector, text=None, id=None, deduplication: bool = True, **kwargs):
        """
        新增一条宽松字段的向量记录（支持按向量去重覆盖）。

        Args:
            vector: 一维向量，长度必须等于 self.size。
            text: 记忆文本，可为空。
            id: 指定点 ID；若为空则自动分配。
            deduplication: 是否启用“同向量覆盖”逻辑。
            **kwargs: 其余业务字段，透传到 payload。

        Returns:
            (text, id): 写入后的文本与点 ID。
        """
        return self._upsert_point(
            vector=vector,
            id=id,
            deduplication=deduplication,
            text=text,
            **kwargs
        )

    def ExportAllData(self, file_name="qdrant_all_data.json"):
        self._log_db_request("export_all_data", file_name=file_name)
        # 导出全部 payload 到可读 JSON，不包含向量，便于人工排查与审计
        all_data = []
        offset = None
        while True:
            records, offset = self.client.scroll(
                collection_name=self.CollectionName,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            if not records:
                break
            for record in records:
                all_data.append({
                    "id": record.id,
                    "payload": record.payload or {}
                })
            if offset is None:
                break
        output_path = os.path.join(os.getcwd(), file_name)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        self._log_db_success("export_all_data", file_name=file_name, record_count=len(all_data))
        return output_path, len(all_data)

    def delete(self, id):
        self._log_db_request("delete_point", id=id)
        """
        按点 ID 删除单条记录。

        Args:
            id: 点 ID。
        """
        self.client.delete(
            collection_name=self.CollectionName,
            points_selector=PointIdsList(points=[id])
        )
        self._log_db_success("delete_point", id=id)
        return id

    def deleteCollection(self, collection_name=None):
        target_collection = collection_name or self.CollectionName
        self._log_db_request("delete_collection", target_collection=target_collection)
        """
        删除指定集合。

        Args:
            collection_name: 目标集合名；为空时使用当前实例集合名。

        Returns:
            实际删除的集合名。
        """
        target_collection = collection_name or self.CollectionName
        local_client = self.client
        need_release = False
        if local_client is None:
            local_client = self._acquire_client()
            need_release = True
        try:
            if local_client.collection_exists(target_collection):
                local_client.delete_collection(target_collection)
            self._log_db_success("delete_collection", target_collection=target_collection)
            return target_collection
        finally:
            if need_release:
                self._release_client()

    def update(self, id, text=None, vector=None, temperature_type=None, textType=None, importance=None, SearchScore:float = 0.1, timestamp=None, ttl=None):
        self._log_db_request("update_point", id=id)
        """
        更新指定点的文本、向量与业务 payload 字段。

        规则：
        - text 为空时先读取旧记录中的 text；
        - 基于最终 text 重新计算 embedding 并回写向量；
        - timestamp 若为 datetime，会统一序列化为 ISO 字符串。
        """
        if text is None:
            current = self.client.retrieve(
                collection_name=self.CollectionName,
                ids=[id],
                with_payload=True,
                with_vectors=False
            )
            if not current:
                return None
            payload = current[0].payload or {}
            text = payload.get("text")
        vector = self._normalize_vector(vector)
        # update 支持 datetime 时间对象，统一转 ISO 字符串存储
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        payload = {"text": text, "temperature_type": temperature_type, "textType": textType,
                 "importance": importance, "SearchScore": SearchScore, "timestamp": timestamp, 
                 "ttl": ttl, "initialTTL": ttl}
        if timestamp is not None and ttl is not None:
            payload["UpdateTime"] = timestamp
        payload = {k: v for k, v in payload.items() if v is not None}
        self.client.upsert(
            collection_name=self.CollectionName,
            points=[PointStruct(id=id, vector=vector, payload=payload)]
        )
        self._log_db_success("update_point", id=id, payload_keys=sorted(payload.keys()))
        return text, id

    def _build_query_filter(self, **kwargs):
        """
        将任意 key=value 条件组装为 Qdrant Filter.must。

        Returns:
            Filter 或 None（当无有效条件时）。
        """
        # 过滤条件支持任意参数个数，按 key=value 生成 must 条件
        conditions = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in kwargs.items()
            if value is not None
        ]
        if not conditions:
            return None
        return Filter(must=conditions)

    def _search_points(self, query, query_filter=None):
        """
        底层检索适配层，兼容不同客户端接口。

        优先使用 search（旧接口），否则回落到 query_points（新接口）。
        """
        # 统一封装底层查询，兼容 search / query_points 两套接口
        if hasattr(self.client, "search"):
            return self.client.search(
                collection_name=self.CollectionName,
                query_vector=query,
                query_filter=query_filter,
                limit=config["VectorSetting"]["Top_k"]
            )
        return self.client.query_points(
            collection_name=self.CollectionName,
            query=query,
            query_filter=query_filter,
            limit=config["VectorSetting"]["Top_k"]
        ).points

    def Search(self, query, query_text=None, **kwargs):
        self._log_db_request(
            "search",
            rerank=bool(query_text),
            filter_keys=sorted(key for key, value in kwargs.items() if value is not None)
        )
        """
        执行检索并按 rerank 结果排序。

        Args:
            query: 查询向量。
            query_text: 查询文本（用于 rerank 排序）。
            **kwargs: 动态过滤条件。

        Returns:
            排序后的记录列表，每个记录包含 score 字段。

        与 SearchRawScore 的区别：
        - Search 会触发命中增益与 rerank 排序；
        - SearchRawScore 只返回原始向量相似度结果。
        """
        query_filter = self._build_query_filter(**kwargs)
        results = self._search_points(query, query_filter)
        reranked_results = self._rerank_and_boost(results, query_text=query_text)
        self._log_db_success(
            "search",
            rerank=bool(query_text),
            result_count=len(reranked_results)
        )
        return reranked_results

    def SearchRawScore(self, query, **kwargs):
        self._log_db_request(
            "search_raw_score",
            filter_keys=sorted(key for key, value in kwargs.items() if value is not None)
        )
        """
        执行检索并返回原始相似度分数，不做重排与增益写回。

        Args:
            query: 查询向量。
            **kwargs: 动态过滤条件。

        Returns:
            原始向量相似度分数列表。
        """
        # 模式二：按动态过滤条件查询，直接返回原始向量分数
        query_filter = self._build_query_filter(**kwargs)
        results = self._search_points(query, query_filter)
        self._log_db_success("search_raw_score", result_count=len(results))
        return results

    def _rerank_and_boost(self, results, query_text=None):
        """
        对检索结果进行“增益+排序”。

        增益逻辑：
        - 当 rerank 命中且 relevance_score > 0.5 时，提升 SearchScore（受上限约束）。

        排序逻辑：
        - 直接使用 rerank 返回顺序与 relevance_score 作为最终结果。
        """
        if not query_text:
            return results
        text_to_records = {}
        result_texts = []
        for record in results:
            record_text = self._extract_text_value((record.payload or {}).get("personalizedText"))
            if not record_text:
                continue
            result_texts.append(record_text)
            text_to_records.setdefault(record_text, []).append(record)
        if not result_texts:
            return results
        try:
            rerank_results = call_rerank(ResultList=result_texts, session=self.session, text=query_text)
        except:
            return results
        logger.info(
            "Rerank threshold | collection=%s | threshold=%s | candidate_count=%s",
            self.CollectionName,
            self.rank_score,
            len(rerank_results or [])
        )
        reranked_records = []
        for index, item in enumerate(rerank_results or [], start=1):
            rerank_text = self._extract_text_value(item.get("text"))
            if not rerank_text or rerank_text not in text_to_records or not text_to_records[rerank_text]:
                continue
            record = text_to_records[rerank_text].pop(0)
            payload = record.payload or {}
            search_score = payload.get("SearchScore")
            relevance_score = item.get("relevance_score")
            try:
                search_score = float(search_score)
                relevance_score = float(relevance_score)
            except:
                record.score = 0
                reranked_records.append(record)
                continue
            logger.info(
                "Rerank score | collection=%s | index=%s | record_id=%s | relevance_score=%s | threshold=%s | passed=%s",
                self.CollectionName,
                index,
                getattr(record, "id", None),
                relevance_score,
                self.rank_score,
                relevance_score > self.rank_score
            )
            if relevance_score > self.rank_score:
                new_search_score = min(self.max_search_score, search_score * self.boost_factor)
                payload["SearchScore"] = new_search_score
                self.client.set_payload(
                    collection_name=self.CollectionName,
                    payload={"SearchScore": new_search_score},
                    points=[record.id]
                )
            record.score = relevance_score
            reranked_records.append(record)
        return reranked_records

    def close(self):
        """
        主动释放资源。

        - 等待后台维护线程短暂退出；
        - 显式关闭 qdrant client，避免依赖解释器析构阶段清理。
        """
        if self.decay_thread is not None and self.decay_thread.is_alive():
            self.decay_thread.join(timeout=2)
        if self.client is not None:
            self._release_client()

    def __del__(self):
        try:
            self.close()
        except:
            pass
    
    
    
if __name__  == '__main__':
    qdrant = Qdrant("test_collection")
    try:
        qdrant.createDB()
        now = datetime.now()
        vector = call_Embedding("这是一个测试", qdrant.session)
        result, id = qdrant.add(
            "这是一个测试",
            "这是一个测试",
            vector,
            temperature_type="hot",
            textType="test",
            importance=0.5,
            SearchScore=0.1,
            timestamp=now,
            ttl=30,
            initialTTL=30
        )
        result = qdrant.Search(call_Embedding("今天天气怎么样", qdrant.session))
        export_path, count = qdrant.ExportAllData()
        print(result, id, export_path, count)
    finally:
        qdrant.close()
