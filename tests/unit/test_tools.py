"""Unit tests for ``app.utils.tools`` (pure logic, stdlib-only).

These characterize the image-intent detection and the prompt-simulated
tool-call parsing so future refactors cannot silently change behavior.
"""

import pytest

from app.utils import tools


class TestImageGenerationIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "画一只猫",
            "帮我画一个 logo",
            "生成一张图",
            "做一张海报",
            "generate an image of a cat",
            "create a picture of the moon",
            "a photo of a dog",
        ],
    )
    def test_positive(self, text):
        assert tools.is_image_generation_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "写一份报告",
            "create a plan",          # 收窄：无图像词，不应触发
            "tell me about cats",
            "总结一下这段文字",
        ],
    )
    def test_negative(self, text):
        assert tools.is_image_generation_intent(text) is False

    def test_draw_a_conclusion_is_not_image_intent(self):
        """曾经的 xfail：'draw a' 子串命中 'draw a conclusion'，已由词边界 + 习语宾语负向前瞻修复。"""
        assert tools.is_image_generation_intent("please draw a conclusion") is False

    @pytest.mark.parametrize(
        "text",
        ["draw a cat", "draw an elephant", "draw a robot in space", "please draw a cat for me"],
    )
    def test_draw_a_an_without_image_noun_is_still_image_intent(self, text):
        """回归守卫：'draw a/an' 后面不跟图像名词（"draw a cat"）也必须命中——这才是最
        常见的英文生图请求。曾经要求「后随图像名词」的写法会把这些也一并误杀。"""
        assert tools.is_image_generation_intent(text) is True


class TestR4RevertKeepsIdiomsExcluded:
    """R4 曾经把 map/maps、card/cards、line/lines 移出 _DRAW_ARTICLE_IDIOM_OBJECTS，
    理由是它们既是习语宾语也是真实可画之物（真歧义）——"draw a map of the kingdom"、
    "draw a card for my friend" 是合理的画图请求。该改动经线上复核实测证实弊大于利，
    已回退：is_image_generation_intent 只在客户端声明了 tools 时才有效果，它决定的是
    要不要压制/丢弃这些 tools。假阳性（习语被误判成生图意图）会让 tools 被静默丢弃、
    agent 的工具循环无声断掉，没有报错没有日志；假阴性（真实生图请求未被识别）只是
    这一次生图被压制成文本回复，用户看得见、能立刻换说法重试。两者代价不对称，因此
    安全方向是排除更多习语而非更少，map/card/line 三组必须留在闭集里。"""

    @pytest.mark.parametrize(
        "text",
        [
            "draw a map of the kingdom",
            "draw a card for my friend",
            "draw a line drawing of a cat",
            "draw a line under this discussion",
        ],
    )
    def test_ambiguous_draw_requests_stay_excluded(self, text):
        assert tools.is_image_generation_intent(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "draw a conclusion",
            "draw an outline of the plan",
            "draw a distinction between them",
        ],
    )
    def test_purely_abstract_idioms_still_excluded(self, text):
        """无歧义的纯抽象习语宾语（conclusion/outline/distinction 等）本来就在闭集里，
        不受这次回退影响，继续被排除在图片意图之外。"""
        assert tools.is_image_generation_intent(text) is False


class TestAsciiIntentReGuard:
    def test_empty_pattern_table_never_matches_everything(self, monkeypatch):
        """若关键词表被清空，alts 为空时绝不能退化成 re.compile("")——那会匹配任意
        字符串，等于把 has_tools 判定打回「全体误判」的老路。"""
        monkeypatch.setattr(tools, "_IMAGE_INTENT_PATTERNS", ())
        compiled = tools._build_ascii_intent_re()
        assert compiled.search("hello world") is None
        assert compiled.search("") is None


class TestMaybeImageGenerationIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "画一只猫",                 # 严格命中也应为 True
            "给我做个壁纸",             # 名词(壁纸)+动词(做/给我)
            "I want a wallpaper",       # noun(wallpaper)+verb(want)
            "搞个头像",
        ],
    )
    def test_positive(self, text):
        assert tools.maybe_image_generation_intent(text) is True

    @pytest.mark.parametrize("text", ["", "今天天气真好", "讲个笑话"])
    def test_negative(self, text):
        assert tools.maybe_image_generation_intent(text) is False

    def test_superset_of_strict(self):
        strict_positive = "generate an image of a fox"
        assert tools.is_image_generation_intent(strict_positive) is True
        assert tools.maybe_image_generation_intent(strict_positive) is True


