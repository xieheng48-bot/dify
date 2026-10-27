import httpx

from .config import Config
from .service import BusinessError


async def chat(config: Config, user: str, query: str, facts: dict) -> dict:
    if not config.dify_api_base:
        return {"mode": "offline_rules", "answer": facts["reason"], "citation": facts["citation"],
                "note": "本地规则演示，没有调用大模型；未创建任何工单。"}
    prompt = ("请依据下面服务端核验结果解释售后规则，不得声称已退款或已创建工单。\n"
              f"核验结果：{facts['reason']}\n政策：{facts['citation']['text']}\n用户问题：{query}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            response = await client.post(config.dify_api_base + "/chat-messages", headers={
                "Authorization": "Bearer " + config.dify_api_key}, json={
                    "inputs": {}, "query": prompt, "response_mode": "blocking", "user": user})
            response.raise_for_status()
            data = response.json()
            answer = data.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("missing answer")
    except httpx.TimeoutException as exc:
        raise BusinessError(504, "dify_timeout", "Dify 响应超时，未执行任何业务写入") from exc
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise BusinessError(502, "dify_error", "Dify 调用失败，请检查服务配置；未执行任何业务写入") from exc
    return {"mode": "dify_chatflow", "answer": answer, "citation": facts["citation"],
            "note": "模型仅解释服务端核验结果，不执行工单或退款。"}
