from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class ChatMessage(BaseModel):
    role: str = Field(..., description="role: user 或 assistant")
    text: str = Field(..., description="对话内容")

class AgentChatRequest(BaseModel):
    script_id: Optional[str] = Field(None, description="如果为空，则创建新会话")
    message: str = Field(..., description="用户的自然语言输入")
    history: List[ChatMessage] = Field(default_factory=list, description="历史对话上下文")
    project_config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="前端传来的全局项目配置，如画幅、风格")
    current_tab: Optional[str] = Field(None, description="前端当前所在工作台 tab")
    workspace_state: Optional[Dict[str, Any]] = Field(default_factory=dict, description="前端当前工作台状态快照，可选")
