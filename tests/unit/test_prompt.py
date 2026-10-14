"""Unit tests for ``app.utils.prompt`` (pure logic, stdlib-only).

Covers message flattening and multimodal attachment extraction for both the
OpenAI (``image_url``) and Claude (``image.source``) content shapes.
"""

import base64

from app.utils import prompt

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


class TestBuildPromptFromMessages:
    def test_roles_are_labeled(self):
        out = prompt.build_prompt_from_messages(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
            ]
        )
        assert "Human: hi" in out
        assert "Assistant: yo" in out

    def test_system_param_prefixed(self):
        out = prompt.build_prompt_from_messages([{"role": "user", "content": "q"}], system="be nice")
        assert out.startswith("System: be nice")

    def test_developer_role_labeled_as_system(self):
        out = prompt.build_prompt_from_messages([{"role": "developer", "content": "instructions"}])
        assert out == "System: instructions"

    def test_list_content_blocks_flattened(self):
        out = prompt.build_prompt_from_messages(
            [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
        )
        assert "Human: a\nb" in out

    def test_tool_prompt_appended(self):
        out = prompt.build_prompt_from_messages([{"role": "user", "content": "x"}], tool_prompt="TOOLS")
        assert out.rstrip().endswith("TOOLS")


class TestBuildPromptFromMessagesNonStrContentR5:
    """R5 硬化：content 目前各协议模型都只产出 str/list（dict/number 理论上不可达），但一次
    模型放宽就会让 "\\n".join([content, *call_parts]) 直接 TypeError 变成 500（实测复现）。
    这里验证非 str content 不再抛异常，且既有 str/list 行为逐字节不变。"""

    def test_dict_content_with_tool_calls_does_not_raise(self):
        out = prompt.build_prompt_from_messages([{
            "role": "user", "content": {"foo": "bar"},
            "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
        }])
        assert "[Tool call: x({})]" in out
        assert "{'foo': 'bar'}" in out

    def test_number_content_with_tool_calls_does_not_raise(self):
        out = prompt.build_prompt_from_messages([{
            "role": "user", "content": 42,
            "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
        }])
        assert "42" in out
        assert "[Tool call: x({})]" in out

    def test_none_content_with_tool_calls_still_works(self):
        """None 走既有的 `or ""` 兜底成空串，不受本次改动影响（回归守卫）。"""
        out = prompt.build_prompt_from_messages([{
            "role": "user", "content": None,
            "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
        }])
        assert "[Tool call: x({})]" in out
        assert "None" not in out

    def test_dict_content_without_tool_calls_unaffected(self):
        """没有 tool_calls 时旧代码本就不抛（f-string 隐式 str()），这里确认新分支
        不改变这条既有可用路径的输出。"""
        out = prompt.build_prompt_from_messages([{"role": "user", "content": {"foo": "bar"}}])
        assert out == "Human: {'foo': 'bar'}"

    def test_existing_str_and_list_behavior_byte_identical(self):
        """零回归：既有 str/list content 的输出必须逐字节不变（新分支只在两者都不是时触发）。"""
        str_out = prompt.build_prompt_from_messages([{"role": "user", "content": "hi"}])
        assert str_out == "Human: hi"

        list_out = prompt.build_prompt_from_messages(
            [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
        )
        assert list_out == "Human: a\nb"

        str_with_tool_calls = prompt.build_prompt_from_messages([{
            "role": "assistant", "content": "thinking...",
            "tool_calls": [{"function": {"name": "run", "arguments": '{"cmd":"ls"}'}}],
        }])
        assert str_with_tool_calls == 'Assistant: thinking...\n[Tool call: run({"cmd":"ls"})]'


class TestExtractAttachments:
    def test_text_only_returns_empty(self):
        assert prompt.extract_attachments([{"role": "user", "content": "plain text"}]) == []

    def test_openai_data_uri(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"}}
                ],
            }
        ]
        atts = prompt.extract_attachments(msgs)
        assert len(atts) == 1
        assert atts[0]["mime"] == "image/png"
        assert atts[0]["filename"].endswith(".png")
        assert isinstance(atts[0]["data"], (bytes, bytearray))

    def test_openai_http_url(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
            }
        ]
        atts = prompt.extract_attachments(msgs)
        assert atts == [{"url": "https://x/y.png", "filename": "image_0", "mime": ""}]

    def test_claude_base64_source(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": _PNG_B64},
                    }
                ],
            }
        ]
        atts = prompt.extract_attachments(msgs)
        assert len(atts) == 1
        assert atts[0]["mime"] == "image/jpeg"
        assert atts[0]["filename"].endswith(".jpg")

    def test_invalid_data_uri_skipped(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,@@@notb64"}}],
            }
        ]
        # _parse_image_url 对非法 base64 返回 None → 被跳过
        assert prompt.extract_attachments(msgs) == []


class TestParseImageUrl:
    def test_http(self):
        assert prompt._parse_image_url("http://a/b", 0) == {
            "url": "http://a/b",
            "filename": "image_0",
            "mime": "",
        }

    def test_non_url_returns_none(self):
        assert prompt._parse_image_url("notaurl", 0) is None
        assert prompt._parse_image_url("", 0) is None
