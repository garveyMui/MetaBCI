from __future__ import annotations

from socket import socket, AF_INET, SOCK_STREAM, SOCK_DGRAM
from typing import Dict, List

from fastapi import APIRouter, Request
from flask_sqlalchemy import SQLAlchemy
from langchain.prompts.prompt import PromptTemplate
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sse_starlette import EventSourceResponse
from zhipuai import ZhipuAI

from chatchat.server.api_server.api_schemas import OpenAIChatInput
from chatchat.server.chat.chat import chat
from chatchat.server.chat.kb_chat import kb_chat
from chatchat.server.chat.feedback import chat_feedback
from chatchat.server.chat.file_chat import file_chat
from chatchat.server.db.repository import add_message_to_db
from chatchat.server.utils import (
    get_OpenAIClient,
    get_prompt_template,
    get_tool,
    get_tool_config,
)
from chatchat.settings import Settings
from chatchat.utils import build_logger
from .openai_routes import openai_request, OpenAIChatOutput

logger = build_logger()

chat_router = APIRouter(prefix="/chat", tags=["ChatChat 对话"])

chat_router.post(
    "/chat",
    summary="与llm模型对话(通过LLMChain)",
)(chat)

chat_router.post(
    "/feedback",
    summary="返回llm模型对话评分",
)(chat_feedback)

chat_router.post("/kb_chat", summary="知识库对话")(kb_chat)
chat_router.post("/file_chat", summary="文件对话")(file_chat)


