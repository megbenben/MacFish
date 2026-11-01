"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI兼容格式，默认使用DeepSeek）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'deepseek-chat')

    # 本地 LLM 配置（Ollama / LM Studio / vLLM 等）
    LOCAL_LLM_API_KEY = os.environ.get('LOCAL_LLM_API_KEY', 'not-needed')
    LOCAL_LLM_BASE_URL = os.environ.get('LOCAL_LLM_BASE_URL', 'http://localhost:11434/v1')
    LOCAL_LLM_MODEL_NAME = os.environ.get('LOCAL_LLM_MODEL_NAME', 'qwen2.5:7b')

    # 当前激活的 LLM 供应源: "network" 或 "local"
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'network')
    
    # 历史遗留：知识图谱早已本地化为 SQLite（LocalGraphClient 根本不使用 api_key），
    # 这个键不再参与任何功能。保留属性只为兼容仍写 `api_key or Config.ZEP_API_KEY`
    # 的旧调用点，取值为 None 完全正常，也不再参与 validate()。
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    # 能真正解析的扩展名（不带点，对照 allowed_file() 的用法）
    ALLOWED_EXTENSIONS = {
        'pdf', 'md', 'markdown', 'txt', 'csv',
        'docx', 'pptx', 'xlsx', 'xlsm',
        'png', 'jpg', 'jpeg', 'heic', 'heif',
        'tiff', 'tif', 'bmp', 'webp', 'gif',
    }
    # 旧版 Office 二进制格式：单独列出，只为了给出「请另存为 .docx/.pptx/.xlsx」
    # 的提示。刻意不放进 ALLOWED_EXTENSIONS —— 否则它们会被存盘然后以库报错收场。
    LEGACY_EXTENSIONS = {'doc', 'ppt', 'xls'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    MAX_CHUNK_SIZE = int(os.environ.get('MAX_CHUNK_SIZE', '20000'))  # 切块上限（防御异常请求）

    # 知识图谱抽取配置
    # 单次抽取送入 LLM 的最大字符数。此前硬编码为 3000，而 chunk_size 最大
    # 可配到 20000，导致超长 chunk 的后半段被静默丢弃。默认放到 12000，
    # 仍保留上限以避免把上下文撑爆。
    GRAPH_EXTRACTION_MAX_CHARS = int(os.environ.get('GRAPH_EXTRACTION_MAX_CHARS', '12000'))
    # 同批 chunk 的并发抽取数。SQLite 用 WAL + busy_timeout，4 路并发足够。
    GRAPH_EXTRACTION_CONCURRENCY = int(os.environ.get('GRAPH_EXTRACTION_CONCURRENCY', '4'))

    # 人设生成缓存：同一实体 + 同一图谱上下文只生成一次，重建/重跑时复用
    PERSONA_CACHE_ENABLED = os.environ.get('PERSONA_CACHE_ENABLED', 'true').lower() == 'true'
    PERSONA_CACHE_DIR = os.path.join(os.path.dirname(__file__), '../uploads/cache/personas')

    # LLM 调用可靠性
    LLM_TIMEOUT = float(os.environ.get('LLM_TIMEOUT', '120'))
    LLM_MAX_RETRIES = int(os.environ.get('LLM_MAX_RETRIES', '3'))
    LLM_RETRY_INITIAL_DELAY = float(os.environ.get('LLM_RETRY_INITIAL_DELAY', '1.0'))

    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    # 预测市场默认参数（可被 simulation_config.json 的 market 块覆盖）
    MARKET_INITIAL_PROBABILITY = float(os.environ.get('MARKET_INITIAL_PROBABILITY', '0.5'))
    MARKET_LIQUIDITY = float(os.environ.get('MARKET_LIQUIDITY', '20'))

    # 单次模拟允许的最大轮数。LLM 生成的时间配置必须被夹在这个范围内，
    # 否则 168h/30min = 336 轮会把调用量放大一个数量级。
    OASIS_MAX_ROUNDS = int(os.environ.get('OASIS_MAX_ROUNDS', '72'))
    OASIS_DEFAULT_TOTAL_HOURS = int(os.environ.get('OASIS_DEFAULT_TOTAL_HOURS', '24'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        errors = []
        # 只在云端 provider 模式下检查 LLM_API_KEY
        if cls.LLM_PROVIDER == 'network' and not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置（云端模式）：请在 .env 中配置 LLM_API_KEY")
        return errors

