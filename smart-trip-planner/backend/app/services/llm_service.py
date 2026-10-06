"""LLM服务模块"""

from hello_agents import HelloAgentsLLM
from ..config import get_settings

# 全局LLM实例
_llm_instance = None


def get_llm() -> HelloAgentsLLM:
    """
    获取LLM实例(单例模式)

    Returns:
        LLM实例（全部 Agent 共用同一个，避免重复建连接）
    """
    global _llm_instance

    if _llm_instance is None:
        settings = get_settings()

        # LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID 从环境变量读取
        _llm_instance = HelloAgentsLLM()
        
        print(f"✅ LLM服务初始化成功")
        print(f"   提供商: {_llm_instance.provider}")
        print(f"   模型: {_llm_instance.model}")
    
    return _llm_instance


def reset_llm():
    """重置LLM实例(用于测试或重新配置)"""
    global _llm_instance
    _llm_instance = None