@chat_router.post("/chat/completions", summary="兼容 openai 的统一 chat 接口")
async def chat_completions(
        request: Request,
        body: OpenAIChatInput,
) -> Dict:
    """
    请求参数与 openai.chat.completions.create 一致，可以通过 extra_body 传入额外参数
    tools 和 tool_choice 可以直接传工具名称，会根据项目里包含的 tools 进行转换
    通过不同的参数组合调用不同的 chat 功能：
    - tool_choice
        - extra_body 中包含 tool_input: 直接调用 tool_choice(tool_input)
        - extra_body 中不包含 tool_input: 通过 agent 调用 tool_choice
    - tools: agent 对话
    - 其它：LLM 对话
    以后还要考虑其它的组合（如文件对话）
    返回与 openai 兼容的 Dict
    """
    # import rich
    # rich.print(body)

    # 当调用本接口且 body 中没有传入 "max_tokens" 参数时, 默认使用配置中定义的值
    if body.max_tokens in [None, 0]:
        body.max_tokens = Settings.model_settings.MAX_TOKENS

    #client = get_OpenAIClient(model_name=body.model, is_async=True)
    #client.chat.completions.create()
    client = ZhipuAI(api_key="APIKey")  # 填写您自己的APIKey
    body.model = "emohaa"
    # body.model = "glm-4"
    # body.stream = False
    extra = {**body.model_extra} or {}

    for key in list(extra):
        delattr(body, key)

    for key in ["top_p", "presence_penalty", 'frequency_penalty']:
        delattr(body, key)
    # check tools & tool_choice in request body
    if isinstance(body.tool_choice, str):
        if t := get_tool(body.tool_choice):
            body.tool_choice = {"function": {"name": t.name}, "type": "function"}
    if isinstance(body.tools, list):
        for i in range(len(body.tools)):
            if isinstance(body.tools[i], str):
                if t := get_tool(body.tools[i]):
                    body.tools[i] = {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.args,
                        },
                    }

    conversation_id = extra.get("conversation_id")

    # chat based on result from one choiced tool
    if body.tool_choice:
        tool = get_tool(body.tool_choice["function"]["name"])
        if not body.tools:
            body.tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.args,
                    },
                }
            ]
        if tool_input := extra.get("tool_input"):
            try:
                message_id = (
                    add_message_to_db(
                        chat_type="tool_call",
                        query=body.messages[-1]["content"],
                        conversation_id=conversation_id,
                    )
                    if conversation_id
                    else None
                )
            except Exception as e:
                logger.warning(f"failed to add message to db: {e}")
                message_id = None

            tool_result = await tool.ainvoke(tool_input)
            prompt_template = PromptTemplate.from_template(
                get_prompt_template("rag", "default"), template_format="jinja2"
            )
            body.messages[-1]["content"] = prompt_template.format(
                context=tool_result, question=body.messages[-1]["content"]
            )
            del body.tools
            del body.tool_choice
            extra_json = {
                "message_id": message_id,
                "status": None,
                "model": body.model,
            }
            header = [
                {
                    **extra_json,
                    "content": f"{tool_result}",
                    "tool_call": tool.get_name(),
                    "tool_output": tool_result.data,
                    "is_ref": False if tool.return_direct else True,
                    "Content-Type": "application/json",
                    "Authorization": "bd111f3f2fadb2e914ed1a55ce1453c6.79ZDHSS4xBMi4Sgw"
                }
            ]
            if tool.return_direct:
                def temp_gen():
                    yield OpenAIChatOutput(**header[0]).model_dump_json()

                return EventSourceResponse(temp_gen())
            else:
                # return await openai_request(
                #     client.chat.completions.create,
                #     body,
                #     extra_json=extra_json,
                #     header=header,
                # )
                return client.chat.completions.create(model="emohaa",  # 填写需要调用的模型名称
                                                      meta={
                                                          "user_info": "30岁的男性软件工程师，兴趣包括阅读、徒步和编程",
                                                          "bot_info": "Emohaa是一款基于Hill助人理论的情感支持AI，拥有专业的心理咨询话术能力",
                                                          "bot_name": "Emohaa",
                                                          "user_name": "张三"
                                                      },
                                                      messages=body.messages,
                                                      stream=False)

    # agent chat with tool calls
    if body.tools:
        try:
            message_id = (
                add_message_to_db(
                    chat_type="agent_chat",
                    query=body.messages[-1]["content"],
                    conversation_id=conversation_id,
                )
                if conversation_id
                else None
            )
        except Exception as e:
            logger.warning(f"failed to add message to db: {e}")
            message_id = None

        chat_model_config = {}  # TODO: 前端支持配置模型
        tool_names = [x["function"]["name"] for x in body.tools]
        tool_config = {name: get_tool_config(name) for name in tool_names}
        result = await chat(
            query=body.messages[-1]["content"],
            metadata=extra.get("metadata", {}),
            conversation_id=extra.get("conversation_id", ""),
            message_id=message_id,
            history_len=-1,
            history=body.messages[:-1],
            stream=body.stream,
            chat_model_config=extra.get("chat_model_config", chat_model_config),
            tool_config=extra.get("tool_config", tool_config),
            max_tokens=body.max_tokens,
        )
        return result
    else:  # LLM chat directly
        try:  # query is complex object that unable add to db when using qwen-vl-chat
            message_id = (
                add_message_to_db(
                    chat_type="llm_chat",
                    query=body.messages[-1]["content"],
                    conversation_id=conversation_id,
                )
                if conversation_id
                else None
            )
        except Exception as e:
            logger.warning(f"failed to add message to db: {e}")
            message_id = None

        extra_json = {
            "message_id": message_id,
            "status": None,
        }
        db = SQLAlchemy()
        class Current(db.Model):
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(80), unique=True, nullable=False)
            info = db.Column(db.String(200), unique=False, nullable=False)

        DATABASE_URI = 'sqlite://demos/chat_demos/instance/users.db'

        # 创建数据库引擎
        engine = create_engine(DATABASE_URI)

        # 创建Session类
        Session = sessionmaker(bind=engine)

        # 创建Session实例
        session = Session()

        # 查询所有用户
        users = [session.query(Current).first()]

        # 打印用户信息
        for user in users:
            print(f'ID: {user.id}, Name: {user.name}, Info: {user.info}')

        user_info = user.info
        user_name = user.name
        # 关闭Session
        session.close()
        meta_info={
          # "user_info": "30岁的男性软件工程师，兴趣包括阅读、徒步和编程",
            "user_info": user_info,
            "bot_info": "MetaBCI-Emo是一款基于Hill助人理论的情感支持AI，拥有专业的心理咨询话术能力",
            "bot_name": "MetaBCI-Emo",
            # "user_name": "张三"
            "user_name": user_name,
        }
        extra_json["meta_info"] = meta_info
        responses = await openai_request(
            client.chat.completions.create, body, extra_json=extra_json
        )

        # async for chunk in responses.body_iterator:
        #     print(chunk)
        return responses