# 修复 "draw a"/"draw an" 的过度收窄（改用负向前瞻）后，_IMG_NOUNS 里为兼容旧版
# maybe_image_generation_intent 而补的 "draw a"/"draw an" 是否还需要保留？
# 用 pre-efbdd1d 的裸子串匹配重建期望值，逐例比对现在的 maybe_image_generation_intent。
_PRE_EFBDD1D_IMG_NOUNS = ("图", "图片", "图像", "海报", "插画", "照片", "壁纸", "logo", "头像", "封面",
                          "image", "picture", "poster", "photo", "drawing", "illustration",
                          "wallpaper", "avatar")


def _pre_efbdd1d_is_intent(text: str) -> bool:
    """efbdd1d 之前 is_image_generation_intent 的实现：无词边界的裸子串匹配。
    关键词表本身自 efbdd1d 起未改一字，所以直接复用 tools._IMAGE_INTENT_PATTERNS。"""
    if not text:
        return False
    return any(p in text.lower() for p in tools._IMAGE_INTENT_PATTERNS)


def _pre_efbdd1d_maybe(text: str) -> bool:
    """efbdd1d 之前 maybe_image_generation_intent 的实现（_IMG_NOUNS 不含 "draw a"/"draw an"）。"""
    if not text:
        return False
    if _pre_efbdd1d_is_intent(text):
        return True
    low = text.lower()
    has_noun = any(n in low for n in _PRE_EFBDD1D_IMG_NOUNS)
    has_verb = any(v in low for v in tools._IMG_VERBS)
    return has_noun and has_verb


class TestMaybeImageGenerationIntentParity:
    """FIX 2 的证据：_IMG_NOUNS 里的 "draw a"/"draw an" 是否还需要保留，取决于去掉它
    是否会让 maybe_image_generation_intent 偏离 efbdd1d 之前的行为。逐例比对下方样本，
    覆盖「原本的过度收窄」新排除的习语闭集（is_image_generation_intent 现在对它们返回
    False，但宽松判断在 efbdd1d 之前对它们是 True）——这正是决定保留与否的关键样本。
    """

    @pytest.mark.parametrize(
        "text",
        [
            # 最常见生图请求（现在严格判断已直接命中，不再依赖 _IMG_NOUNS 里的 "draw a/an"）
            "draw a cat", "draw an elephant", "draw a robot in space",
            "draw a picture of a cat", "generate an image of a cat",
            # 习语闭集：严格判断现在对它们返回 False，宽松判断必须靠 _IMG_NOUNS 里的
            # "draw a"/"draw an" 才能保持和 efbdd1d 之前一致（True）
            "please draw a conclusion", "draw an outline of the plan",
            "we should draw a distinction between the two",
            "draw a line under this discussion",
            "let me draw an inference from the logs",
            "draw a card from the deck", "draw a salary of 50k",
            "draw a crowd", "draw a map of the region",
            # 无关文本（两边都应为 False）
            "今天天气真好", "讲个笑话", "create a plan", "",
            "画一只猫",
        ],
    )
    def test_current_matches_pre_efbdd1d(self, text):
        assert tools.maybe_image_generation_intent(text) == _pre_efbdd1d_maybe(text), (
            f"{text!r}: current={tools.maybe_image_generation_intent(text)} "
            f"pre-efbdd1d={_pre_efbdd1d_maybe(text)}"
        )

    def test_reverting_img_nouns_edit_would_break_parity(self, monkeypatch):
        """反证：把 "draw a"/"draw an" 从 _IMG_NOUNS 里去掉（模拟 revert），习语闭集样本
        就会偏离 efbdd1d 之前的行为——证明这条 _IMG_NOUNS 边不能删，FIX 2 结论是保留。"""
        reverted_nouns = tuple(n for n in tools._IMG_NOUNS if n not in ("draw a", "draw an"))
        monkeypatch.setattr(tools, "_IMG_NOUNS", reverted_nouns)

        idiom_samples = [
            "please draw a conclusion", "draw an outline of the plan",
            "we should draw a distinction between the two",
        ]
        mismatches = [t for t in idiom_samples
                     if tools.maybe_image_generation_intent(t) != _pre_efbdd1d_maybe(t)]
        assert mismatches == idiom_samples, (
            "预期这些习语样本在去掉 _IMG_NOUNS 的 draw a/an 后会与 efbdd1d 之前的行为不一致"
        )


