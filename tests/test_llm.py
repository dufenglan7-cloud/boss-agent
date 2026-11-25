# 通过 HTTPX MockTransport 验证模型协议，全程不请求真实服务商。
import os
import unittest
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from boss_agent.llm import JsonModel
from boss_agent.models import Match

# 本模块测试组；所有业务数据仅使用虚构样例和临时资源。
class ModelTests(unittest.TestCase):
    # 使用内存 HTTP 传输构造响应，不向真实接口发送请求。
    def request(self, content, status=200):
        seen = []
        real_client = httpx.Client
        # 使用内存 HTTP 传输构造响应，不向真实接口发送请求。
        def handler(request):
            seen.append(request)
            return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})
        with patch.dict(os.environ, {"BOSS_AGENT_API_KEY": "test-not-real", "BOSS_AGENT_MODEL": "test-model", "BOSS_AGENT_BASE_URL": "https://example.invalid/v1/"}):
            model = JsonModel()
        client = real_client(transport=httpx.MockTransport(handler))
        try:
            with patch("boss_agent.llm.httpx.Client", return_value=client):
                result = model.ask("Match", {"resume": "Python"}, Match)
        finally:
            client.close()
        return result, seen

    # 兼容模型请求地址和 JSON 结果需符合约定。
    def test_structured_response(self):
        result, seen = self.request('{"job_id":"1","decision":"consider","evidence":[]}')
        self.assertEqual(result.decision, "consider")
        self.assertEqual(str(seen[0].url), "https://example.invalid/v1/chat/completions")
        self.assertEqual(len(seen), 1)

    # 不在业务枚举中的模型结果必须拒绝。
    def test_invalid_model_output(self):
        with self.assertRaises(ValidationError):
            self.request('{"job_id":"1","decision":"invented","evidence":[]}')

    # 上游 HTTP 错误应向业务层传播，不假装返回有效模型结果。
    def test_upstream_error(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.request("error", status=429)