class TestParseToolResponse:
    def test_plain_tool_call(self):
        raw = '{"status": "tool_use", "tool_calls": [{"name": "run", "arguments": {"cmd": "ls"}}]}'
        out = tools.parse_tool_response(raw)
        assert out["type"] == "tool_calls"
        assert out["tool_calls"][0]["name"] == "run"
        assert out["tool_calls"][0]["arguments"] == {"cmd": "ls"}

    def test_markdown_fenced_tool_call(self):
        raw = '```json\n{"status":"tool_use","tool_calls":[{"name":"f","arguments":{}}]}\n```'
        out = tools.parse_tool_response(raw)
        assert out["type"] == "tool_calls"
        assert out["tool_calls"][0]["name"] == "f"

    def test_text_status(self):
        out = tools.parse_tool_response('{"status": "text", "content": "hello"}')
        assert out == {"type": "text", "content": "hello"}

    def test_plain_text_passthrough(self):
        out = tools.parse_tool_response("just a normal reply")
        assert out == {"type": "text", "content": "just a normal reply"}

    def test_arguments_as_json_string_normalized(self):
        raw = '{"tool_calls": [{"name": "g", "arguments": "{\\"a\\": 1}"}]}'
        out = tools.parse_tool_response(raw)
        assert out["type"] == "tool_calls"
        assert out["tool_calls"][0]["arguments"] == {"a": 1}

    def test_malformed_tool_json_not_passed_through(self):
        # Malformed tool call JSON should not be passed through raw to clients
        raw = '{"status": "tool_use", "tool_calls": [{"name": "x", "argumen'
        out = tools.parse_tool_response(raw)
        assert out["type"] == "text"
        assert "tool call" in out["content"].lower()

    def test_text_status_with_literal_control_chars_unwrapped(self):
        # When model returns {"status": "text", "content": "..."} with unescaped literal newlines/tabs
        raw = '{\n  "status": "text",\n  "content": "Line 1\nLine 2\twith tab"\n}'
        out = tools.parse_tool_response(raw)
        assert out == {"type": "text", "content": "Line 1\nLine 2\twith tab"}

    def test_text_status_fallback_unwrap_on_bad_json(self):
        # Even if the JSON is somehow malformed after content, the text envelope unwrap extracts content
        raw = '{"status": "text", "content": "Unwrapped response content", "extra": broken_syntax}'
        out = tools.parse_tool_response(raw)
        assert out == {"type": "text", "content": "Unwrapped response content"}

    def test_empty_returns_text(self):
        assert tools.parse_tool_response("") == {"type": "text", "content": ""}


class TestBuildToolPrompt:
    def test_no_tools_returns_prompt_unchanged(self):
        assert tools.build_tool_prompt("hi", []) == "hi"

    def test_with_tools_embeds_schema(self):
        out = tools.build_tool_prompt(
            "do it",
            [{"function": {"name": "run", "description": "run cmd", "parameters": {}}}],
        )
        assert "run" in out
        assert "tool_use" in out
        assert "User message: do it" in out
        assert "taking actions" in out

    def test_tool_choice_required(self):
        out = tools.build_tool_prompt("x", [{"function": {"name": "a"}}], tool_choice="required")
        assert "MUST use one of the available tools" in out


class TestHelpers:
    def test_estimate_tokens(self):
        assert tools.estimate_tokens("") == 0
        assert tools.estimate_tokens("abcdefgh") == 2  # len 8 // 4

    def test_extract_json_object(self):
        assert tools._extract_json_object('noise {"a": 1} tail') == '{"a": 1}'
        assert tools._extract_json_object("no json here") is None

    def test_strip_code_fence(self):
        assert tools._strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
        assert tools._strip_code_fence("plain") == "plain"
